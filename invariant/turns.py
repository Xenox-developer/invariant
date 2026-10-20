"""One Codex turn driver; terminal and experiment adapters consume the same events."""
import time
from .codex_rpc import RpcCancelled, RpcError


def run_turn(rpc, thread_id, text, *, effort=None, event=None, on_started=None,
             cancel=None, timeout=None):
    turn_id, completed = None, None
    def emit(message):
        nonlocal completed
        params = message.get('params', {})
        if params.get('threadId', thread_id) != thread_id:
            return
        if message.get('method') == 'turn/completed':
            value = params.get('turn', {})
            if turn_id is None or value.get('id') == turn_id:
                completed = value
        if event:
            event(message)
    params = {'threadId': thread_id, 'input': [{'type': 'text', 'text': text}],
              'environments': [{'environmentId': 'invariant', 'cwd': '/workspace'}]}
    if effort is not None:
        params['effort'] = effort
    options = {'event': emit}
    if cancel is not None:
        options['cancel'] = cancel
    if timeout is not None:
        options['timeout'] = min(60, max(.01, timeout))
    deadline = time.monotonic() + timeout if timeout is not None else None
    try:
        reply = rpc.request('turn/start', params, **options)
        turn = reply.get('turn', {})
        turn_id = turn.get('id')
        if not isinstance(turn_id, str) or not turn_id:
            raise RpcError('Codex did not acknowledge a turn ID')
        if on_started:
            on_started(turn_id)
        if turn.get('status') in ('completed', 'failed', 'interrupted'):
            completed = turn
        while completed is None:
            if (cancel is not None and cancel.is_set()) or (deadline is not None and time.monotonic() >= deadline):
                raise RpcCancelled('Codex turn stopped or its deadline expired')
            message = rpc.receive()
            if message:
                emit(message)
    except (KeyboardInterrupt, RpcCancelled) as interrupted:
        if turn_id:
            try:
                rpc.request('turn/interrupt', {'threadId': thread_id, 'turnId': turn_id}, timeout=2, event=emit)
            except RpcError:
                pass
        if isinstance(interrupted, KeyboardInterrupt) and turn_id:
            end = time.monotonic() + 2
            while completed is None and time.monotonic() < end:
                message = rpc.receive()
                if message: emit(message)
            if completed is not None:
                return completed
            raise RpcError('Codex did not confirm interruption; stopping the workspace') from interrupted
        raise
    if completed.get('id', turn_id) != turn_id:
        raise RpcError('Codex completed a different turn')
    return completed
