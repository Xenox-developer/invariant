"""Semantic compression keeps complete code and independently bound decisions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from invariant import review_input,workspace
from invariant.experiments.errors import ExperimentError
from invariant.experiments.task_context import render as context_brief
from invariant.task_review import read_decision


def payload():
    return {'task':'Implement all requirements — including Unicode.\nPreserve every item.',
            'review':{'review_digest':'a'*64,'attempt_id':'attempt-selected','changed_paths':1,
                      'changes':[{'path':'main.py','text_diff':'@@ code\n-old\n+value="'+('b'*64)+'"\n','requires_full_review':False}],
                      'full_review_path':'/private/metadata-only','requires_full_review':False,'conflicts':[]},
            'evidence':[{'attempt_id':'attempt-selected','passed':True,
                         'evaluation':{'status':'completed','command':'python3 tests/check.py','checks':{'tests':35,'failures':0,'errors':0},'fixed_test_count':5,'test_exit_code':0},
                         'inference_usage':{'total_tokens':99999},'resources':{'expected_digest':'same','observed_digest':'same'}}]}


class ReviewInputTests(unittest.TestCase):
    def test_all_requirements_and_full_patch_survive_without_private_metadata(self):
        value=payload();text=review_input.render(value)
        self.assertIn(value['task'],text);self.assertIn(value['review']['changes'][0]['text_diff'],text)
        self.assertIn('python3 tests/check.py',text);self.assertIn('35',text)
        self.assertNotIn('/private/metadata-only',text);self.assertNotIn('99999',text)
        self.assertNotIn('a'*64,text);self.assertIn('b'*64,text) # code hashes are code, never stripped

    def test_validation_tool_receipt_survives_semantic_compression(self):
        value=payload();value['evidence'][0]['evaluation'].update(reused_validation=True,validation_receipt='/private/receipt.json')
        text=review_input.render(value)
        self.assertIn('invariant_validate',text);self.assertNotIn('/private/receipt.json',text)

    def test_prior_failed_attempt_is_not_substituted_for_selected_result(self):
        value=payload();value['evidence'].insert(0,{'attempt_id':'attempt-old','passed':False})
        self.assertIn('35',review_input.render(value))
        value['evidence'][1]['attempt_id']='foreign'
        with self.assertRaises(ExperimentError):review_input.render(value)

    def test_missing_partial_conflicting_unknown_and_unverified_evidence_fail_closed(self):
        for mutation in ('partial','conflict','omitted','unknown','resources','duplicate'):
            value=payload()
            if mutation=='partial':value['review']['changes'][0]['requires_full_review']=True
            elif mutation=='conflict':value['review']['conflicts']=[{'path':'main.py'}]
            elif mutation=='omitted':value['review']['changed_paths']=2
            elif mutation=='unknown':value['evidence'][0]['passed']=None
            elif mutation=='resources':value['evidence'][0]['resources']['observed_digest']='other'
            elif mutation=='duplicate':value['evidence']*=2
            with self.subTest(mutation=mutation),self.assertRaises(ExperimentError):review_input.render(value)

    def test_bare_decision_needs_host_binding_and_wrong_explicit_digest_is_rejected(self):
        text=json.dumps({'approved':True,'reason':'Checked the complete patch'})
        with self.assertRaises(ExperimentError):read_decision([text],'a'*64)
        self.assertEqual(read_decision([text],'a'*64,host_bound=True)['review_digest'],'a'*64)
        with self.assertRaises(ExperimentError):
            read_decision([json.dumps({'approved':True,'reason':'wrong','review_digest':'b'*64})],'a'*64,host_bound=True)

    def test_minimal_runtime_disables_discovery_and_coding_capabilities(self):
        import tomllib
        with tempfile.TemporaryDirectory() as name:
            home=Path(name)
            workspace.codex_review_configuration(home,'codex')
            config=tomllib.loads((home/'config.toml').read_text())
            self.assertFalse(config['features']['shell_tool']);self.assertFalse(config['features']['code_mode_host'])
            self.assertTrue(config['features']['skip_host_skill_discovery'])
            self.assertEqual(config['sandbox_mode'],'read-only')
            self.assertTrue(all(row['enabled'] is False for row in config['skills']['config']))

    def test_context_brief_is_bounded_and_keeps_files_available_for_targeted_reads(self):
        entries={f'source/{i:04}.py':{'type':'file'} for i in range(5000)}
        text=context_brief(entries,{'rg':False,'python3':True})
        self.assertLess(len(text),4000);self.assertIn('additional_files',text);self.assertIn('targeted test',text)
        self.assertNotIn('source/4999.py',text)

    def test_coding_profile_keeps_model_transport_and_waits_for_full_job_deadline(self):
        import tomllib
        with tempfile.TemporaryDirectory() as name:
            home=Path(name)
            policy={**workspace.DEFAULT_CONFIG,'duration_seconds':660}
            workspace.codex_configuration(home,'container','codex',policy=policy,coding_profile=True)
            config=tomllib.loads((home/'config.toml').read_text())
            self.assertTrue(config['features']['code_mode_host'])
            self.assertEqual(config['features']['code_mode']['default_exec_yield_time_ms'],660000)
            self.assertTrue(config['features']['skip_host_skill_discovery'])
            self.assertTrue(all(row['enabled'] is False for row in config['skills']['config']))
