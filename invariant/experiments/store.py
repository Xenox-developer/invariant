"""Transactional study records; large immutable content is published separately."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from ..execution import private_directory, project_lock
from .definitions import canonical, digest
from .errors import ExperimentError

SCHEMA = 5
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}


class StudyStore:
    def __init__(self, root, *, project=None):
        self.project = str(project) if project is not None else None
        self.root = Path(root)
        self.path = self.root / 'store.sqlite3'

    @contextmanager
    def connection(self, *, write=False, allow_initializing=False):
        if not write and not self.path.exists() and not self.path.is_symlink():
            raise ExperimentError('NOT_FOUND', 'This project has no saved experiment studies.')
        # SQLite removes/recreates WAL sidecars when its last connection closes.
        # Serialize short connection lifetimes so an integrity check cannot stat
        # a sidecar while another connection unlinks it (including nlink=0).
        lease = project_lock(self.root / 'database', timeout=10,
                             message='Experiment state is busy; retry the same request ID.')
        try:
            with self._connection(write=write, allow_initializing=allow_initializing) as database:
                yield database
        finally:
            lease.close()

    @contextmanager
    def _connection(self, *, write=False, allow_initializing=False):
        if write:
            private_directory(self.root)
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            os.close(fd)
        elif not self.path.exists() and not self.path.is_symlink():
            raise ExperimentError('NOT_FOUND', 'This project has no saved experiment studies.')
        for path in (self.path, self.path.with_name(self.path.name + '-wal'), self.path.with_name(self.path.name + '-shm')):
            try:
                info = path.lstat()
            except FileNotFoundError:
                if path == self.path:
                    raise
                continue
            else:
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                        or info.st_mode & 0o077):
                    raise ExperimentError('UNSAFE_STATE', 'Experiment database files must be owned private regular files.')
        connection = sqlite3.connect(self.path if write else self.path.as_uri() + '?mode=ro',
                                     uri=not write, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if not write and version == 0 and allow_initializing:
                yield None
                return
            if version not in (0, 1, 2, 3, 4, SCHEMA) or (not write and version not in (1, 2, 3, 4, SCHEMA)):
                raise ExperimentError('UNSUPPORTED_STATE_SCHEMA', 'This experiment database schema is not supported.',
                                      'Use the INVARIANT version that created this state; do not replace the database.')
            if write:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('PRAGMA synchronous=FULL')
                if version == 0:
                    connection.executescript('''
                        BEGIN IMMEDIATE;
                        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                        CREATE TABLE baselines (id TEXT PRIMARY KEY, content_digest TEXT NOT NULL, path TEXT NOT NULL);
                        CREATE TABLE studies (id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL,
                            created_at REAL NOT NULL, baseline_id TEXT NOT NULL REFERENCES baselines(id),
                            definition TEXT NOT NULL, definition_hash TEXT NOT NULL,
                            selection_revision INTEGER NOT NULL DEFAULT 0);
                        CREATE TABLE trials (id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES studies(id),
                            variant_id TEXT NOT NULL, case_id TEXT NOT NULL, repetition INTEGER NOT NULL,
                            state TEXT NOT NULL, UNIQUE(study_id, variant_id, case_id, repetition));
                        CREATE TABLE attempts (id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(id),
                            number INTEGER NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL,
                            UNIQUE(trial_id, number));
                        CREATE TABLE requests (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                            study_id TEXT NOT NULL REFERENCES studies(id));
                        CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                            study_id TEXT NOT NULL REFERENCES studies(id), time REAL NOT NULL,
                            kind TEXT NOT NULL, facts TEXT NOT NULL);
                        PRAGMA user_version=1;
                        COMMIT;
                    ''')
                if version in (0, 1):
                    connection.execute('BEGIN IMMEDIATE')
                    connection.execute("ALTER TABLE attempts ADD COLUMN record TEXT NOT NULL DEFAULT '{}'")
                    connection.execute('PRAGMA user_version=2')
                    connection.commit()
                if version in (0, 1, 2):
                    connection.executescript('''
                        BEGIN IMMEDIATE;
                        CREATE TABLE IF NOT EXISTS dispatches (study_id TEXT PRIMARY KEY REFERENCES studies(id),
                            state TEXT NOT NULL, reason TEXT, updated_at REAL NOT NULL);
                        CREATE TABLE IF NOT EXISTS cancellations (attempt_id TEXT PRIMARY KEY REFERENCES attempts(id),
                            accepted_at REAL NOT NULL);
                        CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                            result TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS log_frames (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                            study_id TEXT NOT NULL REFERENCES studies(id), attempt_id TEXT NOT NULL REFERENCES attempts(id),
                            phase TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
                        CREATE INDEX IF NOT EXISTS log_scope ON log_frames(study_id,sequence);
                        PRAGMA user_version=3;
                        COMMIT;
                    ''')
                if version in (0, 1, 2, 3):
                    connection.executescript('''
                        BEGIN IMMEDIATE;
                        CREATE TABLE IF NOT EXISTS selections (study_id TEXT PRIMARY KEY REFERENCES studies(id),
                            attempt_id TEXT NOT NULL REFERENCES attempts(id));
                        PRAGMA user_version=4;
                        COMMIT;
                    ''')
                if version in (0, 1, 2, 3, 4):
                    connection.executescript('''
                        BEGIN IMMEDIATE;
                        CREATE TABLE IF NOT EXISTS lineage (child_study_id TEXT PRIMARY KEY REFERENCES studies(id),
                            parent_attempt_id TEXT NOT NULL REFERENCES attempts(id));
                        CREATE TABLE IF NOT EXISTS apply_plans (id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES studies(id),
                            attempt_id TEXT NOT NULL REFERENCES attempts(id), record TEXT NOT NULL, digest TEXT NOT NULL);
                        PRAGMA user_version=5;
                        COMMIT;
                    ''')
                connection.execute('BEGIN IMMEDIATE')
            if self.project is not None:
                identity = connection.execute('SELECT value FROM metadata WHERE key=?', ('project',)).fetchone()
                if identity is not None and identity['value'] != self.project:
                    raise ExperimentError('FOREIGN_STATE', 'Experiment database belongs to another project.')
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def lookup_request(self, request_id, fingerprint=None):
        if not self.path.exists() and not self.path.is_symlink():
            return None
        with self.connection(allow_initializing=True) as database:
            if database is None:
                return None
            row = database.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        if row is None:
            return None
        if fingerprint is not None and row['fingerprint'] != fingerprint:
            raise ExperimentError('REQUEST_CONFLICT', 'This request ID was already used with different request content.',
                                  'Inspect the original request or choose a new --request-id.')
        return row['study_id']

    def create(self, *, study_id, baseline, frozen, trials, request_id, fingerprint, project, now=None):
        now = time.time() if now is None else now
        with self.connection(write=True) as database:
            identity = database.execute('SELECT value FROM metadata WHERE key=?', ('project',)).fetchone()
            if identity is not None and identity['value'] != str(project):
                raise ExperimentError('FOREIGN_STATE', 'Experiment database belongs to another project.')
            database.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', ('project', str(project)))
            if database.execute('SELECT 1 FROM operations WHERE id=?', (request_id,)).fetchone():
                raise ExperimentError('REQUEST_CONFLICT', 'Request ID belongs to a control operation.')
            request = database.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
            if request:
                if request['fingerprint'] != fingerprint:
                    raise ExperimentError('REQUEST_CONFLICT', 'Request content differs from its saved operation.')
                return request['study_id']
            database.execute('INSERT OR IGNORE INTO baselines VALUES (?,?,?)',
                             (baseline['id'], baseline['content_digest'], baseline['path']))
            database.execute('INSERT INTO studies(id,name,state,created_at,baseline_id,definition,definition_hash) VALUES (?,?,?,?,?,?,?)',
                             (study_id, frozen['definition']['name'], 'queued', now, baseline['id'], canonical(frozen), digest(frozen)))
            for trial in trials:
                trial_id = 'trial-' + digest({'study': study_id, **trial})[:32]
                attempt_id = 'attempt-' + digest({'trial': trial_id, 'number': 1})[:32]
                database.execute('INSERT INTO trials VALUES (?,?,?,?,?,?)',
                                 (trial_id, study_id, trial['variant_id'], trial['case_id'], trial['repetition'], 'queued'))
                database.execute('INSERT INTO attempts(id,trial_id,number,state,reason) VALUES (?,?,?,?,?)',
                                 (attempt_id, trial_id, 1, 'queued', 'AWAITING_EXECUTION'))
            database.execute('INSERT INTO requests VALUES (?,?,?)', (request_id, fingerprint, study_id))
            if frozen.get('parent'):
                database.execute('INSERT INTO lineage VALUES (?,?)', (study_id, frozen['parent']['attempt_id']))
            database.execute('INSERT INTO events(study_id,time,kind,facts) VALUES (?,?,?,?)',
                             (study_id, now, 'study_created', canonical({'trial_count': len(trials), 'dispatch_available': False})))
        return study_id

    def inspect(self, identifier):
        with self.connection() as database:
            row = database.execute('SELECT * FROM studies WHERE id=?', (identifier,)).fetchone()
            kind = 'study'
            if row is None:
                row = database.execute('SELECT * FROM trials WHERE id=?', (identifier,)).fetchone()
                kind = 'trial'
            if row is None:
                row = database.execute('SELECT a.*,t.study_id FROM attempts a JOIN trials t ON t.id=a.trial_id WHERE a.id=?', (identifier,)).fetchone()
                kind = 'attempt'
            if row is None:
                raise ExperimentError('NOT_FOUND', 'Unknown experiment identifier.', 'Use invariant experiment list to find this project\'s studies.')
            result = dict(row)
            result['kind'] = kind
            if kind == 'attempt':
                result['record'] = json.loads(result.get('record', '{}'))
            if kind == 'study':
                frozen = json.loads(result.pop('definition'))
                if digest(frozen) != result['definition_hash']:
                    raise ExperimentError('CORRUPT_STATE', 'Frozen definition does not match its stored digest.')
                if database.execute('PRAGMA user_version').fetchone()[0] >= 3:
                    dispatch = database.execute('SELECT * FROM dispatches WHERE study_id=?', (identifier,)).fetchone()
                    result['dispatch'] = dict(dispatch) if dispatch else {'state': 'not_requested'}
                result['frozen'] = frozen
                result['dispatch_available'] = True
                selected = database.execute('SELECT attempt_id FROM selections WHERE study_id=?', (identifier,)).fetchone() if database.execute('PRAGMA user_version').fetchone()[0] >= 4 else None
                result['selection'] = {'attempt_id': selected['attempt_id'] if selected else None, 'revision': result['selection_revision']}
                result['execution_notice'] = 'Command and native Codex trials share detached execution, recovery and selection.'
                result['trials'] = [dict(item) for item in database.execute('SELECT * FROM trials WHERE study_id=? ORDER BY rowid', (identifier,))]
                result['request_id'] = database.execute('SELECT id FROM requests WHERE study_id=?', (identifier,)).fetchone()['id']
                result['events'] = [dict(item) for item in database.execute('SELECT * FROM events WHERE study_id=? ORDER BY sequence', (identifier,))]
                for item in result['events']:
                    item['facts'] = json.loads(item['facts'])
                if result.get('dispatch', {}).get('state') == 'recovery_required':
                    for event in reversed(result['events']):
                        if event['kind'] == 'dispatch_recovery_required':
                            result['dispatch']['error'] = event['facts'].get('error')
                            break
                for trial in result['trials']:
                    trial['attempts'] = [self.attempt_record(item) for item in database.execute('SELECT * FROM attempts WHERE trial_id=? ORDER BY number', (trial['id'],))]
            elif kind == 'trial':
                result['attempts'] = [self.attempt_record(item) for item in database.execute('SELECT * FROM attempts WHERE trial_id=? ORDER BY number', (identifier,))]
        return result

    def list(self, limit=100):
        if not self.path.exists() and not self.path.is_symlink():
            return []
        with self.connection() as database:
            rows = [dict(row) for row in database.execute('''SELECT s.id,s.name,s.state,s.created_at,s.baseline_id,
                s.selection_revision,COUNT(t.id) AS trial_count FROM studies s LEFT JOIN trials t ON t.study_id=s.id
                GROUP BY s.id ORDER BY s.created_at DESC,s.id LIMIT ?''', (limit,))]
            selected = dict(database.execute('SELECT study_id,attempt_id FROM selections')) if database.execute('PRAGMA user_version').fetchone()[0] >= 4 else {}
            for row in rows:
                row['selection'] = {'attempt_id': selected.get(row['id']), 'revision': row['selection_revision']}
            return rows


    @staticmethod
    def attempt_record(row):
        result = dict(row)
        try:
            result['record'] = json.loads(result.get('record', '{}'))
        except ValueError:
            raise ExperimentError('CORRUPT_STATE', 'Invalid saved attempt record.') from None
        return result

    def _event(self, database, study_id, kind, facts):
        database.execute('INSERT INTO events(study_id,time,kind,facts) VALUES (?,?,?,?)',
                         (study_id, time.time(), kind, canonical(facts)))

    def blockers(self):
        with self.connection() as database:
            rows = database.execute('SELECT * FROM attempts WHERE state<>?', ('queued',)).fetchall()
        return [item for item in map(self.attempt_record, rows)
                if item['state'] not in TERMINAL or item['record'].get('cleanup_confirmed') is False]

    def claim(self, study_id, trial_id, controller):
        with self.connection(write=True) as database:
            row = database.execute('SELECT * FROM attempts WHERE trial_id=? ORDER BY number DESC LIMIT 1', (trial_id,)).fetchone()
            if row is None or row['state'] != 'queued':
                return None
            if database.execute('SELECT 1 FROM cancellations WHERE attempt_id=?', (row['id'],)).fetchone():
                return None
            record = {'controller': controller, 'dispatch_token': row['id'], 'claimed_at': time.time()}

            database.execute('UPDATE attempts SET state=?,reason=?,record=? WHERE id=?',
                             ('preparing', 'PREPARING', canonical(record), row['id']))
            database.execute('UPDATE trials SET state=? WHERE id=?', ('preparing', trial_id))
            database.execute('UPDATE studies SET state=? WHERE id=?', ('running', study_id))
            self._event(database, study_id, 'resources_reserved', {'attempt_id': row['id'], 'controller': controller})
            return {**self.attempt_record(row), 'record': record}

    def update_attempt(self, attempt_id, state, record, reason=None):
        if state not in TERMINAL | {'preparing', 'running', 'evaluating', 'saving'}:
            raise ExperimentError('INVALID_STATE', 'Invalid attempt lifecycle transition.')
        with self.connection(write=True) as database:
            row = database.execute('SELECT a.*,t.study_id FROM attempts a JOIN trials t ON t.id=a.trial_id WHERE a.id=?', (attempt_id,)).fetchone()
            if row is None or row['state'] in TERMINAL:
                raise ExperimentError('INVALID_STATE', 'A terminal/missing attempt cannot be overwritten.')
            database.execute('UPDATE attempts SET state=?,reason=?,record=? WHERE id=?',
                             (state, reason or state.upper(), canonical(record), attempt_id))
            database.execute('UPDATE trials SET state=? WHERE id=?', (state, row['trial_id']))
            self._event(database, row['study_id'], 'attempt_' + state,
                        {'attempt_id': attempt_id, 'reason': reason, 'cleanup_confirmed': record.get('cleanup_confirmed')})
            if state in TERMINAL:
                self._frame(database, row['study_id'], attempt_id, 'attempt', 'attempt_end',
                            {'state': state, 'reason': reason, 'cleanup_confirmed': record.get('cleanup_confirmed')})
            if state in TERMINAL and record.get('cleanup_confirmed'):
                self._event(database, row['study_id'], 'resources_released', {'attempt_id': attempt_id})

    def cancel_queued(self, study_id):
        with self.connection(write=True) as database:
            rows = database.execute('SELECT a.id,a.trial_id FROM attempts a JOIN trials t ON t.id=a.trial_id WHERE t.study_id=? AND a.state=?', (study_id, 'queued')).fetchall()
            for row in rows:
                record = {'finished_at': time.time(), 'cleanup_confirmed': True,
                          'runner': {'status': 'not_started'}, 'evaluation': {'status': 'not_run'}}
                database.execute('UPDATE attempts SET state=?,reason=?,record=? WHERE id=?',
                                 ('cancelled', 'CANCELLED_BEFORE_START', canonical(record), row['id']))
                database.execute('UPDATE trials SET state=? WHERE id=?', ('cancelled', row['trial_id']))
                self._event(database, study_id, 'attempt_cancelled', {'attempt_id': row['id']})
                self._frame(database, study_id, row['id'], 'attempt', 'attempt_end', {'state': 'cancelled'})

    def finish_study(self, study_id):
        with self.connection(write=True) as database:
            states = [row['state'] for row in database.execute('SELECT state FROM trials WHERE study_id=?', (study_id,))]
            unsafe = any(self.attempt_record(row)['record'].get('cleanup_confirmed') is False
                         for row in database.execute('SELECT a.* FROM attempts a JOIN trials t ON t.id=a.trial_id WHERE t.study_id=?', (study_id,)))
            state = 'completed' if all(item in TERMINAL for item in states) and not unsafe else 'interrupted'
            database.execute('UPDATE studies SET state=? WHERE id=?', (state, study_id))
            self._event(database, study_id, 'study_' + state, {'trial_states': states})

    @staticmethod
    def _frame(database, study_id, attempt_id, phase, kind, data):
        database.execute('INSERT INTO log_frames(study_id,attempt_id,phase,kind,data) VALUES (?,?,?,?,?)',
                         (study_id, attempt_id, phase, kind, canonical(data)))

    def append_log(self, study_id, attempt_id, phase, kind, data):
        with self.connection(write=True) as database:
            self._frame(database, study_id, attempt_id, phase, kind, data)

    def log_frames(self, study_id, after, *, attempt_ids=None, trial_id=None, limit=128):
        with self.connection() as database:
            version = database.execute('PRAGMA user_version').fetchone()[0]
            if version < 3:
                return []
            condition, parameters = '', [study_id, after]
            if trial_id is not None:
                condition = ' AND attempt_id IN (SELECT id FROM attempts WHERE trial_id=?)'
                parameters.append(trial_id)
            elif attempt_ids is not None:
                condition = ' AND attempt_id IN (' + ','.join('?' for _ in attempt_ids) + ')'
                parameters.extend(attempt_ids)
            rows = database.execute('SELECT * FROM log_frames WHERE study_id=? AND sequence>?' + condition +
                                    ' ORDER BY sequence LIMIT ?', (*parameters, limit)).fetchall()
        return [{**dict(row), 'data': json.loads(row['data'])} for row in rows]

    @staticmethod
    def _operation(database, request_id, fingerprint):
        if request_id is None:
            return None
        row = database.execute('SELECT * FROM operations WHERE id=?', (request_id,)).fetchone()
        # Study submissions and control operations share one request-id namespace.
        if database.execute('SELECT 1 FROM requests WHERE id=?', (request_id,)).fetchone():
            raise ExperimentError('REQUEST_CONFLICT', 'This request ID belongs to a study submission.')
        if row is None:
            return None
        if row['fingerprint'] != fingerprint:
            raise ExperimentError('REQUEST_CONFLICT', 'This request ID belongs to a different operation.')
        return json.loads(row['result'])

    @staticmethod
    def _save_operation(database, request_id, fingerprint, result):
        if request_id is not None:
            database.execute('INSERT INTO operations VALUES (?,?,?)', (request_id, fingerprint, canonical(result)))

    def operation(self, request_id):
        with self.connection() as database:
            if database.execute('PRAGMA user_version').fetchone()[0] < 3:
                return None
            row = database.execute('SELECT result FROM operations WHERE id=?', (request_id,)).fetchone()
        return json.loads(row['result']) if row else None

    def dispatch(self, study_id):
        with self.connection(write=True) as database:
            row = database.execute('SELECT state FROM dispatches WHERE study_id=?', (study_id,)).fetchone()
            if row and row['state'] in ('queued', 'running'):
                return
            database.execute('INSERT OR REPLACE INTO dispatches VALUES (?,?,?,?)',
                             (study_id, 'queued', None, time.time()))
            self._event(database, study_id, 'dispatch_requested', {})

    def dispatch_state(self, study_id, state, reason=None, error=None):
        with self.connection(write=True) as database:
            if state == 'completed' and database.execute(
                    "SELECT 1 FROM trials WHERE study_id=? AND state='queued' LIMIT 1", (study_id,)).fetchone():
                state = 'queued'
            database.execute('UPDATE dispatches SET state=?,reason=?,updated_at=? WHERE study_id=?',
                             (state, reason, time.time(), study_id))
            self._event(database, study_id, 'dispatch_' + state, {'reason': reason, 'error': error})

    def next_dispatch(self):
        with self.connection(write=True) as database:
            row = database.execute("SELECT study_id FROM dispatches WHERE state='queued' ORDER BY updated_at LIMIT 1").fetchone()
        return row['study_id'] if row else None

    def cancelled(self, attempt_id):
        with self.connection() as database:
            return bool(database.execute('SELECT 1 FROM cancellations WHERE attempt_id=?', (attempt_id,)).fetchone())

    def stop(self, identifier, *, request_id=None):
        item = self.inspect(identifier)
        if item['kind'] not in ('study', 'trial'):
            raise ExperimentError('INVALID_REQUEST', 'stop requires a study or trial ID.')
        study_id = item['id'] if item['kind'] == 'study' else item['study_id']
        fingerprint = digest({'operation': 'stop', 'identifier': identifier})
        with self.connection(write=True) as database:
            replay = self._operation(database, request_id, fingerprint)
            if replay is not None:
                return replay
            rows = database.execute('SELECT a.* FROM attempts a JOIN trials t ON t.id=a.trial_id WHERE ' +
                ('t.study_id=?' if item['kind'] == 'study' else 't.id=?'), (identifier,)).fetchall()
            targets = []
            for row in rows:
                if row['state'] in TERMINAL:
                    continue
                targets.append(row['id'])
                database.execute('INSERT OR IGNORE INTO cancellations VALUES (?,?)', (row['id'], time.time()))
                if row['state'] == 'queued':
                    record = {'cleanup_confirmed': True, 'finished_at': time.time(),
                              'runner': {'status': 'not_started'}, 'evaluation': {'status': 'not_run'}}
                    database.execute('UPDATE attempts SET state=?,reason=?,record=? WHERE id=?',
                        ('cancelled', 'CANCELLED_BEFORE_START', canonical(record), row['id']))
                    database.execute("UPDATE trials SET state='cancelled' WHERE id=?", (row['trial_id'],))
                    self._frame(database, study_id, row['id'], 'attempt', 'attempt_end', {'state': 'cancelled'})
            result = {'identifier': identifier, 'study_id': study_id, 'stop_accepted': True,
                      'accepted_at': time.time(), 'attempt_ids': targets}
            self._event(database, study_id, 'stop_requested', result)
            self._save_operation(database, request_id, fingerprint, result)
        return result

    def retry(self, trial_id, *, request_id=None):
        fingerprint = digest({'operation': 'retry', 'identifier': trial_id})
        with self.connection(write=True) as database:
            replay = self._operation(database, request_id, fingerprint)
            if replay is not None:
                return replay
            trial = database.execute('SELECT * FROM trials WHERE id=?', (trial_id,)).fetchone()
            row = database.execute('SELECT * FROM attempts WHERE trial_id=? ORDER BY number DESC LIMIT 1', (trial_id,)).fetchone()
            if not trial or not row:
                raise ExperimentError('NOT_FOUND', 'Unknown trial ID.')
            if row['state'] not in TERMINAL or self.attempt_record(row)['record'].get('cleanup_confirmed') is False:
                raise ExperimentError('RECOVERY_REQUIRED', 'Retry requires a terminal attempt with confirmed process stop.',
                                      'Stop/recover the study before explicitly retrying.')
            number = row['number'] + 1
            attempt_id = 'attempt-' + digest({'trial': trial_id, 'number': number})[:32]
            database.execute('INSERT INTO attempts(id,trial_id,number,state,reason) VALUES (?,?,?,?,?)',
                             (attempt_id, trial_id, number, 'queued', 'EXPLICIT_RETRY'))
            database.execute("UPDATE trials SET state='queued' WHERE id=?", (trial_id,))
            database.execute("UPDATE studies SET state='queued' WHERE id=?", (trial['study_id'],))
            result = {'trial_id': trial_id, 'study_id': trial['study_id'], 'attempt_id': attempt_id, 'number': number}
            self._event(database, trial['study_id'], 'retry_created', result)
            self._save_operation(database, request_id, fingerprint, result)
        return result

    def reconcile(self, attempt_id, record, *, reason):
        """Recovery may resolve an interrupted terminal record, never rewrite its outcome."""
        with self.connection(write=True) as database:
            row = database.execute('SELECT a.*,t.study_id FROM attempts a JOIN trials t ON t.id=a.trial_id WHERE a.id=?', (attempt_id,)).fetchone()
            state = row['state'] if row['state'] in TERMINAL else 'interrupted'
            record['recovery_reason'] = reason
            saved_reason = row['reason'] if row['state'] in TERMINAL else reason
            database.execute('UPDATE attempts SET state=?,reason=?,record=? WHERE id=?',
                             (state, saved_reason, canonical(record), attempt_id))
            latest = database.execute('SELECT id FROM attempts WHERE trial_id=? ORDER BY number DESC LIMIT 1', (row['trial_id'],)).fetchone()
            if latest['id'] == attempt_id:
                database.execute('UPDATE trials SET state=? WHERE id=?', (state, row['trial_id']))
            if record.get('cleanup_confirmed') and (row['state'] not in TERMINAL
                    or self.attempt_record(row)['record'].get('cleanup_confirmed') is False):
                self._event(database, row['study_id'], 'resources_released', {'attempt_id': attempt_id})
            self._event(database, row['study_id'], 'attempt_recovered', {'attempt_id': attempt_id, 'reason': reason})
            self._frame(database, row['study_id'], attempt_id, 'attempt', 'attempt_end',
                        {'state': state, 'reason': reason, 'cleanup_confirmed': record.get('cleanup_confirmed')})

    def recovery_operation(self, identifier, request_id, result=None, *, lookup_only=False):
        if request_id is None:
            return result
        fingerprint = digest({'operation': 'recover', 'identifier': identifier})
        with self.connection(write=True) as database:
            replay = self._operation(database, request_id, fingerprint)
            if replay is not None and not replay.get('operation_pending'):
                return replay
            if lookup_only:
                return None
            if replay is None:
                self._save_operation(database, request_id, fingerprint,
                                     {'operation_pending': True, 'study_id': identifier})
            if result is not None:
                database.execute('UPDATE operations SET result=? WHERE id=?', (canonical(result), request_id))
        return result

    def selection(self, identifier, *, revision, request_id=None, clear=False):
        if type(revision) is not int or revision < 0:
            raise ExperimentError('REVISION_REQUIRED', 'Selection needs a nonnegative --if-revision.',
                                  'Read study status/results/list for its current selection revision.')
        fingerprint = digest({'operation': 'selection', 'identifier': identifier, 'revision': revision, 'clear': clear})
        with self.connection(write=True) as database:
            replay = self._operation(database, request_id, fingerprint)
            if replay is not None:
                return replay
            if clear:
                study = database.execute('SELECT * FROM studies WHERE id=?', (identifier,)).fetchone()
                attempt = None
            else:
                attempt = database.execute('SELECT a.*,t.study_id FROM attempts a JOIN trials t ON t.id=a.trial_id WHERE a.id=?', (identifier,)).fetchone()
                if attempt is None:
                    attempts = database.execute('SELECT a.*,t.study_id FROM attempts a JOIN trials t ON t.id=a.trial_id WHERE t.id=? ORDER BY a.number', (identifier,)).fetchall()
                    if len(attempts) > 1:
                        raise ExperimentError('AMBIGUOUS_ATTEMPT', 'This trial has multiple attempts; select an explicit attempt ID.',
                            'Inspect trial status to choose the intended attempt; no latest/best attempt is inferred.',
                            details={'attempt_ids': [row['id'] for row in attempts]})
                    attempt = attempts[0] if attempts else None
                if attempt is None:
                    raise ExperimentError('NOT_FOUND', 'Unknown trial or attempt ID.')
                record = self.attempt_record(attempt)['record']
                if (attempt['state'] not in TERMINAL or not record.get('cleanup_confirmed')
                        or not record.get('capture', {}).get('complete') or not record.get('checkpoint', {}).get('complete')):
                    raise ExperimentError('INCOMPLETE_RESULT', 'Selection requires a terminal, stopped attempt with complete saved files and environment.',
                                          'Recover incomplete work or explicitly retry; inspect capture/checkpoint fields first.')
                study = database.execute('SELECT * FROM studies WHERE id=?', (attempt['study_id'],)).fetchone()
            if study is None:
                raise ExperimentError('NOT_FOUND', 'Unknown study ID.')
            if study['selection_revision'] != revision:
                raise ExperimentError('SELECTION_CONFLICT', 'The selection changed since this revision.',
                    'Read current status/results, review the existing selection, and retry with its revision and a new request ID.',
                    details={'study_id': study['id'], 'current_revision': study['selection_revision']})
            if clear:
                database.execute('DELETE FROM selections WHERE study_id=?', (study['id'],))
            else:
                database.execute('INSERT OR REPLACE INTO selections VALUES (?,?)', (study['id'], attempt['id']))
            database.execute('UPDATE studies SET selection_revision=selection_revision+1 WHERE id=?', (study['id'],))
            result = {'study_id': study['id'], 'attempt_id': attempt['id'] if attempt else None,
                      'trial_id': attempt['trial_id'] if attempt else None, 'revision': revision+1, 'cleared': clear}
            self._event(database, study['id'], 'selection_updated', result)
            self._save_operation(database, request_id, fingerprint, result)
        return result

    def lookup_operation(self, request_id, fingerprint):
        if request_id is None or not self.path.exists():
            return None
        with self.connection(write=True) as database:
            return self._operation(database, request_id, fingerprint)

    def control_operation(self, request_id, fingerprint, result=None, *, complete=False):
        if request_id is None:
            return result
        with self.connection(write=True) as database:
            if self.project is not None:
                database.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)', ('project', self.project))
            replay = self._operation(database, request_id, fingerprint)
            if replay is not None and not replay.get('operation_pending'):
                return replay
            if replay is None:
                self._save_operation(database, request_id, fingerprint, {'operation_pending': True, **(result or {})})
            if complete:
                database.execute('UPDATE operations SET result=? WHERE id=?', (canonical(result), request_id))
        return result
