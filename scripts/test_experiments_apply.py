#!/usr/bin/env python3
"""Real Docker M5 gate: persistent continuation, explicit apply, conflicts and crash rollback."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from invariant import workspace
from invariant.experiments.engine import ExperimentEngine

HARNESS='''import os,pathlib
w=pathlib.Path('/workspace');prompt=os.environ.get('INVARIANT_PROMPT_FILE')
if prompt:
 assert 'phase:child' in pathlib.Path(prompt).read_text()
 assert pathlib.Path('/usr/local/bin/m5-proof').read_text().endswith('persisted\\n')
 assert (w/'value.txt').read_text()=='parent\\n'
 assert (w/'fixture.txt').read_text()=='parent-modified fixture'
 assert (w/'.env').read_text()=='private generated config'
 (w/'value.txt').write_text('child\\n');(w/'child-only.txt').write_text('new task')
else:
 assert (w/'value.txt').read_text()=='patched\\n'
 assert (w/'fixture.txt').read_text()=='initial fixture'
 pathlib.Path('/usr/local/bin/m5-proof').write_text('#!/bin/sh\\nprintf persisted\\n');pathlib.Path('/usr/local/bin/m5-proof').chmod(0o755)
 (w/'value.txt').write_text('parent\\n');(w/'delete.txt').unlink()
 (w/'fixture.txt').write_text('parent-modified fixture');(w/'result-link').symlink_to('value.txt')
 (w/'.env').write_text('private generated config')
print('continued' if prompt else 'parent complete',flush=True)
'''
JUDGE='''import json,os,pathlib
prompt=os.environ.get('INVARIANT_PROMPT_FILE')
child=bool(prompt and 'phase:child' in pathlib.Path(prompt).read_text())
expected='child\\n' if child else 'parent\\n'
passed=(pathlib.Path('/workspace/value.txt').read_text()==expected and pathlib.Path('/usr/local/bin/m5-proof').exists())
print(json.dumps({'schema_version':1,'passed':passed,'metrics':{'continued':int(child),'installed_tool':int(pathlib.Path('/usr/local/bin/m5-proof').exists())},'artifacts':[]}))
'''
FAULT='''import os,sys
sys.path.insert(0,sys.argv[1])
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments import tree
original=tree.set_entry
count=0
def stop(*args,**kwargs):
 global count
 original(*args,**kwargs);count+=1
 if count==2:os._exit(92)
tree.set_entry=stop
ExperimentEngine(sys.argv[2]).apply(sys.argv[3],request_id='fault')
'''


def sources():
 paths=[*ROOT.glob('invariant/**/*.py'),*ROOT.glob('scripts/tests/test_experiment*.py'),Path(__file__),ROOT/'MVP_CONTRACT.md',ROOT/'docs/IMPLEMENTATION_PLAN.md',ROOT/'docs/experiments/ASSISTANT_GUIDE.md']
 return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',metavar='MODEL',help='also run a real native parent and child (two host ChatGPT model turns)');parser.add_argument('--effort',default='medium');args=parser.parse_args()
 os.umask(0o077)
 output=ROOT/'.invariant/experiment-apply-checks'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8])
 project=output/'project';project.mkdir(parents=True);engine=None;studies=[]
 report={'schema_version':1,'passed':False,'scope':'M5 continuation/apply','checks':[],'cleanup_errors':[],'model_turns':0,'source_sha256':sources()}
 def require(ok,message):
  if not ok:raise AssertionError(message)
 def passed(message):report['checks'].append(message);print('PASS '+message,flush=True)
 def cli(*args,expected=0):
  reply=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/invariant'),*args],cwd=project,capture_output=True,text=True,timeout=600)
  require(reply.returncode==expected,reply.stdout+reply.stderr);return reply.stdout
 def machine(*args,expected=0):
  reply=json.loads(cli('experiment',*args,'--json',expected=expected));require(reply['ok']==(expected==0),str(reply));return reply['data'] if expected==0 else reply['error']
 def idle():
  from invariant.execution import project_lock
  end=time.monotonic()+30
  while time.monotonic()<end:
   try:lock=project_lock(engine.root/'controller')
   except RuntimeError:time.sleep(.1)
   else:lock.close();return
  raise AssertionError('Controller lease did not clear')
 def hashes():
  return {str(p.relative_to(project)):('link',os.readlink(p)) if p.is_symlink() else ('file',hashlib.sha256(p.read_bytes()).hexdigest(),p.stat().st_mode&0o777) for p in project.rglob('*') if p.is_symlink() or p.is_file()}
 def choose(study):
  trial=study['trials'][0];require(trial['state']=='succeeded',str(trial));machine('select',trial['id'],'--if-revision','0');return trial['attempts'][0]
 try:
  cli('init');engine=ExperimentEngine(project)
  subprocess.run(['git','init','--quiet',str(project)],check=True,capture_output=True)
  (project/'value.txt').write_text('base\n');(project/'delete.txt').write_text('old\n');(project/'unrelated.txt').write_text('keep')
  (project/'agent.py').write_text(HARNESS);(project/'judge.py').write_text(JUDGE);(project/'fixture-source.txt').write_text('initial fixture')
  (project/'variant.patch').write_text('--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-base\n+patched\n')
  recipe={'schema_version':1,'name':'continuation-parent','runner':{'type':'command','argv':['python3','agent.py']},
   'variants':[{'id':'parent','parameters':{},'patch_file':'variant.patch'}],
   'cases':[{'id':'task','fixtures':[{'source':'fixture-source.txt','destination':'fixture.txt'}]}],
   'execution':{'max_parallel':1,'trial_cpus':.5,'trial_memory_mib':512,'trial_timeout_seconds':120},
   'evaluator':{'argv':['python3','judge.py'],'source_paths':['judge.py']},'artifacts':['value.txt']}
  (project/'parent.json').write_text(json.dumps(recipe));before=hashes()
  parent=machine('run','parent.json','--wait','--request-id','parent');studies.append(parent['id']);idle();parent_attempt=choose(parent)
  require(hashes()==before,'Trial changed original source/Git')
  (project/'next.txt').write_text('phase:child\nContinue using the saved tools and files.');before=hashes()
  child=machine('continue',parent['id'],'--prompt-file','next.txt','--create-only','--request-id','child');studies.append(child['id'])
  require(hashes()==before,'Continuation changed original project')
  cli('experiment','execute',child['id'],'--wait');idle();child=machine('status',child['id']);child_attempt=choose(child)
  results=machine('results',child['id']);report['command_child']=results
  require(results['summary']['quality_passes']==1,'Continuation lost tools/files, reset fixtures, or reapplied variant patch')
  require(child['frozen']['image']['id']==parent_attempt['record']['checkpoint']['image'],'Child substituted an environment')
  parent_before=machine('results',parent['id']);diff=machine('diff',child['id'])
  require({r['path'] for r in diff['changes']}=={'value.txt','child-only.txt'},'Child diff did not use its own parent baseline')
  passed('child inherits exact saved tools/files, keeps private generated config, and does not replay patches/fixtures')
  require(child['frozen']['parent']['attempt_id']==parent_attempt['id'] and child['frozen']['parent']['new_conversation'],'Missing immutable parent/fresh-conversation declaration')
  plan=machine('apply',parent['id'],'--dry-run','--request-id','review');require(not plan['conflicts'],'Unexpected conflicts')
  require(any(r['variant_patch'] and r['path']=='value.txt' for r in plan['changes']),'Variant patch provenance missing')
  require(hashes()==before,'Diff/dry-run wrote project files')
  fault=subprocess.run([sys.executable,'-I','-c',FAULT,str(ROOT),str(project),plan['plan_id']],capture_output=True,text=True,timeout=30)
  require(fault.returncode==92,fault.stdout+fault.stderr)
  recovered=machine('apply','--recover','--request-id','recover-apply');require(recovered['recovered'] and hashes()==before,'Crash did not roll back exactly')
  require(machine('apply','--plan',plan['plan_id'],expected=1)['code']=='PLAN_RECOVERED','Recovered apply replayed')
  passed('crash after filesystem effects restores prior contents/modes before another project mutation')
  plan=machine('apply',parent['id'],'--dry-run');(project/'value.txt').write_text('external edit');edited=hashes()
  require(machine('apply','--plan',plan['plan_id'],expected=1)['code']=='STALE_PLAN','Stale plan accepted')
  require(hashes()==edited,'Stale plan partially wrote project files');(project/'value.txt').write_text('base\n')
  passed('stale destination edits fail whole-plan validation without partial writes')
  plan=machine('apply',parent['id'],'--dry-run');(project/'unrelated.txt').write_text('unrelated edit retained')
  cli('experiment','apply','--plan',plan['plan_id'],'--request-id','apply-parent')
  require((project/'value.txt').read_text()=='parent\n' and not (project/'delete.txt').exists(),'Parent files were not applied')
  require((project/'unrelated.txt').read_text()=='unrelated edit retained','Unrelated edit overwritten')
  require(not (project/'.env').exists() and not (engine.state/'environment.json').exists(),'Apply changed host credentials/environment')
  child_plan=machine('apply',child['id'],'--dry-run');require(not child_plan['conflicts'],'Child should apply after parent')
  machine('apply','--plan',child_plan['plan_id'],'--request-id','apply-child')
  require((project/'value.txt').read_text()=='child\n' and (project/'child-only.txt').exists(),'Child changes missing')
  require(machine('results',parent['id'])==parent_before,'Applying/continuing mutated parent evidence')
  passed('manual/JSON plans apply only reviewed files, preserve unrelated edits and never install a checkpoint on the host')
  machine('select','--clear',parent['id'],'--if-revision','1')
  require(machine('delete',parent['id'],'--confirm',expected=1)['code']=='REFERENCED_STATE','Child did not retain its parent')
  passed('immutable child references prevent deletion of a retained parent')
  if args.live:
   # Separate native parent/child: exactly two real model turns, fresh history each.
   (project/'native-value.txt').write_text('base')
   (project/'native-instructions.txt').write_text('Use the command tool to execute the exact Python snippet in the task, then finish. No network, no other agents.')
   first="import pathlib;p=pathlib.Path('/usr/local/bin/m5-native-proof');p.write_text('installed');pathlib.Path('/workspace/native-value.txt').write_text('parent');print('parent ready')"
   second="import pathlib;assert pathlib.Path('/usr/local/bin/m5-native-proof').read_text()=='installed';assert pathlib.Path('/workspace/native-value.txt').read_text()=='parent';pathlib.Path('/workspace/native-value.txt').write_text('child');print('child ready')"
   (project/'native-prompt.txt').write_text('Run python3 -c '+repr(first))
   (project/'native-next.txt').write_text('Run python3 -c '+repr(second))
   native_recipe={'schema_version':1,'name':'native-continuation','runner':{'type':'codex'},'variants':[{'id':'native','model':args.live,'effort':args.effort,'instructions_file':'native-instructions.txt'}],
     'cases':[{'id':'task','prompt_file':'native-prompt.txt'}], 'execution':{'max_parallel':1,'trial_cpus':.5,'trial_memory_mib':512,'trial_timeout_seconds':240},'artifacts':['native-value.txt']}
   (project/'native.json').write_text(json.dumps(native_recipe))
   original=hashes();native=machine('run','native.json','--wait','--request-id','native-parent');studies.append(native['id']);idle();a=choose(native)
   continued=machine('continue',native['id'],'--prompt-file','native-next.txt','--wait','--request-id','native-child');studies.append(continued['id']);idle();b=choose(continued)
   report['model_turns']=int(bool(a['record']['runner']['native']['turn_id']))+int(bool(b['record']['runner']['native']['turn_id']))
   require(a['record']['runner']['native']['thread_id']!=b['record']['runner']['native']['thread_id'],'Native conversation was reused')
   require(a['record']['runner']['native']['home']!=b['record']['runner']['native']['home'],'Native homes shared')
   saved=Path(b['record']['capture']['workspace']);require((saved/'native-value.txt').read_text()=='child','Native continuation did not retain installed tool/workspace')
   require(hashes()==original,'Native continuation changed original project')
   report['native_parent']=machine('results',native['id']);report['native_child']=machine('results',continued['id'])
   passed('two real native turns continue installed tools and files through distinct conversations and homes')
  report['passed']=True
 except BaseException as error:report['error']=str(error);report['error_type']=type(error).__name__;print('FAIL '+str(error),flush=True)
 finally:
  if engine:
   accepted=set()
   for identifier in studies:
    try:
     for trial in engine.inspect(identifier)['trials']:
      for attempt in trial['attempts']:
       native=attempt['record'].get('runner',{}).get('native',{})
       if native.get('turn_id'):accepted.add((native.get('thread_id'),native['turn_id']))
    except Exception:pass
   report['model_turns']=len(accepted)
   for identifier in reversed(studies):
    try:
     study=engine.inspect(identifier)
     if study['state']!='completed':engine.stop(identifier);idle();engine.recover(identifier)
     selected=engine.inspect(identifier)['selection']
     if selected['attempt_id']:engine.select(identifier,revision=selected['revision'],clear=True)
     engine.delete(identifier,confirm=True)
    except BaseException as error:report['cleanup_errors'].append(str(error))
  report['source_matches']=report['source_sha256']==sources();report['passed'] &= report['source_matches'] and not report['cleanup_errors']
  workspace.private_write(output/'report.json',report);print('Evidence: '+str(output/'report.json'),flush=True)
 return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
