"""M5 exercises real filesystem writes and recovery through the public engine."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from invariant import workspace
from invariant.execution import project_lock
from invariant.experiments import cli, tree
from invariant.experiments.definitions import digest
from invariant.experiments.errors import ExperimentError
from invariant.experiments.files import materialize, capture_workspace
from invariant.experiments.snapshots import seal_tree, unseal_tree
import test_experiments as base

IMAGE='sha256:'+'2'*64


class ApplyTests(unittest.TestCase):
    setUp=base.EngineTests.setUp
    unseal=base.EngineTests.unseal
    submit=base.EngineTests.submit
    change=base.EngineTests.change

    def complete(self, mutate=None):
        self.change(lambda v:v.update(repetitions=1))
        study=self.submit()
        trial=study['trials'][0];attempt=self.engine.store.claim(study['id'],trial['id'],'fixture')
        folder=self.engine.root/'attempts'/attempt['id'];folder.mkdir(parents=True)
        baseline=self.engine.root/'baselines'/study['baseline_id']
        manifest=json.loads((baseline/'manifest.json').read_text())
        materialize(baseline/'workspace',folder/'workspace',manifest['entries'])
        if mutate:mutate(folder/'workspace')
        else:
            (folder/'workspace/agent.py').write_text('print("candidate")\n')
            (folder/'workspace/cases/task.json').unlink()
            (folder/'workspace/new/deep').mkdir(parents=True)
            (folder/'workspace/new/deep/tool').write_text('#!/bin/sh\nprintf result\n');(folder/'workspace/new/deep/tool').chmod(0o755)
            (folder/'workspace/result-link').symlink_to('new/deep/tool')
            (folder/'workspace/.env').write_text('candidate credential must not apply')
        snapshot=folder/'snapshot';snapshot.mkdir()
        saved=capture_workspace(folder/'workspace',snapshot/'workspace',study['frozen']['capture_limits'])
        workspace.private_write(snapshot/'manifest.json',saved);seal_tree(snapshot)
        workspace.private_write(folder/'control/environment.json',{'schema':1,'project':str(self.project),'image':IMAGE,'owner':attempt['id']+'-runner'})
        record={'cleanup_confirmed':True,'runner':{'status':'completed'},'evaluation':{'status':'absent','passed':None},
                'capture':{'complete':True,'content_digest':saved['content_digest'],'workspace':str(snapshot/'workspace')},
                'checkpoint':{'complete':True,'image':IMAGE,'owner':attempt['id']+'-runner'}}
        self.engine.store.update_attempt(attempt['id'],'succeeded',record,'COMPLETED')
        self.engine.store.stop(study['id']);self.engine.store.finish_study(study['id'])
        self.engine.select(attempt['id'],revision=0)
        return self.engine.inspect(study['id']),attempt

    def bytes(self):
        return {str(p.relative_to(self.project)):('link',os.readlink(p)) if p.is_symlink() else ('file',p.read_bytes(),p.stat().st_mode&0o777)
                for p in self.project.rglob('*') if p.is_symlink() or p.is_file()}

    def assert_code(self,code,call):
        with self.assertRaises(ExperimentError) as error:call()
        self.assertEqual(error.exception.code,code,str(error.exception))

    def test_plan_readonly_apply_files_modes_links_and_no_environment_effect(self):
        study,attempt=self.complete();before=self.bytes()
        report=self.engine.diff(study['id']);self.assertEqual(before,self.bytes())
        self.assertIn('.env',[r['path'] for r in report['excluded']])
        plan=self.engine.plan_apply(study['id'],request_id='plan');self.assertFalse(plan['conflicts']);self.assertEqual(before,self.bytes())
        self.assertEqual(plan,self.engine.plan_apply(study['id'],request_id='plan'))
        result=self.engine.apply(plan['plan_id'],request_id='apply')
        self.assertTrue(result['applied']);self.assertFalse(result['environment_applied'])
        self.assertEqual((self.project/'agent.py').read_text(),'print("candidate")\n')
        self.assertFalse((self.project/'cases/task.json').exists())
        self.assertEqual(os.readlink(self.project/'result-link'),'new/deep/tool')
        self.assertEqual((self.project/'new/deep/tool').stat().st_mode&0o777,0o755)
        self.assertFalse((self.project/'.env').exists())
        self.assertFalse((self.state/'environment.json').exists())
        self.assertEqual(result,self.engine.apply(plan['plan_id'],request_id='apply'))
        (self.project/'agent.py').write_text('after apply external edit')
        self.assertEqual(result,self.engine.apply(plan['plan_id']))
        self.assertEqual((self.project/'agent.py').read_text(),'after apply external edit')

    def test_preexisting_conflicts_and_stale_plan_do_not_write_any_path(self):
        study,_=self.complete()
        (self.project/'agent.py').write_text('local edit')
        before=self.bytes();plan=self.engine.plan_apply(study['id'])
        self.assertTrue(plan['conflicts'])
        self.assert_code('APPLY_CONFLICT',lambda:self.engine.apply(plan['plan_id']))
        self.assertEqual(before,self.bytes())
        (self.project/'agent.py').write_text('print("original")\n')
        plan=self.engine.plan_apply(study['id']);(self.project/'cases/task.json').write_text('later edit')
        before=self.bytes();self.assert_code('STALE_PLAN',lambda:self.engine.apply(plan['plan_id']));self.assertEqual(before,self.bytes())

    def test_selection_revision_invalidates_review(self):
        study,_=self.complete();plan=self.engine.plan_apply(study['id']);before=self.bytes()
        self.engine.select(study['id'],revision=1,clear=True)
        self.assert_code('STALE_PLAN',lambda:self.engine.apply(plan['plan_id']));self.assertEqual(before,self.bytes())

    def test_new_external_directory_content_and_symlink_ancestry_are_retained(self):
        def remove(w):shutil.rmtree(w/'cases')
        study,_=self.complete(remove)
        (self.project/'cases/untracked').write_text('keep')
        before=self.bytes();plan=self.engine.plan_apply(study['id']);self.assertTrue(plan['conflicts']);self.assertEqual(before,self.bytes())
        (self.project/'cases/untracked').unlink();plan=self.engine.plan_apply(study['id'])
        external=self.root/'external';external.mkdir();(external/'task.json').write_text('outside')
        shutil.rmtree(self.project/'cases');(self.project/'cases').symlink_to(external)
        self.assert_code('STALE_PLAN',lambda:self.engine.apply(plan['plan_id']))
        self.assertEqual((external/'task.json').read_text(),'outside')

    def test_unsafe_link_and_case_collision_are_rejected_before_writes(self):
        for target in ('../../outside','/etc/passwd','.invariant/config.json'):
            with self.subTest(target=target):
                study,_=self.complete(lambda w:(w/'escape').symlink_to(target))
                before=self.bytes();self.assert_code('UNSAFE_LINK',lambda:self.engine.plan_apply(study['id']));self.assertEqual(before,self.bytes())

    def test_directory_file_type_transitions_and_binary_content(self):
        def mutate(w):
            shutil.rmtree(w/'cases');(w/'cases').write_bytes(b'\x00\xffbinary')
            (w/'agent.py').unlink();(w/'agent.py').mkdir();(w/'agent.py/child').write_text('new')
        study,_=self.complete(mutate);plan=self.engine.plan_apply(study['id']);self.assertFalse(plan['conflicts'])
        self.engine.apply(plan['plan_id'])
        self.assertEqual((self.project/'cases').read_bytes(),b'\x00\xffbinary')
        self.assertEqual((self.project/'agent.py/child').read_text(),'new')

    def test_failure_after_effect_rolls_back_prior_state(self):
        study,_=self.complete();plan=self.engine.plan_apply(study['id']);before=self.bytes();original=tree.set_entry;calls=[]
        def fault(*args,**kwargs):
            original(*args,**kwargs);calls.append(args[1])
            if len(calls)==1:raise RuntimeError('write acknowledgement lost')
        with patch('invariant.experiments.applying.tree.set_entry',side_effect=fault):
            with self.assertRaises(RuntimeError):self.engine.apply(plan['plan_id'])
        self.assertEqual(before,self.bytes());self.assertFalse((self.engine.root/'apply-active.json').exists())
        self.assert_code('PLAN_RECOVERED',lambda:self.engine.apply(plan['plan_id']))

    def crash(self,plan,index=1):
        package=str(Path(__file__).resolve().parents[2])
        code='''import sys,os
sys.path.insert(0,sys.argv[1])
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments import tree
original=tree.set_entry
count=0
def crash(*args,**kwargs):
 global count
 original(*args,**kwargs);count+=1
 if count==int(sys.argv[5]):os._exit(91)
tree.set_entry=crash
ExperimentEngine(sys.argv[2],state=sys.argv[3]).apply(sys.argv[4],request_id='crash-'+sys.argv[4])
'''
        result=subprocess.run([sys.executable,'-I','-c',code,package,str(self.project),str(self.state),plan['plan_id'],str(index)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,91,result.stdout+result.stderr)

    def test_crash_recovery_runs_on_next_project_lease_without_replaying_apply(self):
        study,_=self.complete();plan=self.engine.plan_apply(study['id']);before=self.bytes();self.crash(plan)
        self.assertNotEqual(before,self.bytes())
        with project_lock(self.state):pass
        self.assertEqual(before,self.bytes());self.assertFalse((self.engine.root/'apply-active.json').exists())
        self.assert_code('PLAN_RECOVERED',lambda:self.engine.apply(plan['plan_id'],request_id='crash-'+plan['plan_id']))
        self.assertEqual(self.engine.inspect(request_id='crash-'+plan['plan_id'])['state'],'rolled_back')

    def test_every_write_intent_recovers_without_partial_files_or_temporary_residue(self):
        study,_=self.complete();before=self.bytes();plan=self.engine.plan_apply(study['id'])
        for index in range(1,len(plan['steps'])+1):
            with self.subTest(index=index):
                plan=self.engine.plan_apply(study['id']);self.crash(plan,index)
                self.engine.recover_apply()
                self.assertEqual(before,self.bytes())
                self.assertFalse(any(self.project.rglob('.invariant-apply-*')))

    def test_readonly_preview_and_validation_never_run_rollback(self):
        study,_=self.complete();plan=self.engine.plan_apply(study['id']);self.crash(plan)
        partial=self.bytes()
        self.assert_code('APPLY_RECOVERY_REQUIRED',lambda:self.engine.plan_apply(study['id']))
        self.assert_code('APPLY_RECOVERY_REQUIRED',lambda:self.engine.validate('experiments.json'))
        self.engine.diff(study['id'])
        self.assertEqual(partial,self.bytes())
        self.engine.recover_apply()

    def test_corrupt_recovery_journal_blocks_mutations_without_discarding_evidence(self):
        study,_=self.complete();plan=self.engine.plan_apply(study['id']);self.crash(plan)
        folder=self.engine.root/'apply-plans'/plan['plan_id'];journal=folder/'journal.json'
        saved=journal.read_bytes();value=json.loads(saved);value['progress']=['pending']*len(value['progress'])
        workspace.private_write(journal,value);partial=self.bytes()
        self.assert_code('CORRUPT_PLAN',lambda:self.engine.recover_apply())
        self.assertEqual(partial,self.bytes());self.assertTrue((self.engine.root/'apply-active.json').exists())
        journal.write_bytes(saved);self.engine.recover_apply()

    def test_explicit_host_credential_reference_is_excluded_from_apply(self):
        secret=self.project/'custom-credential.txt';secret.write_text('host credential');secret.chmod(0o600)
        self.change(lambda v:v.update(secret_refs={'provider':'CUSTOM'}))
        def mutate(w):
            (w/'custom-credential.txt').write_text('candidate credential')
            (w/'agent.py').write_text('new agent')
        with patch.dict(os.environ,{'INVARIANT_SECRET_CUSTOM':str(secret)}):
            study,_=self.complete(mutate)
            diff=self.engine.diff(study['id'])
            self.assertIn('custom-credential.txt',[r['path'] for r in diff['excluded']])
            plan=self.engine.plan_apply(study['id']);self.engine.apply(plan['plan_id'])
        self.assertEqual(secret.read_text(),'host credential')

    def test_committed_apply_with_lost_ack_is_not_rolled_back_or_replayed(self):
        study,_=self.complete();plan=self.engine.plan_apply(study['id']);package=str(Path(__file__).resolve().parents[2])
        code='''import sys,os
sys.path.insert(0,sys.argv[1])
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments import applying
original=applying.save_journal
def fault(folder,journal):
 original(folder,journal)
 if journal['state']=='committed':os._exit(93)
applying.save_journal=fault
ExperimentEngine(sys.argv[2],state=sys.argv[3]).apply(sys.argv[4],request_id='committed')
'''
        reply=subprocess.run([sys.executable,'-I','-c',code,package,str(self.project),str(self.state),plan['plan_id']],capture_output=True,text=True,timeout=20)
        self.assertEqual(reply.returncode,93,reply.stderr)
        after=self.bytes()
        with project_lock(self.state):pass
        self.assertEqual(after,self.bytes())
        (self.project/'agent.py').write_text('later external edit')
        self.assertTrue(self.engine.apply(plan['plan_id'],request_id='committed')['applied'])
        self.assertEqual((self.project/'agent.py').read_text(),'later external edit')

    def test_corrupted_staged_content_rolls_back_without_using_bad_bytes(self):
        study,_=self.complete();plan=self.engine.plan_apply(study['id']);before=self.bytes()
        index=next(i for i,s in enumerate(plan['steps']) if s['path']=='agent.py' and s['after'])
        staged=self.engine.root/'apply-plans'/plan['plan_id']/'desired'/str(index)
        staged.chmod(0o600);staged.write_text('tampered')
        self.assert_code('CORRUPT_PLAN',lambda:self.engine.apply(plan['plan_id']))
        self.assertEqual(before,self.bytes())

    def test_backup_disk_failure_happens_before_project_writes(self):
        study,_=self.complete();plan=self.engine.plan_apply(study['id']);before=self.bytes()
        original=__import__('invariant.experiments.applying',fromlist=['write_file']).write_file
        def fail(path,*args,**kwargs):
            if path.parent.name=='backup':raise OSError('synthetic disk failure')
            return original(path,*args,**kwargs)
        with patch('invariant.experiments.applying.write_file',side_effect=fail):
            with self.assertRaises(OSError):self.engine.apply(plan['plan_id'])
        self.assertEqual(before,self.bytes());self.assertFalse((self.engine.root/'apply-active.json').exists())

    def test_external_edit_during_crash_recovery_is_preserved_and_blocks_writes(self):
        study,_=self.complete();plan=self.engine.plan_apply(study['id']);self.crash(plan,4)
        touched=plan['steps'][3]['path'];(self.project/touched).write_text('external edit')
        self.assert_code('APPLY_RECOVERY_REQUIRED',lambda:self.engine.plan_apply(study['id']))
        self.assert_code('APPLY_RECOVERY_BLOCKED',lambda:self.engine.recover_apply())
        self.assertEqual((self.project/touched).read_text(),'external edit')
        self.assert_code('APPLY_RECOVERY_BLOCKED',lambda:self.engine.init_definition('blocked.json'))
        self.assertFalse((self.project/'blocked.json').exists())
        step=plan['steps'][3];blob=self.engine.root/'apply-plans'/plan['plan_id']/'backup/3'
        (self.project/touched).write_bytes(blob.read_bytes());(self.project/touched).chmod(step['before']['mode'])
        result=self.engine.recover_apply();self.assertTrue(result['recovered']);self.assertFalse((self.engine.root/'apply-active.json').exists())

    def test_project_and_legacy_recovery_locks_block_apply(self):
        study,_=self.complete();plan=self.engine.plan_apply(study['id']);before=self.bytes()
        with project_lock(self.state):self.assert_code('PROJECT_BUSY',lambda:self.engine.apply(plan['plan_id']))
        workspace.private_write(self.state/'current.json',{'legacy':'needs recovery'})
        self.assert_code('WORKSPACE_RECOVERY_REQUIRED',lambda:self.engine.apply(plan['plan_id']));self.assertEqual(before,self.bytes())

    def test_continue_is_frozen_child_with_new_baseline_and_parent_reference(self):
        study,attempt=self.complete();(self.project/'next.txt').write_text('new task')
        before=self.bytes()
        with patch('invariant.experiments.continuation.environment.selected_image',return_value=IMAGE):
            child=self.engine.continue_result(study['id'],'next.txt',request_id='child')
            self.assertEqual(child['id'],self.engine.continue_result(study['id'],'next.txt',request_id='child')['id'])
        self.assertEqual(before,self.bytes());self.assertEqual(child['frozen']['image']['id'],IMAGE)
        self.assertTrue(child['frozen']['parent']['new_conversation']);self.assertEqual(child['frozen']['parent']['attempt_id'],attempt['id'])
        self.assertEqual(len(child['trials']),1)
        baseline=self.engine.root/'baselines'/child['baseline_id']
        self.assertEqual((baseline/'workspace/agent.py').read_text(),'print("candidate")\n')
        self.assertTrue((baseline/'workspace/.env').exists())
        self.assertFalse((self.project/'.env').exists())
        definition=child['frozen']['definition'];self.assertFalse(definition['cases'][0]['fixtures']);self.assertNotIn('patch_file',definition['variants'][0])
        self.assertEqual((baseline/'inputs'/definition['cases'][0]['prompt_file']).read_text(),'new task')
        self.engine.select(study['id'],revision=1,clear=True)
        self.assert_code('REFERENCED_STATE',lambda:self.engine.delete(study['id'],confirm=True))

    def test_readable_and_json_apply_share_plan(self):
        study,_=self.complete();stream=io.StringIO()
        with patch('invariant.experiments.cli.ExperimentEngine',return_value=self.engine),contextlib.redirect_stdout(stream):
            code=cli.main(['apply',study['id'],'--dry-run','--json','--request-id','machine-plan'])
        self.assertEqual(code,0);plan=json.loads(stream.getvalue())['data'];stream=io.StringIO()
        with patch('invariant.experiments.cli.ExperimentEngine',return_value=self.engine),contextlib.redirect_stdout(stream):
            code=cli.main(['apply','--plan',plan['plan_id'],'--request-id','human-apply'])
        self.assertEqual(code,0);self.assertIn('Applied: True',stream.getvalue())

if __name__=='__main__':unittest.main()
