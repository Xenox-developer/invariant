"""Baseline readiness must distinguish broken evaluation from an ordinary red test."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from invariant import workspace
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments.preflight import check
from invariant.experiments.errors import ExperimentError
from invariant.experiments.snapshots import unseal_tree
import test_experiments as base


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.project=self.root/'project';self.project.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):workspace.init(self.project)
        self.engine=ExperimentEngine(self.project,state=self.root/'state',runtime=base.FakeRuntime())
        self.addCleanup(lambda:unseal_tree(self.root))
        (self.project/'judge.py').write_text('print("unused fixture")')
        (self.project/'recipe.json').write_text(json.dumps({'schema_version':1,'name':'check',
            'runner':{'type':'command','argv':['/bin/false']},'variants':[{'id':'one'}],'cases':[{'id':'case'}],
            'evaluator':{'argv':['python3','judge.py'],'source_paths':['judge.py']}}))

    def run_case(self,passed,metrics):
        captured=[]
        def execute(identifier):
            study=self.engine.inspect(identifier);captured.append(study['frozen']['definition'])
            return study
        result={'study_id':'fixture','trials':[{'runner':{'status':'completed'},'evaluation':{'status':'completed'},
                    'passed':passed,'metrics':metrics}],'summary':{'quality_passes':int(passed is True)}}
        with patch.object(self.engine,'execute',side_effect=execute),patch.object(self.engine,'results',return_value=result):
            value=check(self.engine,'recipe.json',request_id='check')
        return value,captured

    def test_red_tests_are_ready_and_original_runner_is_never_executed(self):
        value,definitions=self.run_case(False,{'test_command_seconds':.1,'test_exit_code':1})
        self.assertTrue(value['ready']);self.assertEqual(value['model_turns'],0)
        self.assertEqual(definitions[0]['runner'],{'type':'command','argv':['/bin/true']})
        self.assertEqual(json.loads((self.project/'recipe.json').read_text())['runner']['argv'],['/bin/false'])
        self.assertFalse(list((self.project/'experiments').glob('.preflight-*.json')))

    def test_unavailable_test_command_blocks_model_readiness(self):
        value,_=self.run_case(None,{'test_command_seconds':.1})
        self.assertFalse(value['ready'])

    def test_missing_evaluator_is_explicit(self):
        recipe=json.loads((self.project/'recipe.json').read_text());recipe.pop('evaluator')
        (self.project/'recipe.json').write_text(json.dumps(recipe))
        with self.assertRaisesRegex(ExperimentError,'captured evaluator'):check(self.engine,'recipe.json')
