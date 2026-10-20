"""Small private project-bound launch preferences and resource advice."""
import json
import os
import re
import stat

from .chats import MODEL, EFFORT


def read(path, project):
    try:
        with open(path, 'rb', opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW | os.O_NONBLOCK)) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > 4096:
                raise ValueError()
            raw = stream.read(4097)
            if len(raw) > 4096:
                raise ValueError()
            value = json.loads(raw)
        if not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 1 or value.get('project') != str(project):
            raise ValueError()
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise RuntimeError('Saved launch settings are unavailable or invalid. Choose a model explicitly with invariant run --model MODEL_NAME to replace launch preferences. Your saved chats are separate.') from None


def load(state, project):
    value = read(state / 'launch.json', project)
    if value is not None and (not isinstance(value.get('model'), str) or not re.fullmatch(MODEL, value['model'])
            or (value.get('effort') is not None and (not isinstance(value['effort'], str) or not re.fullmatch(EFFORT, value['effort'])))):
        raise RuntimeError('Saved launch settings are invalid. Use invariant run --model MODEL_NAME to choose again.')
    return value


def save(state, project, model, effort):
    from .workspace import private_write
    if not isinstance(model, str) or not re.fullmatch(MODEL, model) or (effort is not None and (not isinstance(effort, str) or not re.fullmatch(EFFORT, effort))):
        raise RuntimeError('Codex returned invalid launch preferences')
    private_write(state / 'launch.json', {'schema': 1, 'project': str(project), 'model': model, 'effort': effort})


def memory_hint(state, project):
    try:
        value = read(state / 'resources.json', project)
    except RuntimeError:
        return None  # Advice is never authority; invalid advice cannot raise caps.
    number = value.get('memory_mib') if value else None
    return number if type(number) is int and 64 <= number <= 4096 else None


def remember_pressure(state, project, plan):
    from .workspace import private_write
    if plan['mode'] != 'auto':
        return None
    memory = plan['limits']['memory_mib']
    increased = min(plan['memory_ceiling_mib'], memory * 2)
    if increased <= memory:
        return None
    private_write(state / 'resources.json', {'schema': 1, 'project': str(project), 'memory_mib': increased})
    return increased
