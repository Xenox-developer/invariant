"""Delivery is independent of snapshot capture and worker-controlled ignore edits."""
import json
from pathlib import Path
import tempfile
import unittest

from invariant.experiments.delivery import DeliveryPolicy
import test_experiments_apply as base


class IgnoreTests(unittest.TestCase):
    def test_nested_patterns_negation_directories_escaped_and_character_classes(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'sub').mkdir()
            (root/'.gitignore').write_text('cache/\n*.tmp\n!keep.tmp\n/root.txt\nfile[0-9]\n\\#notes\nfoo/**/generated\n')
            (root/'sub/.gitignore').write_text('local\n!keep.tmp\n')
            entries={p.relative_to(root).as_posix():{'type':'file'} for p in root.rglob('.gitignore')}
            policy=DeliveryPolicy({'respect_gitignore':True},root,entries)
            for path in ('cache/a','sub/cache/a','foo/a/generated','foo/generated','sub/x.tmp','file3','#notes','root.txt','sub/local'):
                with self.subTest(path=path):self.assertTrue(policy.ignored(path))
            for path in ('keep.tmp','sub/keep.tmp','sub/root.txt','filex','cache.txt','other/local'):
                with self.subTest(path=path):self.assertFalse(policy.ignored(path))

    def test_frozen_ignore_scope_survives_worker_edits_and_continuation(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'.gitignore').write_text('cache/\n')
            entries={'.gitignore':{'type':'file'}}
            frozen=DeliveryPolicy.freeze({'respect_gitignore':True},root,entries)
            (root/'.gitignore').write_text('')
            policy=DeliveryPolicy({},root,entries,frozen=frozen)
            self.assertTrue(policy.ignored('cache/generated'))

    def test_parent_must_be_reincluded_before_child_negation(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'.gitignore').write_text('out/\n!out/keep\n')
            policy=DeliveryPolicy({'respect_gitignore':True},root,{'.gitignore':{'type':'file'}})
            self.assertTrue(policy.ignored('out/keep'))


class DeliveryTests(unittest.TestCase):
    setUp=base.ApplyTests.setUp
    unseal=base.ApplyTests.unseal
    submit=base.ApplyTests.submit
    change=base.ApplyTests.change
    complete=base.ApplyTests.complete
    def test_generated_cache_stays_in_snapshot_and_child_but_never_conflicts_on_host(self):
        (self.project/'.gitignore').write_text('cache/\n')
        self.change(lambda v:v.setdefault('workspace',{}).update(apply={'respect_gitignore':True}))
        def mutate(root):
            (root/'cache').mkdir();(root/'cache/tool').write_text('cached-build')
            (root/'agent.py').write_text('print("deliver")')
            (root/'.gitignore').write_text('')  # Cannot widen the frozen delivery scope.
        study,attempt=self.complete(mutate)
        saved=self.engine.root/'attempts'/attempt['id']/'snapshot/workspace'
        self.assertEqual((saved/'cache/tool').read_text(),'cached-build')
        (self.project/'cache').mkdir();(self.project/'cache/tool').write_text('host-cache')
        plan=self.engine.plan_apply(study['id'])
        self.assertFalse(plan['conflicts'])
        self.assertFalse(any(c['path'].startswith('cache') for c in plan['changes']))
        self.engine.apply(plan['plan_id'])
        self.assertEqual((self.project/'cache/tool').read_text(),'host-cache')
        self.assertEqual((self.project/'agent.py').read_text(),'print("deliver")')
        (self.project/'next.txt').write_text('continue')
        from unittest.mock import patch
        with patch('invariant.experiments.continuation.environment.selected_image',return_value=base.IMAGE):
            child=self.engine.continue_result(attempt['id'],'next.txt',request_id='cache-child')
        baseline=self.engine.root/'baselines'/child['baseline_id']/'workspace'
        self.assertEqual((baseline/'cache/tool').read_text(),'cached-build')
        self.assertEqual(child['frozen']['delivery']['ignore_files']['.gitignore'],'cache/\n')
        self.assertEqual((baseline/'.gitignore').read_text(),'')

    def test_explicit_delivery_exclusion_works_without_ignore_files(self):
        self.change(lambda v:v.setdefault('workspace',{}).update(apply={'exclude':['temporary/**']}))
        def mutate(root):
            (root/'temporary').mkdir();(root/'temporary/data').write_text('snapshot')
            (root/'agent.py').write_text('result')
        study,_=self.complete(mutate)
        self.assertFalse(any(c['path'].startswith('temporary') for c in self.engine.diff(study['id'])['changes']))

    def test_allowlist_includes_required_ancestors_without_unrelated_directories(self):
        self.change(lambda v:v.setdefault('workspace',{}).update(apply={'include':['new/**/*.py']}))
        def mutate(root):
            (root/'new/deep').mkdir(parents=True);(root/'new/deep/result.py').write_text('ok')
            (root/'unrelated').mkdir();(root/'unrelated/cache').write_text('cache')
        study,_=self.complete(mutate)
        paths={c['path'] for c in self.engine.diff(study['id'])['changes']}
        self.assertEqual(paths,{'new','new/deep','new/deep/result.py'})

    def coordinator(self,study):
        from invariant.tasks import TaskRunner
        from unittest.mock import Mock
        backend=Mock();backend.engine.return_value=self.engine
        backend.inspect.side_effect=self.engine.results
        runner=TaskRunner(self.project,state=self.engine.state,backend=backend)
        runner.root.mkdir(parents=True,exist_ok=True)
        runner.save({'schema_version':1,'project':str(self.project),'request_id':'review-test',
                     'state':'submitted','study_id':study['id'],'completed':{},'options':{}})
        return runner

    def test_coordinator_review_apply_lost_ack_and_conflict_refresh(self):
        from invariant import task_results
        from unittest.mock import patch
        study,attempt=self.complete(lambda root:(root/'agent.py').write_text('candidate'))
        runner=self.coordinator(study)
        record=task_results.review(runner,'review-test',attempt['id'])
        package=record['review']
        self.assertFalse(package['requires_full_review'])
        self.assertEqual(task_results.review(runner,'review-test',attempt['id'])['review']['plan_id'],package['plan_id'])
        (self.project/'agent.py').write_text('host-edit')
        with self.assertRaises(Exception):task_results.apply(runner,'review-test',package['review_digest'])
        self.assertEqual((self.project/'agent.py').read_text(),'host-edit')
        refreshed=task_results.review(runner,'review-test',attempt['id'],revision=1)['review']
        self.assertNotEqual(package['plan_id'],refreshed['plan_id'])
        self.assertTrue(refreshed['conflicts'])
        # Resolve the host edit explicitly, then bind a fresh destination plan.
        baseline=self.engine.root/'baselines'/study['baseline_id']/'workspace/agent.py'
        (self.project/'agent.py').write_bytes(baseline.read_bytes())
        package=task_results.review(runner,'review-test',attempt['id'],revision=2)['review']
        with patch.object(runner,'save',side_effect=OSError('lost acknowledgement')):
            with self.assertRaises(OSError):task_results.apply(runner,'review-test',package['review_digest'])
        self.assertEqual((self.project/'agent.py').read_text(),'candidate')
        self.assertTrue(task_results.apply(runner,'review-test',package['review_digest'])['application']['applied'])

    def test_large_binary_review_requires_explicit_full_review_attestation(self):
        from invariant import task_results
        study,attempt=self.complete(lambda root:(root/'agent.py').write_bytes(b'\0'*70000))
        runner=self.coordinator(study)
        package=task_results.review(runner,'review-test',attempt['id'])['review']
        self.assertTrue(package['requires_full_review'])
        with self.assertRaises(Exception) as error:task_results.apply(runner,'review-test',package['review_digest'])
        self.assertEqual(error.exception.code,'FULL_REVIEW_REQUIRED')
        self.assertTrue(task_results.apply(runner,'review-test',package['review_digest'],reviewed_full=True)['application']['applied'])
