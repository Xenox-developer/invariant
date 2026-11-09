"""Unrestricted defaults and explicit restoration of workload restrictions."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from invariant import configuration, guardrails, resources, tasks, workspace
from invariant.policy import NEW_PROJECT_CONFIG, normalize
from invariant.experiments.definitions import capture_limits
from invariant.experiments.files import capture_workspace
from invariant.experiments.runners import BoundedLog
from invariant.experiments.runtime import allocations


class UnrestrictedTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve();self.project=self.root/'project';self.project.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):workspace.init(self.project)

    def test_new_environment_has_no_quotas_or_deadline_and_keeps_host_closed(self):
        policy=workspace.load_project(self.project)
        self.assertFalse(policy['restrictions_enabled'])
        self.assertFalse(policy['host_access'])
        self.assertEqual(policy['duration_seconds'],0)
        selected,plan=resources.select(policy,self.project,{'cpus':1,'memory_bytes':64*1024**2})
        self.assertEqual(plan['mode'],'unlimited')
        contract=resources.execution_contract(selected)
        self.assertTrue(all(v is None for v in contract['limits'].values()))
        self.assertFalse(any(a.startswith(('--cpus','--memory','--ulimit','--tmpfs')) for a in contract['docker_args']))
        command=workspace.container_command('owned',self.project,'sha256:image')
        self.assertNotIn('--privileged',command)
        self.assertIn('--network=bridge',command)
        self.assertIn('--cap-drop=ALL',command)
        self.assertIn('--security-opt=no-new-privileges',command)
        self.assertFalse(any('/host-home' in arg or '/var/run/docker.sock' in arg for arg in command))

    def test_restrictions_preset_is_reversible_and_individual_limits_remain_available(self):
        policy=configuration.set_value(self.project,'restrictions_enabled','true')
        self.assertTrue(policy['restrictions_enabled']);self.assertFalse(policy['host_access'])
        self.assertEqual(policy['resource_mode'],'auto')
        self.assertEqual(policy['command_network'],'none')
        self.assertEqual(policy['duration_seconds'],3600)
        command=workspace.container_command('owned',self.project,'sha256:image')
        self.assertNotIn('--privileged',command)
        self.assertIn('--security-opt=no-new-privileges',command)
        limited=configuration.set_value(self.project,'limits.memory_mib','2048')
        self.assertEqual(limited['resource_mode'],'manual')
        self.assertEqual(resources.resource_limits(limited)['memory_bytes'],2048*1024**2)
        policy=configuration.set_value(self.project,'restrictions_enabled','false')
        self.assertEqual(policy,normalize(NEW_PROJECT_CONFIG))

    def test_unlimited_contract_verifies_zero_quotas_and_rejects_resource_drift(self):
        contract=resources.execution_contract(NEW_PROJECT_CONFIG)
        host={**contract['host_config'],'PidsLimit':-1,'Ulimits':None,'Tmpfs':None}
        self.assertTrue(resources.verify_contract({'HostConfig':host},contract))
        for changes in ({'Memory':1024},{'PidsLimit':128},{'NanoCpus':1000}):
            with self.subTest(changes=changes),self.assertRaises(RuntimeError):
                resources.verify_contract({'HostConfig':{**host,**changes}},contract)

    def test_host_mounts_follow_the_active_docker_endpoint_and_can_be_disabled(self):
        policy=normalize(NEW_PROJECT_CONFIG)
        with patch.dict('os.environ',{'DOCKER_HOST':'unix:///custom/docker.sock','DOCKER_CONTEXT':''}),patch.object(Path,'is_socket',return_value=True),patch.object(workspace.sys,'platform','linux'):
            mounts=workspace.host_mounts(policy)
        self.assertEqual(mounts,())
        self.assertEqual(workspace.host_mounts({**policy,'host_access':False}),())

    def test_tool_profile_is_independent_of_unlimited_resources(self):
        home=self.root/'codex';home.mkdir()
        with patch.object(workspace,'find_docker',return_value='/docker'):
            command,_=workspace.codex_configuration(home,'owned','/codex',policy=normalize(NEW_PROJECT_CONFIG),coding_profile=True)
        self.assertTrue(any('features.' in arg and '=false' in arg for arg in command))
        self.assertIn('web_search = "disabled"',(home/'config.toml').read_text())
        self.assertEqual(normalize(NEW_PROJECT_CONFIG)['resource_mode'],'unlimited')
        with patch.object(workspace,'find_docker',return_value='/docker'):
            command,_=workspace.codex_configuration(home,'owned','/codex',policy={**normalize(NEW_PROJECT_CONFIG),'tool_profile':'full'},coding_profile=True)
        self.assertFalse(any('features.' in arg and '=false' in arg for arg in command))
        self.assertIn('web_search = "live"',(home/'config.toml').read_text())

    def test_unlimited_capture_and_logs_preserve_complete_output(self):
        limits=capture_limits(NEW_PROJECT_CONFIG)
        self.assertIsNone(limits['entries']);self.assertIsNone(limits['source_bytes'])
        (self.project/'data').write_bytes(b'x'*2048)
        captured=capture_workspace(self.project,self.root/'capture',limits)
        self.assertEqual(captured['source_bytes'],2048)
        log=BoundedLog(self.root/'log',None)
        log.write(b'y'*2048);log.close()
        self.assertFalse(log.truncated);self.assertEqual((self.root/'log').stat().st_size,2048)

    def test_task_and_shared_tests_have_no_default_deadline(self):
        options=tasks.options({'task':'task','test_command':'true','test_paths':['test.py']})
        self.assertEqual(options['preparation_timeout'],0);self.assertEqual(options['test_timeout'],0)
        allocation=allocations({'execution':{'max_parallel':10}}, {'policy':normalize(NEW_PROJECT_CONFIG)})
        self.assertEqual(allocation['max_parallel'],10)
        self.assertIsNone(allocation['trial_cpus']);self.assertEqual(allocation['trial_timeout_seconds'],0)
        helper=runpy.run_path(str(workspace.ASSETS/'experiment-check.py'))
        code,reason,_=helper['run']('printf completed',self.project,self.root/'test.log',0,None)
        self.assertEqual(code,0);self.assertIsNone(reason)
        self.assertEqual((self.root/'test.log').read_text(),'completed')


if __name__=='__main__':unittest.main()
