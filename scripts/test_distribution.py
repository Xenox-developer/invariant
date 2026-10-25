#!/usr/bin/env python3
"""Build wheel and sdist, then exercise the installed CLI outside any checkout."""
import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import venv
import zipfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def verify_readme(sdist, wheel, source):
    """Check the description a package index receives, not just the checkout."""
    readme = (source/'README.md').read_text()
    with zipfile.ZipFile(wheel) as archive:
        name = next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))
        metadata = BytesParser().parsebytes(archive.read(name))
    assert metadata['Description-Content-Type'] == 'text/markdown'
    description = metadata.get_payload(decode=True).decode('utf-8')
    assert description.strip() == readme.strip(), 'Wheel description differs from README'
    images = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', description)
    assert images, 'Package description has no cover'
    assert all(urlsplit(url).scheme == 'https' and urlsplit(url).netloc for url in images), \
        'Package description images need absolute HTTPS URLs; relative paths break on package indexes'
    links = re.findall(r'\]\(([^)]+)\)', description)
    assert all(url.startswith('#') or (urlsplit(url).scheme == 'https' and urlsplit(url).netloc)
               for url in links), 'Package description links must also work outside GitHub'
    cover = '.github/assets/invariant-cover.png'
    assert urlsplit(images[0]).path.endswith('/'+cover), 'Description does not use the selected cover'
    with tarfile.open(sdist, 'r:gz') as archive:
        prefix = sdist.name.removesuffix('.tar.gz')
        assert archive.extractfile(prefix+'/README.md').read().decode('utf-8') == readme
        source_metadata = BytesParser().parsebytes(archive.extractfile(prefix+'/PKG-INFO').read())
        assert source_metadata.get_payload(decode=True).decode('utf-8').strip() == readme.strip(), \
            'sdist description differs from README'
        covers = [n for n in archive.getnames() if n.startswith(prefix+'/.github/assets/')]
        assert covers == [prefix+'/'+cover], 'sdist must contain only the selected README cover'
        assert archive.extractfile(prefix+'/'+cover).read() == (source/cover).read_bytes(), \
            'sdist cover differs from the selected image'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=ROOT/'.invariant/distribution/report.json')
    parser.add_argument('--docker', action='store_true', help='also run the installed legacy Docker demo')
    args = parser.parse_args()
    # Real installations may use a private umask; image inputs must still work
    # for the unprivileged workload UID after pip writes package files as 0600.
    os.umask(0o077)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'passed': False, 'checks': [], 'artifacts': []}
    try:
        with tempfile.TemporaryDirectory(prefix='iv-dist-') as temporary:
            work = Path(temporary); checkout=work/'source'; checkout.mkdir()
            for name in ('pyproject.toml','README.md','LICENSE','NOTICE','_build.py','MANIFEST.in','Cargo.toml','Cargo.lock','.dockerignore'):
                shutil.copyfile(ROOT/name,checkout/name)
            for name in ('invariant','crates','docker'):
                shutil.copytree(ROOT/name,checkout/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc','_legacy','target'))
            for name in ('scripts/invariant','scripts/guided_demo.py','examples/guided_mvp.py','sdk/python/invariant_client.py'):
                (checkout/name).parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(ROOT/name,checkout/name)
            cover = Path('.github/assets/invariant-cover.png')
            (checkout/cover).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT/cover, checkout/cover)
            (checkout/cover.parent/'readme-cover-unused.png').write_bytes(b'Unselected local draft')

            def command(argv, *, cwd=work, env=None, expected=0, timeout=180):
                result=subprocess.run(argv,cwd=cwd,env=env,capture_output=True,text=True,timeout=timeout)
                if result.returncode!=expected:
                    details=[]
                    for evidence in (work/'project/.invariant/demo').glob('*/run.log'):
                        details.append(evidence.read_text(errors='replace')[-6000:])
                    raise RuntimeError(str(argv)+'\n'+result.stdout[-3000:]+'\n'+result.stderr[-3000:]+'\n'+'\n'.join(details))
                return result

            command([sys.executable,'-c','from setuptools.build_meta import build_sdist; build_sdist("dist")'],cwd=checkout)
            sdist=next((checkout/'dist').glob('*.tar.gz'))
            command([sys.executable,'-m','pip','wheel','--no-deps','--no-index','--no-build-isolation','--wheel-dir',str(work/'wheels'),str(sdist)])
            wheel=next((work/'wheels').glob('*.whl'))
            # Keep reviewable release files alongside the report.
            for artifact in (sdist,wheel):
                shutil.copyfile(artifact,args.report.parent/artifact.name)
                report['artifacts'].append({'name':artifact.name,'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()})
            verify_readme(sdist, wheel, checkout)
            venv.EnvBuilder(with_pip=True).create(work/'venv')
            binary=work/'venv/bin'; python=binary/'python'; cli=binary/'invariant'
            command([str(python),'-m','pip','install','--no-index','--no-deps',str(wheel)])
            env=os.environ.copy();env.pop('PYTHONPATH',None);env['PATH']=str(binary)+os.pathsep+env.get('PATH','')
            project=work/'project';project.mkdir()
            version=command([str(cli),'--version'],cwd=project,env=env).stdout.strip()
            expected_version = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
            assert version=='invariant '+expected_version,version
            for argv in (['--help'],['experiment','--help'],['experiment','demo','--help'],['demo','--help']):
                command([str(cli),*argv],cwd=project,env=env)
            command([str(python),'-I','-c',
                'from invariant.distribution import legacy_root; r=legacy_root(); '
                'assert "site-packages" in str(r); '
                'assert all((r/p).is_file() for p in ["Cargo.toml","crates/invariant-core/README.md","docker/seccomp.json","scripts/guided_demo.py","sdk/python/invariant_client.py"])'],cwd=project,env=env)
            rejected=command([str(cli),'acceptance'],cwd=project,env=env,expected=1)
            assert 'source checkout' in rejected.stderr and 'Traceback' not in rejected.stderr
            command([str(cli),'init'],cwd=project,env=env)
            command([str(cli),'config','--json'],cwd=project,env=env)
            command([str(cli),'chats','--json'],cwd=project,env=env)
            command([str(cli),'experiment','demo',str(work/'demo'),'--json'],cwd=project,env=env)
            for language in ('python','javascript'):
                destination=work/(language+'-example')
                command([str(cli),'experiment','example',language,str(destination),'--json'],cwd=project,env=env)
                assert (destination/'task.md').is_file() and (destination/'tests').is_dir()
                if language=='javascript':assert (destination/'package-lock.json').is_file()
            doctor=subprocess.run([str(cli),'doctor','--json'],cwd=work,env=env,capture_output=True,text=True,timeout=30)
            assert doctor.returncode in (0,1) and json.loads(doctor.stdout)['ok'],doctor.stderr
            report['checks']=['sdist builds a complete wheel',
                'package description uses HTTPS images and sdist includes only the selected cover',
                'fresh installed version/help and project commands',
                'legacy source resources and examples are installed','maintainer command fails clearly without checkout',
                'installed native demo scaffolds without provider calls',
                'packaged Python/JavaScript examples generate outside a checkout','installed diagnostics produce a safe JSON report']
            if args.docker:
                command([str(cli),'prepare'],cwd=project,env=env,timeout=1200)
                command([str(cli),'demo','--auto'],cwd=project,env=env,timeout=600)
                evidence=list((project/'.invariant/demo').glob('*/demo.json'))
                assert len(evidence)==1 and json.loads(evidence[0].read_text())['passed'], 'Installed demo evidence did not verify'
                report['checks'].append('installed legacy demo prepares images and verifies real Docker effects')
            report['passed']=True
    except (OSError,ValueError,AssertionError,RuntimeError,subprocess.SubprocessError) as error:
        report['error']=str(error)
    args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
