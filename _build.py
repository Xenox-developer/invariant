"""Bundle the source inputs needed by the legacy Docker launcher, without host state."""
from pathlib import Path
import shutil

from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def run(self):
        super().run()
        root = Path(__file__).resolve().parent
        destination = Path(self.build_lib)/'invariant/_legacy'
        if destination.exists():
            shutil.rmtree(destination)
        files = [root/name for name in ('Cargo.toml', 'Cargo.lock', '.dockerignore', 'LICENSE', 'NOTICE',
                 'scripts/invariant', 'scripts/guided_demo.py', 'examples/guided_mvp.py',
                 'sdk/python/invariant_client.py')]
        for folder in ('crates', 'docker'):
            files += [p for p in (root/folder).rglob('*') if p.is_file() and
                      not any(part in ('target', '__pycache__', '.git') for part in p.relative_to(root).parts)]
        for source in files:
            target = destination/source.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
