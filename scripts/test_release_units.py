#!/usr/bin/env python3
"""Save individual unit-test outcomes for the experiment release gate."""
import argparse
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'sdk/python')]


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outcomes = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.outcomes.setdefault(test.id(), {'status': 'passed'})

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.outcomes[test.id()] = {'status': 'failed', 'error': self._exc_info_to_string(err, test)}

    def addError(self, test, err):
        super().addError(test, err)
        self.outcomes[test.id()] = {'status': 'failed', 'error': self._exc_info_to_string(err, test)}

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.outcomes[test.id()] = {'status': 'blocked', 'error': reason}

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.outcomes[test.id()] = {'status': 'blocked', 'error': 'expected failure'}

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.outcomes[test.id()] = {'status': 'failed', 'error': 'unexpected success'}

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err:
            self.outcomes[test.id()] = {'status': 'failed', 'error': self._exc_info_to_string(err, test)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--suite', choices=('scripts', 'sdk'), default='scripts')
    args = parser.parse_args()
    directory = ROOT/('scripts/tests' if args.suite == 'scripts' else 'sdk/python/tests')
    suite = unittest.defaultTestLoader.discover(str(directory))
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
    passed = result.wasSuccessful() and result.testsRun > 0 and all(v['status'] == 'passed' for v in result.outcomes.values())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({'schema_version': 1, 'passed': passed,
        'test_count': result.testsRun, 'tests': result.outcomes}, indent=2)+'\n')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
