#!/usr/bin/env python3
"""Record the exact Rust graph, container packages, upstream notices and build pins."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from invariant import environment, workspace


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    report={'schema_version':1,'passed':False,'cleanup_errors':[],'checks':[],
        'scope':'Inventory for the tested source/images; not legal advice or a blanket redistribution approval.'}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    try:
        data=json.loads(subprocess.check_output(['cargo','metadata','--locked','--format-version','1'],cwd=ROOT,text=True,timeout=120))
        packages=[{'name':p['name'],'version':p['version'],'source':p['source'],'license':p['license']} for p in data['packages']]
        report['rust']=sorted(packages,key=lambda p:(p['name'],p['version']))
        if any(not p['license'] for p in packages):raise ValueError('Rust dependency without a declared license')
        if 'Apache License' not in (ROOT/'LICENSE').read_text():raise ValueError('Repository license missing')
        report['python']={'project_license':'Apache-2.0','runtime_third_party_dependencies':[],
            'build_dependencies':['setuptools>=68'],'host_prerequisites':['Python>=3.11','Docker','Codex CLI for native trials']}
        report['build_pins']={str(p.relative_to(ROOT)):p.read_text() for p in [ROOT/'invariant/assets/install_codex.py',
            ROOT/'invariant/assets/environment.Dockerfile',ROOT/'invariant/assets/codex.Dockerfile',ROOT/'docker/python.Dockerfile',ROOT/'docker/supervisor.Dockerfile']}
        report['images']=[]
        for image in [environment.IMAGE,workspace.IMAGE,'invariant-python:local','invariant-supervisor:local']:
            info=json.loads(workspace.docker('image','inspect',image))[0]
            entry={'tag':image,'id':info['Id'],'repo_digests':info.get('RepoDigests'),
                'architecture':info['Architecture'],'packages':[],'notices':[]}
            name='invariant-release-inventory-'+uuid.uuid4().hex
            label='io.invariant.release-inventory='+name
            try:
                base=['docker','run','--name',name,'--label',label,'--network=none','--read-only','--cap-drop=ALL',
                    '--security-opt=no-new-privileges','--pids-limit=64','--memory=256m','--entrypoint=/bin/sh',image]
                result=subprocess.run(base+['-c',"dpkg-query -W -f='${binary:Package}\t${Version}\n'"],capture_output=True,text=True,timeout=60)
                if result.returncode:raise ValueError('Container package inventory failed: '+image)
                entry['packages']=[dict(zip(('name','version'),line.split('\t',1))) for line in result.stdout.splitlines()]
            finally:
                removed=subprocess.run(['docker','rm','--force',name],capture_output=True,text=True,timeout=15)
                if removed.returncode and 'No such container' not in removed.stderr:report['cleanup_errors'].append(removed.stderr)
            name='invariant-release-notices-'+uuid.uuid4().hex
            try:
                command=['docker','run','--name',name,'--label','io.invariant.release-inventory='+name,
                    '--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=64',
                    '--memory=256m','--entrypoint=/bin/sh',image,'-c',
                    "tar -cf - /usr/share/doc/*/copyright /usr/local/share/invariant/codex-notices/* 2>/dev/null"]
                result=subprocess.run(command,capture_output=True,timeout=60)
                if len(result.stdout)>32*1024**2:raise ValueError('Notice archive too large')
                # Some image types do not carry Codex. Missing globs can set tar's
                # status nonzero; require the actual documented copyright entries.
                archive_path=args.report.parent/(image.replace(':','-')+'-notices.tar')
                archive_path.write_bytes(result.stdout)
                with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
                    for member in archive:
                        if member.isfile():
                            content=archive.extractfile(member).read()
                            entry['notices'].append({'path':member.name,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()})
                if not entry['notices']:raise ValueError('No OS notices found: '+image)
                entry['notice_archive']={'file':archive_path.name,'sha256':hashlib.sha256(result.stdout).hexdigest()}
                if image in (environment.IMAGE,workspace.IMAGE) and not any('codex-notices/' in n['path'] for n in entry['notices']):
                    raise ValueError('Bundled Codex distribution notices are missing; rebuild the environment image')
            finally:
                removed=subprocess.run(['docker','rm','--force',name],capture_output=True,text=True,timeout=15)
                if removed.returncode and 'No such container' not in removed.stderr:report['cleanup_errors'].append(removed.stderr)
            report['images'].append(entry)
        report['checks']=['repository Apache-2.0 metadata and Rust dependency license declarations',
            'exact tested image identities, installed packages and original copyright notices',
            'pinned Codex distribution metadata/notices retained in the environment image']
        report['passed']=not report['cleanup_errors']
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError,tarfile.TarError) as error:report['error']=str(error)
    workspace.private_write(args.report,report)
    print('Inventory: '+str(args.report));return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
