#!/usr/bin/env python3
"""Reproduce transient package failures locally; no internet or model calls.

Run after preparing the standard environment image:
  python3 -B scripts/test_task_automation.py --report .invariant/task-automation
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid


def local_fixture():
    import functools
    import hashlib
    import http.server
    import json
    from pathlib import Path
    import subprocess
    import threading

    root=Path('/tmp/retry-fixture');root.mkdir()
    package=root/'package';(package/'DEBIAN').mkdir(parents=True)
    arch=subprocess.check_output(['dpkg','--print-architecture'],text=True).strip()
    (package/'DEBIAN/control').write_text(f'Package: invariant-retry-fixture\nVersion: 1.0\nArchitecture: {arch}\nMaintainer: Test <test@example.invalid>\nDescription: Local deterministic retry fixture\n')
    (package/'DEBIAN/postinst').write_text('#!/bin/sh\necho configured >> /tmp/invariant-maintainer-calls\n')
    (package/'DEBIAN/postinst').chmod(0o755)
    deb=root/'fixture.deb'
    subprocess.run(['dpkg-deb','--build',str(package),str(deb)],check=True,capture_output=True)
    (root/'Packages').write_text((package/'DEBIAN/control').read_text()+f'Filename: fixture.deb\nSize: {deb.stat().st_size}\nSHA256: {hashlib.sha256(deb.read_bytes()).hexdigest()}\n\n')
    requests=[]
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/fixture.deb':
                requests.append(self.path)
                if len(requests)<3:
                    self.send_response(503);self.end_headers();self.wfile.write(b'transient');return
            super().do_GET()
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(root)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    source=root/'sources.list';source.write_text(f'deb [trusted=yes] http://127.0.0.1:{server.server_port} ./\n')
    Path('/etc/apt/apt.conf.d/99-local-fixture').write_text(f'Dir::Etc::sourcelist "{source}";\nDir::Etc::sourceparts "-";\nAPT::Get::List-Cleanup "0";\n')
    command=Path('/inputs/package-command.txt').read_text()
    result=subprocess.run(['/bin/sh','-lc',command],capture_output=True,text=True,timeout=60)
    Path('/reports/apt-retry.log').write_text(result.stdout+result.stderr)
    maintainer=Path('/tmp/invariant-maintainer-calls')
    calls=maintainer.read_text().splitlines() if maintainer.exists() else []
    report={'exit_code':result.returncode,'download_requests':len(requests),'maintainer_calls':len(calls),'503_before_success':2}
    print(json.dumps(report),flush=True)
    assert result.returncode==0 and len(requests)==3 and len(calls)==1,report
    Path('/reports/apt-retry.json').write_text(json.dumps(report,indent=2))
    server.shutdown()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--image',default='invariant-codex-environment:0.163.0-v1')
    parser.add_argument('--report',type=Path,default=Path('.invariant/task-automation'))
    args=parser.parse_args()
    if args.inside:
        local_fixture()
        return 0
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from invariant.environment_cli import package_command
    from invariant.dependencies import find_docker
    destination=args.report.resolve();destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='invariant-acquisition-') as temporary:
        inputs=Path(temporary).resolve()
        (inputs/'package-command.txt').write_text(package_command(['invariant-retry-fixture']))
        name='invariant-acquisition-'+uuid.uuid4().hex[:12]
        command=[find_docker(),'run','--rm','--name',name,'--network=none',
                 '--cpus=1','--memory=512m','--memory-swap=512m','--pids-limit=128',
                 '--mount',f'type=bind,src={Path(__file__).resolve()},dst=/fixture/check.py,readonly',
                 '--mount',f'type=bind,src={inputs},dst=/inputs,readonly',
                 '--mount',f'type=bind,src={destination},dst=/reports',
                 '--entrypoint','python3',args.image,'/fixture/check.py','--inside']
        result=subprocess.run(command,capture_output=True,text=True,timeout=90)
        (destination/'runner.log').write_text(result.stdout+result.stderr)
        print(result.stdout,end='')
        if result.returncode:print(result.stderr,file=sys.stderr)
        return result.returncode


if __name__=='__main__':raise SystemExit(main())
