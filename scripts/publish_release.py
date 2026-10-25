#!/usr/bin/env python3
"""Build, retain and publish the exact checked artifacts of a GitHub release."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from urllib.error import HTTPError
from urllib.request import Request, urlopen

CRATES = ('invariant-workspace-core', 'invariant-broker')
CARGO = ['cargo', '+1.90.0']
AGENT = 'invariant-release (https://github.com/Xenox-developer/invariant)'


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def request_json(url, **kwargs):
    headers = {'User-Agent': AGENT, **kwargs.pop('headers', {})}
    try:
        with urlopen(Request(url, headers=headers, **kwargs), timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise RuntimeError(f'Registry request failed: HTTP {error.code}') from None


def registry(package):
    name, version = package['name'], package['version']
    if package['kind'] == 'rust':
        data = request_json(f'https://crates.io/api/v1/crates/{name}/{version}')
        if data and data['version']['yanked']:
            raise ValueError(f'{name} {version} is yanked; choose a new version')
        return data
    return request_json(f'https://pypi.org/pypi/{name}/{version}/json')


def output(**values):
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
            for key, value in values.items():
                stream.write(f'{key}={str(value).lower() if isinstance(value, bool) else value}\n')


def make_plan(source, tag, dry_run):
    match = re.fullmatch(r'(rust-)?v(\d+\.\d+\.\d+)', tag)
    if not match:
        raise ValueError('Expected vMAJOR.MINOR.PATCH or rust-vMAJOR.MINOR.PATCH')
    python = tomllib.loads((source/'pyproject.toml').read_text())['project']
    cargo = tomllib.loads((source/'Cargo.toml').read_text())['workspace']['package']
    broker = tomllib.loads((source/'crates/invariant-broker/Cargo.toml').read_text())
    version = cargo['version']
    if python['name'] != 'invariant-workspace' or any(
            not re.fullmatch(r'\d+\.\d+\.\d+', v) for v in (version, python['version'])):
        raise ValueError('Unexpected project name or unsupported package version')
    if broker['dependencies']['invariant-core'].get('version') != version:
        raise ValueError('Update the broker core dependency to the workspace Rust version')
    selected_python = match[1] is None
    if match[2] != (python['version'] if selected_python else version):
        raise ValueError('Release tag does not match the selected package version')
    module = ast.parse((source/'invariant/__init__.py').read_text())
    cli_version = next(ast.literal_eval(node.value) for node in module.body
                       if isinstance(node, ast.Assign) and any(
                           isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets))
    if cli_version != python['version']:
        raise ValueError('pyproject.toml and invariant.__version__ must agree')
    packages = [{'kind': 'rust', 'name': name, 'version': version} for name in CRATES]
    if selected_python:
        packages.append({'kind': 'python', 'name': python['name'], 'version': python['version']})
    for package in packages:
        existing = registry(package)
        package['pending'] = existing is None
        if package['kind'] == 'python' and existing is not None:
            names = {Path(name).name for name in allowed_files({'packages': [package]})}
            package['pending'] = not names <= {item['filename'] for item in existing['urls']}
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    plan = {'tag': tag, 'source_sha': sha, 'dry_run': dry_run, 'packages': packages}
    output(source_sha=sha, python_version=python['version'], rust_version=version,
           has_python=selected_python, need_python=any(p['kind'] == 'python' and p['pending'] for p in packages),
           need_rust=any(p['kind'] == 'rust' and p['pending'] for p in packages))
    return plan


def allowed_files(plan):
    files = set()
    for p in plan['packages']:
        if p['kind'] == 'rust':
            files.add(f"rust/{p['name']}-{p['version']}.crate")
        else:
            stem = f"invariant_workspace-{p['version']}"
            files.update((f'python/{stem}-py3-none-any.whl', f'python/{stem}.tar.gz'))
    return files


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_bundle(bundle, plan):
    manifest = json.loads((bundle/'workflow-publication.json').read_text())
    if manifest['source_sha'] != plan['source_sha'] or manifest['tag'] != plan['tag']:
        raise ValueError('Saved artifacts belong to a different release commit')
    files = manifest['files']
    if not set(files) <= allowed_files(plan):
        raise ValueError('Unexpected artifact path in release manifest')
    for name, expected in files.items():
        if not re.fullmatch('[a-f0-9]{64}', expected) or digest(bundle/name) != expected:
            raise ValueError('Artifact checksum mismatch: '+name)
    for package in plan['packages']:
        if package['pending']:
            required = {name for name in allowed_files({'packages': [package]})}
            if not required <= set(files):
                raise ValueError('Saved artifacts do not include the pending package')
    return manifest


def release_info(tag, missing_ok=False):
    repository = os.environ['GH_REPO']
    result = subprocess.run(['gh', 'api', f'repos/{repository}/releases/tags/{tag}'],
                            capture_output=True, text=True)
    if result.returncode:
        if missing_ok and 'HTTP 404' in result.stderr:
            return None
        raise RuntimeError('Cannot read GitHub release: '+result.stderr)
    info = json.loads(result.stdout)
    if info['draft']:
        raise ValueError('Publish the GitHub release before uploading packages')
    return info


def restore_bundle(bundle, plan):
    info = release_info(plan['tag'], missing_ok=plan['dry_run'])
    if not info or not any(a['name'] == 'workflow-publication.json' for a in info['assets']):
        return False
    run(['gh', 'release', 'download', plan['tag'], '--pattern', 'workflow-publication.json', '--dir', bundle])
    manifest = json.loads((bundle/'workflow-publication.json').read_text())
    if manifest['source_sha'] != plan['source_sha'] or manifest['tag'] != plan['tag']:
        raise ValueError('Release tag moved after its artifacts were built')
    if not set(manifest['files']) <= allowed_files(plan):
        raise ValueError('Unexpected artifact path in release manifest')
    for name in manifest['files']:
        folder = bundle/Path(name).parent
        folder.mkdir(exist_ok=True)
        run(['gh', 'release', 'download', plan['tag'], '--pattern', Path(name).name, '--dir', folder])
    validate_bundle(bundle, plan)
    return True


def build(source, bundle, plan):
    bundle.mkdir(parents=True, exist_ok=True)
    if restore_bundle(bundle, plan):
        print('Reusing checked artifacts for the same release commit')
    else:
        checks = {}
        pending_rust = [p['name'] for p in plan['packages'] if p['kind'] == 'rust' and p['pending']]
        selected_rust = list(CRATES) if plan['dry_run'] else pending_rust
        if selected_rust:
            report = source/'.invariant/release-rust/report.json'
            run([sys.executable, source/'scripts/test_rust_library.py', '--toolchain', '1.90.0', '--report', report], cwd=source)
            checked = json.loads(report.read_text())
            if not checked['passed']:
                raise ValueError('Rust distribution verification failed')
            checks['rust'] = checked['checks']
            # Stage a core only when that version will also be uploaded. For an
            # existing core, broker must resolve its actual registry checksum.
            groups = [selected_rust] if selected_rust == pending_rust else [[name] for name in selected_rust]
            for group in groups:
                run(CARGO+['package', '--locked']+[arg for name in group for arg in ('-p', name)], cwd=source)
            (bundle/'rust').mkdir()
            for name in selected_rust:
                version = next(p['version'] for p in plan['packages'] if p['name'] == name)
                archive = f'{name}-{version}.crate'
                shutil.copyfile(source/'target/package'/archive, bundle/'rust'/archive)
        python = next((p for p in plan['packages'] if p['kind'] == 'python'), None)
        if python and (python['pending'] or plan['dry_run']):
            report = source/'.invariant/release-python/report.json'
            run([sys.executable, source/'scripts/test_distribution.py', '--report', report], cwd=source)
            checked = json.loads(report.read_text())
            if not checked['passed']:
                raise ValueError('Python distribution verification failed')
            checks['python'] = checked['checks']
            (bundle/'python').mkdir()
            for item in checked['artifacts']:
                shutil.copyfile(report.parent/item['name'], bundle/'python'/item['name'])
            run([sys.executable, '-m', 'twine', 'check', '--strict', *sorted((bundle/'python').iterdir())])
        files = {str(p.relative_to(bundle)): digest(p) for p in sorted(bundle.rglob('*')) if p.is_file()}
        manifest = {'schema_version': 1, 'tag': plan['tag'], 'source_sha': plan['source_sha'],
                    'files': files, 'checks': checks}
        (bundle/'workflow-publication.json').write_text(json.dumps(manifest, indent=2)+'\n')
    manifest = validate_bundle(bundle, plan)
    checksums = [f'{sha}  {Path(name).name}\n' for name, sha in sorted(manifest['files'].items())]
    checksums.append(f"{digest(bundle/'workflow-publication.json')}  workflow-publication.json\n")
    (bundle/'SHA256SUMS').write_text(''.join(checksums))
    (bundle/'plan.json').write_text(json.dumps(plan, indent=2)+'\n')
    print(json.dumps({'tag': plan['tag'], 'source_sha': plan['source_sha'], 'packages': plan['packages']}, indent=2))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write('Release `'+plan['tag']+'` at `'+plan['source_sha']+'`\n\n')
            summary.write('Dry run: no packages will be uploaded.\n\n' if plan['dry_run'] else '')
            for p in plan['packages']:
                summary.write('- '+p['name']+' '+p['version']+(': pending upload' if p['pending'] else ': already published, skipped')+'\n')


def attach(bundle, plan):
    manifest = validate_bundle(bundle, plan)
    info = release_info(plan['tag'])
    paths = [bundle/name for name in manifest['files']] + [bundle/'workflow-publication.json', bundle/'SHA256SUMS']
    existing = {a['name'] for a in info['assets']}
    missing = []
    for path in paths:
        if path.name in existing:
            with tempfile.TemporaryDirectory() as temporary:
                run(['gh', 'release', 'download', plan['tag'], '--pattern', path.name, '--dir', temporary])
                if digest(Path(temporary)/path.name) != digest(path):
                    raise ValueError('Refusing to replace an existing release asset: '+path.name)
        else:
            missing.append(path)
    if missing:
        regular = [p for p in missing if p.name != 'workflow-publication.json']
        if regular:
            run(['gh', 'release', 'upload', plan['tag'], *regular])
        if bundle/'workflow-publication.json' in missing:
            run(['gh', 'release', 'upload', plan['tag'], bundle/'workflow-publication.json'])


def publish_rust(source, bundle, plan):
    manifest = validate_bundle(bundle, plan)
    for package in (p for p in plan['packages'] if p['kind'] == 'rust' and p['pending']):
        name, version = package['name'], package['version']
        expected = manifest['files'][f'rust/{name}-{version}.crate']
        existing = registry(package)
        if existing:
            if existing['version']['checksum'] != expected:
                raise ValueError('Published Rust version has different bytes: '+name)
            continue
        if plan['dry_run']:
            continue
        # Package with no credentials and compare before the immutable upload.
        env = os.environ.copy(); env.pop('CARGO_REGISTRY_TOKEN', None)
        run(CARGO+['package', '--locked', '--no-verify', '-p', name], cwd=source, env=env)
        if digest(source/'target/package'/f'{name}-{version}.crate') != expected:
            raise ValueError('Cargo repackaged different bytes; refusing to publish '+name)
        run(CARGO+['publish', '--locked', '--no-verify', '-p', name], cwd=source)
        uploaded = registry(package)
        if not uploaded or uploaded['version']['checksum'] != expected:
            raise ValueError('Rust upload did not match the verified artifact: '+name)


def python_pending(bundle, plan):
    manifest = validate_bundle(bundle, plan)
    package = next(p for p in plan['packages'] if p['kind'] == 'python')
    existing = registry(package)
    files = {Path(name).name: sha for name, sha in manifest['files'].items() if name.startswith('python/')}
    published = {} if existing is None else {f['filename']: f['digests']['sha256'] for f in existing['urls']}
    for name, sha in files.items():
        if name in published and published[name] != sha:
            raise ValueError('Published Python file has different bytes: '+name)
    pending = not set(files) <= set(published)
    output(pending=pending)
    return pending


def pypi_auth():
    request_url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']+'&audience=pypi'
    identity = request_json(request_url, headers={
        'Authorization': 'Bearer '+os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    minted = request_json('https://pypi.org/_/oidc/mint-token', method='POST',
                          headers={'Content-Type': 'application/json'},
                          data=json.dumps({'token': identity['value']}).encode())
    if not minted or not minted.get('token'):
        raise ValueError('PyPI did not issue a trusted publishing token')
    print('PyPI trusted publisher authentication: PASS (no upload)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'build', 'attach', 'rust', 'python-status', 'pypi-auth'])
    parser.add_argument('--source', type=Path, default=Path('.'))
    parser.add_argument('--tag')
    parser.add_argument('--plan', type=Path, default=Path('plan.json'))
    parser.add_argument('--bundle', type=Path, default=Path('bundle'))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    source, bundle = args.source.resolve(), args.bundle.resolve()
    if args.command == 'pypi-auth':
        pypi_auth(); return
    if args.command == 'plan':
        plan = make_plan(source, args.tag, args.dry_run)
        args.plan.write_text(json.dumps(plan, indent=2)+'\n'); return
    plan = json.loads(args.plan.read_text())
    if args.command == 'build': build(source, bundle, plan)
    elif args.command == 'attach': attach(bundle, plan)
    elif args.command == 'rust': publish_rust(source, bundle, plan)
    else: python_pending(bundle, plan)


if __name__ == '__main__':
    main()
