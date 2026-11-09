"""The scientific task and role contract; no INVARIANT imports or model calls."""
import json
from pathlib import Path
import shutil

MODEL='gpt-6.1-sol'
EFFORT='medium'
COMMON='''Research the accuracy of numerical integration of x**2 and x**4 on [0,1], using n=10,20,40,80 subintervals.
The exact integrals are 1/3 and 1/5. Use only Python's standard library, no external services or package installs.
Work in /workspace. Do not spawn additional agents. Do not inspect authentication or unrelated settings.
Do actual computations and preserve runnable source. Keep your final chat reply short; results belong in files.
The fixed acceptance checks are in fixed_checks.py; do not modify this file or evaluate.py or input artifacts.
If invariant_validate is available, call it before finishing and finish immediately after it passes.
Otherwise run the visible acceptance checks once, fix problems if necessary, then finish.
'''
METHOD='''Your assigned method is {method}. Independently implement solution.py with integrate(f,a,b,n).
The function must actually sample f at the n+1 uniform grid points, work on other intervals/functions,
and reject n<=0 with ValueError. Simpson additionally requires an even n (raise ValueError for odd n).
Running python3 -B solution.py must generate result.json with exactly this structure:
{{"method":"{method}","interval":[0,1],"rows":[{{"power":2,"n":10,"estimate":number,"exact":number,"abs_error":number}}, ...]}}
Include all eight (power,n) combinations, power in [2,4], n in [10,20,40,80]. Compute absolute errors from the estimates.
Do not implement the other method or write the final combined report. Generate result.json now.
For local checks if no invariant_validate exists:
python3 -B -c "from fixed_checks import validate; import json; from pathlib import Path; r=validate('{method}',Path('.')); print(json.dumps(r)); assert r['passed']"
'''
CHECKER='''You are the third, independent checking/reporting agent. The two earlier agents have already finished.
Their unmodified solution.py and result.json files are in inputs/trapezoid/ and inputs/simpson/.
Verify all 16 reported values independently against the exact integrals and mathematical error formulas;
execute or examine both implementations. Do not edit the received inputs or replace their calculations.
Implement build_report.py using only the standard library, supporting --inputs DIR --output DIR.
Run it with --inputs inputs --output . and produce these four files:
1. comparison.csv: header method,power,n,estimate,exact,abs_error and all sixteen received numeric rows.
2. report.md: a short Russian research report (at least 500 characters) explaining the methods, exact references,
   measured convergence and rounding for Simpson on x**2. Mention this is a small deterministic numerical experiment.
3. errors.svg: a valid labelled SVG graph with at least three polyline series for trapezoid x**2, trapezoid x**4,
   Simpson x**4, with legends containing literal method names trapezoid and simpson. Use a log scale for error;
   do not take log(0); explain the omitted near-zero Simpson x**2 series in the report.
4. verification.json with exactly these required fields (additional fields allowed):
{"passed":true,"rows_checked":16,"input_sha256":{"trapezoid":"SHA256 of actual received result.json bytes","simpson":"SHA256"},
 "observed_ratios":{"trapezoid_x2":[three actual consecutive error ratios],"trapezoid_x4":[three ratios],"simpson_x4":[three ratios]},
 "conclusions":{"trapezoid_order":2,"simpson_order":4,"simpson_x2_exact_within_roundoff":true}}
Only mark passed if you actually verified the data. Expected error ratios upon doubling n are about 4 and 16.
The generator must reproduce the four outputs from the input artifacts in a fresh output directory.
For local checks if no invariant_validate exists:
python3 -B -c "from fixed_checks import validate; import json; from pathlib import Path; r=validate('checker',Path('.')); print(json.dumps(r)); assert r['passed']"
'''


def prepare(destination, source, *, checker_inputs=None):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    for name in ('fixed_checks.py','evaluate.py'):shutil.copyfile(source/name,destination/name)
    (destination/'TASK.md').write_text(COMMON)
    if checker_inputs is None:
        (destination/'solution.py').write_text('def integrate(f,a,b,n):\n    raise NotImplementedError("Assigned agent implements its numerical method")\n')
        (destination/'result.json').write_text('{"method":"pending","rows":[]}\n')
        roles=('trapezoid','simpson')
        include=['solution.py','result.json','TASK.md','fixed_checks.py']
    else:
        roles=('checker',)
        for role,folder in checker_inputs.items():
            target=destination/'inputs'/role;target.mkdir(parents=True,exist_ok=True)
            for name in ('solution.py','result.json'):shutil.copyfile(Path(folder)/name,target/name)
        include=['inputs/**','TASK.md','fixed_checks.py']
    for role in roles:(destination/(role+'.md')).write_text(CHECKER if role=='checker' else METHOD.format(method=role))
    protected=['fixed_checks.py','evaluate.py']
    if checker_inputs is not None:protected += [f'inputs/{r}/{n}' for r in ('trapezoid','simpson') for n in ('solution.py','result.json')]
    definition={'schema_version':1,'name':'integration-team-'+('check' if checker_inputs else 'methods'),
        'runner':{'type':'codex'},'variants':[{'id':r,'model':MODEL,'effort':EFFORT,'instructions_file':r+'.md'} for r in roles],
        'cases':[{'id':'quadrature','prompt_file':'TASK.md'}],
        'workspace':{'include':include,'exclude':[]},'execution':{'max_parallel':len(roles)},
        'evaluator':{'argv':['python3','evaluate.py'],'source_paths':protected,'timeout_seconds':0},
        'artifacts':['solution.py','result.json','build_report.py','comparison.csv','report.md','errors.svg','verification.json']}
    (destination/'experiment.json').write_text(json.dumps(definition,indent=2)+'\n')
    return definition
