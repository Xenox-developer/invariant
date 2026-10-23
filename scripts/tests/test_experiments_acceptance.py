"""The release gate must reject incomplete, stale, skipped and unbacked evidence."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from invariant.experiments import acceptance as gate


class ExperimentAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.expected={key:'contract requirement '+key for key in gate.REQUIRED}
        self.stages={key:{'status':'passed','exit_code':0,'report':'proof.json'} for key in gate.ALL_STAGES}

    def report(self):
        return {'schema_version':1,'cases':gate.classify(self.stages,self.expected,{'Darwin','Linux'})}

    def bundle(self):
        stages={key:{'status':'blocked','error':'Not run'} for key in gate.ALL_STAGES}
        stages['sdk']={'status':'passed','exit_code':0,'report':'proof.json'}
        proof=self.root/'proof.json';gate.save(proof,{'passed':True,'test_count':11})
        source={'module.py':'0'*64}
        report={'schema_version':1,'kind':'invariant-experiment-acceptance','accepted':False,
            'host':{'system':'Darwin','containerized':False},'source_sha256':source,'source_digest':hashlib.sha256(gate.canonical(source)).hexdigest(),
            'source_unchanged':True,'local_fixture_passed':False,'platforms_verified':[],
            'stages':stages,'cases':gate.classify(stages,self.expected,set()),'blockers':['Missing required evidence'],
            'artifacts':{'proof.json':gate.sha(proof)}}
        self.write(report);return report

    def write(self,report):
        report.pop('report_digest',None)
        report['report_digest']=hashlib.sha256(gate.canonical(report)).hexdigest()
        gate.save(self.root/'report.json',report)

    def test_complete_matrix_requires_all_twenty_one_distinct_cases(self):
        self.assertTrue(gate.matrix_passed(self.report()))
        for mutate in (lambda d:d['cases'].pop(),lambda d:d['cases'].__setitem__(20,d['cases'][0]),
                       lambda d:d['cases'][0].update(status='skipped'),lambda d:d['cases'][0].update(evidence=[]),
                       lambda d:d['cases'][0].update(error='failure')):
            report=self.report();mutate(report);self.assertFalse(gate.matrix_passed(report))

    def test_live_missing_or_provider_blocked_never_turns_release_green(self):
        for status in ('blocked','failed'):
            self.stages['codex']={'status':status,'error':'Provider unavailable'}
            cases=gate.classify(self.stages,self.expected,{'Darwin','Linux'})
            self.assertEqual(next(c for c in cases if c['id']=='P20')['status'],status)
            self.assertFalse(gate.matrix_passed({'schema_version':1,'cases':cases}))

    def test_docker_guest_does_not_substitute_for_linux_host(self):
        cases=gate.classify(self.stages,self.expected,{'Darwin'})
        self.assertEqual(next(c for c in cases if c['id']=='P21')['status'],'blocked')

    def test_partial_bundle_is_valid_but_not_accepted(self):
        self.bundle();self.assertFalse(gate.verify_bundle(self.root/'report.json')['accepted'])
        import contextlib,io
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=gate.main(['--verify',str(self.root/'report.json'),'--json'])
        reply=json.loads(output.getvalue())
        self.assertEqual(code,2);self.assertTrue(reply['ok']);self.assertFalse(reply['data']['accepted'])

    def test_report_flag_tampering_and_changed_artifacts_fail(self):
        self.bundle();p=self.root/'report.json';d=json.loads(p.read_text());d['accepted']=True;gate.save(p,d)
        with self.assertRaisesRegex(ValueError,'checksum'):gate.verify_bundle(p)
        self.bundle();(self.root/'proof.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'changed or missing'):gate.verify_bundle(p)

    def test_recomputed_checksum_cannot_hide_missing_stages_or_failed_cleanup(self):
        d=self.bundle();d['stages'].pop('codex');self.write(d)
        with self.assertRaisesRegex(ValueError,'Missing or unknown'):gate.verify_bundle(self.root/'report.json')
        d=self.bundle();gate.save(self.root/'proof.json',{'passed':True,'cleanup_errors':['orphan']})
        d['artifacts']['proof.json']=gate.sha(self.root/'proof.json');self.write(d)
        with self.assertRaisesRegex(ValueError,'Nonpassing'):gate.verify_bundle(self.root/'report.json')

    def test_false_case_and_platform_claims_fail_even_with_valid_checksum(self):
        d=self.bundle();d['cases'][0].update(status='passed',error=None,evidence=['proof.json']);self.write(d)
        with self.assertRaisesRegex(ValueError,'required stages'):gate.verify_bundle(self.root/'report.json')
        d=self.bundle();d['platforms_verified']=['Darwin','Linux'];self.write(d)
        with self.assertRaisesRegex(ValueError,'platform'):gate.verify_bundle(self.root/'report.json')

    def test_other_source_and_unsafe_artifact_paths_are_rejected(self):
        self.bundle()
        with self.assertRaisesRegex(ValueError,'different source'):gate.verify_bundle(self.root/'report.json',expected_source={'other':'x'})
        for name in ('../escape','/absolute','x\\file'):
            with self.assertRaises(ValueError):gate.artifact_path(self.root,name)
        (self.root/'link').symlink_to(self.root)
        with self.assertRaises(ValueError):gate.artifact_path(self.root,'link/proof.json')

    def test_live_and_ten_variant_observations_cannot_be_replaced_by_success_flags(self):
        for tag in ('codex','resume','execution','creation','units'):
            with self.subTest(tag=tag),self.assertRaises(ValueError):gate.validate_stage(tag,{'passed':True})
        with self.assertRaises(ValueError):gate.validate_stage('codex',{'passed':True,'model_turns':0,'native_results':{'summary':{'quality_passes':2}}})

    def test_container_environment_is_detected_and_not_inferred_from_docker_guest(self):
        from unittest.mock import patch
        with patch.object(Path,'exists',return_value=True):self.assertTrue(gate.containerized())
        with patch.object(Path,'exists',return_value=False),patch.object(Path,'read_text',return_value='0::/kubepods/pod/123'):
            self.assertTrue(gate.containerized())
        with patch.object(Path,'exists',return_value=False),patch.object(Path,'read_text',return_value='0::/'):
            self.assertFalse(gate.containerized())

    def test_release_profile_requires_its_additional_stages(self):
        report=self.bundle();report['release_profile']=gate.RELEASE_PROFILE;self.write(report)
        with self.assertRaisesRegex(ValueError,'Missing or unknown'):
            gate.verify_bundle(self.root/'report.json')
        for tag in gate.OPEN_SOURCE_STAGES:report['stages'][tag]={'status':'blocked','error':'Not run'}
        self.write(report)
        self.assertFalse(gate.verify_bundle(self.root/'report.json')['accepted'])
        for tag in gate.OPEN_SOURCE_STAGES:
            with self.subTest(tag=tag),self.assertRaises(ValueError):gate.validate_stage(tag,{'passed':True})

    def test_contract_and_parser_expose_the_new_gate(self):
        root=Path(__file__).resolve().parents[2]
        self.assertEqual(set(gate.requirements(root)),gate.REQUIRED)
        from invariant.experiments.cli import main
        from unittest.mock import patch
        with patch.object(gate,'main',return_value=2) as runner:
            self.assertEqual(main(['--json','acceptance','--verify','report.json']),2)
            self.assertEqual(runner.call_args.args[0],['--json','--verify','report.json'])


if __name__=='__main__':unittest.main()
