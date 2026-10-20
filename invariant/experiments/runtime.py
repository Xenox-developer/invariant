"""Read-only host preparation: pin images and resolve account-advertised settings."""
import json
import os
from pathlib import Path
import stat
import tempfile

from .. import environment, preferences, resources, workspace
from ..codex_rpc import CodexRpc
from ..dependencies import probe_codex
from ..policy import normalize
from .definitions import text
from .errors import ExperimentError


class LocalRuntime:
    def resolve(self, project, state, definition, policy):
        if not environment.enabled(policy):
            raise ExperimentError('PROFILE_REQUIRED', 'Experiments require the general Linux environment.',
                                  'Run invariant config set profile codex-environment-v2, then prepare that environment.')
        try:
            host = json.loads(workspace.docker('info', '--format', '{{json .}}', timeout=10))
            if host.get('OSType') != 'linux' or type(host.get('NCPU')) is not int or type(host.get('MemTotal')) is not int:
                raise ValueError()
        except (RuntimeError, OSError, ValueError) as error:
            raise ExperimentError('DOCKER_UNAVAILABLE', 'A responsive Linux Docker engine is required.',
                                  'Start Docker and run invariant setup --check; experiment commands never install tools.') from error
        capacity = {'cpus': host['NCPU'], 'memory_bytes': host['MemTotal']}
        try:
            selected = environment.selected_image(state, project, environment.IMAGE)
            info = json.loads(workspace.docker('image', 'inspect', selected))[0]
            if not environment.IMAGE_ID.fullmatch(info.get('Id', '')):
                raise ValueError()
            if info.get('Os') != 'linux':
                raise ValueError()
        except (RuntimeError, OSError, ValueError, IndexError) as error:
            raise ExperimentError('IMAGE_UNAVAILABLE', 'The prepared or saved environment image is unavailable or invalid.',
                                  'Prepare with invariant prepare --agent codex; restore any missing saved checkpoint instead of replacing it.') from error
        effective, sizing = resources.select(policy, project, capacity, preferences.memory_hint(state, project))
        variants = self.models(project, state, definition, effective) if definition['runner']['type'] == 'codex' else definition['variants']
        return {'image': {'id': info['Id'], 'repo_digests': info.get('RepoDigests') or [],
                          'os': info['Os'], 'architecture': info.get('Architecture'),
                          'base_reference': environment.IMAGE},
                'runtime': {'docker_version': host.get('ServerVersion'), 'kernel': host.get('KernelVersion'),
                            'host_os': host.get('OperatingSystem'), 'adapter_version': 1},
                'capacity': capacity, 'policy': effective, 'resource_sizing': sizing, 'variants': variants}

    def models(self, project, state, definition, policy):
        status = probe_codex()
        if status['status'] != 'ready':
            raise ExperimentError('CODEX_UNAVAILABLE', status['message'], 'Run invariant setup before using the Codex runner.')
        # No executor is started and no thread/turn is created for catalog lookup.
        with tempfile.TemporaryDirectory(prefix='invariant-catalog-') as directory:
            home = Path(directory)
            command, env = workspace.codex_configuration(home, 'invariant-catalog-unused', status['path'], policy=policy)
            rpc = CodexRpc(command, env=env, cwd=home, guard_parent=True)
            try:
                rpc.initialize()
                reply = rpc.request('account/read', {'refreshToken': False})
                if not isinstance(reply, dict):
                    raise ExperimentError('INVALID_CATALOG', 'Codex returned an invalid account record.')
                account = reply.get('account')
                if not isinstance(account, dict) or account.get('type') != 'chatgpt':
                    raise ExperimentError('AUTH_REQUIRED', 'Codex requires an existing ChatGPT sign-in.',
                                          'Sign in with Codex first, then retry. No interactive login is started here.')
                catalog, cursor, seen = [], None, set()
                while True:
                    params = {'limit': 100, 'includeHidden': True}
                    if cursor is not None:
                        params['cursor'] = cursor
                    page = rpc.request('model/list', params)
                    if not isinstance(page, dict) or not isinstance(page.get('data'), list):
                        raise ExperimentError('INVALID_CATALOG', 'Codex returned an invalid model catalog.')
                    catalog.extend(page['data'])
                    cursor = page.get('nextCursor')
                    if cursor is None:
                        break
                    if not isinstance(cursor, str) or cursor in seen or len(seen) >= 100:
                        raise ExperimentError('INVALID_CATALOG', 'Codex returned invalid catalog pagination.')
                    seen.add(cursor)
                remembered = preferences.load(state, project)
                return resolve_models(definition['variants'], catalog, remembered)
            finally:
                rpc.close()


def resolve_models(variants, catalog, remembered=None):
    if not isinstance(catalog, list) or len(catalog) > 10100 or not all(isinstance(item, dict) for item in catalog):
        raise ExperimentError('INVALID_CATALOG', 'Codex returned invalid model entries.')
    for item in catalog:
        modalities = item.get('inputModalities', ['text'])
        efforts = item.get('supportedReasoningEfforts', [])
        if not isinstance(modalities, list) or not isinstance(efforts, list) or not all(isinstance(row, dict) for row in efforts):
            raise ExperimentError('INVALID_CATALOG', 'Codex returned invalid model capabilities.')
    defaults = [item for item in catalog if item.get('isDefault') is True and not item.get('hidden')
                and 'text' in item.get('inputModalities', ['text'])]
    resolved = []
    for variant in variants:
        requested = variant.get('model') or (remembered or {}).get('model')
        candidates = [item for item in catalog if requested in (item.get('model'), item.get('id'))] if requested else defaults
        if len(candidates) != 1:
            raise ExperimentError('MODEL_UNAVAILABLE', 'No single account-advertised model matches variant ' + variant['id'],
                                  'Choose an available model explicitly in the variant definition.')
        item = candidates[0]
        model = text(item.get('model') or item.get('id'), 'catalog model', maximum=128, pattern=r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}')
        if 'text' not in item.get('inputModalities', ['text']):
            raise ExperimentError('MODEL_UNAVAILABLE', 'The selected model does not support text input.')
        effort = variant.get('effort')
        if effort is None and 'model' not in variant and remembered and remembered['model'] == model:
            effort = remembered.get('effort')
        if effort is None:
            effort = item.get('defaultReasoningEffort')
        supported = [row.get('reasoningEffort') for row in item.get('supportedReasoningEfforts', [])]
        if effort is not None and effort not in supported:
            raise ExperimentError('EFFORT_UNSUPPORTED', 'The requested/default effort is not supported for variant ' + variant['id'],
                                  'Choose an effort advertised by this model.')
        if effort is not None:
            text(effort, 'catalog effort', maximum=32, pattern=r'[a-z][a-z0-9_-]{0,31}')
        resolved.append({**variant, 'model': model, 'effort': effort})
    return resolved


def secret_files(definition, environ=None):
    env = os.environ if environ is None else environ
    files = []
    for reference in definition['secret_refs'].values():
        # Reference names map to path-valued environment variables, never token values.
        variable = 'INVARIANT_SECRET_' + reference
        value = env.get(variable)
        if not value or not Path(value).is_absolute():
            raise ExperimentError('SECRET_UNAVAILABLE', 'Missing secret file reference: ' + reference,
                                  'Set ' + variable + ' to an absolute private credential-file path.')
        path = Path(value)
        try:
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or info.st_mode & 0o077 or not 0 < info.st_size <= 65536):
                raise ValueError()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            os.close(fd)
        except (OSError, ValueError):
            raise ExperimentError('SECRET_UNAVAILABLE', 'Secret reference is not an owned private regular file: ' + reference,
                                  'Use a nonempty single-link credential file with mode 0600 or 0400.') from None
        files.append(path.resolve())
    return files


def allocations(definition, prepared):
    policy = prepared['policy']
    requested = definition['execution']
    limits = policy['limits']
    cpus = requested.get('trial_cpus', limits['cpus'])
    memory = requested.get('trial_memory_mib', limits['memory_mib'])
    duration = requested.get('trial_timeout_seconds', policy['duration_seconds'])
    if cpus > limits['cpus'] or memory > limits['memory_mib'] or duration > policy['duration_seconds']:
        raise ExperimentError('POLICY_EXCEEDED', 'Trial resources/deadline exceed the effective project policy.',
                              'Lower the experiment request or explicitly edit the project limits before submitting.')
    capacity = prepared['capacity']
    available_cpu = max(0.1, int(capacity['cpus'] * 75) / 100)
    available_memory = (capacity['memory_bytes'] // (1024 ** 2) * 3 // 4 // 64) * 64
    cpu_budget = requested.get('cpu_budget', available_cpu)
    memory_budget = requested.get('memory_budget_mib', available_memory)
    if cpu_budget > available_cpu or memory_budget > available_memory or cpu_budget < cpus or memory_budget < memory:
        raise ExperimentError('CAPACITY_EXCEEDED', 'The aggregate budget cannot fit a trial with reserved Docker headroom.',
                              'Reduce trial resources or increase Docker capacity; trial limits were not changed.')
    slots = min(requested['max_parallel'], int((cpu_budget + 1e-9) / cpus), memory_budget // memory)
    effective = normalize({**policy, 'resource_mode': 'manual', 'duration_seconds': duration,
                           'limits': {**limits, 'cpus': cpus, 'memory_mib': memory}})
    return {'requested_max_parallel': requested['max_parallel'], 'max_parallel': slots,
            'trial_cpus': cpus, 'trial_memory_mib': memory, 'trial_timeout_seconds': duration,
            'cpu_budget': cpu_budget, 'memory_budget_mib': memory_budget,
            'headroom_fraction': 0.25, 'trial_policy': effective}
