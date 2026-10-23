import contextlib
import copy
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from invariant import workspace
from invariant.experiments import cli, demo, tree
from invariant.experiments.definitions import validate
from invariant.experiments.errors import ExperimentError

REFERENCE = '''def merge_windows(windows):
    if not isinstance(windows, (list, tuple)):
        raise ValueError()
    valid = []
    for pair in windows:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or any(type(x) is not int for x in pair) or pair[0] > pair[1]:
            raise ValueError()
        if pair[0] != pair[1]:
            valid.append(list(pair))
    out = []
    for start, end in sorted(valid):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return out
'''


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.project = self.root/'demo'

    def test_scaffold_is_offline_isolated_and_reopens_without_overwriting(self):
        with patch.object(workspace, 'docker') as docker:
            info = demo.create(self.project, model='chosen')
            original = {str(p.relative_to(self.project)): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
            repeated = demo.create(self.project)
            docker.assert_not_called()
        definition = validate(json.loads((self.project/'compare.json').read_text()))
        self.assertEqual([v['effort'] for v in definition['variants']], ['low','medium','high'])
        self.assertEqual({v['model'] for v in definition['variants']}, {'chosen'})
        self.assertEqual(definition['workspace']['include'], ['solution.py'])
        self.assertEqual(definition['evaluator']['source_paths'], ['verify.py','suite.json'])
        self.assertEqual(len(json.loads((self.project/'suite.json').read_text())), 23)
        self.assertEqual(workspace.load_project(self.project)['command_network'], 'none')
        self.assertTrue(repeated['reused']); self.assertFalse(info['reused'])
        self.assertEqual(original, {str(p.relative_to(self.project)): p.read_bytes() for p in self.project.rglob('*') if p.is_file()})
        with self.assertRaisesRegex(ExperimentError, 'different settings'):
            demo.create(self.project, efforts=['medium','high','xhigh'])

    def test_occupied_paths_symlinks_and_invalid_configuration_are_preserved(self):
        self.project.mkdir(); (self.project/'keep').write_text('keep')
        with self.assertRaises(ExperimentError): demo.create(self.project)
        self.assertEqual((self.project/'keep').read_text(),'keep')
        link=self.root/'link'; link.symlink_to(self.project)
        with self.assertRaises(ExperimentError): demo.create(link)
        invalid=self.root/'invalid'
        with self.assertRaises(ExperimentError): demo.create(invalid, efforts=['high']*3)
        self.assertFalse(invalid.exists())

    def test_shared_tests_reject_stub_mutation_exit_and_wrong_values(self):
        demo.create(self.project)
        evaluate=runpy.run_path(str(self.project/'verify.py'))['evaluate']
        p=self.project/'solution.py';suite=self.project/'suite.json';out=self.root/'reports'
        result=evaluate(p,suite,out); self.assertFalse(result['passed'])
        p.write_text(REFERENCE)
        result=evaluate(p,suite,out);self.assertTrue(result['passed']);self.assertEqual(result['metrics']['tests_passed'],23)
        p.write_text(REFERENCE.replace('valid = []','windows.sort()\n    valid = []'))
        self.assertFalse(evaluate(p,suite,out)['passed'])
        p.write_text('import sys\nsys.exit(0)\n')
        self.assertFalse(evaluate(p,suite,out)['passed'])
        p.write_text('def merge_windows(windows): return []\n')
        result=evaluate(p,suite,out)
        self.assertFalse(result['passed']);self.assertLess(result['metrics']['tests_passed'],23)
        self.assertTrue((out/'tests.json').is_file())

    def test_json_cli_never_prompts_even_on_a_terminal(self):
        output=io.StringIO()
        with (contextlib.redirect_stdout(output),patch.object(sys.stdin,'isatty',return_value=True),
              patch.object(demo,'execute',side_effect=lambda info:{**info,'study_id':'study-test'}),
              patch.object(demo,'interact') as interact):
            self.assertEqual(cli.main(['demo',str(self.project),'--run','--json']),0)
        self.assertTrue(json.loads(output.getvalue())['ok']);interact.assert_not_called()

    def test_reopen_executes_the_same_key_and_missing_usage_stays_unknown(self):
        info=demo.create(self.project)
        engine=Mock();engine.submit.return_value={'id':'study-test','frozen':{'execution':{'max_parallel':2}}}
        engine.results.return_value={'study_id':'study-test'}
        with patch.object(demo,'ExperimentEngine',return_value=engine),patch.object(cli,'wait_study'),contextlib.redirect_stderr(io.StringIO()):
            result=demo.execute(info)
        engine.submit.assert_called_once_with('compare.json',request_id=demo.REQUEST)
        engine.launch.assert_called_once_with('study-test');self.assertFalse(result['applied'])
        trial={'variant_id':'low','metrics':{},'runner':{},'state':'failed','attempt_id':'attempt','passed':None,'reason':'AUTH_REQUIRED'}
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            demo.present({'project':str(self.project),'results':{'study_id':'study','trials':[trial],'selection':{'revision':0}}})
        self.assertIn('unavailable',output.getvalue());self.assertIn('Quality unavailable',output.getvalue())

    def test_human_choice_uses_shared_selection_revision_and_can_leave_without_selecting(self):
        engine=Mock();engine.inspect.return_value={'id':'study','selection':{'revision':4}}
        engine.diff.return_value={}
        info={'project':str(self.project),'study_id':'study','results':{'trials':[{'attempt_id':str(n)} for n in range(3)]}}
        with patch.object(demo,'ExperimentEngine',return_value=engine),patch('builtins.input',side_effect=['2','']),contextlib.redirect_stdout(io.StringIO()):
            demo.interact(info)
        engine.select.assert_called_once_with('1',revision=4)
        engine.reset_mock()
        with patch.object(demo,'ExperimentEngine',return_value=engine),patch('builtins.input',return_value=''):
            demo.interact(info)
        engine.select.assert_not_called();engine.apply.assert_not_called()


class DemoApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.project=Path(self.tmp.name);self.file=self.project/'solution.py';self.file.write_text('stub')
        before=tree.entry(self.project,'solution.py');self.file.write_text('parent')
        parent=tree.entry(self.project,'solution.py');self.file.write_text('child')
        child=tree.entry(self.project,'solution.py');self.file.write_text('stub')
        self.reports={name:{'attempt_id':name,'parent':ancestor,'changes':[{'path':'solution.py','change':'modified','before':b,'after':a}]}
                      for name,ancestor,b,a in [('parent',None,before,parent),('child',{'attempt_id':'parent'},parent,child)]}
        self.engine=Mock();self.engine.project=self.project;self.engine.diff.side_effect=lambda identifier:copy.deepcopy(self.reports[identifier])
        self.order=[]
        def plan(identifier):
            self.order.append(identifier)
            return {'plan_id':identifier,'changes':[],'conflicts':[]}
        self.engine.plan_apply.side_effect=plan
        self.engine.apply.side_effect=lambda identifier:(self.file.write_text(identifier),{'applied':True})[1]

    def test_continued_result_applies_parent_then_child_and_skips_matching_ancestor(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(demo.review_apply(self.engine,'child',confirm=lambda _: 'y'))
        self.assertEqual(self.order,['parent','child']);self.assertEqual(self.file.read_text(),'child')
        self.file.write_text('parent');self.order.clear()
        with contextlib.redirect_stdout(io.StringIO()):demo.review_apply(self.engine,'child',confirm=lambda _: 'y')
        self.assertEqual(self.order,['child'])

    def test_decline_and_conflicts_do_not_apply(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(demo.review_apply(self.engine,'child',confirm=lambda _: 'n'))
        self.engine.apply.assert_not_called();self.assertEqual(self.file.read_text(),'stub')
        self.engine.plan_apply.side_effect=lambda _: {'plan_id':'blocked','conflicts':[{'path':'solution.py','reason':'baseline_changed'}]}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(demo.review_apply(self.engine,'child',confirm=lambda _: 'y'))
        self.engine.apply.assert_not_called()
