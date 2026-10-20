"""Native Codex adapter: host authentication, private history, Docker-only tools."""
import json
from pathlib import Path
import time

from .. import environment, workspace
from ..chats import valid_id
from ..codex_rpc import CodexRpc, RpcCancelled, RpcError
from ..dependencies import probe_codex
from ..execution import private_directory
from ..turns import run_turn
from .errors import ExperimentError
from .runners import BoundedLog, owned_info, stop_owned
from .runtime import resolve_models


def failure_code(error):
    info = error.get('codexErrorInfo') if isinstance(error, dict) else getattr(error, 'data', None)
    info = info if isinstance(info, dict) else {'code': info}
    raw = json.dumps(info).lower()
    message = str(error.get('message', '') if isinstance(error, dict) else error).lower()
    if any(word in raw for word in ('usagelimit', 'ratelimit', 'serveroverloaded', 'flexunavailable')) or '429' in raw:
        return 'THROTTLED'
    if 'unauthorized' in raw or '401' in raw or any(word in message for word in ('not authenticated', 'sign in', 'unauthorized', 'authentication')):
        return 'AUTH_REQUIRED'
    if 'contextwindowexceeded' in raw or 'sessionbudgetexceeded' in raw:
        return 'MODEL_CONTEXT_LIMIT'
    if 'model' in message and any(word in message for word in ('not found', 'not supported', 'unavailable', 'does not exist')):
        return 'MODEL_UNAVAILABLE'
    if 'reasoning' in message and any(word in message for word in ('unsupported', 'not supported', 'invalid')):
        return 'EFFORT_UNSUPPORTED'
    return 'CODEX_TURN_FAILED'


def usage_record(value):
    """Keep only native numeric counters; absence is never reported as zero."""
    if not isinstance(value, dict):
        return None
    result = {}
    for section in ('total', 'last'):
        row = value.get(section)
        if not isinstance(row, dict):
            return None
        result[section] = {key: row[key] for key in ('totalTokens', 'inputTokens', 'cachedInputTokens',
            'cacheWriteInputTokens', 'outputTokens', 'reasoningOutputTokens')
            if type(row.get(key)) is int and row[key] >= 0}
        if not result[section]:
            return None
    window = value.get('modelContextWindow')
    result['modelContextWindow'] = window if type(window) is int and window > 0 else None
    return result


class Budget:
    def __init__(self, cancel, deadline, log):
        self.cancel, self.deadline, self.log = cancel, deadline, log
    def is_set(self):
        return self.cancel.is_set() or time.monotonic() >= self.deadline or self.log.truncated
    def reason(self):
        return 'CANCELLED' if self.cancel.is_set() else 'OUTPUT_LIMIT' if self.log.truncated else 'DEADLINE_EXCEEDED'


class CodexRunner:
    def __init__(self, *, rpc_factory=CodexRpc, probe=probe_codex):
        self.rpc_factory, self.probe = rpc_factory, probe

    def run(self, context, *, variant, prompt, instructions, home, timeout, cancel,
            log_path, limit, secrets=(), on_started=None, on_intent=None, on_log=None,
            on_native=None):
        started = time.time()
        log = BoundedLog(log_path, limit, secrets, on_log)
        budget = Budget(cancel, time.monotonic() + timeout, log)
        native = {'requested_model': variant['model'], 'requested_effort': variant.get('effort'),
                  'thread_id': None, 'turn_id': None, 'model': None, 'effort': None, 'home': str(home)}
        outcome = {'status': 'failed', 'reason': 'CODEX_START_FAILED', 'exit_code': None,
                   'native': native, 'token_usage': None, 'usage_unavailable_reason': 'not_reported',
                   'container': context.owner_token, 'image': context.image, 'started_at': started}
        rpc = None
        def remaining():
            return max(.01, min(60, budget.deadline-time.monotonic()))
        def request(method, params):
            return rpc.request(method, params, cancel=budget, timeout=remaining())
        def persist():
            if on_native: on_native(dict(native))
        printed = set()
        def event(message):
            method, params = message.get('method'), message.get('params', {})
            if params.get('threadId', native['thread_id']) != native['thread_id']:
                return
            if native['turn_id'] is not None and params.get('turnId', native['turn_id']) != native['turn_id']:
                return
            if method == 'thread/tokenUsage/updated':
                usage = usage_record(params.get('tokenUsage'))
                if usage is not None:
                    outcome['token_usage'], outcome['usage_unavailable_reason'] = usage, None
            elif method == 'model/rerouted':
                native['rerouted_model'] = params.get('toModel')
                persist()
                raise ExperimentError('MODEL_MISMATCH', 'Codex rerouted the frozen model; this attempt was stopped.')
            elif method == 'error':
                error = params.get('error') or {}
                code = failure_code(error)
                if params.get('willRetry') or code in ('THROTTLED', 'AUTH_REQUIRED'):
                    raise ExperimentError(code, 'Codex reported a provider failure; automatic trial retry is disabled.')
            elif method == 'item/agentMessage/delta':
                printed.add(params.get('itemId'))
                log.write(str(params.get('delta', '')).encode())
            elif method == 'item/commandExecution/outputDelta':
                log.write(str(params.get('delta', '')).encode())
            elif method == 'item/completed':
                item = params.get('item', {})
                if item.get('type') == 'agentMessage' and item.get('id') not in printed:
                    log.write(str(item.get('text', '')).encode())
                elif item.get('type') in ('commandExecution', 'fileChange'):
                    log.write(('\n' + json.dumps({'tool': item['type'], 'status': item.get('status')}) + '\n').encode())
        try:
            status = self.probe()
            if status['status'] != 'ready':
                raise ExperimentError('CODEX_UNAVAILABLE', 'The supported Codex CLI is unavailable.', 'Run invariant setup; no installer is started by experiments.')
            private_directory(home)
            command, env = workspace.codex_configuration(home, context.owner_token, status['path'], policy=context.policy)
            native['cli_version'] = status.get('version')
            if on_intent: on_intent('create')
            if budget.is_set(): raise RpcCancelled('Stopped before executor creation')
            workspace.docker(*workspace.container_command(context.owner_token, context.project, context.image, context=context))
            info = owned_info(context)
            workspace.verify_container(context.owner_token, context.project, context.image, context=context, info=info)
            outcome['runtime'] = {'image': info['Image'], 'mounts': info['Mounts'],
                'memory_bytes': info['HostConfig']['Memory'], 'nano_cpus': info['HostConfig']['NanoCpus'],
                'pids_limit': info['HostConfig']['PidsLimit'], 'network': info['HostConfig']['NetworkMode']}
            if on_intent: on_intent('start')
            if budget.is_set(): raise RpcCancelled('Stopped before executor startup')
            rpc = self.rpc_factory(command, env=env, cwd=home, guard_parent=True)
            rpc.initialize(cancel=budget, timeout=remaining())
            account = request('account/read', {'refreshToken': False}).get('account')
            if not isinstance(account, dict) or account.get('type') != 'chatgpt':
                raise ExperimentError('AUTH_REQUIRED', 'A host ChatGPT sign-in is required.', 'Sign in with Codex on the host, then explicitly retry. No login prompt was opened.')
            catalog, cursor, seen = [], None, set()
            while True:
                page = request('model/list', {'limit': 100, 'includeHidden': True, **({'cursor': cursor} if cursor else {})})
                if not isinstance(page.get('data'), list):
                    raise ExperimentError('INVALID_CATALOG', 'Codex returned an invalid model catalog.')
                catalog.extend(page.get('data') or [])
                cursor = page.get('nextCursor')
                if cursor is None: break
                if not isinstance(cursor, str) or cursor in seen or len(seen) >= 100:
                    raise ExperimentError('INVALID_CATALOG', 'Invalid Codex catalog pagination.')
                seen.add(cursor)
            resolved = resolve_models([variant], catalog)[0]
            if resolved['model'] != variant['model'] or resolved.get('effort') != variant.get('effort'):
                raise ExperimentError('MODEL_MISMATCH', 'The frozen model/effort no longer matches the account catalog.')
            connected = request('environment/info', {'environmentId': 'invariant'})
            if connected.get('cwd') not in ('/workspace', 'file:///workspace'):
                raise ExperimentError('EXECUTOR_MISMATCH', 'Codex did not connect to the isolated workspace.')
            workspace.docker('exec', context.owner_token, environment.INSPECTOR,
                            'startup-' + ('rw' if context.policy['workspace_access']=='read-write' else 'ro'))
            if on_started: on_started()
            params = {'model': variant['model'], 'cwd': '/workspace', 'approvalPolicy': 'never',
                'sandbox': 'danger-full-access', 'ephemeral': False,
                'environments': [{'environmentId': 'invariant', 'cwd': '/workspace'}],
                'developerInstructions': 'Work only in /workspace. Docker is the external sandbox. ' +
                    workspace.network_instructions(context.policy) + environment.instructions(context.policy) +
                    ' Do not request host tools, credentials, permissions or sandbox escalation.\n' + instructions}
            if variant.get('effort') is not None:
                params['config'] = {'model_reasoning_effort': variant['effort']}
            thread = request('thread/start', params)
            identity = thread.get('thread') or {}
            native.update(thread_id=identity.get('id'), model=thread.get('model'), effort=thread.get('reasoningEffort'),
                          environments=identity.get('environments'))
            persist()
            if not valid_id(native['thread_id']) or identity.get('ephemeral') is not False:
                raise ExperimentError('THREAD_MISMATCH', 'Codex did not create the isolated persistent conversation.')
            if native['model'] != variant['model'] or native['effort'] != variant.get('effort'):
                raise ExperimentError('MODEL_MISMATCH', 'Codex selected different model/effort; refusing a fallback.')
            environments = identity.get('environments')
            if (not isinstance(environments, list) or len(environments) != 1
                    or environments[0].get('environmentId') != 'invariant' or environments[0].get('cwd') != '/workspace'
                    or environments[0].get('runtimeWorkspaceRoots') != ['/workspace']):
                raise ExperimentError('EXECUTOR_MISMATCH', 'Codex did not bind the conversation to the isolated executor.')
            if on_intent: on_intent('model_turn')
            def accepted(turn_id):
                native['turn_id'] = turn_id; persist()
            completed = run_turn(rpc, native['thread_id'], prompt, effort=variant.get('effort'), event=event,
                                 on_started=accepted, cancel=budget, timeout=max(.01, budget.deadline-time.monotonic()))
            for _ in range(64):
                message = rpc.receive(timeout=0)
                if message is None: break
                event(message)
            native['turn_status'] = completed.get('status')
            if completed.get('id') != native['turn_id']:
                raise ExperimentError('TURN_MISMATCH', 'Codex completed another turn.')
            if completed.get('status') == 'completed':
                outcome.update(status='completed', reason=None)
            elif completed.get('status') == 'interrupted':
                outcome.update(status='cancelled', reason='CANCELLED')
            else:
                outcome['reason'] = failure_code(completed.get('error') or {})
        except RpcCancelled:
            reason = budget.reason()
            outcome.update(status='cancelled' if reason=='CANCELLED' else 'failed', reason=reason)
        except (ExperimentError, RpcError) as error:
            outcome['reason'] = budget.reason() if budget.is_set() else error.code if isinstance(error, ExperimentError) else failure_code(error)
            if outcome['reason'] == 'CANCELLED': outcome['status'] = 'cancelled'
        finally:
            try:
                if rpc is not None: rpc.close()
            finally:
                try:
                    outcome['cleanup_confirmed'] = stop_owned(context)
                finally:
                    log.close()
            outcome.update(finished_at=time.time(), duration_seconds=time.time()-started,
                           log=str(log_path), log_bytes=log.saved, log_truncated=log.truncated)
        return outcome
