"""Build-time download of exact official packages with pinned integrity."""
import base64
import hashlib
import json
import pathlib
import sys
import tarfile
import tempfile
import urllib.request

ARCHES = {
    "arm64": ("arm64", "aarch64", "IH2/R+4OarjGvsAf1ll3fdyLwrPgYLl3uzp6u9CyD9sj2Lw+ViDSe3QBZVHdcHJs0eIiZFg6Wprcuy4CRZaUGw=="),
    "amd64": ("x64", "x86_64", "uwRaOsrYogLQhcxAM/GoeWrw0QIWEMRo7c9gM8sKAviVkZQHlu9kRLJ5PkufbV+ABGjyZgIwiFdJ0LCJ0N6e8w=="),
}
arch, triple, integrity = ARCHES[sys.argv[1]]
url = f"https://registry.npmjs.org/@openai/codex/-/codex-0.163.0-linux-{arch}.tgz"
with tempfile.TemporaryFile() as package:
    digest, count = hashlib.sha512(), 0
    with urllib.request.urlopen(url, timeout=120) as response:
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > 512 * 1024 * 1024:
                raise SystemExit("Codex package exceeds build download limit")
            digest.update(chunk)
            package.write(chunk)
    if base64.b64encode(digest.digest()).decode() != integrity:
        raise SystemExit("Codex package integrity mismatch")
    package.seek(0)
    with tarfile.open(fileobj=package, mode="r:gz") as archive:
        metadata = archive.extractfile('package/package.json').read()
        if json.loads(metadata).get('license') != 'Apache-2.0':
            raise SystemExit('Codex package license changed; review before distribution')
        notices = pathlib.Path('/usr/local/share/invariant/codex-notices')
        notices.mkdir(parents=True, exist_ok=True)
        (notices/'package.json').write_bytes(metadata)
        (notices/'LICENSE').write_bytes(pathlib.Path(__file__).with_name('codex-package-LICENSE').read_bytes())
        prefix = f'package/vendor/{triple}-unknown-linux-musl/codex-resources/'
        for member in archive.getmembers():
            if member.isfile() and member.name.startswith(prefix) and any(word in member.name.lower() for word in ('license', 'notice')):
                relative = pathlib.PurePosixPath(member.name[len(prefix):])
                if relative.is_absolute() or '..' in relative.parts or member.size > 1024*1024:
                    raise SystemExit('Unsafe Codex notice')
                target = notices/'resources'/str(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
        name = f"package/vendor/{triple}-unknown-linux-musl/bin/codex"
        binary = archive.extractfile(name)
        if binary is None:
            raise SystemExit("Codex binary missing")
        path = pathlib.Path("/usr/local/bin/codex")
        path.write_bytes(binary.read())
        path.chmod(0o755)
