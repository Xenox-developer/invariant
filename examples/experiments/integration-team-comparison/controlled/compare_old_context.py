"""Summarize model-visible initial messages without exporting private settings."""
import hashlib,json,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;TASK=HERE.parent;sys.path.insert(0,str(TASK.parents[2]))
from invariant.docker_storage import docker,IMAGE
BASE=TASK/'results/v5';BASE.mkdir(exist_ok=True)
SCRIPT=r'''import hashlib,json,sys
from pathlib import Path
result=[]
for path in Path(sys.argv[1]).rglob('*.jsonl'):
 messages=[];base=None;context_keys=None
 for line in path.read_text().splitlines():
  row=json.loads(line);p=row.get('payload',{})
  if row.get('type')=='session_meta':
   b=p.get('base_instructions',{});text=b.get('text','') if isinstance(b,dict) else str(b);base={'chars':len(text),'sha256':hashlib.sha256(text.encode()).hexdigest()}
  if row.get('type')=='turn_context':context_keys=list(p)
  if row.get('type')=='response_item' and p.get('type')=='message' and p.get('role') in ('developer','user'):
   text='\n'.join(item.get('text','') for item in p.get('content',[]) if isinstance(item,dict))
   messages.append({'role':p['role'],'chars':len(text),'sha256':hashlib.sha256(text.encode()).hexdigest(),'prefix':text[:180]})
  if row.get('type')=='event_msg' and p.get('type')=='token_count':break
 result.append({'base':base,'messages':messages,'turn_context_keys':context_keys})
print(json.dumps(result))'''
def main():
 native=json.loads((TASK/'results/v4/warm1/with-invariant/summary.json').read_text());direct=json.loads((TASK/'results/v4/warm1/without-invariant/summary.json').read_text())
 project=native['project'];key=hashlib.sha256(project.encode()).hexdigest()[:24]
 attempt=next(a for a in native['agents'] if a['role']=='trapezoid')['attempt_id']
 root='/Users/nikchin/.invariant/workspaces/'+key+'/experiments/attempts/'+attempt+'/native-home/sessions'
 a=json.loads(docker('exec','invariant-manager-'+key,'python3','-B','-c',SCRIPT,root).stdout)
 b=json.loads(docker('run','--rm','--network=none','--read-only','--mount',f"type=volume,src={direct['state_volume']},dst=/state,readonly",'--entrypoint','python3',IMAGE,'-B','-c',SCRIPT,'/state/homes/trapezoid/codex/sessions').stdout)
 result={'native':a,'direct':b};(BASE/'old-context-comparison.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
