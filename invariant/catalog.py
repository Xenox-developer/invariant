"""Account-scoped, expiring model metadata; the connection never starts a turn."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import threading
import time

from . import workspace
from .agent_runtime import account_home
from .codex_rpc import CodexRpc
from .dependencies import probe_codex,CODEX_VERSION
from .execution import project_lock
from .telemetry import span

TTL=300
_LOCK=threading.RLock()
_CLIENT=None
_AUTH=None


def auth_key():
    source=account_home()/'auth.json'
    return hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else 'missing'


def close(*,sync_auth=True):
    global _CLIENT,_AUTH
    with _LOCK:
        if _CLIENT is not None:
            if not sync_auth and getattr(_CLIENT,'agent_runtime',None):
                name,_,executor=_CLIENT.agent_runtime;_CLIENT.agent_runtime=(name,None,executor)
            _CLIENT.close()
        _CLIENT=None;_AUTH=None


def models(project,policy,*,clock=time.time):
    global _CLIENT,_AUTH
    with _LOCK,span('catalog.resolve'):
        root=Path.home()/'.invariant/catalog-v3'
        with project_lock(root,reconcile=False,timeout=60):
            key=auth_key();path=root/'models.json'
            saved=json.loads(path.read_text()) if path.exists() else {}
            if saved.get('auth')==key and saved.get('version')==CODEX_VERSION and 0<=clock()-saved.get('checked_at',0)<TTL:
                return copy.deepcopy(saved['models'])
            if _AUTH!=key:close(sync_auth=False)
            if _CLIENT is None:
                home=workspace.state_for(project)/'catalog-v3';home.mkdir(parents=True,exist_ok=True,mode=0o700)
                if key=='missing':raise RuntimeError('ChatGPT sign-in is required')
                shutil.copyfile(account_home()/'auth.json',home/'auth.json');(home/'auth.json').chmod(0o600)
                status=probe_codex()
                if status['status']!='ready':raise RuntimeError('Supported Codex runtime unavailable')
                command,env=workspace.codex_configuration(home,'invariant-catalog-unused',status['path'],policy={**policy,'tool_profile':'coding'})
                _CLIENT=CodexRpc(command,env=env,cwd=home,guard_parent=True);_AUTH=key
                try:
                    with span('catalog.initialize'):_CLIENT.initialize()
                except BaseException:close();raise
            try:
                with span('catalog.account'):
                    account=_CLIENT.request('account/read',{'refreshToken':False}).get('account')
                if not isinstance(account,dict) or account.get('type')!='chatgpt':raise RuntimeError('ChatGPT sign-in is required')
                rows=[];cursor=None;seen=set()
                with span('catalog.list'):
                    while True:
                        response=_CLIENT.request('model/list',{'limit':100,'includeHidden':True,**({'cursor':cursor} if cursor else {})})
                        if not isinstance(response.get('data'),list):raise RuntimeError('Invalid model catalog')
                        rows.extend(response['data']);cursor=response.get('nextCursor')
                        if cursor is None:break
                        if not isinstance(cursor,str) or cursor in seen or len(seen)>=100:raise RuntimeError('Invalid model catalog cursor')
                        seen.add(cursor)
                workspace.private_write(path,{'auth':key,'version':CODEX_VERSION,'checked_at':clock(),'models':rows})
                return copy.deepcopy(rows)
            except BaseException:close();raise
