"""Read-only forensic analysis of retained Docker sessions; no model calls."""
import collections,datetime,hashlib,json,re,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parents[2]))
from invariant.docker_storage import docker
BASE=HERE/'results/v3';OUT=BASE/'diagnostics'
SCRIPT=r'''
import json,sys,hashlib
from pathlib import Path
requests=json.load(sys.stdin);result={}
for role,root in requests.items():
 records=[]
 for path in Path(root).rglob('*.jsonl'):
  for line in path.read_text().splitlines():
   row=json.loads(line);payload=row.get('payload',{});kind=payload.get('type');typ=row.get('type')
   if typ=='response_item' and kind in ('function_call','custom_tool_call','function_call_output','custom_tool_call_output'):
    item={k:payload[k] for k in ('type','name','call_id','arguments','input') if k in payload}
    if 'output' in payload:
     output=payload['output'];item['output_chars']=len(str(output))
     # Numerical research tasks only: retain short summaries, not arbitrary session settings.
     item['output_preview']=str(output)[:500]
    records.append({'timestamp':row.get('timestamp'),**item})
   elif typ=='event_msg' and kind=='token_count':
    records.append({'timestamp':row.get('timestamp'),'type':'usage',**(payload.get('info') or {})})
   elif typ=='turn_context':
    records.append({'timestamp':row.get('timestamp'),'type':'turn_context'})
   elif typ=='event_msg' and kind in ('task_started','task_complete','agent_message'):
    records.append({'timestamp':row.get('timestamp'),'type':kind,'message':str(payload.get('message',''))[:250]})
   elif typ=='session_meta':
    value=payload.get('base_instructions',{})
    text=value.get('text','') if isinstance(value,dict) else str(value)
    records.append({'timestamp':row.get('timestamp'),'type':'base_instructions','chars':len(text),'sha256':hashlib.sha256(text.encode()).hexdigest()})
 result[role]=records
print(json.dumps(result))
'''
def epoch(value):return datetime.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
def read(path):return json.loads(path.read_text())
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def summarize(rows):
 calls={};actions=[];usage=[]
 for r in rows:
  if r['type']=='usage':usage.append({'timestamp':r['timestamp'],'last':r['last_token_usage'],'total':r['total_token_usage']})
  elif r['type'] in ('function_call','custom_tool_call'):
   calls[r['call_id']]=r;actions.append(r)
  elif r['type'] in ('function_call_output','custom_tool_call_output'):
   call=calls.get(r['call_id'])
   if call:
    call['finished_at']=r['timestamp'];call['tool_seconds']=epoch(r['timestamp'])-epoch(call['timestamp'])
    call['output_chars']=r['output_chars'];call['output_preview']=r['output_preview']
 return {'actions':actions,'responses':usage,'base_instructions':[r for r in rows if r['type']=='base_instructions'],'timeline':[r for r in rows if r['type'] in ('task_started','task_complete','agent_message','turn_context')]}
def main():
 OUT.mkdir(parents=True,exist_ok=True);result={}
 for label in ('cold','warm'):
  native=read(BASE/label/'with-invariant/summary.json');bare=read(BASE/label/'without-invariant/summary.json')
  context=read(BASE/label/'with-invariant/context.json');key=hashlib.sha256(context['project'].encode()).hexdigest()[:24]
  prefix='/Users/nikchin/.invariant/workspaces/'+key+'/experiments/attempts/'
  roots={a['role']:prefix+a['attempt_id']+'/native-home/sessions' for a in native['agents']}
  data=json.loads(docker('exec','--interactive','invariant-manager-'+key,'python3','-B','-c',SCRIPT,input=json.dumps(roots).encode()).stdout)
  plain_roots={a['role']:'/state/homes/'+a['role']+'/codex/sessions' for a in bare['agents']}
  direct=json.loads(docker('run','--rm','--interactive','--network=none','--read-only','--cap-drop=ALL',
   '--security-opt=no-new-privileges','--mount',f"type=volume,src={bare['state_volume']},dst=/state,readonly",'--entrypoint','python3',
   native['starting_image'],'-B','-c',SCRIPT,input=json.dumps(plain_roots).encode()).stdout)
  result[label]={'with-invariant':{k:summarize(v) for k,v in data.items()},'without-invariant':{k:summarize(v) for k,v in direct.items()}}
  # Private validation receipts contain precise snapshot and evaluator durations.
  script=r'''import json,sys
from pathlib import Path
result={}
for role,root in json.load(sys.stdin).items():
 result[role]=[]
 for p in Path(root).parent.parent.glob('validations/*/receipt.json'):
  v=json.loads(p.read_text());snapshot=v.get('snapshot',{});evaluation=v.get('evaluation',{})
  result[role].append({'created_at':v['created_at'],'finished_at':v.get('finished_at'),'state':v['state'],
   'snapshot_timings':snapshot.get('timings'),'evaluation':evaluation})
print(json.dumps(result))'''
  receipts=json.loads(docker('exec','--interactive','invariant-manager-'+key,'python3','-B','-c',script,input=json.dumps(roots).encode()).stdout)
  write(OUT/(label+'-validation-receipts.json'),receipts)
 write(OUT/'sessions.json',result)
 for label,arms in result.items():
  for arm,roles in arms.items():
   print(label,arm)
   for role,r in roles.items():
    print(role,'responses',len(r['responses']),'first',r['responses'][0]['last']['input_tokens'])
    for a in r['actions']:
     text=a.get('input',a.get('arguments',''));print(a['timestamp'],round(a.get('tool_seconds',0),3),a.get('name'),text[:200].replace('\n',' '))
if __name__=='__main__':main()
