import unittest
from unittest.mock import patch
from pathlib import Path
import runpy

from invariant.codex_rpc import CodexRpc
NoProviderRpc=runpy.run_path(str(Path(__file__).resolve().parents[1]/'fixture_cli.py'))['NoProviderRpc']


class FixtureBoundaryTests(unittest.TestCase):
    def test_fixture_forbids_provider_turns_and_login_but_keeps_real_rpc(self):
        rpc=object.__new__(NoProviderRpc)
        with patch.object(CodexRpc,'request',return_value={'data':[]}) as real:
            self.assertEqual(rpc.request('model/list',{}),{'data':[]})
            for method in ('turn/start','account/login/start'):
                with self.assertRaisesRegex(AssertionError,'Provider requests are forbidden'):
                    rpc.request(method,{})
            real.assert_called_once_with('model/list',{})
