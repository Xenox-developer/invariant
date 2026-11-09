"""No-model probes of observation, result lookup and collection on the retained run."""
import json,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parents[2]))
from invariant import docker_storage as storage,host_delivery
from invariant.run_context import RunContext
BASE=HERE/'results/v4';rows=[]
def main():
 saved=json.loads((BASE/'warm3/with-invariant/context.json').read_text());old=storage.Manager.request
 def request(self,op,payload=None,**kwargs):
  start=time.monotonic()
  try:return old(self,op,payload,**kwargs)
  finally:rows.append({'kind':'rpc','operation':op,'method':(payload or {}).get('method'),'seconds':time.monotonic()-start})
 storage.Manager.request=request
 start=time.monotonic();context=RunContext(saved['project'],run_ref=saved['run_ref']);rows.append({'kind':'phase','name':'reopen','seconds':time.monotonic()-start})
 for name,operation in [('status',context.status),('collect1',context.collect),('collect2',context.collect)]:
  start=time.monotonic();value=operation();rows.append({'kind':'phase','name':name,'seconds':time.monotonic()-start,
   'result_bytes':len(json.dumps(value))})
 path=BASE/'diagnostics/saved-observation-profile.json';path.write_text(json.dumps({'model_calls':0,'rows':rows},indent=2)+'\n')
 print(json.dumps(rows,indent=2))
if __name__=='__main__':main()
