"""Profile one isolated agent/executor handshake, deliberately without any model turn."""
import json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parents[2]))
from invariant.docker_storage import docker
BASE=HERE/'results/v3'
SCRIPT=r'''
import contextlib,json,shutil,sys,time,uuid
from pathlib import Path
from invariant import workspace,environment,docker_storage as storage,agent_runtime
from invariant.execution import ExecutionContext
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments.runners import owned_info,stop_owned
from invariant.codex_rpc import CodexRpc
from invariant.dependencies import probe_codex
payload=json.load(sys.stdin);project=Path(payload['project']);study=ExperimentEngine(project).inspect(payload['study'])
rows=[];depth=0
for module in (workspace,storage):
 original=module.docker
 def wrapped(*args,_original=original,**kwargs):
  global depth
  level=depth;depth+=1;started=time.monotonic()
  try:return _original(*args,**kwargs)
  finally:
   depth-=1
   if level==0:rows.append({'kind':'docker','operation':str(args[0]),'subcommand':str(args[1]) if len(args)>1 else '', 'seconds':time.monotonic()-started})
 module.docker=wrapped
@contextlib.contextmanager
def phase(name):
 started=time.monotonic()
 try:yield
 finally:rows.append({'kind':'phase','name':name,'seconds':time.monotonic()-started})
root=workspace.state_for(project)/('diagnostic-startup-'+uuid.uuid4().hex);root.mkdir(mode=0o700)
name='invariant-probe-'+uuid.uuid4().hex
rpc=None;env=None;context=None
try:
 with phase('prepare'):
  writable=root/'workspace';writable.mkdir();state=root/'control';(state/'policy').mkdir(parents=True)
  policy=study['frozen']['effective_policy'];workspace.private_write(state/'policy/config.json',policy)
  image=study['frozen']['image']['id'];control=environment.prepare_control(project,image,path=root/'unused')
  context=ExecutionContext.create(project=project,workspace=writable,state=state,image=image,owner_token=name,
   policy=policy,policy_directory=state/'policy',control_directory=control,checkpoint_owner=name+'-runner')
  home=root/'native-home';home.mkdir();status=probe_codex()
 with phase('agent_configuration_and_create'):command,env=workspace.codex_configuration(home,name,status['path'],policy=policy,coding_profile=True)
 with phase('executor_create'):workspace.docker(*workspace.container_command(name,project,image,context=context))
 with phase('connect_internet_and_verify'):
  agent_runtime.connect_internet(name,policy);workspace.verify_container(name,project,image,context=context,info=owned_info(context))
 with phase('app_server_initialize'):
  rpc=CodexRpc(command,env=env,cwd=home,guard_parent=True);rpc.initialize()
 with phase('environment_info'):rpc.request('environment/info',{'environmentId':'invariant'})
 with phase('executor_startup_inspection'):
  workspace.docker('exec',name,environment.INSPECTOR,'startup-rw')
  workspace.docker('exec',name,'/bin/sh','-c','command -v python3 git sed')
 # No turn/start, turn/steer, prompt, model request or task execution occurs.
finally:
 with phase('cleanup'):
  if rpc is not None:rpc.close()
  elif env is not None:agent_runtime.cleanup(env['INVARIANT_AGENT_CONTAINER'],home,name)
  if context is not None:
   stop_owned(context);workspace.remove_owned(name);agent_runtime.cleanup_network(name)
  shutil.rmtree(root)
print(json.dumps({'model_turns':0,'rows':rows}))
'''
def main():
 saved=json.loads((BASE/'warm/with-invariant/context.json').read_text());summary=json.loads((BASE/'warm/with-invariant/summary.json').read_text())
 raw=docker('exec','--interactive','invariant-manager-b79a08a230ce0fb461f09363','python3','-B','-c',SCRIPT,
  input=json.dumps({'project':saved['project'],'study':summary['agents'][0]['study_id']}).encode(),timeout=180).stdout
 value=json.loads(raw);(BASE/'diagnostics/startup-profile.json').write_text(json.dumps(value,indent=2)+'\n')
 print(json.dumps([r for r in value['rows'] if r['kind']=='phase'],indent=2))
if __name__=='__main__':main()
