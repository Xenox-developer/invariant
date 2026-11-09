"""Reconcile v3 counters, fixed results and negative controls without model calls."""
import csv,hashlib,json,shutil,sys
from pathlib import Path
from analyze import normalize,response_overlap
from audit_checker import SCRIPT
from baseline_v3 import docker
HERE=Path(__file__).resolve().parent;BASE=HERE/'results/v3'
def read(path):return json.loads(path.read_text())
def write(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
def main():
    comparisons={};negative={};all_csv=[]
    for label in ('cold','warm'):
        comparisons[label]={};negative[label]={}
        for arm in ('with-invariant','without-invariant'):
            root=BASE/label/arm;summary=read(root/'summary.json')
            result=normalize(summary);result['parallel_model_evidence']=response_overlap(BASE/label,summary)
            comparisons[label][arm]=result
            files={'build_report.py':(root/'agents/checker/build_report.py').read_text(),'fixed_checks.py':(HERE/'fixed_checks.py').read_text()}
            for method in ('trapezoid','simpson'):
                target=root/'agents/checker/inputs'/method;target.mkdir(parents=True,exist_ok=True)
                for name in ('solution.py','result.json'):
                    source=root/'agents'/method/name;shutil.copyfile(source,target/name)
                    files[f'inputs/{method}/{name}']=source.read_text()
            payload=docker('run','--rm','--interactive','--network=none','--read-only','--cap-drop=ALL',
                '--security-opt=no-new-privileges','--tmpfs','/audit:rw,nosuid,nodev',
                '--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint','python3',summary['starting_image'],
                '-B','-c',SCRIPT,input=json.dumps({'files':files}).encode()).stdout
            negative[label][arm]=json.loads(payload)
            with (root/'agents/checker/comparison.csv').open() as stream:
                rows=list(csv.DictReader(stream))
            values={(r['method'],int(r['power']),int(r['n'])):float(r['estimate']) for r in rows}
            assert len(values)==16;all_csv.append(values)
    assert all(v.keys()==all_csv[0].keys() for v in all_csv)
    difference=max(abs(v[k]-all_csv[0][k]) for v in all_csv for k in v);assert difference<1e-12
    old=read(HERE/'results/pair-20261107-a/comparison.json')
    # Old normalized comparison is retained verbatim, not regenerated from mutable core.
    value={'rounds':comparisons,'max_estimate_difference':difference,
           'checks_sha256':hashlib.sha256((HERE/'fixed_checks.py').read_bytes()).hexdigest(),
           'previous_comparison':old,'actual_billed_usd':None,'outer_chat_usage':None}
    write(BASE/'measurements.json',value);write(BASE/'checker-negative-control.json',negative)
    print(json.dumps({label:{arm:{k:r[k] for k in ('wall_seconds','usage','provider_responses','standard_credit_equivalent','outside_agent_windows_seconds')} for arm,r in arms.items()} for label,arms in comparisons.items()},indent=2))
if __name__=='__main__':main()
