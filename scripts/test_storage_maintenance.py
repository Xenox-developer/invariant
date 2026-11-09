#!/usr/bin/env python3
"""Exercise real Docker GC on isolated fixtures, never other workloads.

The integration driver narrows Docker inventory to the objects it creates. All
reference scanning, leases, age checks and deletion calls use production code.
"""
import hashlib
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from invariant import docker_storage as storage


def main():
    token = uuid.uuid4().hex
    prefix = 'invariant-gc-check-' + token[:12]
    volume = prefix + '-state'
    code = storage.RUNTIME_VOLUME_PREFIX + token[:16]
    names, images = [], []
    try:
        storage.docker('volume', 'create', '--label', storage.OWNER+'=v2', volume)
        storage.docker('volume', 'create', '--label', storage.OWNER+'=v2', code)
        def make(suffix, role=None, image=storage.IMAGE, command=('/bin/true',)):
            name = ('invariant-agent-gc-check-' + token[:12] if role == 'agent-runtime' else prefix) + '-' + suffix
            args = ['create', '--name', name, '--network=none', '--entrypoint', command[0]]
            if role:
                args += ['--label', storage.OWNER+'=v2', '--label', 'io.invariant.role='+role,
                         '--label', 'io.invariant.session='+name]
            storage.docker(*args, image, *command[1:])
            names.append(name)
            return name
        seed = make('seed')
        project = '/project'
        project_key = hashlib.sha256(project.encode()).hexdigest()
        for kind in ('saved', 'obsolete'):
            identity = storage.docker('commit', '--change', 'LABEL io.invariant.checkpoint-project='+project_key,
                        '--change', 'LABEL io.invariant.checkpoint-owner=worker',
                        '--change', 'LABEL io.invariant.gc-fixture='+token+'-'+kind, seed).stdout.decode().strip()
            images.append(identity)
        agent = make('agent', 'agent-runtime')
        unsaved = make('unsaved', 'codex-workspace', command=('/bin/sh','-c','echo recovery > /recovery-file'))
        storage.docker('start', '--attach', unsaved)
        foreign = make('foreign')
        script = r'''
import hashlib,json,os,time
from pathlib import Path
from invariant import storage_gc as gc, docker_storage as storage, environment
fixture=json.loads(os.environ['GC_FIXTURE'])
state=gc.root()
def write(name,body):
    path=state/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body);return path
write('workspaces/p/environment.json',json.dumps({'schema':1,'project':'/project','owner':'worker','image':fixture['images'][0]}))
paths=['workspaces/p/codex/auth.json','workspaces/p/codex/sessions/chat.json',
       'workspaces/p/results/report.json','workspaces/p/logs/run.log',
       'workspaces/p/experiments/attempts/a/snapshot/workspace/source.py',
       'workspaces/p/codex/home/node_modules/installed.js','workspaces/p/codex/home/.venv/pyvenv.cfg']
for path in paths:write(path,'{}' if path.endswith('.json') else 'preserve')
cache=write('workspaces/p/codex/home/.cache/pip/http-v2/old','download')
os.utime(cache,(1,1))
before={p:hashlib.sha256((state/p).read_bytes()).hexdigest() for p in paths}
original=gc.inspect_all
ids=set(fixture['names'])
def inventory(kind):
    if kind=='container':return [c for c in original(kind) if c['Name'].lstrip('/') in ids]
    if kind=='image':return [i for i in original(kind) if i['Id'] in fixture['images']]
    return [v for v in original(kind) if v['Name']==fixture['code']]
gc.inspect_all=inventory
with gc.gate():assert gc.collect(confirm=True)['state']=='busy'
preview=gc.collect(older_than_days=0)
assert preview['state']=='preview',preview
assert preview['candidates']['images']==[fixture['images'][1]],preview
assert len(preview['candidates']['containers'])==1,preview
assert preview['candidates']['code_volumes']==[fixture['code']],preview
assert cache.exists()
result=gc.collect(confirm=True,older_than_days=0)
assert not result['errors'],result
assert len(result['removed']['cache_files'])==1,result
assert not cache.exists()
assert before=={p:hashlib.sha256((state/p).read_bytes()).hexdigest() for p in paths}
assert environment.selected_image(state/'workspaces/p',Path('/project'),storage.IMAGE,owner='worker')==fixture['images'][0]
assert storage.docker('container','inspect',fixture['agent'],check=False).returncode!=0
assert storage.docker('container','inspect',fixture['unsaved'],check=False).returncode==0
assert storage.docker('container','inspect',fixture['foreign'],check=False).returncode==0
assert storage.docker('image','inspect',fixture['images'][1],check=False).returncode!=0
assert storage.docker('volume','inspect',fixture['code'],check=False).returncode!=0
assert gc.collect(confirm=True,scheduled=True)['state']=='not_due'
print(json.dumps({'passed':True,'removed_counts':{k:len(v) for k,v in result['removed'].items()},
    'evidence_files_unchanged':len(paths),'saved_environment_resolves':True,
    'unsaved_executor_retained':True,'foreign_container_retained':True,
    'shared_lease_defers_cleanup':True,'scheduled_runs_deduplicated':True}))
'''
        fixture = {'names':names,'images':images,'code':code,'agent':agent,'unsaved':unsaved,'foreign':foreign}
        result=storage.docker('run','--rm','--interactive','--network=none','--read-only','--cap-drop=ALL',
                 '--security-opt=no-new-privileges','--label',storage.OWNER+'=v2',
                 '--mount','type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock',
                 '--mount','type=volume,src='+volume+',dst=/state',
                 '--env','HOME=/state','--env','INVARIANT_DOCKER_MANAGER=1',
                 '--env','GC_FIXTURE='+json.dumps(fixture),'--entrypoint','python3',storage.IMAGE,
                 '-B','-',input=script.encode(),timeout=180)
        print(result.stdout.decode(),end='')
    finally:
        for name in names:storage.docker('container','rm','--force',name,check=False)
        for image in images:storage.docker('image','rm','--no-prune',image,check=False)
        for name in (volume,code):storage.docker('volume','rm',name,check=False)


if __name__=='__main__':main()
