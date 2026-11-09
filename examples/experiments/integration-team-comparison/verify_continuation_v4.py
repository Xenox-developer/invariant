"""Restart/idempotency and two real continuations, including a retained pip venv."""
import hashlib,json,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parents[2]))
from invariant.run_context import RunContext
from invariant.docker_storage import Manager,docker
from invariant.host_delivery import export_results,export_logs
BASE=HERE/'results/v4'
CHECK_SCRIPT='import sys,json,six,hashlib,importlib.metadata; from pathlib import Path; p=Path(six.__file__); marker=Path("/opt/invariant-continuation-v4/marker.txt").read_text().strip(); assert six.__version__==importlib.metadata.version("six")=="1.17.0"; assert sys.prefix=="/workspace/.venv"; assert marker=="persistent-rootfs-proof"; print(json.dumps({"six_version":six.__version__,"sys_prefix":sys.prefix,"six_sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"marker":marker,"independent":True}))'
def read(path):return json.loads(path.read_text())
def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')

def restart(saved):
    manager=Manager(saved['project']);assert not manager.request('busy',raw=True)['busy']
    manager.request('shutdown_metadata',raw=True)
    docker('update','--restart=no',manager.name)
    try:docker('kill','--signal=KILL',manager.name)
    finally:
        docker('start',manager.name);docker('update','--restart=unless-stopped',manager.name)
    return RunContext(saved['project'],run_ref=saved['run_ref'])

def main():
    assert not (BASE/'continuation.json').exists(),'Do not repeat completed proof'
    saved=read(BASE/'warm3/with-invariant/context.json');summary=read(BASE/'warm3/with-invariant/summary.json')
    parent=next(a for a in summary['agents'] if a['role']=='trapezoid');context=restart(saved)
    before=context.status();one=context.collect();two=context.collect();after=context.status();assert before==after and one==two
    replay=context.submit('methods.json',request_id='v4-warm3-methods',sync=False)
    assert replay['id']==parent['study_id'] and replay['idempotent_replay']
    context.execute(replay['id']);assert context.status()==before
    sync=context.manager.sync();assert sync['unchanged']
    observations={'manager_restarted':True,'manager_abruptly_killed':True,'completed_submission_and_execute_replayed_without_new_attempt':True,'status_preserved':True,'unchanged_collect_reuses_paths':True,'source_not_reimported':sync,'parent':parent['attempt_id']}
    stages=[]
    acceptance=BASE/'continuation-acceptance.py';acceptance.write_text(CHECK_SCRIPT+'\n')
    acceptance_sha=hashlib.sha256(acceptance.read_bytes()).hexdigest()
    prompts=[
      'Continue the saved trapezoid task. Keep solution.py and result.json byte-for-byte unchanged. Create /workspace/.venv with python3 -m venv .venv, then install six==1.17.0 into that venv using its pip (internet is available). Do not install anything outside Docker. Create /opt/invariant-continuation-v4/marker.txt containing persistent-rootfs-proof. Write venv-proof.json with passed=true, stage="installed", six_version, sys_prefix and marker from a subprocess running .venv/bin/python. The venv prefix must be /workspace/.venv. Call invariant_validate and let the coordinator finish; no final chat is needed. Do not modify fixed checks or spawn agents.',
      'Continue from the saved checkpoint. Do not reinstall or recreate the existing /workspace/.venv. Keep solution.py and result.json unchanged. Run .venv/bin/python to import six, confirm its version is 1.17.0, confirm sys.prefix is /workspace/.venv and /opt/invariant-continuation-v4/marker.txt still contains persistent-rootfs-proof. Write venv-proof.json with passed=true, stage="reused", six_version, sys_prefix, marker. Call invariant_validate and let the coordinator finish. Do not modify checks or spawn agents.'
    ]
    identifier=parent['attempt_id']
    for index,prompt in enumerate(prompts,1):
        context.close();context=restart(saved)
        source=Path(saved['project'])/f'continue-v4-{index}.md';source.write_text(prompt+'\n')
        dispatch=BASE/f'continuation-{index}-dispatch.json'
        child=context.engine.continue_result(identifier,source.name,request_id=f'v4-continuation-{index}')
        context.manager.request('run_add',{'run_ref':saved['run_ref'],'study_id':child['id']},raw=True)
        if not dispatch.exists():write(dispatch,{'id':child['id'],'parent':identifier,'started_at':time.time(),'additional_acceptance_sha256':acceptance_sha,'additional_acceptance_file':str(acceptance)})
        # execute is idempotent for completed studies; never creates another attempt.
        context.engine.execute(child['id']);rows=context.engine.results(child['id']);trial=rows['trials'][0]
        assert trial['passed'] is True and trial['evaluation']['metrics']['tests_passed']==35
        folder=Path(context.manager.home)/'.invariant/workspaces'/context.manager.key/'experiments/attempts'/trial['attempt_id']/'snapshot/workspace'
        proof=json.loads(docker('exec',context.manager.name,'cat',str(folder/'venv-proof.json')).stdout)
        assert proof['passed'] and proof['six_version']=='1.17.0' and proof['sys_prefix']=='/workspace/.venv' and proof['marker'].strip()=='persistent-rootfs-proof',proof
        for name in ('solution.py','result.json'):
            assert docker('exec',context.manager.name,'cat',str(folder/name)).stdout==(BASE/'warm3/with-invariant/agents/trapezoid'/name).read_bytes()
        # Independently run the saved interpreter from the exact saved rootfs.
        relative=folder.relative_to(Path(context.manager.home)/'.invariant').as_posix()
        mapping=next(m for m in context.manager.map if m['volume']=='invariant-state-v2')
        daemon_path=str(Path(mapping['daemon_path'])/relative)
        assert hashlib.sha256(acceptance.read_bytes()).hexdigest()==acceptance_sha
        check=acceptance.read_text()
        independent=json.loads(docker('run','--rm','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
          '--mount',f'type=bind,src={daemon_path},dst=/workspace,readonly','--env','PYTHONDONTWRITEBYTECODE=1',
          '--entrypoint','/workspace/.venv/bin/python',trial['checkpoint']['image'],'-B','-c',check).stdout)
        if stages:assert independent['six_sha256']==stages[0]['independent_checks']['six_sha256']
        replay=context.engine.continue_result(identifier,source.name,request_id=f'v4-continuation-{index}')
        assert replay['id']==child['id'] and replay['idempotent_replay']
        paths=export_results(context.manager,studies=[child['id']])
        assert all(not (Path(p)/'files/.venv').exists() for p in paths)
        stages.append({'study_id':child['id'],'trial':trial,'proof':proof,'independent_checks':independent,'idempotent_replay':True,'exported_results':paths})
        write(BASE/f'continuation-{index}.json',stages[-1]);identifier=trial['attempt_id']
    write(BASE/'continuation.json',{**observations,'stages':stages,'parent_files_unchanged':True,'venv_not_exported':True,
       'venv_reused_without_installation':True,'rootfs_marker_preserved':True,'additional_acceptance_sha256':acceptance_sha,'additional_acceptance_is_independent_of_worker':True,'excluded_from_benchmark':True,'exported_logs':export_logs(context.manager)})
    context.close();print(json.dumps({'continued':True,'checks':70,'venv_and_rootfs_preserved':True}))
if __name__=='__main__':main()
