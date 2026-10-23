"""Small editable starters; keyed retries preserve the same planned files."""
import json
import os

from ..execution import project_lock
from .definitions import digest
from .errors import ExperimentError
from .lifecycle import request_key
from .snapshots import regular_bytes


def create(engine, path, *, request_id=None, runner='command'):
    request_id = request_key(request_id)
    if runner not in ('command', 'codex'):
        raise ExperimentError('INVALID_ARGUMENTS', 'Starter runner must be command or codex.')
    relative = engine._path(path)
    target = engine.project / relative
    guide = target.with_suffix('.guide.md')
    prompt = target.with_suffix('.prompt.txt')
    starter = {'schema_version': 1, 'name': 'my-first-study',
               'runner': {'type': 'command', 'argv': ['/bin/true']},
               'variants': [{'id': 'baseline', 'parameters': {}}, {'id': 'alternative', 'parameters': {}}],
               'cases': [{'id': 'example'}], 'repetitions': 1,
               'execution': {'max_parallel': 2}, 'artifacts': ['outputs/**']}
    if runner == 'codex':
        starter.update(runner={'type': 'codex'}, variants=[{'id': 'baseline'}, {'id': 'alternative'}],
                       cases=[{'id': 'example', 'prompt_file': prompt.relative_to(engine.project).as_posix()}])
    guide_text = '''# Experiment starter

Edit the definition before running it. Command starters run two no-op commands;
replace runner.argv with your harness. Arguments are literal, without shell expansion.
Your harness reads INVARIANT_VARIANT_FILE / INVARIANT_CASE_FILE and writes under
INVARIANT_OUTPUT_DIR. Codex starters use a captured case prompt; variants can set
model, effort, instructions_file and patch_file. Omitted models/efforts resolve to
account defaults when the study is frozen; edit them to compare different settings.
Host Codex ChatGPT sign-in and Docker are required. Nothing installs automatically.

Add an evaluator to judge quality. A completed run is not a quality verdict.
validate FILE previews capture; run FILE submits detached trials; run --create-only
freezes without executing. execute STUDY_ID starts a frozen study. Use status,
results, logs ID --follow, stop ID, recover STUDY_ID and retry TRIAL_ID.
select TRIAL_OR_ATTEMPT_ID --if-revision REV saves a choice without changing files.
Read REV from status/results/list; clear with select --clear STUDY_ID --if-revision REV.
Every command accepts --json; mutations accept --request-id. Manual and assistant
clients use the same durable state. Continue a saved result with continue ID --prompt-file FILE.
Review diff ID and apply ID --dry-run, then apply --plan PLAN_ID explicitly.
See docs/experiments/README.md and ASSISTANT_GUIDE.md in the INVARIANT checkout.
'''
    files = {target: json.dumps(starter, indent=2)+'\n', guide: guide_text}
    if runner == 'codex':
        files[prompt] = 'Create outputs/answer.txt containing hello. Do not access the network.\n'
    if len(files) != (3 if runner == 'codex' else 2):
        raise ExperimentError('INVALID_PATH', 'Definition path collides with a starter support file.')
    result = {'definition': str(target), 'guide': str(guide), 'dispatch_available': True}
    if runner == 'codex': result['prompt'] = str(prompt)
    return publish(engine, files, result, request_id=request_id)


def publish(engine, files, result, *, request_id=None, operation='init'):
    """Publish owned support files with the same collision and replay rules."""
    from .engine import sync_directory
    request_id = request_key(request_id)
    for file in files:
        engine._path(file)
    fingerprint = digest({'operation': operation, 'files': {str(p): s for p, s in files.items()}})
    replay = engine.store.lookup_operation(request_id, fingerprint)
    if replay is not None and not replay.get('operation_pending'):
        return replay
    # Without a key init remains a file-only operation and creates no database.
    with project_lock(engine.root / 'mutations', timeout=3), engine._lock():
        replay = engine.store.lookup_operation(request_id, fingerprint)
        if replay is not None and not replay.get('operation_pending'):
            return replay
        for file in files:
            for parent in (file, *file.parents):
                if parent == engine.project: break
                if parent.is_symlink():
                    raise ExperimentError('INVALID_PATH', 'Starter paths must not traverse symlinks.')
            if file.exists():
                if replay is None:
                    raise ExperimentError('ALREADY_EXISTS', 'A starter file already exists; nothing was replaced.')
                if regular_bytes(engine.project, file.relative_to(engine.project).as_posix())[0] != files[file].encode():
                    raise ExperimentError('STARTER_CONFLICT', 'A partially created starter was edited.',
                                          'Keep your edits; use a new path and request ID for a new starter.')
        engine.store.control_operation(request_id, fingerprint)
        for file in files:
            file.parent.mkdir(parents=True, exist_ok=True)
        written = []
        try:
            for file, content in files.items():
                if file.exists(): continue  # Verified keyed partial write above.
                fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'w') as output:
                    written.append(file)
                    output.write(content); output.flush(); os.fsync(output.fileno())
            for parent in {file.parent for file in files}:
                sync_directory(parent)
        except BaseException:
            if request_id is None:
                for file in written: file.unlink(missing_ok=True)
            raise
        return engine.store.control_operation(request_id, fingerprint, result, complete=True)
