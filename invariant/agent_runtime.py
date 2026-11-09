"""Codex runs in its own container, connected to one authenticated executor."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import uuid
import contextvars
import functools

from . import docker_storage as storage

PORT=4501
_links=contextvars.ContextVar('invariant_startup_links',default=None)


def reuse_link(function):
    @functools.wraps(function)
    def invoke(*args,**kwargs):
        token=_links.set({})
        try:return function(*args,**kwargs)
        finally:_links.reset(token)
    return invoke


def account_home():
    return Path.home()/'.invariant/accounts/current'


def transport(owner):
    from . import workspace
    cached=_links.get()
    if cached is not None and owner in cached:return dict(cached[owner])
    directory=Path.home()/'.invariant/transports'/owner
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    token_file=directory/'capability'
    if not token_file.exists():
        fd=os.open(token_file,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as stream:stream.write(secrets.token_urlsafe(48))
    token=token_file.read_text()
    network='invariant-link-'+hashlib.sha256(owner.encode()).hexdigest()[:24]
    current=workspace.docker('network','inspect',network,timeout=15) if _network_exists(network) else None
    if current is None:
        workspace.docker('network','create','--internal','--label',storage.OWNER+'=v2',network)
    elif (json.loads(current)[0].get('Labels') or {}).get(storage.OWNER)!='v2':
        raise RuntimeError('Foreign executor network')
    result={'network':network,'token':token,'sha256':hashlib.sha256(token.encode()).hexdigest()}
    if cached is not None:cached[owner]=result
    return result


def _network_exists(name):
    return storage.docker('network','inspect',name,check=False).returncode==0


def server_arguments(owner):
    config=transport(owner)
    return ['exec-server','--listen',f'ws://0.0.0.0:{PORT}','--ws-auth','capability-token','--ws-token-sha256',config['sha256']]


def executor_network(owner):
    return transport(owner)['network']


def connect_internet(name,policy):
    """Only the optional second interface supplies workload internet access."""
    from . import workspace
    if policy['command_network']=='internet':
        info=json.loads(workspace.docker('inspect',name))[0]
        if 'bridge' not in (info.get('NetworkSettings',{}).get('Networks') or {}):
            workspace.docker('network','connect','bridge',name)


def seed_private_auth(home):
    source=account_home()/'auth.json';target=Path(home)/'auth.json'
    if target.is_symlink():target.unlink()
    if not target.exists() and source.exists() and source.resolve()!=target.absolute():
        shutil.copyfile(source,target);target.chmod(0o600)


def configuration(home, executor, command, env):
    from . import workspace
    home=Path(home);home.mkdir(parents=True,exist_ok=True,mode=0o700)
    seed_private_auth(home)
    link=transport(executor)
    token=home/'executor-token';token.write_text(link['token']);token.chmod(0o600)
    environments=home/'environments.toml'
    if environments.exists():
        environments.write_text('default = "invariant"\ninclude_local = false\n[[environments]]\n'
            'id = "invariant"\nprogram = "/opt/agent-runtime/bin/python3"\nargs = '+json.dumps([
                '/opt/invariant/invariant/assets/exec-ws-bridge.py',f'ws://{executor}:{PORT}',str(token)])+'\n')
    name='invariant-agent-'+uuid.uuid4().hex
    home_user=home/'home';home_user.mkdir(exist_ok=True,mode=0o700)
    arguments=['create','--name',name,'--init','--interactive','--read-only','--cap-drop=ALL',
               '--security-opt=no-new-privileges','--tmpfs=/tmp:rw,nosuid,nodev',
               '--label',storage.OWNER+'=v2','--label','io.invariant.role='+('catalog-runtime' if executor=='invariant-catalog-unused' else 'agent-runtime'),
               '--label','io.invariant.executor='+executor,'--network',link['network'],
               '--mount',f'type=bind,src={home},dst={home}',
               '--mount','type=bind,src=/opt/invariant,dst=/opt/invariant,readonly',
               '--workdir',str(home),'--env','CODEX_HOME='+str(home),'--env','HOME='+str(home_user),
               '--env','XDG_CACHE_HOME='+str(home_user/'.cache'),
               '--env','PATH=/opt/agent-runtime/bin:/usr/local/bin:/usr/bin:/bin',
               '--entrypoint=/__invariant/codex',storage.IMAGE,*command[1:]]
    workspace.docker(*arguments)
    workspace.docker('network','connect','bridge',name)
    # This invokes only Docker's attached stream; no agent runs in the manager.
    from .dependencies import find_docker
    return [find_docker(),'start','--attach','--interactive',name], {
        **env,'INVARIANT_AGENT_CONTAINER':name,'INVARIANT_AGENT_HOME':str(home),
        'INVARIANT_AGENT_EXECUTOR':executor}


def cleanup(name,home=None,executor=None):
    from . import workspace
    value=storage.docker('inspect',name,check=False)
    if value.returncode==0:
        info=json.loads(value.stdout)[0]
        if (info['Config'].get('Labels') or {}).get('io.invariant.role')not in ('agent-runtime','catalog-runtime'):
            raise RuntimeError('Refusing to remove a foreign agent runtime')
        workspace.docker('rm','--force','--volumes',name)
    if home:
        source=Path(home)/'auth.json';destination=account_home()/'auth.json'
        if source.exists() and source.resolve()!=destination.absolute():
            from .execution import project_lock
            account_home().mkdir(parents=True,exist_ok=True,mode=0o700)
            with project_lock(account_home(),reconcile=False,timeout=30):
                if not destination.exists() or source.stat().st_mtime_ns>destination.stat().st_mtime_ns:
                    temporary=account_home()/('auth-'+uuid.uuid4().hex)
                    shutil.copyfile(source,temporary);temporary.chmod(0o600);os.replace(temporary,destination)
    if executor:cleanup_network(executor)


def cleanup_network(owner):
    network='invariant-link-'+hashlib.sha256(owner.encode()).hexdigest()[:24]
    value=storage.docker('network','inspect',network,check=False)
    if value.returncode==0:
        info=json.loads(value.stdout)[0]
        if not info.get('Containers') and (info.get('Labels') or {}).get(storage.OWNER)=='v2':
            storage.docker('network','rm',network)


def account_command(arguments, *, capture=False):
    from . import workspace
    home=account_home();home.mkdir(parents=True,exist_ok=True,mode=0o700)
    executable='/__invariant/codex'
    env=workspace.codex_private_environment(home)
    command,env=configuration(home,'account-'+uuid.uuid4().hex,[executable,*arguments],env)
    name=env['INVARIANT_AGENT_CONTAINER']
    try:
        return subprocess.run(command,env=env,capture_output=capture,text=True,timeout=600 if not capture else 30)
    finally:cleanup(name,home,env['INVARIANT_AGENT_EXECUTOR'])
