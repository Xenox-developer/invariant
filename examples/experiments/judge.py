import json
import os
from pathlib import Path

case = json.loads(Path(os.environ['INVARIANT_CASE_FILE']).read_text())
candidate = Path(os.environ['INVARIANT_CANDIDATE_DIR'])/'result.txt'
passed = candidate.read_text().strip() == str(case['expected'])
print(json.dumps({'schema_version': 1, 'passed': passed, 'metrics': {'correct': int(passed)}, 'artifacts': []}))
