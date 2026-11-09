"""One fixed acceptance suite for both assigned AST tools."""
import json
import os
from pathlib import Path
import subprocess

output=Path(os.environ['INVARIANT_EVALUATION_OUTPUT_DIR'])
output.mkdir(parents=True,exist_ok=True)
result=subprocess.run(['node','tests/acceptance.cjs'],capture_output=True,text=True)
(output/'test-output.txt').write_text(result.stdout+result.stderr)
try:
    report=json.loads(result.stdout.strip().splitlines()[-1])
    assert report['passed']==(result.returncode==0)
    (output/'test-report.json').write_text(json.dumps(report,indent=2)+'\n')
    verdict={'schema_version':1,'passed':report['passed'],
             'metrics':{'tests_total':report['tests'],'tests_passed':report['tests']-report['failures'],
                        'tests_failed':report['failures']},
             'artifacts':['test-output.txt','test-report.json']}
except (ValueError,IndexError,KeyError,AssertionError):
    verdict={'schema_version':1,'passed':None,'metrics':{},'artifacts':['test-output.txt']}
print(json.dumps(verdict))
