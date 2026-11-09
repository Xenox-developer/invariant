"""Summarize paired measurements; never equate cache normalization to billing."""
import hashlib,json,statistics
from pathlib import Path
HERE=Path(__file__).resolve().parent
BASE=HERE.parent/'results/v5'
ARMS=('with-invariant','without-invariant')
LIVE=('live-c','live-d')
REPLAY=('replay-b','replay-c','replay-d')
def read(path):return json.loads(path.read_text())
def write(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
def cost(i,c,o):return ((i-c)*50+c*2.5+o*250)/1_000_000
def summarize(pair,arm):
 phases=pair['phases'][arm]
 records=[r for phase in phases.values() for r in phase['records']]
 workloads=[r['workload'] for r in records]
 active=[];work=[];preflight=[];evaluation=[]
 for phase in phases.values():
  # Native stops its timer before reading metadata. Give direct the same boundary.
  finished=phase['finished_at'] if arm==ARMS[0] else max(r['finished_at'] for r in phase['records'])
  active.append(finished-phase['started_at'])
  # Union on the critical path, rather than summing two parallel agents.
  starts=[r['workload']['workload_started_at'] for r in phase['records']]
  ends=[r['workload']['workload_finished_at'] for r in phase['records']]
  work.append(max(ends)-min(starts))
  if arm==ARMS[0]:
   preflight.append(phase['baseline_finished_at']-phase['submitted_at'])
   evaluation.append(max(r['evaluation']['finished_at'] for r in phase['records'])-min(r['evaluation']['started_at'] for r in phase['records']))
 seconds=sum(active)+pair['export_seconds'][arm]
 usage={key:sum((w.get('usage') or {}).get(key,0) for w in workloads) for key in ('input_tokens','cached_input_tokens','output_tokens','total_tokens')}
 i,c,o=(usage[k] for k in ('input_tokens','cached_input_tokens','output_tokens'))
 return {'seconds':seconds,'phase_seconds':active,'export_seconds':pair['export_seconds'][arm],
  'critical_workload_seconds':sum(work),'outside_workload_seconds':seconds-sum(work),
  'baseline_preflight_seconds':sum(preflight),'protected_evaluation_window_seconds':sum(evaluation),
  'usage':usage,'model_turns':sum(w['model_turns'] for w in workloads),
  'provider_responses':sum(w['provider_responses'] for w in workloads),
  'cache_fraction':c/i if i else None,'credits_observed_cache':cost(i,c,o),
  'credits_normalized_zero_cache':cost(i,0,o),'credits_normalized_75pct_cache':cost(i,i*.75,o),
  'first_requests':{r['role']:{'input':r['workload'].get('first_input_tokens'),
    'cached':r['workload'].get('first_cached_input_tokens')} for r in records},
  'checks':sum(r['evaluation']['metrics']['tests_total'] for r in records) if arm==ARMS[0] else sum(v['tests'] for v in pair['acceptance'].values())}
def scientific_equality(label):
 root=BASE/label
 files={}
 for role,names in [('trapezoid',('solution.py','result.json')),('simpson',('solution.py','result.json')),
  ('checker',('build_report.py','comparison.csv','report.md','errors.svg','verification.json'))]:
  for name in names:
   a=(root/ARMS[0]/'agents'/role/name).read_bytes();b=(root/ARMS[1]/'agents'/role/name).read_bytes()
   assert a==b,(label,role,name)
   files[role+'/'+name]=hashlib.sha256(a).hexdigest()
 return files
def main():
 rows={};hashes={}
 for label in LIVE+REPLAY:
  pair=read(BASE/label/'pair.json');rows[label]={arm:summarize(pair,arm) for arm in ARMS}
  assert all(row['checks']==85 for row in rows[label].values())
  if pair['mode']=='replay':
   hashes[label]=scientific_equality(label)
   assert all(row['model_turns']==row['provider_responses']==row['usage']['total_tokens']==0 for row in rows[label].values())
  else:
   assert pair['context_equal']
   assert all(rows[label][arm]['model_turns']==3 for arm in ARMS)
   assert {k:v['input'] for k,v in rows[label][ARMS[0]]['first_requests'].items()}=={k:v['input'] for k,v in rows[label][ARMS[1]]['first_requests'].items()}
 metrics=('seconds','outside_workload_seconds','critical_workload_seconds','baseline_preflight_seconds',
  'protected_evaluation_window_seconds','credits_observed_cache','credits_normalized_zero_cache','credits_normalized_75pct_cache')
 medians={}
 for name,labels in [('live',LIVE),('replay',REPLAY)]:
  summary={arm:{metric:statistics.median(rows[label][arm][metric] for label in labels) for metric in metrics} for arm in ARMS}
  for arm in ARMS:
   summary[arm]['tokens']=statistics.median(rows[label][arm]['usage']['total_tokens'] for label in labels)
   summary[arm]['cached_fraction_total']=sum(rows[label][arm]['usage']['cached_input_tokens'] for label in labels)/max(1,sum(rows[label][arm]['usage']['input_tokens'] for label in labels))
  summary['paired_seconds_delta']=[rows[label][ARMS[0]]['seconds']-rows[label][ARMS[1]]['seconds'] for label in labels]
  summary['paired_delta_median']=statistics.median(summary['paired_seconds_delta'])
  medians[name]=summary
 result={'pairs':rows,'medians':medians,'replay_scientific_file_hashes':hashes,
  'timing_definition':'Sum of methods critical-path stage + checker stage + export; image/SDK preparation, initial fixture upload, metadata reading and cleanup excluded in both arms.',
  'credit_definition':'Historical frozen weights: uncached input 50, cached input 2.5, output 250 credits per million. Estimates, not a bill. Zero/75% cache are counterfactual normalizations.',
  'scope':'Same stock CLI in direct Docker and generic INVARIANT command runner. Not a native CodexRunner benchmark.'}
 write(BASE/'measurements.json',result)
 print(json.dumps(medians,indent=2))
if __name__=='__main__':main()
