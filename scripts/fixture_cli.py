#!/usr/bin/env python3
"""Credential-free CLI fixture: real Codex/executor, no authentication or turns.

Production entrypoints never import this module. Live acceptance uses the real
entrypoint and verifies authentication separately.
"""
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from invariant import cli, workspace
from invariant.codex_rpc import CodexRpc


class NoProviderRpc(CodexRpc):
    def request(self, method, params=None, **kwargs):
        if method == 'turn/start' or method.startswith('account/login/'):
            raise AssertionError('Provider requests are forbidden in fixture-only checks')
        return super().request(method, params, **kwargs)


def main():
    # Do not reuse the developer's account even when fixtures run locally.
    with tempfile.TemporaryDirectory(prefix='invariant-no-provider-') as home:
        os.environ['CODEX_HOME'] = home
        workspace.authenticate = lambda rpc: None
        workspace.CodexRpc = NoProviderRpc
        cli.entrypoint()


if __name__ == '__main__':
    main()
