"""Docker-owned state and a small, credential-free host registry.

The host is a client. Existing coordinators run in a trusted manager with their
filesystem in named volumes. Agent containers never receive its Docker socket.
"""
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading

SCHEMA = 2
OWNER = 'io.invariant.storage'
IMAGE = 'invariant-agent-runtime:0.163.0-v2'
STATE_VOLUME = 'invariant-state-v2'
RUNTIME_VOLUME_PREFIX = 'invariant-code-v2-'
EXCLUDED = {'.git', '.invariant', '__pycache__', 'node_modules', '.venv', 'venv',
            '.npm', '.cache', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'target',
            '.invariant-deps','__pypackages__','.tox','.nox','.uv','.pnpm-store',
            '.yarn-cache','.gradle','.m2','.cargo','.rustup','.bun','.agents','.codex','.DS_Store'}


def managed():
    return os.environ.get('INVARIANT_DOCKER_MANAGER') == '1'


def mappings():
    return json.loads(os.environ.get('INVARIANT_VOLUME_MAP', '[]')) if managed() else []


def volume_ref(path):
    path = Path(path).absolute()
    for item in sorted(mappings(), key=lambda row: len(row['path']), reverse=True):
        try: relative = path.relative_to(item['path']).as_posix()
        except ValueError: continue
        return {'backend':'docker', 'volume':item['volume'], 'path':relative}
    raise RuntimeError('Path is outside Docker-managed state: ' + str(path))


def mount_source(path):
    if not managed(): return str(path)
    ref = volume_ref(path)
    item = next(row for row in mappings() if row['volume'] == ref['volume'])
    return str(PurePosixPath(item['daemon_path']) / ref['path'])


def translate_mounts(arguments):
    if not managed(): return list(arguments)
    result = list(arguments)
    for index, value in enumerate(result):
        if index and result[index-1] == '--mount' and value.startswith('type=bind,'):
            parts = value.split(',')
            for i, part in enumerate(parts):
                if part.startswith(('src=', 'source=')):
                    key, source = part.split('=',1)
                    if '..' in PurePosixPath(source).parts:raise RuntimeError('Mount source cannot traverse parent directories')
                    # Already-translated paths are allowed only in owned volumes.
                    if not any(source == m['daemon_path'] or source.startswith(m['daemon_path']+'/') for m in mappings()):
                        source = mount_source(Path(source).resolve())
                    parts[i] = key+'='+source
            result[index] = ','.join(parts)
    return result


def clear_desktop_mount_hints(arguments):
    """Desktop checkpoint labels must never resurrect original host mounts."""
    result=list(arguments)
    if not managed() or not result or result[0] not in ('create','run'):return result
    import re
    index=next((i for i,a in enumerate(result) if re.fullmatch(r'sha256:[0-9a-f]{64}',a) or a==IMAGE),None)
    if index is None:return result
    info=json.loads(docker('image','inspect',result[index]).stdout)[0]
    hints=[key for key in (info['Config'].get('Labels') or {}) if key.startswith('desktop.docker.io/')]
    result[index:index]=[value for key in sorted(hints) for value in ('--label',key+'=')]
    return result


def registry_path():
    return Path.home()/'.invariant/registry.json'


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.registry-', dir=path.parent)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value, stream, indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(name,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)


@contextlib.contextmanager
def registry_lock():
    path=registry_path();path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=path.with_suffix('.lock')
    with open(lock,'a') as stream:
        os.chmod(lock,0o600);fcntl.flock(stream,fcntl.LOCK_EX)
        if path.exists():
            if path.is_symlink():raise RuntimeError('Registry cannot be a symlink')
            value=json.loads(path.read_text())
            if value.get('schema_version')!=SCHEMA:raise RuntimeError('Unsupported storage registry')
        else:value={'schema_version':SCHEMA,'projects':{}}
        yield value
        atomic_json(path,value)


def docker(*arguments, input=None, check=True, timeout=120, capture=True):
    from .dependencies import find_docker, docker_env
    executable=find_docker()
    result=subprocess.run([executable,*map(str,arguments)],input=input,capture_output=capture,
                          env=docker_env(executable),timeout=timeout)
    if check and result.returncode:
        message=(result.stderr or b'').decode(errors='replace')[-1800:]
        raise RuntimeError('Docker '+str(arguments[0])+' failed: '+message)
    return result


def source_digest():
    package=Path(__file__).parent;sha=hashlib.sha256()
    for path in sorted(package.rglob('*')):
        if not path.is_file() or any(p in ('__pycache__','_legacy') for p in path.relative_to(package).parts) or path.suffix=='.pyc':continue
        sha.update(path.relative_to(package).as_posix().encode());sha.update(path.read_bytes())
    return sha.hexdigest()


def ensure_image():
    digest=source_digest()
    probe=docker('image','inspect',IMAGE,check=False)
    if probe.returncode==0 and json.loads(probe.stdout)[0].get('Config',{}).get('Labels',{}).get(OWNER+'.source')==digest:
        return digest
    package=Path(__file__).parent
    from .environment import IMAGE as base_image
    if docker('image','inspect',base_image,check=False).returncode:
        from .workspace import prepare
        prepare(persistent=True)
    # No checkout, credentials or state are sent as build context.
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w',format=tarfile.GNU_FORMAT) as archive:
        for path in sorted(package.rglob('*')):
            if not path.is_file() or any(p in ('__pycache__','_legacy') for p in path.relative_to(package).parts) or path.suffix=='.pyc':continue
            archive.add(path,arcname='package/'+path.relative_to(package).as_posix(),recursive=False)
        from .distribution import legacy_root
        root=legacy_root()
        for name in ('Cargo.toml','Cargo.lock','scripts/guided_demo.py','sdk/python/invariant_client.py'):
            path=root/name
            if path.is_file():archive.add(path,arcname='legacy/'+name,recursive=False)
        for folder in ('docker','crates'):
            for path in sorted((root/folder).rglob('*')):
                if path.is_file() and not any(p in ('target','__pycache__','.git') for p in path.relative_to(root).parts):
                    archive.add(path,arcname='legacy/'+path.relative_to(root).as_posix(),recursive=False)
        raw=(package/'assets/docker-runtime.Dockerfile').read_bytes()
        info=tarfile.TarInfo('Dockerfile');info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
    print('Preparing Docker-owned agent runtime…',file=sys.stderr,flush=True)
    result=docker('build','--label',OWNER+'.source='+digest,'--tag',IMAGE,'-',input=stream.getvalue(),timeout=1200)
    if result.stdout:print(result.stdout.decode(errors='replace')[-1200:],file=sys.stderr)
    return digest


def owned_volume(name):
    result=docker('volume','inspect',name,check=False)
    if result.returncode:
        docker('volume','create','--label',OWNER+'=v2',name)
        result=docker('volume','inspect',name)
    value=json.loads(result.stdout)[0]
    if (value.get('Labels') or {}).get(OWNER)!='v2':raise RuntimeError('Foreign Docker volume: '+name)
    return value


def local_source_manifest(root):
    """Inputs only; caches and INVARIANT's host outputs are never imported."""
    from .experiments import tree
    result={}
    for base,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if name not in EXCLUDED and not name.startswith(('.invariant-import-','.invariant-apply-'))]
        for name in sorted(dirs+files):
            if name in EXCLUDED or name.startswith(('.invariant-import-','.invariant-apply-')):continue
            path=Path(base)/name;relative=path.relative_to(root).as_posix()
            row=tree.entry(root,relative)
            if row is not None:result[relative]=row
    return result


class Manager:
    def __init__(self, project=None, *, prepare=True):
        self.project=Path(project or Path.cwd()).resolve()
        if self.project in (Path.home().resolve(),Path(self.project.anchor)):
            raise RuntimeError('Choose a project directory, not your home or filesystem root')
        self.key=hashlib.sha256(os.fsencode(self.project)).hexdigest()[:24]
        self.name='invariant-manager-'+self.key
        self.home=str(Path.home())
        if prepare:self.ensure()

    def ensure(self):
        code=ensure_image();code_volume=RUNTIME_VOLUME_PREFIX+code[:16]
        state=owned_volume(STATE_VOLUME);project=owned_volume('invariant-project-'+self.key+'-v2')
        runtime=owned_volume(code_volume)
        self.map=[{'path':str(Path(self.home)/'.invariant'),'volume':STATE_VOLUME,'daemon_path':state['Mountpoint']},
                  {'path':str(self.project),'volume':project['Name'],'daemon_path':project['Mountpoint']},
                  {'path':'/opt/invariant','volume':code_volume,'daemon_path':runtime['Mountpoint']}]
        current=docker('inspect',self.name,check=False)
        if current.returncode==0:
            info=json.loads(current.stdout)[0];labels=info['Config'].get('Labels') or {}
            if labels.get(OWNER)!='v2' or labels.get(OWNER+'.project')!=self.key:raise RuntimeError('Foreign storage manager')
            if labels.get(OWNER+'.source')!=code:
                if info['State']['Running']:
                    self._legacy_rpc=info['Config'].get('Cmd')!=['-B','-m','invariant.manager_service']
                    busy=self.request('busy',{},raw=True)
                    if busy.get('busy'):raise RuntimeError('The existing runtime is active; finish or recover it before updating')
                if info['Config'].get('Cmd')==['-B','-m','invariant.manager_service']:
                    self.request('shutdown_metadata',raw=True)
                docker('rm','--force',self.name);current.returncode=1
                self._legacy_rpc=False
        if current.returncode:
            args=['create','--name',self.name,'--init','--label',OWNER+'=v2','--label',OWNER+'.project='+self.key,
                  '--label',OWNER+'.source='+code,'--restart=unless-stopped','--entrypoint=python3',
                  '--mount','type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock',
                  '--mount',f'type=volume,src={STATE_VOLUME},dst={self.home}/.invariant',
                  '--mount',f'type=volume,src={project["Name"]},dst={self.project}',
                  '--mount',f'type=volume,src={code_volume},dst=/opt/invariant,readonly',
                  '--env','HOME='+self.home,'--env','PYTHONPATH=/opt/invariant','--env','PYTHONDONTWRITEBYTECODE=1',
                  '--env','INVARIANT_DOCKER_MANAGER=1','--env','INVARIANT_PROJECT='+str(self.project),
                  '--env','INVARIANT_VOLUME_MAP='+json.dumps(self.map,separators=(',',':')),
                  IMAGE,'-B','-m','invariant.manager_service']
            docker(*args)
        if current.returncode or not json.loads(current.stdout)[0]['State']['Running']:docker('start',self.name)
        with registry_lock() as registry:
            registry['projects'].setdefault(self.key,{}).update(project=str(self.project),manager=self.name,
                state_volume=STATE_VOLUME,project_volume=project['Name'],runtime_volume=code_volume,layout_version=2)
        # The client remains thin; wait for the private service socket after boot.
        ready=docker('exec',self.name,'python3','-B','-c',
            'import socket,time; p='+repr(str(Path(self.home)/'.invariant/workspaces'/self.key/'manager.sock'))+'; '+
            'deadline=time.monotonic()+30\nwhile True:\n try:\n  s=socket.socket(socket.AF_UNIX); s.connect(p); s.close(); break\n except OSError:\n  s.close()\n  if time.monotonic()>deadline: raise\n  time.sleep(.05)')
        self.seed_auth()

    def request(self, operation, payload=None, *, raw=False):
        request=json.dumps({'operation':operation,'project':str(self.project),'payload':payload or {}},default=os.fspath).encode()
        if getattr(self,'_stream_enabled',False):
            with self._stream_lock:
                if getattr(self,'_stream',None) is None:
                    from .dependencies import find_docker,docker_env
                    executable=find_docker()
                    self._stream=subprocess.Popen([executable,'exec','--interactive',self.name,'python3','-B','-m','invariant.manager_service','stream'],
                        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=docker_env(executable))
                try:
                    self._stream.stdin.write(request+b'\n');self._stream.stdin.flush()
                    output=self._stream.stdout.readline()
                    if not output:raise OSError('Manager connection closed')
                except (OSError,ValueError):
                    self.close()
                    raise RuntimeError('Manager connection lost; request was not replayed. Reopen the context and reconcile its request ID.') from None
        else:
            protocol=['invariant.storage_worker'] if getattr(self,'_legacy_rpc',False) else ['invariant.manager_service','client']
            output=docker('exec','--interactive',self.name,'python3','-B','-m',*protocol,
                         input=request,timeout=None,check=False).stdout
        try:reply=json.loads(output)
        except ValueError:raise RuntimeError('Invalid storage manager reply') from None
        if not reply.get('ok'):
            from .experiments.errors import ExperimentError
            error=reply.get('error',{})
            raise ExperimentError(error.get('code','STORAGE_ERROR'),error.get('message','Storage operation failed'),details=error.get('details',{}))
        if raw:return reply.get('data')
        result=self.public(reply.get('data'))
        if isinstance(result,dict):
            result.setdefault('storage_layout',2);self.reflect_delivery(result)
        return result

    def enable_stream(self):
        if not getattr(self,'_stream_enabled',False):
            import atexit
            self._stream_enabled=True;self._stream_lock=threading.RLock();atexit.register(self.close)

    def close(self):
        process=getattr(self,'_stream',None);self._stream=None
        if process is not None:
            try:process.stdin.close()
            except (OSError,ValueError):pass
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
            finally:process.stdout.close()

    def reflect_delivery(self,result):
        application=result.get('application') if isinstance(result.get('application'),dict) else result if 'applied' in result else None
        if not application or not application.get('plan_id'):return result
        state=self.request('delivery_status',{'plan_id':application['plan_id']},raw=True)['state']
        application['container_applied']=application.get('container_applied',application.get('applied',False))
        application['applied']=state in ('committed','legacy_committed')
        application['host_delivery_state']=state
        if 'application' in result:
            result['completion_state']='applied' if application['applied'] else 'delivery_pending' if state=='pending' else 'conflicted'
        return result

    def seed_auth(self):
        state=self.request('auth_status',raw=True)
        if state['present']:return
        original=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'auth.json'
        if original.is_file():
            # Docker copies the original directly. No token is parsed, logged or staged on the host.
            source=original.resolve();info=source.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_size>1024*1024:
                raise RuntimeError('Unsafe initial sign-in file')
            destination=state['destination'];docker('exec',self.name,'mkdir','-p',str(PurePosixPath(destination).parent))
            docker('cp',str(source),self.name+':'+destination)
            docker('exec',self.name,'chown','0:0',destination)
            docker('exec',self.name,'chmod','600',destination)

    def sync(self,extra_inputs=()):
        from .host_delivery import deliver_pending
        recovery=deliver_pending(self,recover_only=True)
        if any(row['state']!='committed' for row in recovery):raise RuntimeError('Host delivery recovery retained external edits; inspect the exported conflict report')
        manifest=local_source_manifest(self.project)
        from .experiments import tree
        for name in extra_inputs:
            relative=Path(name)
            if relative.is_absolute():relative=relative.relative_to(self.project)
            if '..' in relative.parts:raise RuntimeError('Task input must be inside the project')
            key=relative.as_posix();row=tree.entry(self.project,key)
            if row is None or row['type']!='file':raise RuntimeError('Task input must be a regular file')
            manifest[key]=row
        from .experiments.definitions import digest
        revision=digest(manifest)
        if self.request('source_matches',{'revision':revision},raw=True)['matches']:
            self._source_revision=revision
            return {'imported':0,'unchanged':True,'source_revision':revision,'storage_layout':2}
        from .dependencies import find_docker,docker_env
        command=[find_docker(),'exec','--interactive',self.name,'python3','-B','-m','invariant.storage_worker','import-source',str(self.project)]
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=docker_env(find_docker()))
        try:
            with tarfile.open(fileobj=process.stdin,mode='w|') as archive:
                for relative in manifest:
                    archive.add(self.project/relative,arcname=relative,recursive=False)
            process.stdin.close();output=process.stdout.read();error=process.stderr.read();code=process.wait()
        finally:
            if process.poll() is None:process.kill();process.wait()
            process.stdout.close();process.stderr.close()
        if code:raise RuntimeError('Source import failed: '+error.decode(errors='replace')[-1600:])
        reply=json.loads(output)
        if not reply.get('ok'):raise RuntimeError(reply['error']['message'])
        self._source_revision=revision
        return {**reply['data'],'source_revision':revision}

    def public(self,value):
        if isinstance(value,list):return [self.public(v) for v in value]
        if isinstance(value,dict):
            result={}
            for key,item in value.items():
                if key=='workspace' and isinstance(item,str) and self.is_managed_path(item):
                    result['workspace_ref']=self.path_ref(item)
                elif key=='home' and isinstance(item,str) and self.is_managed_path(item):
                    result['home_ref']=self.path_ref(item)
                elif key=='project' and item==str(self.project):
                    result['project_ref']=self.path_ref(item);result['project_id']=self.key
                else:result[key]=self.public(item)
            return result
        if isinstance(value,str) and self.is_managed_path(value):
            ref=self.path_ref(value)
            return 'docker-volume://'+ref['volume']+'/'+ref['path']
        return value

    def is_managed_path(self,path):
        return (path.startswith(self.home+'/.invariant/') or path==str(self.project) or path.startswith(str(self.project)+'/')
                or any(path==row.get('daemon_path') or path.startswith(row.get('daemon_path','\0')+'/') for row in self.map))

    def path_ref(self,path):
        for row in sorted(self.map,key=lambda r:len(r['path']),reverse=True):
            for base in (row['path'],row.get('daemon_path')):
                if not base:continue
                try:relative=Path(path).relative_to(base).as_posix()
                except ValueError:continue
                return {'backend':'docker','volume':row['volume'],'path':relative}
        raise RuntimeError('Unmanaged state path')



class ContainerEngine:
    """Public Python experiment API delegates to the same volume-backed coordinator."""
    def __init__(self,project=None,*,manager=None,sync=False,auto_export=True,run_ref=None):
        self.manager=manager or Manager(project);self.project=self.manager.project
        self.auto_export=auto_export;self.run_ref=run_ref
        if manager is None:
            from .storage_migration import migrate_project
            migrate_project(self.project,manager=self.manager)
        if sync:self.manager.sync()
    def __getattr__(self,name):
        allowed={'validate','submit','inspect','list','execute','continue_result','diff','plan_apply','apply',
                 'recover_apply','launch','select','stop','recover','retry','delete','results','logs','init_definition'}
        if name not in allowed:raise AttributeError(name)
        def invoke(*args,**kwargs):
            if name in ('submit','validate','init_definition','continue_result'):self.manager.sync()
            result=self.manager.request('engine',{'method':name,'args':args,'kwargs':kwargs,'run_ref':self.run_ref})
            if self.auto_export and name in ('execute','results','apply'):
                from .host_delivery import export_results,export_logs,deliver_pending
                result['exported_results']=export_results(self.manager)
                result['exported_logs']=export_logs(self.manager)
                if name=='apply':
                    from .host_delivery import deliver_plan
                    result['host_delivery']=[deliver_plan(self.manager,args[0])]
                    self.manager.reflect_delivery(result)
                    if any(row['state']!='committed' for row in result['host_delivery']):result.update(applied=False,state='conflicted')
            return result
        return invoke


class ContainerReviewer:
    def __init__(self,project):
        self.manager=Manager(project)
        from .storage_migration import migrate_project
        migrate_project(self.manager.project,manager=self.manager);self.manager.sync()
    def ready(self,folder=None):return self.manager.request('review_ready')
    def decide(self,folder,payload,*,model,effort,timeout=120):
        from .experiments.definitions import digest
        return self.manager.request('review_decide',{'folder_key':digest(str(folder)),
            'payload':payload,'model':model,'effort':effort,'timeout':timeout})


class ContainerTaskRunner:
    def __init__(self,project=None):
        self.manager=Manager(project);self.project=self.manager.project
        from .storage_migration import migrate_project
        migrate_project(self.project,manager=self.manager)
    def __getattr__(self,name):
        if name not in ('run','status','read','continue_task'):raise AttributeError(name)
        def invoke(*args,**kwargs):
            if name in ('run','continue_task'):self.manager.sync()
            return self.manager.request('task_runner',{'method':name,'args':args,'kwargs':kwargs})
        return invoke
    def result(self,method,args=(),kwargs=None):
        result=self.manager.request('task_result',{'method':method,'args':args,'kwargs':kwargs or {}})
        if method=='apply':
            from .host_delivery import deliver_pending,deliver_plan
            identifier=result.get('application',{}).get('plan_id')
            result['host_delivery']=[deliver_plan(self.manager,identifier)] if identifier else deliver_pending(self.manager)
            self.manager.reflect_delivery(result)
            if any(row['state']!='committed' for row in result['host_delivery']):result['application']['applied']=False
        return result


class ContainerCoordinator:
    def __init__(self,project=None):
        self.manager=Manager(project)
        from .storage_migration import migrate_project
        migrate_project(self.manager.project,manager=self.manager)
        self.manager.sync()
    def execute(self,key,request,*,apply=True,timeout=None):
        result=self.manager.request('task_execute',{'key':key,'request':request,'apply':apply,'timeout':timeout})
        from .host_delivery import export_logs,export_results,deliver_pending
        result['logs']=export_logs(self.manager);result['exported_results']=export_results(self.manager)
        result['host_delivery']=deliver_pending(self.manager)
        self.manager.reflect_delivery(result)
        if any(row['state']!='committed' for row in result['host_delivery']):
            result['completion_state']='conflicted'
            if 'application' in result:result['application']['applied']=False
        return result


def main(arguments):
    import argparse
    parser=argparse.ArgumentParser(prog='invariant storage')
    parser.add_argument('action',choices=('status','migrate','cleanup','maintenance'))
    parser.add_argument('--all',action='store_true');parser.add_argument('--json',action='store_true')
    parser.add_argument('--confirm',action='store_true',help='apply the cleanup preview')
    parser.add_argument('--older-than-days',type=float,help='age threshold, default 30 days')
    parser.add_argument('--interval-hours',type=float,help='automatic maintenance interval, default 1 hour')
    parser.add_argument('--enabled',choices=('true','false'),help='enable/disable automatic maintenance')
    args=parser.parse_args(arguments)
    if args.confirm and args.action!='cleanup':parser.error('--confirm applies only to cleanup')
    if args.all and args.action!='migrate':parser.error('--all applies only to migrate')
    if (args.enabled is not None or args.interval_hours is not None) and args.action!='maintenance':
        parser.error('--enabled and --interval-hours apply only to maintenance')
    if args.older_than_days is not None and args.action not in ('cleanup','maintenance'):
        parser.error('--older-than-days applies only to cleanup or maintenance')
    if args.action in ('cleanup','maintenance'):
        manager=Manager()
        if args.action=='cleanup':
            result=manager.request('gc_collect',{'confirm':args.confirm,'older_than_days':args.older_than_days})
        else:
            changes={key:value for key,value in {'older_than_days':args.older_than_days,
                'interval_hours':args.interval_hours,'enabled':None if args.enabled is None else args.enabled=='true'}.items() if value is not None}
            result=manager.request('gc_configure',changes) if changes else manager.request('gc_status')
    elif args.action=='migrate':
        from .storage_migration import migrate_all,migrate_project
        result=migrate_all() if args.all else migrate_project(Path.cwd())
    else:
        from .storage_audit import status
        result=status()
    print(json.dumps({'ok':True,'data':result,'error':None},indent=2))
    return 0


def readiness(arguments):
    """Read-only setup/doctor probes never install a host or container runtime."""
    import argparse
    parser=argparse.ArgumentParser(prog='invariant '+arguments[0])
    parser.add_argument('--json',action='store_true')
    if arguments[0]=='doctor':parser.add_argument('--output')
    else:parser.add_argument('--check',action='store_true')
    parser.parse_args(arguments[1:])
    from .dependencies import probe_docker
    engine=probe_docker();image=docker('image','inspect',IMAGE,check=False) if engine['status']=='ready' else None
    ready=image is not None and image.returncode==0
    runtime={'status':'ready' if ready else 'missing','version':'codex-cli 0.163.0' if ready else None,'backend':'docker'}
    data={'ready':engine['status']=='ready' and ready,'storage_layout':2,'codex':runtime,'docker':engine,
          'issues':[] if ready else [{'code':'RUNTIME_UNAVAILABLE','action':'Run invariant setup to prepare the Docker runtime.'}]}
    if arguments[0]=='doctor':
        data['kind']='invariant-diagnostics';data['tools']={'codex':runtime,'docker':{'status':engine['status']}}
        if '--output' in arguments:
            target=Path(arguments[arguments.index('--output')+1]).expanduser()
            fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as stream:json.dump(data,stream,indent=2)
    if '--json' in arguments:print(json.dumps({'ok':True,'data':data,'error':None,'storage_layout':2}))
    else:print('Docker agent runtime is ready.' if data['ready'] else 'Run invariant setup to prepare the Docker agent runtime.')
    return 0 if data['ready'] else 1


def forward(arguments):
    """Host entrypoint. Only an explicit host delivery can change source files."""
    if not arguments or '--help' in arguments or '-h' in arguments or '--version' in arguments:return None
    if arguments[0]=='context':
        from .run_context import main as context_main
        return context_main(arguments[1:])
    if arguments[0]=='storage':return main(arguments[1:])
    if arguments[0]=='doctor' or arguments[0]=='setup' and ('--check' in arguments or '--json' in arguments):return readiness(arguments)
    if arguments[:2]==['experiment','export']:return None
    manager=Manager()
    from .storage_migration import migrate_project
    migrate_project(manager.project,manager=manager)
    observe=arguments[0] in ('resume','chats','guardrails','status','stop','login','config','setup')
    observe=observe or arguments[0]=='task' and len(arguments)>1 and arguments[1] in ('status','wait','review','apply')
    observe=observe or arguments[0]=='experiment' and len(arguments)>1 and arguments[1] in ('list','status','results','logs','diff','select','stop','recover','storage','cleanup','review','apply','backup')
    if not observe:manager.sync([arguments[i+1] for i,a in enumerate(arguments[:-1]) if a=='--input'])
    from .dependencies import find_docker,docker_env
    command=[find_docker(),'exec','--interactive','--workdir',str(manager.project),manager.name,
             'python3','-B','-m','invariant.storage_worker','cli',*arguments]
    machine='--json' in arguments
    result=subprocess.run(command,env=docker_env(find_docker()),stdout=subprocess.PIPE if machine else None,text=True)
    from .host_delivery import export_logs, export_results, deliver_pending
    collecting=arguments[0] in ('compare','run','resume') or arguments[0]=='task' and len(arguments)>1 and arguments[1] in ('run','execute','continue','wait','review','apply') or arguments[0]=='experiment' and len(arguments)>1 and arguments[1] in ('run','execute','continue','results','review','apply')
    logs=export_logs(manager) if collecting else None;outputs=[];delivered=[]
    if collecting:
        outputs=export_results(manager,ordinary=arguments[0] in ('run','resume'))
        delivered=deliver_pending(manager)
        if arguments[:2]==['experiment','apply']:
            identifier=arguments[arguments.index('--plan')+1] if '--plan' in arguments else next((a.split('=',1)[1] for a in arguments if a.startswith('--plan=')),None)
            if identifier and not any(r.get('plan_id')==identifier for r in delivered):
                from .host_delivery import deliver_plan
                delivered.append(deliver_plan(manager,identifier))
    conflict=any(row['state']!='committed' for row in delivered)
    if machine:
        for line in (result.stdout or '').splitlines():
            try:reply=json.loads(line)
            except ValueError:print(line,file=sys.stderr);continue
            if isinstance(reply.get('data'),dict):
                reply['data'].update(exported_logs=logs,exported_results=outputs,host_delivery=delivered)
                manager.reflect_delivery(reply['data'])
                if conflict:
                    reply['data']['completion_state']='conflicted'
                    if 'application' in reply['data']:reply['data']['application']['applied']=False
            if conflict:reply.update(ok=False,error={'code':'HOST_CONFLICT','message':'External changes retained; inspect the exported conflict report'})
            print(json.dumps(reply))
    elif conflict:
        print('External changes retained; inspect the exported conflict report.',file=sys.stderr)
    if not machine:
        if logs:print('Exported logs:',logs)
        for output in outputs:print('Exported result:',output)
    return 1 if conflict else result.returncode
