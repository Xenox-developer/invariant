"""Plain-language recovery guidance; never replay interrupted work."""
from .codex_rpc import inert


def failure(error, policy, *, saved=False, oom=False):
    text = str(error).lower()
    if oom:
        print('The workspace ran out of memory. Project edits may already be saved; interrupted work was not retried.')
    elif 'already has an active' in text:
        print('This project is already open. Return to its terminal; invariant status shows the active session.')
    elif 'thread not found' in text or 'history' in text or 'thread/resume' in text:
        print('Codex could not restore this conversation. Use invariant chats to choose another, or invariant run for a new chat. Existing files and chat records were kept.')
    elif 'connection closed' in text or 'timed out' in text:
        print('The connection to Codex or its workspace stopped. Project files and saved conversation records were kept.')
    elif 'reasoning effort' in text or 'model' in text:
        print('The selected model settings could not be used. Choose a supported model with invariant run --model MODEL_NAME; add --effort LEVEL if needed.')
    else:
        print('The workspace could not finish this session. Project files were kept; interrupted work was not automatically retried.')
    print('Continue with invariant resume.' if saved else 'Try invariant run again. Use invariant setup --check if startup still fails.')
    if policy['command_network'] == 'none':
        print('Internet is off. If the task needs downloads, run invariant config set command_network internet, then start or resume again.')
    print('Diagnostic:', inert(str(error)))


def closed(*, saved, reason=None):
    if reason == 'deadline':
        print('The session time limit was reached. Your project files were kept.')
    elif reason == 'operator':
        print('The workspace was stopped by the operator. Your project files were kept.')
    elif reason == 'hangup':
        print('The terminal closed. Your project files were kept.')
    if saved:
        print('Chat saved. Continue later with invariant resume.')
    else:
        print('No new chat was saved because no message was accepted. Start again with invariant run; any earlier saved chat is unchanged.')
