"""Matched CLI context and deterministic replay isolate the orchestration cost."""
from concurrent.futures import ThreadPoolExecutor
import argparse,hashlib,json,shutil,subprocess,sys,time,uuid
from pathlib import Path
HERE=Path(__file__).resolve().parent;TASK=HERE.parent;REPO=TASK.parents[2];sys.path.insert(0,str(REPO));sys.path.insert(0,str(TASK))
from specification import prepare,COMMON,METHOD,CHECKER,MODEL,EFFORT
from run_v3 import FEATURES
from baseline_v3 import docker,archive_files,CAPS
from invariant.docker_storage import Manager,IMAGE,STATE_VOLUME,source_digest
from invariant.run_context import RunContext
BASE=TASK/'results/v5';COMPUTE=json.loads((TASK/'results/v4/protocol.json').read_text())['image']
FILES=('solution.py','result.json','build_report.py','comparison.csv','report.md','errors.svg','verification.json','workload.json')
def write(path,value):path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
def read(path):return json.loads(Path(path).read_text())
def note(stage,**kw):print(json.dumps({'stage':stage,**kw}),flush=True)

def seed(project,mode,nonce):
 project.mkdir(parents=True,exist_ok=True)
 definition=prepare(project,TASK)
 config={**FEATURES,'model_reasoning_effort':EFFORT,'cli_auth_credentials_store':'file','web_search':'disabled',
  'approval_policy':'never','sandbox_mode':'danger-full-access','check_for_update_on_startup':False,
  'project_root_markers':[],'project_doc_max_bytes':0,
  'skills.config':[{'name':n,'enabled':False} for n in ('imagegen','openai-docs','skill-creator','skill-installer')],
  'shell_environment_policy.inherit':'none',
  'shell_environment_policy.set':{'HOME':'/root','TMPDIR':'/scratch','PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'},
  'developer_instructions':'Matched overhead experiment cohort '+nonce+'. Work in /workspace. Batch related reads and short sequential tool calls. Run local acceptance once. Do not inspect .codex, driver or profile files. No invariant_validate tool is present. Do not spawn agents.'}
 common=COMMON.split('If invariant_validate is available')[0]
 prompts={r:common+'\n'+(CHECKER if r=='checker' else METHOD.format(method=r))+'\nRun the visible local acceptance command once, then finish with a brief message. Do not look for invariant_validate.\n' for r in ('trapezoid','simpson','checker')}
 profile={'codex_path':'/opt/bench-tools/codex','model':MODEL,'effort':EFFORT,'config':config,'prompts':prompts,'path':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'}
 write(project/'profile.json',profile);write(project/'case.json',{'mode':mode});shutil.copyfile(HERE/'agent_driver.py',project/'agent_driver.py')
 (project/'TASK.md').write_text(common)
 if mode=='replay':
  for role in ('trapezoid','simpson','checker'):
   source=TASK/'results/v4/first/with-invariant/agents'/role
   target=project/'reference'/role;target.mkdir(parents=True,exist_ok=True)
   for name in (('build_report.py',) if role=='checker' else ('solution.py',)):shutil.copyfile(source/name,target/name)
 for phase,roles in [('methods',('trapezoid','simpson')),('checker',('checker',))]:
  protected=['fixed_checks.py','evaluate.py']
  if phase=='checker':protected += [f'inputs/{r}/{n}' for r in ('trapezoid','simpson') for n in ('solution.py','result.json')]
  include=['TASK.md','fixed_checks.py','agent_driver.py','profile.json']+(['solution.py','result.json'] if phase=='methods' else ['inputs/**'])+(['reference/**'] if mode=='replay' else [])
  recipe={'schema_version':1,'name':'matched-'+phase,'runner':{'type':'command','argv':['python3','-B','agent_driver.py']},
   'variants':[{'id':r,'parameters':{}} for r in roles],'cases':[{'id':'quadrature','input_file':'case.json'}],
   'workspace':{'include':include,'exclude':[]},'execution':{'max_parallel':len(roles)},
   'evaluator':{'argv':['python3','evaluate.py'],'source_paths':protected,'timeout_seconds':0},
   'artifacts':list(FILES),'secret_refs':{'login':'BENCH_AUTH'} if mode=='live' else {}}
  write(project/(phase+'.json'),recipe)
 return profile

class Direct:
 def __init__(self,label,project,output,mode):
  self.state='research-v5-'+label+'-state';self.output=output;self.mode=mode;self.names=[];self.image=COMPUTE
  assert docker('volume','inspect',self.state,check=False).returncode!=0,'Do not reuse a measured direct namespace'
  docker('volume','create','--label','io.invariant.research=integration-controlled',self.state)
  self.path=read_bytes_json(docker('volume','inspect',self.state).stdout)[0]['Mountpoint']
  self.helper('''import shutil
from pathlib import Path
for role in ('trapezoid','simpson','checker'):
 d=Path('/state/input')/role;d.mkdir(parents=True)
 (d/'variant.json').write_text(__import__('json').dumps({'id':role,'parameters':{}}))
 (d/'case.json').write_text(CASE)
 if LIVE:
  shutil.copyfile('/seed/accounts/current/auth.json',d/'login.json');(d/'login.json').chmod(0o600)
 (d/'secrets.json').write_text('{"login":"/run-input/login.json"}' if LIVE else '{}')
 (Path('/state/work')/role).mkdir(parents=True)
'''.replace('CASE',repr((project/'case.json').read_text())).replace('LIVE',repr(mode=='live')),auth=True)
  self.upload(project,('trapezoid','simpson','checker'))
  from invariant.policy import NEW_PROJECT_CONFIG
  self.helper("from pathlib import Path; import json\nfor role in ('trapezoid','simpson','checker'):\n p=Path('/state/work')/role/'.invariant';p.mkdir(mode=0o700);(p/'config.json').write_text(json.dumps(POLICY))".replace('POLICY',repr(NEW_PROJECT_CONFIG)))
 def helper(self,script,*,input=None,auth=False):
  args=['run','--rm','--interactive','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges',
    '--mount',f'type=volume,src={self.state},dst=/state']
  if auth:args+=['--mount',f'type=volume,src={STATE_VOLUME},dst=/seed,readonly']
  return docker(*args,'--entrypoint','python3',IMAGE,'-B','-c',script,input=input)
 def upload(self,source,roles):
  for role in roles:
   names={'TASK.md','fixed_checks.py','agent_driver.py','profile.json'}
   if role!='checker':names|={'solution.py','result.json'}
   files={p.relative_to(source).as_posix():p.read_bytes() for p in source.rglob('*')
     if p.is_file() and (p.relative_to(source).as_posix() in names or self.mode=='replay' and p.relative_to(source).parts[0]=='reference')}
   script="""import sys,io,tarfile
from pathlib import Path,PurePosixPath
with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read())) as archive:
 for entry in archive:
  path=PurePosixPath(entry.name)
  assert not path.is_absolute() and '..' not in path.parts and entry.isfile()
  target=Path('/state/work')/ROLE/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.extractfile(entry).read())
""".replace('ROLE',repr(role))
   self.helper(script,input=archive_files(files))
 def run(self,role):
  name='research-v5-'+self.state.replace('-state','')+'-'+role;self.names.append(name);started=time.time()
  args=['create','--name',name,'--init','--interactive','--network=bridge','--cap-drop=ALL','--security-opt=no-new-privileges',
   '--workdir=/workspace','--mount',f'type=bind,src={self.path}/work/{role},dst=/workspace',
   '--mount',f'type=bind,src={self.path}/input/{role},dst=/run-input,readonly','--mount',f'type=bind,src={self.path}/work/{role}/.invariant,dst=/workspace/.invariant,readonly','--volume=/scratch',
   '--label','io.invariant.research=integration-controlled',
   '--env','INVARIANT_VARIANT_FILE=/run-input/variant.json','--env','INVARIANT_CASE_FILE=/run-input/case.json','--env','INVARIANT_SECRET_FILES=/run-input/secrets.json',
   '--env','PYTHONDONTWRITEBYTECODE=1','--pids-limit=-1','--security-opt=seccomp='+str(REPO/'invariant/assets/seccomp-environment-internet.json'),*[f'--cap-add={cap}' for cap in CAPS],'--entrypoint=python3',self.image,'-B','agent_driver.py']
  docker(*args);info=read_bytes_json(docker('inspect',name).stdout)[0]
  assert info['Image']==COMPUTE and not info['HostConfig']['Privileged'] and not info['HostConfig']['Devices']
  log=docker('start','--attach','--interactive',name,timeout=1200).stdout
  finished=time.time();(self.output/(role+'.log')).write_bytes(log)
  files=self.helper('''import json
from pathlib import Path
print(json.dumps(json.loads((Path('/state/work')/ROLE/'workload.json').read_text())))'''.replace('ROLE',repr(role))).stdout
  workload=read_bytes_json(files);return {'role':role,'started_at':started,'finished_at':finished,'seconds':finished-started,'workload':workload}
 def phase(self,phase):
  started=time.time();roles=('trapezoid','simpson') if phase=='methods' else ('checker',)
  if phase=='checker':self.helper('''from pathlib import Path
import shutil
for role in ('trapezoid','simpson'):
 target=Path('/state/work/checker/inputs')/role;target.mkdir(parents=True,exist_ok=True)
 for name in ('solution.py','result.json'):shutil.copyfile(Path('/state/work')/role/name,target/name)
shutil.copytree('/state/work/checker/inputs','/state/reference/inputs')
''')
  with ThreadPoolExecutor(max_workers=len(roles)) as pool:records=list(pool.map(self.run,roles))
  return {'records':records,'started_at':started,'finished_at':time.time(),'seconds':time.time()-started}
 def collect(self):
  script='''import io,json,sys,tarfile
from pathlib import Path
sys.path.insert(0,'/state/work/checker')
from fixed_checks import validate
reports={}
buf=io.BytesIO()
with tarfile.open(fileobj=buf,mode='w') as archive:
 for role in ('trapezoid','simpson','checker'):
  root=Path('/state/work')/role
  reports[role]=validate(role,root,Path('/state/reference') if role=='checker' else None)
  for name in FILES:
   path=root/name
   if path.is_file():archive.add(path,arcname=role+'/'+name,recursive=False)
 raw=json.dumps(reports).encode();entry=tarfile.TarInfo('acceptance.json');entry.size=len(raw);archive.addfile(entry,io.BytesIO(raw))
sys.stdout.buffer.write(buf.getvalue())'''.replace('FILES',repr(FILES))
  import io,tarfile
  raw=self.helper(script).stdout
  from baseline_v3 import unpack_files
  with tarfile.open(fileobj=io.BytesIO(raw)) as archive:unpack_files(archive,self.output/'agents')
  reports=read(self.output/'agents/acceptance.json');assert all(v['passed'] for v in reports.values()),reports
  return reports
 def close(self):
  for name in self.names:docker('rm','--force','--volumes',name,check=False)

def read_bytes_json(raw):return json.loads(raw)
def engine_call(manager,payload):
 script=(HERE/'engine_phase.py').read_text()
 result=docker('exec','--interactive',manager.name,'python3','-B','-c',script,input=json.dumps(payload).encode(),timeout=1200)
 return json.loads(result.stdout)

def native_collect(context,output,phases):
 collected=context.collect()
 for phase in phases.values():
  for record in phase['records']:
   target=output/'agents'/record['role'];target.mkdir(parents=True,exist_ok=True)
   export=next(Path(p)/'files' for p in collected['exported_results'] if Path(p).name.startswith(record['attempt_id']))
   for name in FILES:
    if (export/name).is_file():shutil.copyfile(export/name,target/name)
 write(output/'collection.json',collected)
 return collected

def main():
 global COMPUTE
 parser=argparse.ArgumentParser();parser.add_argument('label');parser.add_argument('--mode',choices=('live','replay'),required=True);parser.add_argument('--reverse',action='store_true');args=parser.parse_args()
 output=BASE/args.label;assert not (output/'pair.json').exists(),'Do not replay a completed pair';output.mkdir(parents=True,exist_ok=True)
 nonce=uuid.uuid4().hex;project=TASK/'projects/v5-controlled';profile=seed(project,args.mode,nonce)
 context=RunContext(project,key='v5-'+args.label)
 from invariant.dependencies import find_docker,docker_env
 executable=find_docker()
 guard=subprocess.Popen([executable,'exec','--interactive',context.manager.name,'python3','-B','-c','from invariant.storage_gc import gate; import sys; lease=gate(); lease.__enter__(); print("READY",flush=True); sys.stdin.buffer.read(); lease.__exit__(None,None,None)'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=docker_env(executable))
 assert guard.stdout.readline().strip()==b'READY'
 write(output/'context.json',{'project':str(project),'run_ref':context.run_ref})
 for arm in ('with-invariant','without-invariant'):(output/arm).mkdir(exist_ok=True)
 prepared=engine_call(context.manager,{'action':'prepare_tools','project':str(project)})
 COMPUTE=prepared['image'];write(output/'preparation.json',prepared)
 direct=Direct(args.label,project,output/'without-invariant',args.mode)
 payload={'action':'phase','project':str(project),'run_ref':context.run_ref};phases={'with-invariant':{},'without-invariant':{}};refs=[]
 try:
  order=('without-invariant','with-invariant') if args.reverse else ('with-invariant','without-invariant')
  for phase in ('methods','checker'):
   for arm in order:
    note('dispatch',label=args.label,mode=args.mode,phase=phase,arm=arm)
    if arm=='with-invariant':
     data=engine_call(context.manager,{**payload,'recipe':phase+'.json','request_id':'v5-'+args.label+'-'+phase,'result_inputs':refs})
     if phase=='methods':refs=[{'attempt_id':r['attempt_id'],'result_digest':r['capture']['content_digest'],'files':{n:'inputs/'+r['role']+'/'+n for n in ('solution.py','result.json')}} for r in data['records']]
    else:data=direct.phase(phase)
    phases[arm][phase]=data;write(output/arm/(phase+'.json'),data)
   if args.mode=='live':
    for role in ('trapezoid','simpson') if phase=='methods' else ('checker',):
     a=next(r['workload'] for r in phases['with-invariant'][phase]['records'] if r['role']==role)
     b=next(r['workload'] for r in phases['without-invariant'][phase]['records'] if r['role']==role)
     assert all(a[k]==b[k] for k in ('prompt_sha256','profile_sha256','driver_sha256','argv_sha256','environment_sha256','cli_version','first_input_tokens')),(role,a.get('first_input_tokens'),b.get('first_input_tokens'))
     assert a['sessions'][0]['base_instructions']==b['sessions'][0]['base_instructions']
  started=time.time();native_collect(context,output/'with-invariant',phases['with-invariant']);native_export=time.time()-started
  started=time.time();acceptance=direct.collect();direct_export=time.time()-started
  result={'label':args.label,'mode':args.mode,'order':list(order),'source_digest':source_digest(),'model':MODEL,'effort':EFFORT,'compute_image':COMPUTE,
   'profile':profile,'phases':phases,'export_seconds':{'with-invariant':native_export,'without-invariant':direct_export},'acceptance':acceptance,
   'context_equal':args.mode=='live','not_native_codex_adapter':True,'scope':'Generic command runner around byte-identical stock Codex CLI; deterministic replay has no model calls'}
  write(output/'pair.json',result);note('finished',label=args.label)
 finally:
  direct.close();context.close();guard.stdin.close();guard.wait(timeout=30);guard.stdout.close();guard.stderr.close()
if __name__=='__main__':main()
