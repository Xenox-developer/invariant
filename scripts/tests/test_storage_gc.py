"""Maintenance preserves continuation authority, evidence and unrelated Docker data."""
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from invariant import storage_gc as gc
from invariant import docker_storage as storage

OLD = '2020-01-01T00:00:00Z'
NEW = '2099-01-01T00:00:00Z'
PROJECT = 'c' * 64
A = 'sha256:' + 'a' * 64
B = 'sha256:' + 'b' * 64
C = 'sha256:' + 'c' * 64


def image(identity=A, *, tagged=False, owned=True, created=OLD, labels=None):
    return {'Id': identity, 'Created': created, 'RepoTags': ['tools:saved'] if tagged else [],
            'Config': {'Labels': labels if labels is not None else {'io.invariant.checkpoint-project': PROJECT} if owned else {}}}


def container(name='invariant-agent-old', *, running=False, role='agent-runtime', created=OLD):
    return {'Id': name + '-id', 'Name': '/' + name, 'Image': B, 'Created': created,
            'State': {'Running': running, 'FinishedAt': OLD},
            'Config': {'Labels': {storage.OWNER: 'v2', 'io.invariant.role': role,
                                  'io.invariant.session': name, 'io.invariant.project': PROJECT,
                                  'io.invariant.owner': 'worker'}}}


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.original_root = gc.root
        self.patcher = patch.object(gc, 'root', return_value=self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def write(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value))
        return target

    def plan(self, containers=(), images=()):
        return gc.plan(self.root, list(containers), list(images), cutoff=1800000000)

    def test_collects_only_old_unreferenced_untagged_owned_images(self):
        self.write('workspaces/other/environment.json', {'schema': 1, 'image': A})
        candidates = self.plan(images=[image(A), image(B), image(C, tagged=True),
                                       image('sha256:'+'d'*64, owned=False), image('sha256:'+'e'*64, created=NEW)])
        self.assertEqual(candidates['images'], [B])

    def test_sqlite_overflow_and_wal_references_protect_other_projects(self):
        path = self.root / 'other.sqlite'
        database = sqlite3.connect(path)
        self.addCleanup(database.close)
        database.execute('PRAGMA journal_mode=WAL')
        database.execute('CREATE TABLE evidence (record TEXT)')
        database.execute('INSERT INTO evidence VALUES (?)', ('x'*4000 + A + 'y'*9000,))
        database.commit()
        before = {p.name: p.read_bytes() for p in self.root.glob('other.sqlite*')}
        self.assertEqual(self.plan(images=[image(A)])['images'], [])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.glob('other.sqlite*')})

    def test_old_log_mentions_do_not_keep_obsolete_checkpoints_forever(self):
        (self.root / 'session-audit.jsonl').write_text(json.dumps({'old_image': A}) + '\n')
        result = self.plan(images=[image(A)])
        self.assertEqual(result['images'], [A])
        self.assertTrue((self.root / 'session-audit.jsonl').exists())

    def test_stopped_foreign_containers_and_in_use_images_are_preserved(self):
        foreign = container('personal-job', role='other')
        self.assertEqual(self.plan([foreign], [image(B)])['images'], [])
        self.assertEqual(self.plan([foreign])['containers'], [])

    def test_stopped_agent_can_expire_but_current_and_recent_agents_cannot(self):
        self.write('workspaces/p/current.json', {'session': 'ws-recover', 'agent': 'invariant-agent-recover'})
        rows = [container(), container('invariant-agent-recover'), container('invariant-agent-new', created=NEW)]
        self.assertEqual(self.plan(rows)['containers'], ['invariant-agent-old-id'])

    def test_unsaved_executor_is_retained_even_when_stopped_and_old(self):
        row = container('invariant-ws-unsaved', role='codex-workspace')
        self.assertEqual(self.plan([row])['containers'], [])

    def test_saved_executor_requires_checkpoint_from_that_exact_session(self):
        row = container('invariant-ws-saved', role='codex-workspace')
        self.write('workspaces/p/environment.json', {'schema': 1, 'image': A})
        labels = {'io.invariant.session': 'invariant-ws-saved',
                  'io.invariant.checkpoint-owner': 'worker', 'io.invariant.checkpoint-project': PROJECT}
        checkpoint = image(A, created='2020-01-02T00:00:00Z', labels=labels)
        self.assertEqual(self.plan([row], [checkpoint])['containers'], [row['Id']])
        checkpoint['Created'] = '2019-01-01T00:00:00Z'
        self.assertEqual(self.plan([row], [checkpoint])['containers'], [])
        checkpoint['Created'] = '2020-01-02T00:00:00Z'
        checkpoint['Config']['Labels']['io.invariant.session'] = 'invariant-another-session'
        self.assertEqual(self.plan([row], [checkpoint])['containers'], [])

    def test_downloads_expire_but_auth_chats_packages_snapshots_and_symlinks_survive(self):
        home = self.root / 'workspaces/p/codex/home'
        cache = home / '.cache/pip/http-v2/old'
        cache.parent.mkdir(parents=True)
        cache.write_text('download')
        os.utime(cache, (1, 1))
        self.write('workspaces/p/codex/auth.json', {'tokens': 'synthetic'})
        self.write('workspaces/p/codex/sessions/chat.json', {'conversation': 'saved'})
        package = home / 'node_modules/installed.js'
        package.parent.mkdir()
        package.write_text('installed')
        snapshot = self.root / 'workspaces/p/experiments/attempts/a/snapshot/workspace/native-home/home/.npm/_cacache/saved'
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text('immutable')
        outside = self.root / 'external-cache'
        outside.write_text('keep')
        (cache.parent / 'link').symlink_to(outside)
        with patch.object(gc, 'inspect_all', return_value=[]), patch.object(storage, 'docker') as docker:
            result = gc.collect(confirm=True, now=1800000000)
        self.assertEqual(len(result['removed']['cache_files']), 1)
        self.assertFalse(cache.exists())
        for path in (package, snapshot, outside, cache.parent/'link', self.root/'workspaces/p/codex/auth.json', self.root/'workspaces/p/codex/sessions/chat.json'):
            self.assertTrue(path.exists())
        docker.assert_not_called()

    def test_shared_lease_defers_cleanup_without_even_listing_docker(self):
        with gc.gate(), patch.object(gc, 'inspect_all') as inspect:
            self.assertEqual(gc.collect(confirm=True)['state'], 'busy')
            inspect.assert_not_called()

    def test_active_executor_defers_cleanup(self):
        with patch.object(gc, 'inspect_all', return_value=[container(running=True)]):
            self.assertEqual(gc.collect(confirm=True)['state'], 'busy')

    def test_preview_is_readonly_and_deletion_never_forces_or_prunes_ancestors(self):
        with patch.object(gc, 'inspect_all', side_effect=lambda kind: {'container': [container()], 'image': [image(A)], 'volume': []}[kind]), patch.object(storage, 'docker', return_value=Mock(returncode=0)) as docker:
            self.assertEqual(gc.collect(now=1800000000)['state'], 'preview')
            docker.assert_not_called()
            self.assertFalse((self.root / 'maintenance/last.json').exists())
            result = gc.collect(confirm=True, now=1800000000)
            self.assertEqual(result['removed']['images'], [A])
            calls = [call.args for call in docker.call_args_list]
            self.assertIn(('image', 'rm', '--no-prune', A), calls)
            self.assertIn(('container', 'rm', 'invariant-agent-old-id'), calls)
            self.assertFalse(any('--force' in call or 'prune' in call for call in calls))

    def test_scheduled_cleanup_is_enabled_by_default_deduplicated_and_configurable(self):
        self.assertTrue(gc.status()['policy']['enabled'])
        with patch.object(gc, 'inspect_all', return_value=[]):
            self.assertEqual(gc.collect(confirm=True, scheduled=True, now=1800000000)['state'], 'completed')
            self.assertEqual(gc.collect(confirm=True, scheduled=True, now=1800000010)['state'], 'not_due')
            gc.configure({'enabled': False})
            self.assertEqual(gc.collect(confirm=True, scheduled=True, now=1800010000)['state'], 'not_due')
        with self.assertRaises(ValueError):gc.configure({'older_than_days': -1})
        self.assertFalse(gc.status()['policy']['enabled'])

    def test_incomplete_reference_discovery_aborts_before_any_deletion(self):
        (self.root / 'broken.sqlite').write_bytes(b'not a database')
        with patch.object(gc, 'inspect_all', return_value=[]), patch.object(storage, 'docker') as docker:
            with self.assertRaises(sqlite3.DatabaseError):gc.collect(confirm=True)
            docker.assert_not_called()

    def test_host_cannot_create_private_maintenance_state(self):
        with patch.object(storage, 'managed', return_value=False), patch.object(Path, 'home') as home:
            # The real root() has no host-side effects.
            with self.assertRaises(RuntimeError):self.original_root()
            home.assert_not_called()

    def test_only_unmounted_old_runtime_code_volumes_are_disposable(self):
        def volume(name):return {'Name':name, 'Mountpoint':'/volumes/'+name,
                                 'CreatedAt':OLD, 'Labels':{storage.OWNER:'v2'}}
        obsolete = volume(storage.RUNTIME_VOLUME_PREFIX+'a'*16)
        used = volume(storage.RUNTIME_VOLUME_PREFIX+'b'*16)
        state = volume(storage.STATE_VOLUME)
        row = container()
        row['Mounts'] = [{'Source':used['Mountpoint']+'/invariant'}]
        result = gc.plan(self.root, [row], [], cutoff=1800000000, volumes=[obsolete, used, state])
        self.assertEqual(result['code_volumes'], [obsolete['Name']])

    def test_unfinished_migration_blocks_cleanup(self):
        self.write('migration/owned/journal.json', {'phase':'verified'})
        with patch.object(gc, 'inspect_all') as inspect:
            self.assertEqual(gc.collect(confirm=True)['reason'], 'migration_incomplete')
            inspect.assert_not_called()


if __name__ == '__main__':unittest.main()
