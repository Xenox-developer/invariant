import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location('publish_release', Path(__file__).resolve().parents[1]/'publish_release.py')
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)


class PublishReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root/'source'; self.source.mkdir()
        (self.source/'crates/invariant-broker').mkdir(parents=True)
        (self.source/'invariant').mkdir()
        (self.source/'pyproject.toml').write_text('[project]\nname="invariant-workspace"\nversion="0.4.0"\n')
        (self.source/'invariant/__init__.py').write_text('__version__="0.4.0"\n')
        (self.source/'Cargo.toml').write_text('[workspace.package]\nversion="0.2.0"\n')
        (self.source/'crates/invariant-broker/Cargo.toml').write_text('[dependencies]\ninvariant-core={version="0.2.0"}\n')
        self.output = patch.object(publish, 'output'); self.output.start(); self.addCleanup(self.output.stop)
        self.plan = {'tag': 'v0.4.0', 'source_sha': 'a'*40, 'dry_run': False, 'packages': [
            {'kind': 'rust', 'name': n, 'version': '0.2.0', 'pending': True} for n in publish.CRATES] + [
            {'kind': 'python', 'name': 'invariant-workspace', 'version': '0.4.0', 'pending': True}]}
        self.bundle = self.root/'bundle'; self.bundle.mkdir()
        files = {}
        for name in publish.allowed_files(self.plan):
            target = self.bundle/name; target.parent.mkdir(exist_ok=True)
            target.write_bytes(('verified '+name).encode()); files[name] = publish.digest(target)
        self.manifest = {'source_sha': 'a'*40, 'tag': 'v0.4.0', 'files': files}
        self.save_manifest()

    def save_manifest(self):
        (self.bundle/'workflow-publication.json').write_text(json.dumps(self.manifest))

    def make_plan(self, tag):
        with patch.object(publish, 'registry', return_value=None), patch.object(
                publish.subprocess, 'check_output', return_value='a'*40):
            return publish.make_plan(self.source, tag, False)

    def test_application_tag_includes_both_rust_crates_and_python(self):
        self.assertEqual([p['name'] for p in self.make_plan('v0.4.0')['packages']],
                         [*publish.CRATES, 'invariant-workspace'])

    def test_rust_tag_does_not_publish_python(self):
        self.assertEqual([p['kind'] for p in self.make_plan('rust-v0.2.0')['packages']], ['rust', 'rust'])

    def test_invalid_tags_and_version_mismatches_fail_before_registry_access(self):
        for tag in ('main', 'v0.4.0;echo bad', '../../v0.4.0', 'v0.3.0', 'rust-v0.1.0'):
            with self.subTest(tag=tag), patch.object(publish, 'registry') as registry:
                with self.assertRaises(ValueError): publish.make_plan(self.source, tag, False)
                registry.assert_not_called()

    def test_cli_version_and_broker_dependency_must_be_bumped(self):
        (self.source/'invariant/__init__.py').write_text('__version__="0.3.1"\n')
        with self.assertRaisesRegex(ValueError, '__version__'): self.make_plan('v0.4.0')
        (self.source/'invariant/__init__.py').write_text('__version__="0.4.0"\n')
        (self.source/'crates/invariant-broker/Cargo.toml').write_text('[dependencies]\ninvariant-core={version="0.1.0"}\n')
        with self.assertRaisesRegex(ValueError, 'broker'): self.make_plan('v0.4.0')

    def test_existing_versions_are_not_scheduled_for_upload(self):
        with patch.object(publish, 'registry', side_effect=[{}, {}, {'urls': [
                {'filename': 'invariant_workspace-0.4.0-py3-none-any.whl'},
                {'filename': 'invariant_workspace-0.4.0.tar.gz'}]}]), patch.object(
                publish.subprocess, 'check_output', return_value='a'*40):
            plan = publish.make_plan(self.source, 'v0.4.0', False)
        self.assertFalse(any(p['pending'] for p in plan['packages']))

    def test_plan_resumes_python_version_with_only_one_uploaded_file(self):
        with patch.object(publish, 'registry', side_effect=[{}, {}, {'urls': [
                {'filename': 'invariant_workspace-0.4.0-py3-none-any.whl'}]}]), patch.object(
                publish.subprocess, 'check_output', return_value='a'*40):
            plan = publish.make_plan(self.source, 'v0.4.0', False)
        self.assertTrue(plan['packages'][-1]['pending'])

    def test_registry_outage_is_not_treated_as_unpublished(self):
        for status in (403, 429, 500):
            with patch.object(publish, 'urlopen', side_effect=HTTPError('https://registry/', status, 'failure', {}, None)):
                with self.assertRaises(RuntimeError): publish.request_json('https://registry/')
        with patch.object(publish, 'urlopen', side_effect=HTTPError('https://registry/', 404, 'missing', {}, None)):
            self.assertIsNone(publish.request_json('https://registry/'))

    def test_bundle_rejects_tampering_paths_and_moved_tags(self):
        self.assertEqual(publish.validate_bundle(self.bundle, self.plan), self.manifest)
        name = next(iter(self.manifest['files']))
        (self.bundle/name).write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'checksum'): publish.validate_bundle(self.bundle, self.plan)
        self.manifest['files'] = {'../../credential': 'f'*64}; self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'path'): publish.validate_bundle(self.bundle, self.plan)
        self.manifest['source_sha'] = 'b'*40; self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'different release'): publish.validate_bundle(self.bundle, self.plan)

    def test_core_is_published_before_broker_and_packaging_has_no_token(self):
        def run(args, **kwargs):
            if args[2] == 'package':
                self.assertNotIn('CARGO_REGISTRY_TOKEN', kwargs['env'])
                name = args[-1]; target = self.source/'target/package'/f'{name}-0.2.0.crate'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((self.bundle/'rust'/target.name).read_bytes())
        hashes = [self.manifest['files'][f'rust/{name}-0.2.0.crate'] for name in publish.CRATES]
        with patch.dict(os.environ, {'CARGO_REGISTRY_TOKEN': 'test-secret'}), patch.object(
                publish, 'run', side_effect=run) as command, patch.object(publish, 'registry', side_effect=[
                    None, {'version': {'checksum': hashes[0]}}, None, {'version': {'checksum': hashes[1]}}]):
            publish.publish_rust(self.source, self.bundle, self.plan)
        uploads = [call.args[0][-1] for call in command.call_args_list if call.args[0][2] == 'publish']
        self.assertEqual(uploads, list(publish.CRATES))

    def test_repackaged_different_bytes_cannot_be_uploaded(self):
        def run(args, **kwargs):
            if args[2] == 'package':
                target = self.source/'target/package'/f'{args[-1]}-0.2.0.crate'
                target.parent.mkdir(parents=True); target.write_bytes(b'different')
        with patch.object(publish, 'registry', return_value=None), patch.object(publish, 'run', side_effect=run) as command:
            with self.assertRaisesRegex(ValueError, 'different bytes'):
                publish.publish_rust(self.source, self.bundle, self.plan)
        self.assertFalse(any(call.args[0][2] == 'publish' for call in command.call_args_list))

    def test_rust_partial_upload_resumes_only_matching_bytes(self):
        hashes = [self.manifest['files'][f'rust/{name}-0.2.0.crate'] for name in publish.CRATES]
        with patch.object(publish, 'registry', side_effect=[{'version': {'checksum': h}} for h in hashes]), patch.object(publish, 'run') as command:
            publish.publish_rust(self.source, self.bundle, self.plan)
            command.assert_not_called()
        with patch.object(publish, 'registry', return_value={'version': {'checksum': 'f'*64}}):
            with self.assertRaisesRegex(ValueError, 'different bytes'):
                publish.publish_rust(self.source, self.bundle, self.plan)

    def test_python_partial_upload_requires_matching_existing_files(self):
        files = [(Path(name).name, sha) for name, sha in self.manifest['files'].items() if name.startswith('python/')]
        with patch.object(publish, 'registry', return_value={'urls': [
                {'filename': files[0][0], 'digests': {'sha256': files[0][1]}}]}):
            self.assertTrue(publish.python_pending(self.bundle, self.plan))
        with patch.object(publish, 'registry', return_value={'urls': [
                {'filename': n, 'digests': {'sha256': h}} for n, h in files]}):
            self.assertFalse(publish.python_pending(self.bundle, self.plan))
        with patch.object(publish, 'registry', return_value={'urls': [
                {'filename': files[0][0], 'digests': {'sha256': 'f'*64}}]}):
            with self.assertRaisesRegex(ValueError, 'different bytes'): publish.python_pending(self.bundle, self.plan)

    def test_restored_bundle_is_reused_without_rebuilding(self):
        before = (self.bundle/'workflow-publication.json').read_bytes()
        with patch.object(publish, 'restore_bundle', return_value=True), patch.object(publish, 'run') as command:
            with contextlib.redirect_stdout(io.StringIO()):
                publish.build(self.source, self.bundle, self.plan)
            command.assert_not_called()
        self.assertEqual((self.bundle/'workflow-publication.json').read_bytes(), before)
        self.assertTrue((self.bundle/'SHA256SUMS').is_file())
        self.assertEqual(json.loads((self.bundle/'plan.json').read_text()), self.plan)

    def test_existing_github_asset_with_other_bytes_is_never_overwritten(self):
        (self.bundle/'SHA256SUMS').write_text('checksums')
        name = next(iter(self.manifest['files']))
        def download(args, **kwargs):
            (Path(args[-1])/Path(name).name).write_bytes(b'conflicting existing asset')
        with patch.object(publish, 'release_info', return_value={'assets': [{'name': Path(name).name}]}), patch.object(
                publish, 'run', side_effect=download) as command:
            with self.assertRaisesRegex(ValueError, 'Refusing to replace'):
                publish.attach(self.bundle, self.plan)
        self.assertFalse(any('upload' in call.args[0] for call in command.call_args_list))

    def test_release_manifest_is_attached_after_all_distribution_files(self):
        (self.bundle/'SHA256SUMS').write_text('checksums')
        with patch.object(publish, 'release_info', return_value={'assets': []}), patch.object(publish, 'run') as command:
            publish.attach(self.bundle, self.plan)
        self.assertEqual(command.call_args_list[-1].args[0][-1], self.bundle/'workflow-publication.json')
        self.assertNotIn(self.bundle/'workflow-publication.json', command.call_args_list[0].args[0])

    def test_dry_run_never_invokes_cargo_upload(self):
        self.plan['dry_run'] = True
        with patch.object(publish, 'registry', return_value=None), patch.object(publish, 'run') as command:
            publish.publish_rust(self.source, self.bundle, self.plan)
            command.assert_not_called()

    def test_pypi_authentication_does_not_print_or_use_token_to_upload(self):
        with patch.dict(os.environ, {'ACTIONS_ID_TOKEN_REQUEST_URL': 'https://github/?x=1',
                                   'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'github-secret'}), patch.object(
                publish, 'request_json', side_effect=[{'value': 'oidc-secret'}, {'token': 'pypi-secret'}]) as request:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout): publish.pypi_auth()
        self.assertIn('PASS', stdout.getvalue())
        self.assertNotIn('secret', stdout.getvalue())
        self.assertEqual(request.call_count, 2)


if __name__ == '__main__':
    unittest.main()
