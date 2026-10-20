"""Versioned persisted log frames with project/scope-bound resumable cursors."""
import base64
import json
import time

from .definitions import canonical, digest
from .errors import ExperimentError
from .store import TERMINAL


def cursor(engine, identifier, sequence):
    value = {'v': 1, 'project': digest(str(engine.project)), 'scope': identifier, 'sequence': sequence}
    return base64.urlsafe_b64encode(canonical(value).encode()).decode().rstrip('=')


def position(engine, identifier, value):
    if value is None:
        return 0
    try:
        if len(value) > 2048:
            raise ValueError()
        parsed = json.loads(base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True))
        if (parsed.get('v') != 1 or parsed.get('project') != digest(str(engine.project))
                or parsed.get('scope') != identifier or type(parsed.get('sequence')) is not int or parsed['sequence'] < 0):
            raise ValueError()
        return parsed['sequence']
    except (ValueError, TypeError, AttributeError):
        raise ExperimentError('INVALID_CURSOR', 'Log cursor is invalid or belongs to another project/scope.',
                              'Use the cursor returned for this exact study, trial or attempt ID.') from None


def frames(engine, identifier, *, after=None, follow=False):
    item = engine.inspect(identifier)
    study_id = item['id'] if item['kind'] == 'study' else item['study_id']
    attempts = [item['id']] if item['kind'] == 'attempt' else None
    trial_id = item['id'] if item['kind'] == 'trial' else None
    sequence = position(engine, identifier, after)
    with engine.store.connection(write=True) as database:
        maximum = database.execute('SELECT COALESCE(MAX(sequence),0) FROM log_frames').fetchone()[0]
    if sequence > maximum:
        raise ExperimentError('INVALID_CURSOR', 'Cursor is ahead of saved logs; saved state may have been replaced.')
    while True:
        batch = engine.store.log_frames(study_id, sequence, attempt_ids=attempts, trial_id=trial_id)
        for row in batch:
            sequence = row['sequence']
            yield {'schema_version': 1, **row, 'cursor': cursor(engine, identifier, sequence)}
        if batch:
            continue
        current = engine.inspect(identifier)
        rows = [a for t in current['trials'] for a in t['attempts']] if current['kind'] == 'study' else current['attempts'] if current['kind'] == 'trial' else [current]
        terminal = all(row['state'] in TERMINAL for row in rows)
        # Terminal records and their final log frames commit in one transaction.
        # Re-read once after observing terminal, so a final frame cannot be missed.
        if terminal and engine.store.log_frames(study_id, sequence, attempt_ids=attempts, trial_id=trial_id, limit=1):
            continue
        if terminal or not follow:
            yield {'schema_version': 1, 'kind': 'follow_end', 'state': current['state'],
                   'terminal': terminal, 'cursor': cursor(engine, identifier, sequence)}
            return
        time.sleep(.2)
