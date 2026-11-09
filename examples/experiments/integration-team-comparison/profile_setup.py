"""Measure only disposable runtime preparation/catalog metadata, never model turns."""
import hashlib,json,os
from pathlib import Path
from plain_backend import docker
HERE=Path(__file__).resolve().parent
RUN=HERE/'results/pair-20261107-a'
project=HERE/'projects/pair-20261107-a/with-invariant-methods'
manager='invariant-manager-'+hashlib.sha256(os.fsencode(project)).hexdigest()[:24]
script=r'''
import json,shutil,time,uuid
from pathlib import Path
from invariant import workspace,environment
from invariant.experiments.runtime import LocalRuntime
from invariant.codex_rpc import CodexRpc
from invariant.storage_gc import gate
project=Path.cwd();state=workspace.state_for(project)
original=workspace.docker;events=[]
def traced(*args,**kwargs):
    start=time.perf_counter()
    try:return original(*args,**kwargs)
    finally:events.append({'operation':args[0],'seconds':time.perf_counter()-start})
workspace.docker=traced
request=CodexRpc.request;calls=[]
def metadata_only(self,method,*args,**kwargs):
    if method in ('turn/start','turn/steer','thread/start'):raise RuntimeError('Inference/thread creation forbidden in setup profiler')
    start=time.perf_counter()
    try:return request(self,method,*args,**kwargs)
    finally:calls.append({'method':method,'seconds':time.perf_counter()-start})
CodexRpc.request=metadata_only
folder=state/'diagnostics'/('control-'+uuid.uuid4().hex)
try:
    with gate():
        start=time.perf_counter();path=environment.prepare_control(project,environment.IMAGE,path=folder/'control');cold=time.perf_counter()-start
        cold_events=events[:];events.clear()
        start=time.perf_counter();environment.prepare_control(project,environment.IMAGE,path=folder/'control');warm=time.perf_counter()-start
        size=sum(p.stat().st_size for p in path.iterdir())
        runtime=LocalRuntime();definition=json.loads((project/'experiment.json').read_text());policy=workspace.load_project(project)
        start=time.perf_counter();runtime.models(project,state,definition,policy);catalog=time.perf_counter()-start
        catalog_events=events[:];events.clear()
        start=time.perf_counter();runtime.models(project,state,definition,policy);catalog_cached=time.perf_counter()-start
        print(json.dumps({'model_turns':0,'control_bytes':size,'cold_control_seconds':cold,'warm_control_seconds':warm,
            'cold_control_docker_calls':cold_events,'catalog_seconds':catalog,'catalog_same_instance_seconds':catalog_cached,
            'catalog_metadata_rpc_calls':calls,'catalog_docker_calls':catalog_events,
            'scope':'Diagnostic replay on disposable runtime copy; original run timings unchanged.'}))
finally:
    if folder.exists():shutil.rmtree(folder)
'''
result=docker('exec','--interactive','--workdir',str(project),manager,'python3','-B','-',input=script.encode())
data=json.loads(result.stdout)
(RUN/'diagnostics/setup-profile.json').write_text(json.dumps(data,indent=2)+'\n')
print(json.dumps(data,indent=2))
