"""Compare actual initial session messages, without exporting private history."""
import json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;TASK=HERE.parent;sys.path.insert(0,str(TASK));sys.path.insert(0,str(TASK.parents[2]))
from baseline_v3 import docker
from invariant.docker_storage import Manager,IMAGE
BASE=TASK/'results/v5'
SCRIPT='''import json,hashlib,sys
from pathlib import Path
root=Path(sys.argv[1]);sessions=list(root.rglob('*.jsonl'));assert len(sessions)==1
messages=[];base=None
for line in sessions[0].read_text().splitlines():
 r=json.loads(line);p=r.get('payload',{})
 if r.get('type')=='session_meta':base=p.get('base_instructions')
 if r.get('type')=='response_item':
  if p.get('type')!='message' or p.get('role') not in ('developer','user'):break
  content=p.get('content',[])
  messages.append({'role':p['role'],'content_sha256':hashlib.sha256(json.dumps(content,sort_keys=True).encode()).hexdigest(),
   'chars':sum(len(x.get('text','')) for x in content)})
assert messages
print(json.dumps({'initial_messages':messages,'base_instructions_sha256':hashlib.sha256(json.dumps(base,sort_keys=True).encode()).hexdigest()}))
'''
def main():
 result={}
 for label in ('live-c','live-d'):
  pair=json.loads((BASE/label/'pair.json').read_text());saved=json.loads((BASE/label/'context.json').read_text());manager=Manager(saved['project']);result[label]={}
  for phase in ('methods','checker'):
   for record in pair['phases']['with-invariant'][phase]['records']:
    role=record['role'];path=record['capture']['workspace']+'/.codex/sessions'
    native=json.loads(docker('exec',manager.name,'python3','-B','-c',SCRIPT,path).stdout)
    direct=json.loads(docker('run','--rm','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
     '--mount',f'type=volume,src=research-v5-{label}-state,dst=/state,readonly','--entrypoint','python3',IMAGE,'-B','-c',SCRIPT,f'/state/work/{role}/.codex/sessions').stdout)
    assert native==direct,(label,role,native,direct)
    result[label][role]={'equal':True,**native}
  manager.close()
 (BASE/'initial-context-audit.json').write_text(json.dumps(result,indent=2)+'\n')
 # Retain the spent usage of the failed CLI bundle calibration separately.
 saved=json.loads((BASE/'live-b/context.json').read_text());manager=Manager(saved['project'])
 script='''import json,sys
from pathlib import Path
rows=[]
for path in Path(sys.argv[1]).glob('*/snapshot/workspace/workload.json'):
 w=json.loads(path.read_text())
 if w.get('usage'):rows.append({k:w[k] for k in ('role','usage','provider_responses')})
print(json.dumps(rows))'''
 root=str(Path(manager.home)/'.invariant/workspaces'/manager.key/'experiments/attempts')
 native=json.loads(docker('exec',manager.name,'python3','-B','-c',script,root).stdout)
 direct=[{k:r['workload'][k] for k in ('role','usage','provider_responses')} for r in json.loads((BASE/'live-b/without-invariant/methods.json').read_text())['records']]
 assert len(native)==len(direct)==2
 totals={key:sum(r['usage'][key] for r in native+direct) for key in ('input_tokens','cached_input_tokens','output_tokens','total_tokens')}
 i,c,o=(totals[k] for k in ('input_tokens','cached_input_tokens','output_tokens'))
 calibration={'label':'live-b','excluded_from_paired_measurements':True,'reason':'Missing Code Mode host in the initial shared CLI bundle; task not implemented',
  'records':{'with-invariant':native,'without-invariant':direct},'usage':totals,
  'credits_observed_cache_frozen_weights':((i-c)*50+c*2.5+o*250)/1_000_000,'actual_bill':None}
 (BASE/'calibration-usage.json').write_text(json.dumps(calibration,indent=2)+'\n');manager.close()
 print('Actual initial message content matches for all six paired live roles')
if __name__=='__main__':main()
