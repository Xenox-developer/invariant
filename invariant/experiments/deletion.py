"""Reference-checked terminal study deletion with a durable cleanup manifest."""
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from .. import environment, workspace
from ..execution import project_lock
from .errors import ExperimentError
from .definitions import digest
from .lifecycle import identities, request_key
from .runners import owned_info
from .snapshots import unseal_tree
from .store import TERMINAL


def delete(engine, identifier, *, confirm=False, request_id=None):
    request_id = request_key(request_id)
    fingerprint = digest({'operation': 'delete', 'identifier': identifier, 'confirm': confirm})
    replay = engine.store.lookup_operation(request_id, fingerprint)
    if replay is not None and not replay.get('operation_pending'):
        return replay
    try:
        lock = project_lock(engine.root / 'controller', message='An experiment controller is active.')
    except RuntimeError as error:
        raise ExperimentError('CONTROLLER_BUSY', str(error), 'Wait for execution to finish before deleting state.') from error
    key = 'deletion:' + identifier
    capture_lock = mutation_lock = None
    try:
        mutation_lock = project_lock(engine.root / 'mutations', timeout=3)
        capture_lock = engine._lock(reconcile=False)
        with engine.store.connection(write=True) as database:
            if database.execute('SELECT 1 FROM selections WHERE study_id=?', (identifier,)).fetchone():
                raise ExperimentError('SELECTED_STATE', 'This study contains the selected attempt.',
                                      'Clear the selection with select --clear STUDY_ID --if-revision REV before deleting it.')
            children = database.execute('SELECT l.child_study_id FROM lineage l JOIN attempts a ON a.id=l.parent_attempt_id JOIN trials t ON t.id=a.trial_id WHERE t.study_id=?',(identifier,)).fetchall()
            if children:
                raise ExperimentError('REFERENCED_STATE','Child studies still reference this result.',
                    'Delete dependent child studies first.',details={'child_study_ids':[r['child_study_id'] for r in children]})
            row = database.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
        if row:
            plan = json.loads(row['value'])
        else:
            study = engine.inspect(identifier)
            if study['kind'] != 'study':
                raise ExperimentError('INVALID_REQUEST', 'delete requires a terminal study ID.')
            attempts = [a for t in study['trials'] for a in t['attempts']]
            if any(a['state'] not in TERMINAL or a['record'].get('cleanup_confirmed') is False for a in attempts):
                raise ExperimentError('ACTIVE_STATE', 'This study has queued, active or unresolved attempts.',
                                      'Stop/recover the study before deleting it.')
            # Discover only checkpoint images belonging to this study's attempts.
            images, containers = {}, []
            for attempt in attempts:
                for phase, identity in identities(engine, attempt):
                    info = owned_info(identity)
                    if info is not None:
                        if info.get('State', {}).get('Running'):
                            raise ExperimentError('ACTIVE_STATE', 'An owned container is still running; recover first.')
                        containers.append(identity.owner_token)
                    checkpoint = environment.read_record(identity.state / 'environment.json')
                    if checkpoint:
                        images[checkpoint['image']] = identity.checkpoint_owner
            references = []
            with engine.store.connection() as database:
                others = database.execute('SELECT id,definition FROM studies WHERE id<>?', (identifier,)).fetchall()
                other_records = database.execute('SELECT a.id,a.record FROM attempts a JOIN trials t ON t.id=a.trial_id WHERE t.study_id<>?', (identifier,)).fetchall()
            for row in others:
                if json.loads(row['definition'])['image']['id'] in images:
                    references.append(row['id'])
            for row in other_records:
                # Saved records refer to image IDs in checkpoints and runtime evidence.
                if any(image in row['record'] for image in images):
                    references.append(row['id'])
            if references:
                raise ExperimentError('REFERENCED_STATE', 'Other saved executions reference this study\'s images.',
                                      'Retain the study until its dependent executions have been deleted.',
                                      details={'references': sorted(set(references))})
            shared = any(json.loads(row['definition'])['baseline_id'] == study['baseline_id'] for row in others)
            paths = ['studies/' + identifier] + ['attempts/' + a['id'] for a in attempts]
            with engine.store.connection() as database:
                plans=[r['id'] for r in database.execute('SELECT id FROM apply_plans WHERE study_id=?',(identifier,))]
            paths += ['apply-plans/'+p for p in plans]
            paths.append('references/' + identifier + '.json')
            if not shared:
                paths += ['baselines/' + study['baseline_id'], 'runtimes/' + study['baseline_id']]
            plan = {'study_id': identifier, 'paths': paths, 'images': images, 'containers': containers,
                    'baseline_retained_for_other_studies': shared, 'deleted': False}
            if confirm:
                # Save before removing anything. A repeated delete resumes this manifest.
                with engine.store.connection(write=True) as database:
                    database.execute('INSERT INTO metadata VALUES (?,?)', (key, json.dumps(plan)))
        if not confirm:
            result = {**plan, 'next': 'Repeat delete STUDY_ID --confirm to remove this saved state.'}
            return engine.store.control_operation(request_id, fingerprint, result, complete=True)
        engine.store.control_operation(request_id, fingerprint)
        # Check ownership each time, including after a partial deletion/restart.
        for name in plan['containers']:
            identity = SimpleNamespace(project=engine.project, owner_token=name,
                checkpoint_owner='attempt-' + name.removeprefix('invariant-exp-'))
            info = owned_info(identity)
            if info is not None:
                workspace.docker('rm', '--force', info['Id'])
        for image, owner in plan['images'].items():
            try:
                info = json.loads(workspace.docker('image', 'inspect', image))[0]
            except RuntimeError as error:
                if 'No such image' in str(error):
                    continue
                raise
            labels = info.get('Config', {}).get('Labels') or {}
            if (labels.get('io.invariant.checkpoint-project') != environment.project_key(engine.project)
                    or labels.get('io.invariant.checkpoint-owner') != owner):
                raise ExperimentError('FOREIGN_IMAGE', 'Refusing to delete an image without matching checkpoint ownership.')
            # Docker otherwise prunes untagged ancestors, including frozen bases
            # retained by other studies or the ordinary prepared environment.
            workspace.docker('image', 'rm', '--no-prune', image, timeout=120)
        for relative in plan['paths']:
            target = engine.root / relative
            if target.is_symlink():
                raise ExperimentError('UNSAFE_STATE', 'Refusing redirected deletion state.')
            if target.is_dir():
                unseal_tree(target); shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        with engine.store.connection(write=True) as database:
            database.execute('DELETE FROM apply_plans WHERE study_id=?',(identifier,))
            database.execute('DELETE FROM lineage WHERE child_study_id=?',(identifier,))
            trial_ids = [r['id'] for r in database.execute('SELECT id FROM trials WHERE study_id=?', (identifier,))]
            for trial_id in trial_ids:
                database.execute('DELETE FROM cancellations WHERE attempt_id IN (SELECT id FROM attempts WHERE trial_id=?)', (trial_id,))
            database.execute('DELETE FROM log_frames WHERE study_id=?', (identifier,))
            database.execute('DELETE FROM attempts WHERE trial_id IN (SELECT id FROM trials WHERE study_id=?)', (identifier,))
            for table in ('trials', 'requests', 'events', 'dispatches'):
                database.execute('DELETE FROM ' + table + ' WHERE study_id=?', (identifier,))
            database.execute('DELETE FROM studies WHERE id=?', (identifier,))
            database.execute('DELETE FROM baselines WHERE id NOT IN (SELECT baseline_id FROM studies)')
            plan['deleted'] = True
            database.execute('UPDATE metadata SET value=? WHERE key=?', (json.dumps(plan), key))
        return engine.store.control_operation(request_id, fingerprint, plan, complete=True)
    finally:
        if capture_lock is not None:
            capture_lock.close()
        if mutation_lock is not None:
            mutation_lock.close()
        lock.close()
