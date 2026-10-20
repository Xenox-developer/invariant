import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("guided_demo", Path(__file__).resolve().parents[1] / "guided_demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class DemoEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="iv-demo-evidence-")
        self.addCleanup(self.directory.cleanup)
        self.evidence = Path(self.directory.name)
        self.run = self.evidence / ("iv-" + "a" * 32)
        self.run.mkdir()
        self.execution = "run-" + "b" * 64
        self.hash = "c" * 64
        self.journal = []

        def event(kind, **fields):
            self.journal.append(dict(sequence=len(self.journal) + 1, execution_id=self.execution,
                                     initial_artifact_digest=self.hash, event=kind, **fields))

        event("execution_started")
        event("denied", request_id="demo-no-grant", reason="missing_grant")
        event("grant_issued", grant_id="grant-demo", max_requests=20)
        for i in range(1, 21):
            event("admitted", request_id="read-" + str(i), grant_id="grant-demo", ordinal=i)
            event("response_received", request_id="read-" + str(i), grant_id="grant-demo", status=200)
            if i == 1:
                event("denied", request_id="demo-delete", reason="out_of_scope")
        event("denied", request_id="demo-over-budget", reason="budget_exhausted")
        event("grant_revoked")
        event("denied", request_id="demo-after-revoke", reason="revoked")
        event("execution_terminated")
        self.result = {"execution_id": self.execution, "termination": "stopped", "admissions": 20,
                       "output": 'INVARIANT_DEMO_SUMMARY {"invoices":1,"total":42}\n'}
        self.footer = (f"\nExecution: {self.execution}\nStopped: Stopped\nExit code: None\n"
                       "Admissions: 20\nMock API requests: 20\nEvidence: /state/run/isolation.json\n")
        for name in ("isolation.json", "configuration.json"):
            (self.run / name).write_text("{}")

    def verify(self, *, journal=None, footer=None):
        rows = self.journal if journal is None else journal
        (self.run / "audit.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        (self.run / "result.json").write_text(json.dumps(self.result))
        return demo.verify_evidence(self.evidence, self.execution, self.hash, self.footer if footer is None else footer)

    def test_report_requires_broker_and_independent_target_agreement(self):
        facts = self.verify()
        self.assertEqual((facts["total"], facts["admissions"], facts["target_requests"]), (42, 20, 20))
        self.assertEqual(facts["audit_trail"][-1]["event"], "execution_terminated")

    def test_workload_cannot_substitute_a_fake_target_count_footer(self):
        with self.assertRaises(demo.DemoError):
            self.verify(footer=self.footer + self.footer.replace("Mock API requests: 20", "Mock API requests: 21"))

    def test_missing_denial_wrong_execution_and_changed_program_block_success(self):
        for key, value in (("reason", "capacity_exceeded"), ("execution_id", "other-run"), ("initial_artifact_digest", "other-source")):
            rows = copy.deepcopy(self.journal)
            rows[1][key] = value
            with self.subTest(key=key), self.assertRaises(demo.DemoError):
                self.verify(journal=rows)

    def test_extra_grant_or_admission_after_revocation_blocks_success(self):
        rows = copy.deepcopy(self.journal)
        rows[-3]["event"] = "grant_issued"
        rows[-3]["max_requests"] = 20
        with self.assertRaises(demo.DemoError):
            self.verify(journal=rows)
        rows = copy.deepcopy(self.journal)
        rows[-3], rows[3] = rows[3], rows[-3]
        for i, row in enumerate(rows, 1):
            row["sequence"] = i
        with self.assertRaises(demo.DemoError):
            self.verify(journal=rows)

    def test_private_staging_files_block_export_verification(self):
        (self.run / "capability").write_text("private-test-placeholder")
        with self.assertRaises(demo.DemoError):
            self.verify()

    def test_live_polling_does_not_parse_a_partial_appended_record(self):
        self.assertEqual(demo.audit_snapshot('{"event":"denied"}\n{"event":"grant'), [{"event": "denied"}])
        with self.assertRaises(ValueError):
            demo.audit_snapshot('{broken-json}\n')


if __name__ == "__main__":
    unittest.main()
