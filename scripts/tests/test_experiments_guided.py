"""Guided creation, frozen command tests and interchangeable human/JSON controls."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import shlex
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from invariant import workspace
from invariant.experiments import cli, guided, review
from invariant.experiments.definitions import validate
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments.errors import ExperimentError
from invariant.experiments.snapshots import unseal_tree
import test_experiments as base


class GuidedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve(); self.project=self.root/'project'; self.project.mkdir()
        with contextlib.redirect_stdout(io.StringIO()): workspace.init(self.project)
        (self.project/'solution.py').write_text('value=1\n')
        (self.project/'tests').mkdir(); (self.project/'tests/check.py').write_text('assert True\n')
        self.engine=ExperimentEngine(self.project,state=self.root/'state',runtime=base.FakeRuntime())
        self.addCleanup(lambda:unseal_tree(self.root))

    def create(self, **options):
        return guided.create(self.engine,task='Improve solution',test_command='python3 -B tests/check.py',test_paths=['tests'],**options)

    def test_offline_creation_captures_shared_paths_without_executing_any_command(self):
        with patch.object(workspace,'docker') as docker:
            info=self.create(model='chosen')
        docker.assert_not_called()
        recipe=validate(json.loads(Path(info['definition']).read_text()))
        self.assertEqual([v['effort'] for v in recipe['variants']],['low','medium','high'])
        self.assertEqual(recipe['execution']['max_parallel'],3)
        self.assertIn('tests/check.py',recipe['evaluator']['source_paths'])
        self.assertEqual((self.project/'solution.py').read_text(),'value=1\n')
        study=self.engine.submit(info['definition'],request_id='study')
        (self.project/'tests/check.py').write_text('raise Exception("changed after capture")\n')
        saved=self.engine.root/'baselines'/study['baseline_id']/'inputs/tests/check.py'
        self.assertEqual(saved.read_text(),'assert True\n')
        self.assertIn(str(Path(info['definition']).relative_to(self.project)),recipe['workspace']['exclude'])

    def test_keyed_replay_preserves_user_edits_and_settings_conflicts(self):
        info=self.create(request_id='setup')
        Path(info['prompt']).write_text('user edit kept')
        self.assertEqual(self.create(request_id='setup'),info)
        self.assertEqual(Path(info['prompt']).read_text(),'user edit kept')
        with self.assertRaises(ExperimentError): self.create(request_id='setup',efforts=['medium','high'])
        with self.assertRaises(ExperimentError): self.create()

    def test_invalid_paths_and_settings_leave_project_files_alone(self):
        linked=self.project/'linked'; linked.symlink_to(self.project/'tests',target_is_directory=True)
        for paths in (['../escape'],['.invariant'],['missing'],['linked'],['tests','tests/check.py']):
            with self.subTest(paths=paths),self.assertRaises((ExperimentError,OSError)):
                guided.create(self.engine,task='task',test_command='true',test_paths=paths)
        for options in ({'efforts':['high','high']},{'max_parallel':0},{'test_timeout':0},
                        {'variants':[('one','medium')],'model':'two'}):
            with self.subTest(options=options),self.assertRaises(ExperimentError): self.create(**options)
        self.assertFalse((self.project/'experiments').exists())

    def test_cross_model_configurations_and_quoted_paths_are_supported(self):
        (self.project/'test suite').mkdir();(self.project/'test suite/check.sh').write_text('exit 0')
        info=guided.create(self.engine,task='task',test_command='sh "test suite/check.sh"',test_paths=['test suite'],
                           variants=[('model-one','medium'),('model-two','high')],max_parallel=1)
        self.assertEqual([v['model'] for v in info['configurations']],['model-one','model-two'])
        self.assertEqual(info['requested_parallel'],1)

    def test_machine_missing_inputs_never_prompt_and_no_files_are_created(self):
        out=io.StringIO()
        with patch.object(cli,'ExperimentEngine',return_value=self.engine),patch('builtins.input') as ask,contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['init','--json']),2)
        ask.assert_not_called();self.assertEqual(json.loads(out.getvalue())['error']['code'],'INVALID_ARGUMENTS')
        self.assertFalse((self.project/'experiments').exists())

    def test_human_answers_and_machine_flags_create_identical_files(self):
        out=io.StringIO()
        with (patch.object(cli,'ExperimentEngine',return_value=self.engine),patch.object(sys.stdin,'isatty',return_value=True),
              patch('builtins.input',side_effect=['Improve solution','python3 -B tests/check.py','tests','','','','n']),
              contextlib.redirect_stdout(out)):
            self.assertEqual(cli.main(['init']),0)
        first={p.name:p.read_bytes() for p in (self.project/'experiments/comparison').iterdir()}
        shutil.rmtree(self.project/'experiments')
        out=io.StringIO()
        with (patch.object(cli,'ExperimentEngine',return_value=self.engine),patch.object(sys.stdin,'isatty',return_value=True),
              patch('builtins.input') as ask,contextlib.redirect_stdout(out)):
            self.assertEqual(cli.main(['init','--task','Improve solution','--test-command','python3 -B tests/check.py','--test-path','tests','--json']),0)
        ask.assert_not_called()
        self.assertEqual(first,{p.name:p.read_bytes() for p in (self.project/'experiments/comparison').iterdir()})

    def test_json_run_and_keyed_reopening_share_one_submission_key(self):
        info=self.create(request_id='setup'); engine=Mock();engine.submit.return_value={'id':'study'};engine.results.return_value={}
        with patch.object(cli,'wait_study'),contextlib.redirect_stderr(io.StringIO()):
            guided.execute(engine,info,request_id='same');guided.execute(engine,info,request_id='same')
        self.assertEqual(engine.submit.call_args_list[0],engine.submit.call_args_list[1])
        output=io.StringIO()
        with (patch.object(cli,'ExperimentEngine',return_value=self.engine),patch('invariant.experiments.guided.execute',return_value={**info,'study_id':'study'}),
              patch.object(review,'interact') as interact,patch('builtins.input') as ask,contextlib.redirect_stdout(output)):
            self.assertEqual(cli.main(['init','--task','Improve solution','--test-command','python3 -B tests/check.py','--test-path','tests','--request-id','setup','--run','--json']),0)
        self.assertTrue(json.loads(output.getvalue())['ok'])
        ask.assert_not_called();interact.assert_not_called()

    def test_inline_prompt_is_keyed_and_refuses_substitution_after_edit(self):
        path=guided.inline_prompt(self.engine,'Document this result')
        self.assertEqual(path,guided.inline_prompt(self.engine,'Document this result'))
        (self.project/path).write_text('changed')
        with self.assertRaisesRegex(ExperimentError,'edited'):guided.inline_prompt(self.engine,'Document this result')

    def test_continue_task_and_review_json_use_the_existing_engine_without_prompts(self):
        engine=Mock();engine.continue_result.return_value={'kind':'study','id':'child'}
        out=io.StringIO()
        with (patch.object(cli,'ExperimentEngine',return_value=engine),patch.object(guided,'inline_prompt',return_value='new-task.txt'),
              patch('builtins.input') as ask,contextlib.redirect_stdout(out)):
            self.assertEqual(cli.main(['continue','parent','--task','Next task','--create-only','--json']),0)
        engine.continue_result.assert_called_once_with('parent','new-task.txt',request_id=None);ask.assert_not_called()
        engine.results.return_value={'study_id':'saved'};out=io.StringIO()
        with patch.object(cli,'ExperimentEngine',return_value=engine),patch.object(review,'interact') as interact,contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['review','saved','--json']),0)
        self.assertEqual(json.loads(out.getvalue())['data'],{'study_id':'saved'});interact.assert_not_called()

    def test_review_can_continue_inline_through_shared_selection_state(self):
        engine=Mock();engine.results.side_effect=[{'trials':[{'attempt_id':'parent-attempt'}]},
            {'study_id':'child','state':'completed','selection':{'revision':0,'attempt_id':None},'trials':[
                {'attempt_id':'child-attempt','variant_id':'low','passed':True,'metrics':{},'runner':{},'state':'succeeded'}]}]
        engine.inspect.return_value={'selection':{'revision':7}};engine.diff.return_value={}
        engine.continue_result.return_value={'id':'child'}
        with (patch('builtins.input',side_effect=['1','c','Document it','']),patch.object(guided,'inline_prompt',return_value='prompt'),
              patch.object(cli,'wait_study'),contextlib.redirect_stdout(io.StringIO())):
            review.interact(engine,'parent')
        engine.select.assert_any_call('parent-attempt',revision=7)
        engine.select.assert_any_call('child-attempt',revision=0)
        engine.continue_result.assert_called_once_with('parent','prompt')
        engine.apply.assert_not_called()


class CommandEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'source';self.candidate=self.root/'candidate'
        self.source.mkdir();self.candidate.mkdir();(self.source/'tests').mkdir();(self.candidate/'tests').mkdir()
        (self.source/'tests/check.py').write_text('import solution\nassert solution.value==42\nprint("shared checks passed")\n')
        (self.candidate/'tests/check.py').write_text('raise SystemExit(0)')
        (self.candidate/'tests/extra.py').write_text('added candidate test')
        (self.candidate/'solution.py').write_text('value=42\n')
        self.evaluate=runpy.run_path(str(workspace.ASSETS/'experiment-check.py'))['evaluate']
        self.spec={'roots':['tests'],'files':[{'path':'tests/check.py','mode':0o644}],
                   'command':shlex.quote(sys.executable)+' -B -c '+shlex.quote('import runpy; runpy.run_path("tests/check.py")'), 'timeout_seconds':2}

    def run_check(self):return self.evaluate(self.spec,self.source,self.candidate,self.root/'output')

    def test_frozen_tests_replace_candidate_edits_and_pass_only_correct_code(self):
        value=self.run_check();self.assertTrue(value['passed']);self.assertEqual(value['metrics']['test_exit_code'],0)
        self.assertFalse((self.candidate/'tests/extra.py').exists())
        self.assertIn('shared checks passed',(self.root/'output/test-output.txt').read_text())
        (self.candidate/'solution.py').write_text('value=0\n')
        self.assertFalse(self.run_check()['passed'])

    def test_timeouts_missing_commands_and_output_limits_are_unknown(self):
        for command,reason in ((shlex.quote(sys.executable)+' -c '+shlex.quote('import time; time.sleep(10)'),'timeout'),
                               ('invariant-command-does-not-exist','command_unavailable'),
                               (shlex.quote(sys.executable)+' -c '+shlex.quote('print("x"*2000000)'),'output_limit')):
            self.spec.update(command=command,timeout_seconds=1)
            self.assertIsNone(self.run_check()['passed'])
            self.assertEqual(json.loads((self.root/'output/test-result.json').read_text())['reason'],reason)
            self.assertLessEqual((self.root/'output/test-output.txt').stat().st_size,1024*1024)

    def test_candidate_symlinks_are_replaced_without_following_them(self):
        outside=self.root/'keep';outside.mkdir();(outside/'check.py').write_text('keep')
        shutil.rmtree(self.candidate/'tests');(self.candidate/'tests').symlink_to(outside,target_is_directory=True)
        self.assertTrue(self.run_check()['passed'])
        self.assertEqual((outside/'check.py').read_text(),'keep')
