"""Identical workload executable for Docker directly and INVARIANT command jobs."""
import hashlib,json,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path('/workspace')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def main():
 variant=json.loads(Path(os.environ.get('INVARIANT_VARIANT_FILE','/run-input/variant.json')).read_text())
 case=json.loads(Path(os.environ.get('INVARIANT_CASE_FILE','/run-input/case.json')).read_text())
 role=variant['id'];mode=case['mode'];profile=json.loads((ROOT/'profile.json').read_text());prompt=profile['prompts'][role]
 metadata={'role':role,'mode':mode,'prompt_sha256':sha(prompt.encode()),'profile_sha256':sha((ROOT/'profile.json').read_bytes()),
  'driver_sha256':sha(Path(__file__).read_bytes()),'model':profile['model'],'effort':profile['effort'],'started_at':time.time()}
 if mode=='replay':
  started=time.time()
  if role in ('trapezoid','simpson'):
   shutil.copyfile(ROOT/'reference'/role/'solution.py',ROOT/'solution.py')
   subprocess.run([sys.executable,'-B','solution.py'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
  else:
   shutil.copyfile(ROOT/'reference/checker/build_report.py',ROOT/'build_report.py')
   subprocess.run([sys.executable,'-B','build_report.py','--inputs','inputs','--output','.'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
  metadata.update(model_turns=0,provider_responses=0,usage=None,workload_started_at=started,workload_finished_at=time.time())
 else:
  home=ROOT/'.codex';home.mkdir(mode=0o700,exist_ok=True)
  secrets=json.loads(Path(os.environ.get('INVARIANT_SECRET_FILES','/run-input/secrets.json')).read_text())
  target=home/'auth.json'
  if not target.exists():shutil.copyfile(secrets['login'],target);target.chmod(0o600)
  flags=[]
  for key,value in sorted(profile['config'].items()):flags+=['-c',key+'='+json.dumps(value,separators=(',',':'))]
  argv=['/__invariant/codex','exec','--json','--ignore-user-config','--ignore-rules','--skip-git-repo-check',
    '--dangerously-bypass-approvals-and-sandbox','--model',profile['model'],*flags,'-C','/workspace','-']
  environment={'CODEX_HOME':str(home),'HOME':'/root','TMPDIR':'/scratch','PATH':profile['path'],
    'PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'}
  metadata.update(argv_sha256=sha(json.dumps(argv,separators=(',',':')).encode()),environment_sha256=sha(json.dumps(environment,sort_keys=True).encode()),
    cli_version=subprocess.check_output(['/__invariant/codex','--version'],env=environment,text=True).strip(),
    workload_started_at=time.time())
  events=[]
  with (home/'driver.events.jsonl').open('w') as log,(home/'driver.stderr.log').open('w') as err:
   process=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,cwd=ROOT,env=environment,text=True)
   process.stdin.write(prompt);process.stdin.close()
   for line in process.stdout:
    log.write(line);log.flush()
    try:events.append({'timestamp':time.time(),'event':json.loads(line)})
    except ValueError:pass
   process.stdout.close();exit_code=process.wait()
  metadata['workload_finished_at']=time.time();metadata['exit_code']=exit_code
  completed=[e for e in events if e['event'].get('type')=='turn.completed'];assert exit_code==0 and completed,'CLI did not complete'
  metadata['cli_usage']=completed[-1]['event']['usage'];metadata['turn_started_at']=next(e['timestamp'] for e in events if e['event'].get('type')=='turn.started')
  metadata['turn_finished_at']=completed[-1]['timestamp'];sessions=[]
  for path in (home/'sessions').rglob('*.jsonl'):
   rows=[];base=None;model=None;effort=None;user=[]
   for line in path.read_text().splitlines():
    r=json.loads(line);p=r.get('payload',{})
    if r.get('type')=='session_meta':
     b=p.get('base_instructions',{});text=b.get('text','') if isinstance(b,dict) else str(b);base={'sha256':sha(text.encode()),'chars':len(text)}
    if r.get('type')=='turn_context':model=p.get('model');effort=p.get('effort')
    if r.get('type')=='event_msg' and p.get('type')=='token_count' and isinstance(p.get('info'),dict):
     info=p['info'];rows.append({'timestamp':r['timestamp'],'total':info['total_token_usage'],'last':info['last_token_usage']})
   if rows:sessions.append({'model':model,'effort':effort,'base_instructions':base,'observations':rows})
  assert len(sessions)==1 and sessions[0]['model']==profile['model'] and sessions[0]['effort']==profile['effort'],sessions
  metadata['sessions']=sessions;observations=sessions[0]['observations'];metadata['model_turns']=1;metadata['provider_responses']=len(observations)
  fields=('input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens','total_tokens');total=observations[-1]['total']
  assert all(sum(x['last'][k] for x in observations)==total[k] for k in fields),'Usage not reconciled'
  assert all(metadata['cli_usage'][k]==total[k] for k in ('input_tokens','cached_input_tokens','output_tokens'))
  metadata.update(usage=total,first_input_tokens=observations[0]['last']['input_tokens'],first_cached_input_tokens=observations[0]['last']['cached_input_tokens'])
 metadata['finished_at']=time.time();(ROOT/'workload.json').write_text(json.dumps(metadata,indent=2)+'\n')
 print(json.dumps({'driver_completed':True,'role':role,'mode':mode}),flush=True)
if __name__=='__main__':main()
