"""Read-only analysis of the existing six sessions; never creates model turns."""
import json
from pathlib import Path
import sys
from plain_backend import docker

HERE=Path(__file__).resolve().parent
RUN=HERE/'results/pair-20261107-a'
SCRIPT=r'''
from collections import Counter
import hashlib,json,os
from pathlib import Path
heads=json.loads(os.environ['DIAG_HEADS'])
results=[]
def text_of(content):
    if isinstance(content,str):return content
    if isinstance(content,list):return '\n'.join(c.get('text','') for c in content if isinstance(c,dict))
    return ''
for head in heads:
    sessions=[]
    for path in (Path(head['mount'])/head['path']/'sessions').rglob('*.jsonl'):
        messages=[];calls=[];usages=[];meta={};contexts=[];types=Counter();items=Counter()
        for line in path.open():
            row=json.loads(line);typ=row.get('type');types[typ]+=1;p=row.get('payload',{})
            if typ=='session_meta':
                base=p.get('base_instructions',{})
                text=base.get('text','') if isinstance(base,dict) else str(base)
                meta={'keys':list(p),'base_instruction_chars':len(text),
                      'base_instruction_sha256':hashlib.sha256(text.encode()).hexdigest(),
                      'dynamic_tools_count':len(p.get('dynamic_tools') or []),
                      'dynamic_tools_chars':len(json.dumps(p.get('dynamic_tools'))),
                      'dynamic_tools_names':[t.get('name') for t in p.get('dynamic_tools',[])],
                      'source':p.get('source'),'cli_version':p.get('cli_version')}
            elif typ=='turn_context':
                contexts.append({'keys':list(p),'effort':p.get('effort'),'model':p.get('model'),
                    'developer_instructions_chars':len(p.get('developer_instructions') or ''),
                    'user_instructions_chars':len(p.get('user_instructions') or ''),
                    'tools':p.get('tools'),'approval_policy':p.get('approval_policy')})
            elif typ=='response_item':
                pt=p.get('type');items[pt]+=1
                if pt=='message':
                    text=text_of(p.get('content'))
                    messages.append({'timestamp':row.get('timestamp'),'role':p.get('role'),'chars':len(text),
                        'sha256':hashlib.sha256(text.encode()).hexdigest(),
                        'invariant_validation_mentions':text.count('invariant_validate')})
                elif pt in ('function_call','custom_tool_call'):
                    arguments=p.get('arguments',p.get('input',''))
                    calls.append({'timestamp':row.get('timestamp'),'id':p.get('call_id'),
                        'name':p.get('name'),'argument_chars':len(str(arguments)),
                        'argument_start':str(arguments)[:180]})
                elif pt in ('function_call_output','custom_tool_call_output'):
                    out=p.get('output','')
                    calls.append({'timestamp':row.get('timestamp'),'output_for':p.get('call_id'),
                        'output_chars':len(str(out)),'output_start':str(out)[:120]})
            elif typ=='event_msg' and p.get('type')=='token_count' and p.get('info'):
                total=p['info'].get('total_token_usage')
                if not usages or usages[-1]['total']!=total:
                    usages.append({'timestamp':row.get('timestamp'),'total':total,'last':p['info'].get('last_token_usage')})
        sessions.append({'meta':meta,'contexts':contexts,'row_types':dict(types),'item_types':dict(items),
                         'messages':messages,'calls':calls,'usages':usages})
    results.append({'arm':head['arm'],'role':head['role'],'sessions':sessions})
print(json.dumps(results))
'''


def main():
    heads=[]
    for stage in ('methods','checker'):
        e=json.loads((RUN/'with-invariant'/stage/'execution.json').read_text())
        for row in e['trials']:
            ref=row['runner']['native']['home_ref']
            heads.append({'arm':'invariant','role':row['variant_id'],'mount':'/native','path':ref['path']})
    plain=json.loads((RUN/'without-invariant/summary.json').read_text())
    for role in ('trapezoid','simpson','checker'):
        heads.append({'arm':'plain','role':role,'mount':'/plain','path':'homes/'+role+'/codex'})
    result=docker('run','--rm','--network=none','--read-only','--cap-drop=ALL',
        '--security-opt=no-new-privileges','--mount','type=volume,src=invariant-state-v2,dst=/native,readonly',
        '--mount','type=volume,src='+plain['state_volume']+',dst=/plain,readonly',
        '--env','DIAG_HEADS='+json.dumps(heads),'--entrypoint','python3','invariant-agent-runtime:0.163.0-v2',
        '-B','-c',SCRIPT)
    sessions=json.loads(result.stdout)
    target=RUN/'diagnostics';target.mkdir(exist_ok=True)
    (target/'sessions.json').write_text(json.dumps(sessions,indent=2)+'\n')
    for role in sessions:
        s=role['sessions'][0]
        print(role['arm'],role['role'],'base',s['meta'],'contexts',s['contexts'])
        print('items',s['item_types'],'messages',[(m['role'],m['chars']) for m in s['messages']])
        print('calls',[(c.get('name') or 'output',c.get('argument_chars',c.get('output_chars'))) for c in s['calls']])


if __name__=='__main__':main()
