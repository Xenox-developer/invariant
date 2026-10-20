import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("acceptance_runner", Path(__file__).resolve().parents[1] / "test_mvp_acceptance.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class AcceptanceGateTests(unittest.TestCase):
    def setUp(self):
        self.report = {"accepted": True, "cases": [
            {"id": f"A{i:02d}", "status": "passed", "error": None,
             "setup": "fixture", "expected": "requirement", "actual": {"targets": []}}
            for i in range(1, 21)
        ]}

    def test_complete_matrix_passes(self):
        self.assertTrue(runner.matrix_passed(self.report))

    def test_partial_matrix_fails_even_with_success_flag(self):
        self.report["cases"].pop()
        self.assertFalse(runner.matrix_passed(self.report))

    def test_duplicate_or_unknown_requirement_cannot_replace_a_case(self):
        for replacement in ("A01", "A99"):
            report = copy.deepcopy(self.report)
            report["cases"][-1]["id"] = replacement
            self.assertFalse(runner.matrix_passed(report))

    def test_failed_skipped_or_errored_case_blocks_acceptance(self):
        for field, value in (("status", "failed"), ("status", "skipped"), ("error", "failure")):
            report = copy.deepcopy(self.report)
            report["cases"][0][field] = value
            self.assertFalse(runner.matrix_passed(report))

    def test_missing_observations_or_expectation_blocks_acceptance(self):
        for field in ("setup", "expected", "actual"):
            report = copy.deepcopy(self.report)
            del report["cases"][0][field]
            self.assertFalse(runner.matrix_passed(report))

    def test_malformed_or_incomplete_report_fails_closed(self):
        for report in (None, [], {}, {"accepted": True, "cases": [None] * 20}, {"accepted": "true", "cases": self.report["cases"]}):
            self.assertFalse(runner.matrix_passed(report))


if __name__ == "__main__":
    unittest.main()
