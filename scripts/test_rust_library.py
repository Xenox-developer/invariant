#!/usr/bin/env python3
"""Package the Rust libraries and verify a consumer outside the source checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ('invariant-workspace-core', 'invariant-broker')
SOURCE_DIRECTORIES = {'invariant-workspace-core': 'invariant-core', 'invariant-broker': 'invariant-broker'}


def source_hashes():
    files = [ROOT/name for name in ('Cargo.toml', 'Cargo.lock', 'LICENSE', 'NOTICE')]
    files += [Path(__file__).resolve()]
    for folder in ('crates', 'examples/rust-embedding'):
        files += [p for p in (ROOT/folder).rglob('*')
                  if p.is_file() and 'target' not in p.relative_to(ROOT).parts]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)}


def unpack(archive, destination, expected):
    """Extract only bounded ordinary package files, never archive links."""
    total = 0
    with tarfile.open(archive, 'r:gz') as package:
        for member in package:
            relative = PurePosixPath(member.name)
            if (relative.is_absolute() or '..' in relative.parts or not relative.parts
                    or relative.parts[0] != expected or '\\' in member.name):
                raise ValueError('Unsafe package path: '+member.name)
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                total += member.size
                if member.size > 16*1024**2 or total > 64*1024**2:
                    raise ValueError('Package exceeds verification limits')
                target.parent.mkdir(parents=True, exist_ok=True)
                with package.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
            else:
                raise ValueError('Package contains a link or special file: '+member.name)
    return destination/expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain', help='installed Rust toolchain, for example 1.90.0')
    parser.add_argument('--offline', action='store_true', help='require cached third-party dependencies')
    parser.add_argument('--report', type=Path, default=ROOT/'.invariant/rust-library-checks/report.json')
    args = parser.parse_args()
    report_path = args.report.expanduser().absolute()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = report_path.with_suffix('.log')
    report = {'schema_version': 1, 'passed': False, 'checks': [], 'commands': [],
              'source_sha256': source_hashes(), 'archives': [],
              'host': {'system': platform.system(), 'machine': platform.machine()}}
    cargo = ['cargo'] + (['+'+args.toolchain] if args.toolchain else [])
    flags = ['--offline'] if args.offline else []

    try:
        report['cargo_version'] = subprocess.check_output(cargo+['--version'], text=True, timeout=30).strip()
        rustc = ['rustc'] + (['+'+args.toolchain] if args.toolchain else [])
        report['rustc_version'] = subprocess.check_output(rustc+['--version'], text=True, timeout=30).strip()
        with log_path.open('w') as log, tempfile.TemporaryDirectory(prefix='iv-rust-') as temporary:
            work = Path(temporary)
            env = os.environ.copy()
            env['CARGO_TARGET_DIR'] = str(work/'target')

            def command(arguments, *, cwd):
                separator = arguments.index('--') if '--' in arguments else len(arguments)
                invocation = cargo + arguments[:separator] + flags + arguments[separator:]
                print('Checking: '+' '.join(arguments[:4]), flush=True)
                log.write(json.dumps(invocation)+'\n'); log.flush()
                result = subprocess.run(invocation, cwd=cwd, env=env, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=300)
                report['commands'].append({'arguments': arguments, 'exit_code': result.returncode})
                if result.returncode:
                    raise RuntimeError('Rust verification failed; see '+str(log_path))

            # A source export prevents Git state or sibling Python files from making
            # packaging pass. No user workspace, image or provider account is touched.
            checkout = work/'checkout'; checkout.mkdir()
            for name in ('Cargo.toml', 'Cargo.lock'):
                shutil.copyfile(ROOT/name, checkout/name)
            shutil.copytree(ROOT/'crates', checkout/'crates')
            command(['package', '--locked', '-p', PACKAGES[0], '-p', PACKAGES[1]], cwd=checkout)
            report['checks'].append('both libraries package and verify from a source export')

            version = tomllib.loads((checkout/'Cargo.toml').read_text())['workspace']['package']['version']
            packages = work/'packages'; packages.mkdir()
            extracted = {}
            for name in PACKAGES:
                archive = work/'target/package'/f'{name}-{version}.crate'
                saved = report_path.parent/'packages'; saved.mkdir(exist_ok=True)
                shutil.copyfile(archive, saved/archive.name)
                folder = unpack(archive, packages, f'{name}-{version}')
                extracted[name] = folder
                report['archives'].append({'name': archive.name,
                    'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'bytes': archive.stat().st_size})
                for notice in ('LICENSE', 'NOTICE'):
                    if (folder/notice).read_bytes() != (ROOT/notice).read_bytes():
                        raise ValueError(name+' has missing or outdated '+notice)
                manifest = tomllib.loads((folder/'Cargo.toml').read_text())
                if 'workspace' in manifest or (folder/'README.md').read_bytes() != (ROOT/'crates'/SOURCE_DIRECTORIES[name]/'README.md').read_bytes():
                    raise ValueError('Package still depends on workspace metadata or has a stale README')
            report['checks'].append('archives contain standalone manifests, current READMEs and original notices')

            broker = extracted['invariant-broker']
            if (broker/'tests/unix.rs').exists() or (broker/'examples/python.rs').exists():
                raise ValueError('Checkout-only Python integrations leaked into the Rust package')

            # Patch the normalized registry dependency independently of registry availability.
            # Use this exact extracted core without editing either packaged manifest.
            patch = ['--config', 'patch.crates-io.invariant-workspace-core.path='+json.dumps(str(extracted['invariant-workspace-core']))]
            for name in PACKAGES:
                command(['generate-lockfile', *patch], cwd=extracted[name])
                command(['test', '--locked', '--all-targets', *patch], cwd=extracted[name])
                command(['test', '--locked', '--doc', *patch], cwd=extracted[name])
            report['checks'].append('packaged Rust tests, examples and README doctests pass without the checkout SDK')

            consumer = work/'consumer'; consumer.mkdir()
            shutil.copytree(ROOT/'examples/rust-embedding/src', consumer/'src')
            # Only dependency locations change from the published example.
            manifest = tomllib.loads((ROOT/'examples/rust-embedding/Cargo.toml').read_text())
            text = ['[package]']
            for key in ('name', 'version', 'edition', 'rust-version'):
                text.append(key+' = '+json.dumps(manifest['package'][key]))
            text += ['publish = false', '[workspace]', '[dependencies]']
            text += [('invariant-core' if name == 'invariant-workspace-core' else name)+' = { package = '+json.dumps(name)+', path = '+json.dumps(str(extracted[name]))+' }' for name in PACKAGES]
            text += ['[patch.crates-io]', 'invariant-workspace-core = { path = '+json.dumps(str(extracted['invariant-workspace-core']))+' }']
            (consumer/'Cargo.toml').write_text('\n'.join(text)+'\n')
            command(['generate-lockfile'], cwd=consumer)
            command(['clippy', '--locked', '--all-targets', '--', '-D', 'warnings'], cwd=consumer)
            command(['run', '--locked'], cwd=consumer)
            report['checks'].append('independent consumer verifies HTTP, replay, scope, revocation, audit and Unix transport')
            report['checks'].append('direct invariant_core and broker::core types remain interchangeable')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as error:
        report['error'] = str(error)
    report['source_unchanged'] = report['source_sha256'] == source_hashes()
    report['passed'] = 'error' not in report and report['source_unchanged']
    report_path.write_text(json.dumps(report, indent=2)+'\n')
    print('Rust library verification: '+('PASS' if report['passed'] else 'FAIL'))
    print('Report: '+str(report_path))
    if not report['passed']:
        print(log_path.read_text()[-6000:])
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
