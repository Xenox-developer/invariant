"""Reconcile additive timeline and accounting; scenarios are not measured savings."""
import datetime,json
from pathlib import Path
HERE=Path(__file__).resolve().parent;BASE=HERE/'results/v3';OUT=BASE/'diagnostics'
def read(p):return json.loads(p.read_text())
def t(value):return datetime.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
def cost(u):return ((u['input_tokens']-u['cached_input_tokens'])*50+u['cached_input_tokens']*2.5+u['output_tokens']*250)/1e6
def main():
 sessions=read(OUT/'sessions.json');measurements=read(BASE/'measurements.json');result={'model_calls_for_diagnosis':0,'rounds':{}}
 for label in ('cold','warm'):
  result['rounds'][label]={}
  for arm,roles in sessions[label].items():
   raw=read(BASE/label/arm/'summary.json');by_role={}
   for role,r in roles.items():
    starts=[t(x['timestamp']) for x in r['timeline'] if x['type']=='task_started'];ends=[t(x['timestamp']) for x in r['timeline'] if x['type']=='task_complete'];assert len(starts)==len(ends)==1
    counts=r['responses'];usage={k:sum(x['last'][k] for x in counts) for k in ('input_tokens','cached_input_tokens','output_tokens','total_tokens')}
    # Independently reconcile the complete saved sessions with published totals.
    expected=next(x for x in measurements['rounds'][label][arm]['agents'] if x['role']==role)['usage']
    assert all(expected[k]==v for k,v in usage.items())
    validation=[a for a in r['actions'] if 'invariant_validate' in a.get('input',a.get('arguments',''))]
    by_role[role]={'task_start':starts[0],'task_finish':ends[0],'task_seconds':ends[0]-starts[0],
     'responses':len(counts),'input_usage':usage,'credit_equivalent':cost(usage),
     'first_input_tokens':counts[0]['last']['input_tokens'],'validation_tool_seconds':sum(a['tool_seconds'] for a in validation),
     'final_response':counts[-1]['last'],'base_instructions':r['base_instructions']}
   start=min(by_role[k]['task_start'] for k in ('trapezoid','simpson'));end=max(by_role[k]['task_finish'] for k in ('trapezoid','simpson'))
   c=by_role['checker'];windows={'before_method_turns':start-raw['started_at'],'method_turns':end-start,
    'between_turns':c['task_start']-end,'checker_turn':c['task_seconds'],'after_checker_turn':raw['finished_at']-c['task_finish']}
   assert abs(sum(windows.values())-raw['wall_seconds'])<.001
   result['rounds'][label][arm]={'windows':windows,'agents':by_role,'wall_seconds':raw['wall_seconds']}
 warm=result['rounds']['warm'];native=warm['with-invariant'];direct=warm['without-invariant']
 result['warm_additive_gap']={k:native['windows'][k]-direct['windows'][k] for k in native['windows']}
 na=measurements['rounds']['warm']['with-invariant']['usage'];da=measurements['rounds']['warm']['without-invariant']['usage']
 result['warm_cost_gap']={'uncached_input_tokens':(na['input_tokens']-na['cached_input_tokens'])-(da['input_tokens']-da['cached_input_tokens']),
  'cached_input_tokens':na['cached_input_tokens']-da['cached_input_tokens'],'output_tokens':na['output_tokens']-da['output_tokens']}
 result['warm_cost_gap_credits']={k:v*rate/1e6 for (k,v),rate in zip(result['warm_cost_gap'].items(),(50,2.5,250))}
 finals=[v['final_response'] for v in native['agents'].values()]
 result['scenarios_not_predictions']={'without_three_final_responses':{'tokens_avoided':sum(x['total_tokens'] for x in finals),'credits_avoided':sum(cost(x) for x in finals)},
  'two_missed_prefixes_hit_11520_tokens_each':{'additional_cached_tokens':23040,'credits_avoided':23040*47.5/1e6}}
 (OUT/'breakdown.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='rounds'},indent=2))
if __name__=='__main__':main()
