"""One real continuation after restarting the manager, outside measured arms."""
import hashlib,json,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[2]))
from invariant.run_context import RunContext
from invariant.docker_storage import Manager,docker
from invariant.host_delivery import export_results,export_logs
BASE=HERE/'results/v3'
def write(name,value):(BASE/name).write_text(json.dumps(value,indent=2)+'\n')

def main():
    assert not (BASE/'continuation.json').exists(),'Do not repeat a paid continuation'
    saved=json.loads((BASE/'warm/with-invariant/context.json').read_text())
    summary=json.loads((BASE/'warm/with-invariant/summary.json').read_text())
    parent=next(a for a in summary['agents'] if a['role']=='trapezoid')
    manager=Manager(saved['project']);assert not manager.request('busy',raw=True)['busy']
    manager.request('shutdown_metadata',raw=True);docker('restart',manager.name)
    restored=RunContext(saved['project'],run_ref=saved['run_ref'])
    before=restored.status();first=restored.collect();second=restored.collect();after=restored.status()
    assert before==after and first==second
    sync=restored.manager.sync();assert sync['unchanged']
    # This new prompt is an intentional input import, after observation checks.
    prompt=Path(saved['project'])/'continue-v3.md'
    prompt.write_text('Continue from the saved trapezoid workspace. Do not change solution.py or result.json. Verify that integrate(lambda x: x*x,0,1,80) matches result.json for power 2 and n 80. Write continuation-proof.json with passed=true, n=80, and the observed estimate. Call invariant_validate once and finish after it passes. Do not spawn agents.\n')
    dispatched=BASE/'continuation-dispatched.json'
    started=json.loads(dispatched.read_text())['started_at'] if dispatched.exists() else time.time()
    child=restored.engine.continue_result(parent['attempt_id'],'continue-v3.md',request_id='v3-continuation-verified-001')
    if not dispatched.exists():
        write('continuation-dispatched.json',{'child':child['id'],'parent':parent['attempt_id'],'started_at':started})
        restored.engine.execute(child['id'])
    rows=restored.engine.results(child['id'])
    paths=export_results(restored.manager,studies=[child['id']]);logs=export_logs(restored.manager)
    trial=rows['trials'][0];assert trial['passed'] is True
    folder=next(Path(p)/'files' for p in paths if Path(p).name.startswith(trial['attempt_id']))
    proof=json.loads((folder/'continuation-proof.json').read_text());assert proof['passed'] and proof['n']==80
    root=Path(restored.manager.home)/'.invariant/workspaces'/restored.manager.key/'experiments/attempts'/trial['attempt_id']/'snapshot/workspace'
    for name in ('solution.py','result.json'):
        raw=docker('exec',restored.manager.name,'cat',str(root/name)).stdout
        assert raw==(BASE/'warm/with-invariant/agents/trapezoid'/name).read_bytes()
    # The new study inherits all 35 fixed checks and the exact checkpoint image.
    assert trial['evaluation']['metrics']['tests_passed']==35
    replay=restored.engine.continue_result(parent['attempt_id'],'continue-v3.md',request_id='v3-continuation-verified-001')
    assert replay['id']==child['id'] and replay['idempotent_replay']
    write('continuation.json',{'manager_restarted':True,'run_ref':saved['run_ref'],'status_preserved':before==after,
        'unchanged_collect_reuses_paths':first==second,'source_not_reimported':sync,'child':child['id'],
        'parent':parent['attempt_id'],'idempotent_replay':True,'parent_files_unchanged':True,
        'wall_seconds':time.time()-started,'trial':trial,'proof':proof,'exported_results':paths,'exported_logs':logs,
        'excluded_from_benchmark':True})
    print(json.dumps({'continued':True,'tests':35,'child':child['id']}),flush=True)
if __name__=='__main__':main()
