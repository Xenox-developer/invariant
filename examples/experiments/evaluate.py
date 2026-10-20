"""Evaluator protocol example; no candidate code is imported by the evaluator."""
import json
import os
from pathlib import Path

case = json.loads(Path(os.environ['INVARIANT_CASE_FILE']).read_text())
answer = json.loads((Path(os.environ['INVARIANT_CANDIDATE_DIR']) / 'outputs/answer.json').read_text())
passed = answer.get('sum') == case['expected_sum']
print(json.dumps({'schema_version': 1, 'passed': passed,
                  'metrics': {'correct': int(passed)}, 'artifacts': []}))
