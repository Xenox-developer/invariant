import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from invariant.diagnostics import report


class DiagnosticTests(unittest.TestCase):
    def test_only_allowlisted_readiness_is_shared(self):
        state={'supported':True,'ready':False,'docker':{'status':'unavailable','path':'/PRIVATE_SECRET/docker','message':'SECRET_OUTPUT'},
               'codex':{'status':'incompatible','path':'/PRIVATE_SECRET/codex','version':'SECRET_OUTPUT'}}
        with tempfile.TemporaryDirectory() as folder,patch('invariant.dependencies.inspect_dependencies',return_value=state):
            value=report(Path(folder))
        self.assertNotIn('PRIVATE_SECRET',json.dumps(value));self.assertNotIn('SECRET_OUTPUT',json.dumps(value))
        self.assertFalse(value['ready']);self.assertIsNone(value['tools']['codex']['version'])
        self.assertEqual({i['code'] for i in value['issues']},{'DOCKER_UNAVAILABLE','CODEX_UNAVAILABLE'})
