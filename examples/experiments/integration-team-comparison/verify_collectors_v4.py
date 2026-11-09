"""Two independent host processes must reuse one unchanged publication."""
import json,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;sys.path.insert(0,str(HERE.parents[2]))
from invariant.run_context import RunContext
BASE=HERE/'results/v4'
SCRIPT='''import json,sys
from invariant.run_context import RunContext
value=json.load(sys.stdin)
with RunContext(value['project'],run_ref=value['run_ref']) as run:print(json.dumps(run.collect()))
'''
def main():
 saved=json.loads((BASE/'warm3/with-invariant/context.json').read_text())
 with RunContext(saved['project'],run_ref=saved['run_ref']) as context:expected=context.collect()
 directory=Path(saved['project'])/'.invariant/logs';before={p.name for p in directory.iterdir() if p.is_dir()}
 processes=[subprocess.Popen([sys.executable,'-B','-c',SCRIPT],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=HERE.parents[2]) for _ in range(2)]
 for p in processes:p.stdin.write(json.dumps(saved).encode());p.stdin.close()
 results=[]
 for p in processes:
  raw=p.stdout.read();error=p.stderr.read();assert p.wait()==0,error.decode();results.append(json.loads(raw))
 after={p.name for p in directory.iterdir() if p.is_dir()};assert results[0]==results[1]==expected and before==after
 (BASE/'concurrent-collection.json').write_text(json.dumps({'model_calls':0,'independent_processes':2,
   'same_paths':True,'no_new_log_exports':True,'result':expected},indent=2)+'\n')
 print('Concurrent collectors reuse the same paths without new exports.')
if __name__=='__main__':main()
