"""Persistent trusted manager. Unix socket lives only in the Docker state volume."""
import contextlib
import json
import os
from pathlib import Path
import signal
import socket
import socketserver
import sys
import threading

from . import docker_storage as storage, storage_worker, workspace
from .telemetry import session,span


def socket_path(project):return workspace.state_for(project)/'manager.sock'


def reply(project,request):
    if request.get('project')!=str(project):raise RuntimeError('Wrong project identity')
    operation=request['operation'];payload=request.get('payload',{})
    from .storage_gc import gate
    # Maintenance owns its own exclusive lease.
    lease=contextlib.nullcontext() if operation in ('gc_collect','gc_configure') else gate()
    observing=operation in ('source_matches','auth_status','run_status','run_view','collect_prepare','export_logs','result_bundles','export_cache') or operation=='engine' and payload.get('method') in ('inspect','list','results','logs','diff')
    tracing=contextlib.nullcontext() if observing else span('manager.'+operation)
    with lease,session(project,payload.get('run_ref')),tracing:
        return storage_worker.operate(project,operation,payload)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while self.handle_one():pass

    def handle_one(self):
        try:
            raw=self.rfile.readline()
            if not raw:return
            request=json.loads(raw)
            data=reply(self.server.project,request)
            result={'ok':True,'data':data,'error':None}
        except Exception as error:
            result={'ok':False,'data':None,'error':error.record() if hasattr(error,'record') else
                    {'code':'STORAGE_ERROR','message':str(error)}}
        try:
            self.wfile.write((json.dumps(result,default=os.fspath)+'\n').encode());self.wfile.flush()
        except OSError:return False
        return True


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True
    allow_reuse_address=True


def client():
    project=Path(os.environ['INVARIANT_PROJECT'])
    request=sys.stdin.buffer.read()
    with socket.socket(socket.AF_UNIX) as stream:
        stream.connect(str(socket_path(project)));stream.sendall(request+b'\n')
        with stream.makefile('rb') as incoming:sys.stdout.buffer.write(incoming.readline())


def stream_client():
    project=Path(os.environ['INVARIANT_PROJECT'])
    with socket.socket(socket.AF_UNIX) as stream:
        stream.connect(str(socket_path(project)))
        with stream.makefile('rb') as incoming:
            for request in sys.stdin.buffer:
                stream.sendall(request)
                response=incoming.readline()
                if not response:raise RuntimeError('Manager disconnected; reconcile the request before retrying')
                sys.stdout.buffer.write(response);sys.stdout.buffer.flush()


def serve():
    if not storage.managed():raise RuntimeError('Manager service must run in Docker')
    project=Path(os.environ['INVARIANT_PROJECT']);path=socket_path(project)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.exists() or path.is_socket():path.unlink()
    server=Server(str(path),Handler);server.project=project;os.chmod(path,0o600)
    stopped=threading.Event()
    def stop(*_):
        stopped.set();threading.Thread(target=server.shutdown,daemon=True).start()
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,stop)
    from .storage_gc import daemon
    maintenance=threading.Thread(target=daemon,args=(stopped,),daemon=True);maintenance.start()
    try:server.serve_forever(poll_interval=.2)
    finally:
        stopped.set();server.server_close();path.unlink(missing_ok=True)
        from .catalog import close
        close()


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='client':client()
    elif len(sys.argv)>1 and sys.argv[1]=='stream':stream_client()
    else:serve()
