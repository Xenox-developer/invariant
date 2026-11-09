"""Run in the trusted Docker manager, using the public generic command engine."""
import json,os,sys,time
from pathlib import Path
from invariant.agent_runtime import account_home
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments.lifecycle import references
from invariant.run_context import operate
from invariant import workspace

from invariant.storage_gc import gate
with gate():
 value=json.load(sys.stdin);project=Path(value['project']);os.environ['INVARIANT_SECRET_BENCH_AUTH']=str(account_home()/'auth.json')
 engine=ExperimentEngine(project)
 if value['action']=='prepare_tools':
  import hashlib,shutil
  from invariant.environment_cli import prepare
  record=workspace.state_for(project)/'matched-tools-v5.json'
  if record.exists():result=json.loads(record.read_text())
  else:
   stage=project/'.invariant/matched-tool-source';stage.mkdir(parents=True,exist_ok=True)
   hashes={}
   for name in ('codex','codex-code-mode-host'):
    source=Path('/usr/local/bin')/name;shutil.copyfile(source,stage/name);hashes[name]=hashlib.sha256(source.read_bytes()).hexdigest()
   result=prepare('mkdir -p /opt/bench-tools && cp /workspace/.invariant/matched-tool-source/codex /workspace/.invariant/matched-tool-source/codex-code-mode-host /opt/bench-tools/ && chmod 555 /opt/bench-tools/*',project=project,timeout=0)
   assert result['prepared'];result['binary_sha256']=hashes;workspace.private_write(record,result)
 elif value['action']=='phase':
  started=time.time();study=engine.submit(value['recipe'],request_id=value['request_id'],result_inputs=value.get('result_inputs'))
  references(engine,study,update=True);operate(project,'run_add',{'run_ref':value['run_ref'],'study_id':study['id']})
  prepared=time.time()
  from invariant.experiments.preflight import check_frozen
  preflight=check_frozen(engine,study['id'],request_id=value['request_id']+'-baseline');assert preflight['ready']
  checked=time.time();engine.execute(study['id']);finished=time.time();rows=engine.results(study['id'])
  assert all(r['passed'] is True for r in rows['trials']),[(r['state'],r.get('reason')) for r in rows['trials']]
  records=[]
  for row in rows['trials']:
   folder=engine.root/'attempts'/row['attempt_id']/'snapshot/workspace'
   records.append({'role':row['variant_id'],'attempt_id':row['attempt_id'],'study_id':study['id'],
     'capture':row['capture'],'checkpoint':row['checkpoint'],'evaluation':row['evaluation'],
     'runner':row['runner'],'workload':json.loads((folder/'workload.json').read_text())})
  result={'study_id':study['id'],'records':records,'started_at':started,'submitted_at':prepared,'baseline_finished_at':checked,'baseline':preflight,'finished_at':finished,'seconds':finished-started}
 elif value['action']=='continue':
  child=engine.continue_result(value['attempt_id'],value['prompt_file'],request_id=value['request_id'])
  engine.execute(child['id']);rows=engine.results(child['id']);row=rows['trials'][0];assert row['passed']
  root=engine.root/'attempts'/row['attempt_id']/'snapshot/workspace'
  result={'study_id':child['id'],'trial':row,'proof':json.loads((root/'continuation-proof.json').read_text())}
 else:raise ValueError('Unknown operation')
 print(json.dumps(result))
