Your assigned method is simpson. Independently implement solution.py with integrate(f,a,b,n).
The function must actually sample f at the n+1 uniform grid points, work on other intervals/functions,
and reject n<=0 with ValueError. Simpson additionally requires an even n (raise ValueError for odd n).
Running python3 -B solution.py must generate result.json with exactly this structure:
{"method":"simpson","interval":[0,1],"rows":[{"power":2,"n":10,"estimate":number,"exact":number,"abs_error":number}, ...]}
Include all eight (power,n) combinations, power in [2,4], n in [10,20,40,80]. Compute absolute errors from the estimates.
Do not implement the other method or write the final combined report. Generate result.json now.
For local checks if no invariant_validate exists:
python3 -B -c "from fixed_checks import validate; import json; from pathlib import Path; r=validate('simpson',Path('.')); print(json.dumps(r)); assert r['passed']"
