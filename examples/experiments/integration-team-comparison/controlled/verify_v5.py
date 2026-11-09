"""Independent checker negative controls and restart/continuation evidence."""
import hashlib,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;TASK=HERE.parent;sys.path.insert(0,str(TASK));sys.path.insert(0,str(TASK.parents[2]))
from audit_checker import SCRIPT
from baseline_v3 import docker
from invariant.run_context import RunContext
from invariant.docker_storage import Manager
from run_v5 import engine_call
BASE=TASK/'results/v5'
def read(path):return json.loads(path.read_text())
def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def restart(saved):
 manager=Manager(saved['project']);assert not manager.request('busy',raw=True)['busy']
 manager.request('shutdown_metadata',raw=True);docker('update','--restart=no',manager.name)
 try:docker('kill','--signal=KILL',manager.name)
 finally:docker('start',manager.name);docker('update','--restart=unless-stopped',manager.name)
 return RunContext(saved['project'],run_ref=saved['run_ref'])
def main():
 negatives={}
 for label in ('live-c','live-d','replay-b','replay-c','replay-d'):
  pair=read(BASE/label/'pair.json');negatives[label]={}
  for arm in ('with-invariant','without-invariant'):
   base=BASE/label/arm/'agents';files={'build_report.py':(base/'checker/build_report.py').read_text(),'fixed_checks.py':(TASK/'fixed_checks.py').read_text()}
   for role in ('trapezoid','simpson'):
    for name in ('solution.py','result.json'):files[f'inputs/{role}/{name}']=(base/role/name).read_text()
   raw=docker('run','--rm','--interactive','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
    '--tmpfs','/audit:rw,nosuid,nodev','--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint','python3',pair['compute_image'],
    '-B','-c',SCRIPT,input=json.dumps({'files':files}).encode()).stdout
   negatives[label][arm]=json.loads(raw)
 write(BASE/'negative-controls.json',negatives)
 assert not (BASE/'continuation.json').exists(),'Do not repeat the completed continuation'
 saved=read(BASE/'live-d/context.json');pair=read(BASE/'live-d/pair.json');context=restart(saved)
 parent=next(r for r in pair['phases']['with-invariant']['methods']['records'] if r['role']=='trapezoid')
 before=context.status();one=context.collect();two=context.collect();assert before==context.status() and one==two
 # Address the sealed study by ID: the shared fixture has since been reseeded.
 context.engine.execute(parent['study_id']);assert context.status()==before
 prompt=Path(saved['project'])/'continue-v5.md';prompt.write_text('VERIFY_RETAINED_CONTEXT\n')
 context.manager.sync()
 proof=engine_call(context.manager,{'action':'continue','project':saved['project'],'attempt_id':parent['attempt_id'],
  'prompt_file':prompt.name,'request_id':'v5-context-continuation'})
 assert proof['proof']['passed'] and proof['proof']['history_files']>0 and proof['proof']['rootfs_marker_preserved'] and proof['proof']['model_calls']==0
 assert proof['trial']['evaluation']['metrics']['tests_passed']==35
 folder=Path(context.manager.home)/'.invariant/workspaces'/context.manager.key/'experiments/attempts'/proof['trial']['attempt_id']/'snapshot/workspace'
 hashes={}
 for name in ('solution.py','result.json'):
  raw=docker('exec',context.manager.name,'cat',str(folder/name)).stdout
  assert raw==(BASE/'live-d/with-invariant/agents/trapezoid'/name).read_bytes();hashes[name]=hashlib.sha256(raw).hexdigest()
 write(BASE/'continuation.json',{'manager_abruptly_restarted':True,'original_completed_execution_idempotent':True,
  'double_collection_idempotent':True,'parent_files_unchanged':hashes,'continuation':proof,'additional_model_calls':0,'excluded_from_timing':True})
 context.close();print(json.dumps({'negative_controls':len(negatives)*2,'continuation_checks':35,'model_calls':0}))
if __name__=='__main__':main()
