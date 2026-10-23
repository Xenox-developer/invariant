"""Shared captured checks. Candidate code runs in bounded child processes."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys


def check(solution, case):
    value = case['input']
    if case.get('as_tuples'):
        value = tuple(tuple(pair) for pair in value)
    before = copy.deepcopy(value)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        spec = importlib.util.spec_from_file_location('demo_candidate', solution)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            result = module.merge_windows(value)
        except ValueError:
            if case.get('error') != 'ValueError':
                raise AssertionError('unexpected ValueError')
        else:
            if 'error' in case:
                raise AssertionError('expected ValueError')
            if (type(result) is not list or any(type(pair) is not list or len(pair) != 2
                    or any(type(n) is not int for n in pair) for pair in result)
                    or result != case['expected']):
                raise AssertionError('wrong result or return type')
        if value != before:
            raise AssertionError('input was mutated')
    return {'passed': True}


def evaluate(solution, suite, output):
    rows = []
    for case in json.loads(suite.read_text()):
        row = {'name': case['name'], 'passed': False}
        try:
            child = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                                    '--case', str(solution)], input=json.dumps(case),
                                   capture_output=True, text=True, timeout=2)
            row['passed'] = child.returncode == 0 and json.loads(child.stdout) == {'passed': True}
            if not row['passed']:
                row['error'] = 'assertion, import or candidate failure'
        except subprocess.TimeoutExpired:
            row['error'] = 'test exceeded two seconds'
        except (ValueError, OSError):
            row['error'] = 'invalid or missing test result'
        rows.append(row)
    output.mkdir(parents=True, exist_ok=True)
    (output/'tests.json').write_text(json.dumps({'tests': rows}, indent=2)+'\n')
    count = sum(row['passed'] for row in rows)
    return {'schema_version': 1, 'passed': bool(rows) and count == len(rows),
            'metrics': {'tests_passed': count, 'tests_total': len(rows)}, 'artifacts': ['tests.json']}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--case':
        print(json.dumps(check(Path(sys.argv[2]), json.loads(sys.stdin.read()))))
    else:
        print(json.dumps(evaluate(Path(os.environ['INVARIANT_CANDIDATE_DIR'])/'solution.py',
                                  Path(__file__).with_name('suite.json'),
                                  Path(os.environ['INVARIANT_EVALUATION_OUTPUT_DIR']))))
