"""Frozen acceptance checks, shared by both experimental arms (standard library)."""
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

NS = (10, 20, 40, 80)
POWERS = (2, 4)


def expected(method, power, n):
    exact = 1 / (power + 1)
    if method == 'trapezoid':
        return exact + (1 / (6*n*n) if power == 2 else 1/(3*n*n)-1/(30*n**4))
    return exact if power == 2 else exact + 2/(15*n**4)


def near(actual, target, atol=2e-13, rtol=2e-11):
    assert isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isfinite(actual)
    assert math.isclose(actual, target, abs_tol=atol, rel_tol=rtol), (actual, target)


def module(root):
    spec = importlib.util.spec_from_file_location('candidate_solution', root / 'solution.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    assert callable(result.integrate)
    return result


def document(root):
    return json.loads((root/'result.json').read_text())


def row(root, power, n):
    rows = [r for r in document(root)['rows'] if r['power'] == power and r['n'] == n]
    assert len(rows) == 1
    return rows[0]


class Checks:
    def __init__(self):self.rows=[]
    def check(self, name, function):
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):function()
            self.rows.append({'name':name,'passed':True})
        except Exception as error:
            self.rows.append({'name':name,'passed':False,'error':type(error).__name__+': '+str(error)[:250]})
    def result(self):
        failed=sum(not r['passed'] for r in self.rows)
        return {'passed':failed==0,'tests':len(self.rows),'failures':failed,'errors':0,'checks':self.rows}


def equal(actual, target):assert actual == target, (actual, target)


def validate_method(method, root):
    tests=Checks()
    tests.check('callable_general_integrator', lambda: module(root))
    tests.check('assigned_method',lambda: equal(document(root)['method'],method))
    tests.check('interval',lambda: equal(document(root)['interval'],[0,1]))
    tests.check('eight_unique_rows',lambda: equal(sorted((r['power'],r['n']) for r in document(root)['rows']),
        sorted((p,n) for p in POWERS for n in NS)))
    for p in POWERS:
        for n in NS:
            tests.check(f'computed_estimate_x{p}_n{n}',lambda p=p,n=n:near(row(root,p,n)['estimate'],expected(method,p,n)))
            tests.check(f'exact_reference_x{p}_n{n}',lambda p=p,n=n:near(row(root,p,n)['exact'],1/(p+1)))
            tests.check(f'correct_error_x{p}_n{n}',lambda p=p,n=n:near(row(root,p,n)['abs_error'],abs(expected(method,p,n)-1/(p+1)),atol=2e-14,rtol=2e-7))
    for a,b,n in ((-1,2,10),(.25,1.25,20),(-2,0,40)):
        tests.check(f'general_linear_{a}_{b}',lambda a=a,b=b,n=n:near(module(root).integrate(lambda x:3*x+2,a,b,n),1.5*(b*b-a*a)+2*(b-a)))
    tests.check('nonpolynomial_sine',lambda:near(module(root).integrate(math.sin,0,math.pi,100),2,
        atol=2e-4 if method=='trapezoid' else 2e-8,rtol=0))
    def sample_count():
        calls=[]
        module(root).integrate(lambda x:calls.append(x) or x*x,0,1,10)
        equal(len(calls),11)
        equal(len(set(calls)),11)
    tests.check('uses_function_samples',sample_count)
    for n in ((0,-2,3) if method=='simpson' else (0,-2)):
        def invalid(n=n):
            try:module(root).integrate(lambda x:x*x,0,1,n)
            except ValueError:return
            raise AssertionError('Expected ValueError for invalid n')
        tests.check(f'invalid_n_{n}',invalid)
    return tests.result()


def input_document(root, method):return json.loads((root/'inputs'/method/'result.json').read_text())


def validate_report(root, reference):
    tests=Checks()
    def verify_csv():
        with (root/'comparison.csv').open(newline='') as stream:rows=list(csv.DictReader(stream))
        equal(len(rows),16)
        keys=[]
        for r in rows:
            m,p,n=r['method'],int(r['power']),int(r['n']);keys.append((m,p,n))
            assert m in ('trapezoid','simpson') and p in POWERS and n in NS
            source=next(s for s in input_document(reference,m)['rows'] if s['power']==p and s['n']==n)
            for key in ('estimate','exact','abs_error'):near(float(r[key]),source[key])
        equal(len(set(keys)),16)
    tests.check('csv_matches_all_sixteen_received_rows',verify_csv)
    tests.check('report_has_substantive_text',lambda:equal(len((root/'report.md').read_text())>=500,True))
    def verification():return json.loads((root/'verification.json').read_text())
    tests.check('verification_passed',lambda:equal(verification()['passed'],True))
    tests.check('verified_sixteen_rows',lambda:equal(verification()['rows_checked'],16))
    for method in ('trapezoid','simpson'):
        def provenance(method=method):
            path=reference/'inputs'/method/'result.json'
            equal(verification()['input_sha256'][method],hashlib.sha256(path.read_bytes()).hexdigest())
            equal((root/'inputs'/method/'result.json').read_bytes(),path.read_bytes())
            equal((root/'inputs'/method/'solution.py').read_bytes(),(reference/'inputs'/method/'solution.py').read_bytes())
        tests.check('input_provenance_'+method,provenance)
    tests.check('trapezoid_order',lambda:equal(verification()['conclusions']['trapezoid_order'],2))
    tests.check('simpson_order',lambda:equal(verification()['conclusions']['simpson_order'],4))
    tests.check('simpson_quadratic_roundoff',lambda:equal(verification()['conclusions']['simpson_x2_exact_within_roundoff'],True))
    for method,p,target in (('trapezoid',2,4),('trapezoid',4,4),('simpson',4,16)):
        def ratios(method=method,p=p,target=target):
            found=verification()['observed_ratios'][f'{method}_x{p}']
            equal(len(found),3)
            data={r['n']:r for r in input_document(reference,method)['rows'] if r['power']==p}
            for n,r in zip(NS[:-1],found):
                near(r,data[n]['abs_error']/data[n*2]['abs_error'],atol=1e-6,rtol=1e-6)
                near(r,target,atol=.015,rtol=0)
        tests.check('observed_ratios_'+method+'_x'+str(p),ratios)
    def svg():
        image=ET.parse(root/'errors.svg').getroot()
        equal(image.tag.rsplit('}',1)[-1],'svg')
        assert len([e for e in image.iter() if e.tag.rsplit('}',1)[-1]=='polyline'])>=3
        text=' '.join(image.itertext()).lower()
        assert 'trapezoid' in text and 'simpson' in text
    tests.check('svg_has_three_named_error_series',svg)
    def reproduce():
        with tempfile.TemporaryDirectory() as folder:
            result=subprocess.run([sys.executable,'-B',str(root/'build_report.py'),'--inputs',str(reference/'inputs'),
                '--output',folder],cwd=root,capture_output=True,text=True,timeout=20)
            equal(result.returncode,0)
            for name in ('comparison.csv','verification.json','errors.svg','report.md'):
                assert (Path(folder)/name).is_file(),name
            with (Path(folder)/'comparison.csv').open(newline='') as stream:rows=list(csv.DictReader(stream))
            equal(len(rows),16)
            equal(json.loads((Path(folder)/'verification.json').read_text())['passed'],True)
    tests.check('report_generator_reproducible',reproduce)
    return tests.result()


def validate(role, root, reference=None):
    return validate_report(Path(root),Path(reference or root)) if role=='checker' else validate_method(role,Path(root))
