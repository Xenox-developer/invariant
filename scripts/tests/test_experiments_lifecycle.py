"""M3 durable control semantics and replay exclusions, independent of Docker."""
import base64
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch

from invariant import workspace
from invariant.execution import project_lock
from invariant.experiments.errors import ExperimentError
from invariant.experiments.runners import BoundedLog
import test_experiments as base


class LifecycleTests(unittest.TestCase):
    setUp = base.EngineTests.setUp
    unseal = base.EngineTests.unseal
    change = base.EngineTests.change
    submit = base.EngineTests.submit

    def test_detached_credentials_use_reference_values_and_saved_launch_paths(self):
        from invariant.experiments.lifecycle import references
        secret=self.root/'credential';secret.write_text('test-secret');secret.chmod(0o600)
        self.change(lambda value:value.update(secret_refs={'provider':'ACCOUNT'}))
        with patch.dict(os.environ,{'INVARIANT_SECRET_ACCOUNT':str(secret)}):
            study=self.submit()
            references(self.engine,study,update=True)
        with patch.dict(os.environ,{'INVARIANT_SECRET_ACCOUNT':'/stale/daemon/path'}):
            self.assertEqual(references(self.engine,study),{'INVARIANT_SECRET_ACCOUNT':str(secret)})
        saved=self.engine.root/'references'/(study['id']+'.json')
        self.assertNotIn('test-secret',saved.read_text())

    def test_launcher_uses_trusted_imports_and_detached_stdio(self):
        study=self.submit()
        with patch('invariant.experiments.lifecycle.subprocess.Popen') as spawn:
            self.engine.launch(study['id'])
        arguments=spawn.call_args.args[0];options=spawn.call_args.kwargs
        self.assertIn('-I',arguments)
        self.assertNotEqual(Path(options['cwd']),self.project)
        self.assertTrue(options['start_new_session'])
        self.assertTrue(options['close_fds'])
        self.assertEqual(self.engine.inspect(study['id'])['dispatch']['state'],'queued')

    def test_stop_commits_before_claim_and_is_idempotent(self):
        study = self.submit()
        stopped = self.engine.store.stop(study['id'], request_id='cancel')
        self.assertEqual(len(stopped['attempt_ids']), 4)
        self.assertEqual(self.engine.store.stop(study['id'], request_id='cancel'), stopped)
        for trial in study['trials']:
            self.assertIsNone(self.engine.store.claim(study['id'], trial['id'], 'late'))
        self.assertEqual({t['state'] for t in self.engine.inspect(study['id'])['trials']}, {'cancelled'})
        with self.assertRaisesRegex(ExperimentError, 'different operation'):
            self.engine.retry(study['trials'][0]['id'], request_id='cancel', dispatch=False)
        self.assertTrue(self.engine.inspect(request_id='cancel')['stop_accepted'])
        with self.assertRaisesRegex(ExperimentError,'not both'):
            self.engine.inspect(study['id'],request_id='cancel')

    def test_retry_adds_history_not_better_score_and_request_replay_does_not_add(self):
        study = self.submit();trial = study['trials'][0]
        self.engine.store.stop(study['id'])
        result = self.engine.retry(trial['id'], request_id='retry-1', dispatch=False)
        self.assertEqual(result, self.engine.retry(trial['id'], request_id='retry-1', dispatch=False))
        attempt = self.engine.store.claim(study['id'], trial['id'], 'new-controller')
        self.assertEqual(attempt['number'], 2)
        self.assertNotEqual(attempt['id'], trial['attempts'][0]['id'])
        self.engine.store.update_attempt(attempt['id'], 'succeeded', {'cleanup_confirmed': True,
                     'runner': {'status': 'completed'}, 'evaluation': {'status': 'completed','passed': True, 'metrics': {}}})
        self.assertEqual(self.engine.results(study['id'])['summary']['runner_successes'], 0)
        self.assertEqual(len(self.engine.inspect(trial['id'])['attempts']), 2)
        with self.assertRaisesRegex(ExperimentError, 'control operation'):
            self.submit(request_id='retry-1')

    def test_recover_never_requeues_claimed_missing_container(self):
        study = self.submit();trial = study['trials'][0]
        attempt = self.engine.store.claim(study['id'], trial['id'], 'dead-controller')
        self.engine.store.dispatch(study['id']);self.engine.store.dispatch_state(study['id'], 'running')
        with (patch('invariant.experiments.lifecycle.stop_owned',return_value=True),
              patch('invariant.experiments.lifecycle.owned_info',return_value=None),
              patch('invariant.experiments.lifecycle.wake') as launch):
            result = self.engine.recover(study['id'], request_id='recover-1')
            self.assertEqual(result['recovered_attempt_ids'], [attempt['id']])
            self.assertTrue(result['queued_dispatched'])
            launch.assert_called_once()
            replay = self.engine.recover(study['id'], request_id='recover-1')
            self.assertEqual(replay, result)
            self.assertEqual(launch.call_count,2)
        self.assertEqual(self.engine.inspect(attempt['id'])['state'], 'interrupted')
        releases=[e for e in self.engine.inspect(study['id'])['events'] if e['kind']=='resources_released']
        self.assertEqual(len(releases),1)
        self.assertEqual(self.engine.inspect(trial['id'])['attempts'][0]['number'], 1)
        self.assertEqual(self.engine.retry(trial['id'],dispatch=False)['number'],2)

    def test_unreachable_docker_remains_blocked_and_never_dispatches(self):
        study=self.submit();trial=study['trials'][0]
        attempt=self.engine.store.claim(study['id'],trial['id'],'dead')
        with (patch('invariant.experiments.lifecycle.stop_owned',side_effect=RuntimeError('Docker unavailable')),
              patch('invariant.experiments.lifecycle.wake') as launch):
            result=self.engine.recover(study['id'])
            self.assertEqual(result['unresolved_attempt_ids'],[attempt['id']])
            launch.assert_not_called()
        with self.assertRaisesRegex(ExperimentError,'confirmed process stop'):
            self.engine.retry(trial['id'],dispatch=False)
        self.assertEqual(len(self.engine.store.blockers()),1)

    def test_recovery_refuses_live_controller_even_with_old_heartbeat(self):
        study=self.submit()
        lease=project_lock(self.engine.root/'controller')
        try:
            with self.assertRaisesRegex(ExperimentError,'controller is active'):
                self.engine.recover(study['id'])
        finally:lease.close()

    def test_resumable_log_cursor_keeps_redacted_bytes_exactly_once(self):
        study=self.submit();attempt=study['trials'][0]['attempts'][0]
        events=lambda kind,data:self.engine.store.append_log(study['id'],attempt['id'],'runner',kind,data)
        log=BoundedLog(self.root/'follow.log',100,[b'credential'],on_log=events)
        log.write(b'hello cred');log.write(b'ential world \xe2\x82');log.write(b'\xac\n');log.close()
        iterator=self.engine.log_frames(study['id']);first=next(iterator);iterator.close()
        tail=list(self.engine.log_frames(study['id'],after=first['cursor']))
        records=[first,*tail[:-1]]
        content=b''.join(base64.b64decode(row['data']['data_base64']) for row in records if row['kind']=='log_chunk')
        self.assertEqual(content,b'hello [REDACTED] world \xe2\x82\xac\n')
        sequences=[row['sequence'] for row in records]
        self.assertEqual(len(sequences),len(set(sequences)))
        self.assertEqual(sequences,sorted(sequences))
        with self.assertRaisesRegex(ExperimentError,'another project/scope'):
            list(self.engine.log_frames(attempt['id'],after=first['cursor']))

    def test_follow_delivers_final_flush_and_queued_stop_frames(self):
        study=self.submit()
        received=[]
        def follow():received.extend(self.engine.log_frames(study['id'],follow=True))
        viewer=threading.Thread(target=follow);viewer.start()
        self.engine.store.stop(study['id'])
        viewer.join(timeout=3)
        self.assertFalse(viewer.is_alive())
        self.assertEqual(sum(row['kind']=='attempt_end' for row in received),4)
        self.assertTrue(received[-1]['terminal'])

    def test_delete_refuses_active_and_preview_does_not_mutate(self):
        study=self.submit()
        with self.assertRaisesRegex(ExperimentError,'queued, active'):
            self.engine.delete(study['id'])
        self.engine.store.stop(study['id'])
        with patch('invariant.experiments.deletion.owned_info',return_value=None):
            plan=self.engine.delete(study['id'])
            self.assertFalse(plan['deleted'])
            self.assertEqual(self.engine.inspect(study['id'])['id'],study['id'])
            deleted=self.engine.delete(study['id'],confirm=True)
            self.assertTrue(deleted['deleted'])
            self.assertTrue(self.engine.delete(study['id'],confirm=True)['deleted'])
        self.assertEqual(self.engine.list()['studies'],[])

    def test_deleting_checkpoint_keeps_untagged_frozen_parent_images(self):
        from invariant import environment
        study=self.submit();self.engine.store.stop(study['id'])
        attempt=study['trials'][0]['attempts'][0]
        image='sha256:'+'3'*64;owner=attempt['id']+'-runner'
        workspace.private_write(self.engine.root/'attempts'/attempt['id']/'control/environment.json',
                                {'schema':1,'project':str(self.project),'image':image,'owner':owner})
        retained={image,study['frozen']['image']['id']}
        def docker(*args,**kwargs):
            if args[:2]==('image','inspect'):
                return json.dumps([{'Config':{'Labels':{'io.invariant.checkpoint-project':environment.project_key(self.project),
                                                        'io.invariant.checkpoint-owner':owner}}}])
            if args[:2]==('image','rm'):
                retained.discard(image)
                if '--no-prune' not in args:retained.clear()
                return ''
            self.fail('Unexpected Docker mutation '+str(args))
        with patch('invariant.experiments.deletion.owned_info',return_value=None),patch.object(workspace,'docker',side_effect=docker):
            self.engine.delete(study['id'],confirm=True)
        self.assertEqual(retained,{study['frozen']['image']['id']})

    def test_study_deletion_retains_shared_baseline(self):
        first=self.submit(request_id='one');second=self.submit(request_id='two')
        self.assertEqual(first['baseline_id'],second['baseline_id'])
        self.engine.store.stop(first['id'])
        with patch('invariant.experiments.deletion.owned_info',return_value=None):
            deleted=self.engine.delete(first['id'],confirm=True)
        self.assertTrue(deleted['baseline_retained_for_other_studies'])
        self.assertEqual(self.engine.inspect(second['id'])['baseline_id'],second['baseline_id'])

    def test_trial_stop_keeps_sibling_dispatchable(self):
        study=self.submit();first,second=study['trials'][:2]
        self.engine.store.stop(first['id'])
        self.assertIsNone(self.engine.store.claim(study['id'],first['id'],'controller'))
        self.assertIsNotNone(self.engine.store.claim(study['id'],second['id'],'controller'))

    def test_connection_lifetimes_remain_private_under_status_log_polling(self):
        study=self.submit();attempt=study['trials'][0]['attempts'][0]
        errors=[]
        def poll():
            try:
                for _ in range(50):
                    self.engine.inspect(study['id'])
                    self.engine.store.append_log(study['id'],attempt['id'],'runner','log_chunk',{'data_base64':'eA=='})
            except Exception as error:errors.append(error)
        workers=[threading.Thread(target=poll) for _ in range(4)]
        for worker in workers:worker.start()
        for worker in workers:worker.join(timeout=20)
        self.assertFalse(errors,errors)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        frames=[];after=0
        while batch:=self.engine.store.log_frames(study['id'],after):
            frames.extend(batch);after=batch[-1]['sequence']
        self.assertEqual(len(frames),200)

    def test_concurrent_stop_and_claim_never_claims_after_commit(self):
        study=self.submit();trial=study['trials'][0]
        self.engine.store.stop(trial['id'])
        results=[]
        threads=[threading.Thread(target=lambda:results.append(self.engine.store.claim(study['id'],trial['id'],'competing'))) for _ in range(8)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(results,[None]*8)


if __name__=='__main__':unittest.main()
