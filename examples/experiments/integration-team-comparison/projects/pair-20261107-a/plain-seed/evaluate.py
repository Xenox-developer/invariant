"""Fixed evaluator; executed from frozen source, never worker-supplied tests."""
import json
import os
from pathlib import Path
from fixed_checks import validate

variant=json.loads(Path(os.environ['INVARIANT_VARIANT_FILE']).read_text())['id']
if variant=='baseline':variant='checker' if (Path(__file__).resolve().parent/'inputs').exists() else 'trapezoid'
report=validate(variant,Path(os.environ['INVARIANT_CANDIDATE_DIR']),Path(__file__).resolve().parent)
output=Path(os.environ['INVARIANT_EVALUATION_OUTPUT_DIR']);output.mkdir(parents=True,exist_ok=True)
(output/'checks.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'schema_version':1,'passed':report['passed'],
    'metrics':{'tests_total':report['tests'],'tests_passed':report['tests']-report['failures'],'tests_failed':report['failures']},
    'artifacts':['checks.json']}))
