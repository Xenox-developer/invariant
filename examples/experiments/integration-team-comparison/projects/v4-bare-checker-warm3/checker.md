You are the third, independent checking/reporting agent. The two earlier agents have already finished.
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
