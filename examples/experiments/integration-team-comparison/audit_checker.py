"""Post-run negative control: the checker must reject a corrupted numerical row."""
import json
from pathlib import Path
import sys
from plain_backend import docker

HERE=Path(__file__).resolve().parent
SCRIPT=r'''
import json,sys,subprocess,tempfile
from pathlib import Path
payload=json.load(sys.stdin)
root=Path(tempfile.mkdtemp(dir='/audit'))
for name,text in payload['files'].items():
    target=root/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
def run(inputs,output):
    result=subprocess.run([sys.executable,'-B',str(root/'build_report.py'),'--inputs',str(inputs),'--output',str(output)],
        cwd=root,capture_output=True,text=True,timeout=20)
    path=output/'verification.json'
    passed=json.loads(path.read_text()).get('passed') if path.exists() else None
    return result.returncode,passed
code,passed=run(root/'inputs',root/'valid-output')
assert code==0 and passed is True,('Valid fixture failed',code,passed)
path=root/'inputs/trapezoid/result.json'
data=json.loads(path.read_text())
data['rows'][0]['estimate']+=.125
data['rows'][0]['abs_error']=abs(data['rows'][0]['estimate']-data['rows'][0]['exact'])
path.write_text(json.dumps(data))
code,passed=run(root/'inputs',root/'corrupt-output')
assert code!=0 or passed is False,'Checker falsely accepted the corrupted estimate'
print(json.dumps({'valid_inputs_accepted':True,'corrupt_estimate_rejected':True,
 'corrupt_exit_code':code,'corrupt_verdict':passed,'model_calls':0}))
'''


def main():
    run=HERE/'results'/sys.argv[1]
    summary=json.loads((run/'with-invariant/summary.json').read_text())
    results={}
    for arm in ('with-invariant','without-invariant'):
        base=run/arm/'agents'
        files={'build_report.py':(base/'checker/build_report.py').read_text(),'fixed_checks.py':(HERE/'fixed_checks.py').read_text()}
        for role in ('trapezoid','simpson'):
            for name in ('solution.py','result.json'):files[f'inputs/{role}/{name}']=(base/role/name).read_text()
        # The driver is a standard-library helper, not a seventh research agent.
        raw=docker('run','--rm','--interactive','--network=none','--read-only','--cap-drop=ALL',
            '--security-opt=no-new-privileges','--tmpfs','/audit:rw,nosuid,nodev',
            '--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint','python3',summary['starting_image'],
            '-B','-c',SCRIPT,input=json.dumps({'files':files}).encode()).stdout
        results[arm]=json.loads(raw)
    (run/'checker-negative-control.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))


if __name__=='__main__':main()
