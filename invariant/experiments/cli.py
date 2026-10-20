"""Human and JSON adapters over the same experiment engine."""
import argparse
import base64
import json
import sqlite3
import subprocess
import sys

from ..codex_rpc import inert
from .engine import ExperimentEngine
from .errors import ExperimentError


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ExperimentError('INVALID_ARGUMENTS', message, 'Use invariant experiment --help for the implemented commands.')


def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    acceptance_arguments=[arg for arg in arguments if arg!='--json']
    if acceptance_arguments and acceptance_arguments[0]=='acceptance':
        from .acceptance import main as acceptance_main
        arguments.remove('acceptance')
        return acceptance_main(arguments)
    machine = '--json' in arguments
    # Accept --json on either side of the subcommand without separate adapters.
    arguments = [arg for arg in arguments if arg != '--json']
    parser = Parser(prog='invariant experiment', description='Run and compare frozen command or native Codex experiments in parallel. Release verification: invariant experiment acceptance --help.')
    parser.add_argument('--json', action='store_true', help='versioned structured output; no prompts/installers')
    commands = parser.add_subparsers(dest='command', required=True, parser_class=Parser)
    commands.add_parser('acceptance',help='verify P01–P21 and release evidence; use acceptance --help')
    for name in ('init', 'validate', 'run'):
        command = commands.add_parser(name)
        command.add_argument('file', help='project-relative experiment JSON file')
        if name == 'init':
            command.add_argument('--request-id', help='idempotency key for starter creation')
            command.add_argument('--runner', choices=('command', 'codex'), default='command')
        if name == 'run':
            command.add_argument('--request-id', help='idempotency key for durable study creation')
            command.add_argument('--create-only', action='store_true', help='freeze/queue without executing')
            command.add_argument('--wait', action='store_true', help='wait for the detached study; Ctrl+C only closes the viewer')
    for name in ('execute', 'results', 'logs', 'stop', 'recover', 'retry', 'delete', 'diff', 'continue'):
        command = commands.add_parser(name)
        command.add_argument('identifier')
        if name == 'logs':
            command.add_argument('--tail-bytes', type=int, default=65536)
            command.add_argument('--follow', action='store_true', help='follow saved frames; does not control execution')
            command.add_argument('--after', help='resume after the cursor from the last received frame')
        if name in ('execute', 'stop', 'recover', 'retry', 'delete', 'continue'):
            command.add_argument('--request-id', help='idempotency key for this control operation')
        if name == 'execute':
            command.add_argument('--wait', action='store_true')
        if name == 'delete':
            command.add_argument('--confirm', action='store_true', help='delete the displayed terminal study state')
        if name == 'continue':
            command.add_argument('--prompt-file', required=True, help='project-relative new task; starts a fresh conversation')
            command.add_argument('--create-only', action='store_true')
            command.add_argument('--wait', action='store_true')

    applying = commands.add_parser('apply', help='review a dry-run plan, apply that exact plan, or recover an interrupted apply')
    applying.add_argument('identifier', nargs='?', help='study/selected trial/attempt ID for --dry-run')
    actions = applying.add_mutually_exclusive_group(required=True)
    actions.add_argument('--dry-run', action='store_true')
    actions.add_argument('--plan', metavar='PLAN_ID')
    actions.add_argument('--recover', action='store_true')
    applying.add_argument('--request-id')

    listing = commands.add_parser('list')
    listing.add_argument('--limit', type=int, default=100)
    status = commands.add_parser('status')
    status.add_argument('identifier', nargs='?', help='study, trial or attempt ID')
    status.add_argument('--request-id', help='find a study after a lost submission acknowledgement')
    selection = commands.add_parser('select', help='save a choice without changing project files')
    selection.add_argument('identifier', nargs='?', help='trial or explicit attempt ID')
    selection.add_argument('--clear', metavar='STUDY_ID', help='clear this study selection')
    selection.add_argument('--if-revision', type=int, required=True, help='current selection revision from status/results/list')
    selection.add_argument('--request-id', help='idempotency key for this selection update')
    try:
        args = parser.parse_args(arguments)
        engine = ExperimentEngine()
        if args.command == 'init':
            result = engine.init_definition(args.file, request_id=args.request_id, runner=args.runner)
        elif args.command == 'validate':
            result = engine.validate(args.file)
        elif args.command == 'run':
            result = engine.submit(args.file, request_id=args.request_id)
            if not args.create_only and result['dispatch_available']:
                if not machine:
                    print('Study:', result['id'], '— submitting detached trials.', flush=True)
                result = engine.launch(result['id'])
                if args.wait:
                    result = wait_study(engine, result['id'])
        elif args.command == 'execute':
            result = engine.launch(args.identifier, request_id=args.request_id)
            if args.wait:
                result = wait_study(engine, args.identifier)
        elif args.command == 'results':
            result = engine.results(args.identifier)
        elif args.command == 'stop':
            result = engine.stop(args.identifier, request_id=args.request_id)
        elif args.command == 'recover':
            result = engine.recover(args.identifier, request_id=args.request_id)
        elif args.command == 'retry':
            result = engine.retry(args.identifier, request_id=args.request_id)
        elif args.command == 'delete':
            result = engine.delete(args.identifier, confirm=args.confirm, request_id=args.request_id)
        elif args.command == 'select':
            if bool(args.identifier) == bool(args.clear):
                raise ExperimentError('INVALID_ARGUMENTS', 'Provide either a trial/attempt ID or --clear STUDY_ID.')
            result = engine.select(args.identifier or args.clear, revision=args.if_revision,
                                   request_id=args.request_id, clear=bool(args.clear))
        elif args.command == 'continue':
            result = engine.continue_result(args.identifier, args.prompt_file, request_id=args.request_id)
            if not args.create_only:
                result = engine.launch(result['id'])
                if args.wait: result = wait_study(engine, result['id'])
        elif args.command == 'diff':
            result = engine.diff(args.identifier)
        elif args.command == 'apply':
            if args.dry_run:
                if not args.identifier: raise ExperimentError('INVALID_ARGUMENTS','--dry-run requires a result ID.')
                result = engine.plan_apply(args.identifier, request_id=args.request_id)
            else:
                if args.identifier: raise ExperimentError('INVALID_ARGUMENTS','Use a result ID only with --dry-run.')
                result = engine.recover_apply(request_id=args.request_id) if args.recover else engine.apply(args.plan, request_id=args.request_id)
        elif args.command == 'logs':
            if args.follow or args.after is not None:
                for frame in engine.log_frames(args.identifier, after=args.after, follow=args.follow):
                    if machine:
                        print(json.dumps(frame, allow_nan=False), flush=True)
                    elif frame['kind'] == 'log_chunk':
                        print(inert(base64.b64decode(frame['data']['data_base64']).decode('utf-8', errors='replace')), end='', flush=True)
                    elif frame['kind'] == 'log_end' and frame['data'].get('truncated'):
                        print('\n[Log capture limit reached for ' + frame['attempt_id'] + ' ' + frame['phase'] + ']')
                    elif frame['kind'] == 'follow_end':
                        print('\nLog cursor:', frame['cursor'])
                return 0
            result = engine.logs(args.identifier, tail_bytes=args.tail_bytes)
        elif args.command == 'list':
            result = engine.list(limit=args.limit)
        else:
            result = engine.inspect(args.identifier, request_id=args.request_id)
        if machine:
            print(json.dumps({'schema_version': 1, 'ok': True, 'data': result, 'error': None}, allow_nan=False))
        else:
            render(args.command, result)
        return 1 if result.get('termination_confirmed') is False or result.get('unresolved_attempt_ids') else 0
    except (KeyboardInterrupt, BrokenPipeError):
        if not machine and 'frame' in locals() and not isinstance(sys.exc_info()[1], BrokenPipeError):
            print('\nResume logs: invariant experiment logs ' + args.identifier + ' --follow --after ' + frame['cursor'])
        return 130
    except (ExperimentError, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        if not isinstance(error, ExperimentError):
            code = 'CORRUPT_STATE' if isinstance(error,(ValueError,KeyError,TypeError)) else 'STATE_UNAVAILABLE' if isinstance(error, (OSError, sqlite3.Error)) else 'PREPARATION_FAILED'
            deleting = 'args' in locals() and args.command == 'delete'
            error = ExperimentError('DELETE_PENDING' if deleting else code,
                                    'Deletion could not finish; its saved cleanup manifest is retained.' if deleting else 'Experiment preparation/state is unavailable.',
                                    'Restore Docker/disk access, then repeat delete ID --confirm.' if deleting else
                                    'Check Docker, available disk and private-state permissions; retry with the same request ID.')
        if machine:
            print(json.dumps({'schema_version': 1, 'ok': False, 'data': None, 'error': error.record()}, allow_nan=False))
        else:
            print('INVARIANT experiment: ' + inert(str(error)), file=sys.stderr)
            print('Next: ' + inert(error.recovery), file=sys.stderr)
            if error.details:
                print(inert(json.dumps(error.details)), file=sys.stderr)
        return 2 if error.code in ('INVALID_ARGUMENTS', 'INVALID_DEFINITION', 'INVALID_PATH', 'UNSUPPORTED_SCHEMA') else 1


def render(command, result):
    if command == 'init':
        print('Created definition:', inert(result['definition']))
        print('Edit guide:', inert(result['guide']))
    elif command == 'validate':
        print('Valid experiment:', result['trial_count'], 'trials')
        execution = result['frozen_preview']['execution']
        print('Concurrency:', execution['max_parallel'], 'of', execution['requested_max_parallel'], 'requested')
        print('Source bytes:', result['capture']['source_bytes'])
        print('Excluded:', inert(', '.join(result['capture']['excluded']) or '(none)'))
        print('Image:', result['frozen_preview']['image']['id'])
    elif command == 'list':
        for study in result['studies']:
            print(study['id'], inert(study['name']), study['state'], str(study['trial_count']) + ' trials')
            render_selection(study)
        if not result['studies']:
            print('No saved experiment studies in this project.')
    elif command == 'results':
        print('Study:', result['study_id'], result['state'])
        print('Variant | scheduled | runner OK | evaluated | quality passes / scheduled | failures')
        for row in result['variants']:
            print(inert(row['variant_id']), '|', row['scheduled'], '|', row['runner_successes'], '|', row['evaluated'], '|', str(row['quality_passes']) + '/' + str(row['scheduled']), '|', row['failed'])
            for name, metric in row['metrics'].items():
                print(' ', inert(name), 'mean:', metric['mean'], 'range:', metric['min'], 'to', metric['max'], 'missing:', metric['missing'])
        print('Missing quality results:', result['summary']['missing_quality'])
        for trial in result['trials']:
            print(trial['trial_id'], inert(trial['variant_id']), inert(trial['case_id']), trial['state'], 'attempt:', trial['attempt_id'])
        render_selection(result)
    elif command == 'logs':
        for row in result['logs']:
            print(row['attempt_id'], row['phase'])
            print(inert(row['text']), end='' if row['text'].endswith('\n') else '\n')
    elif command in ('diff', 'apply'):
        if result.get('attempt_id'): print('Result:', result['attempt_id'])
        if result.get('plan_id'): print('Plan:', result['plan_id'])
        if result.get('destination_revision'): print('Destination revision:', result['destination_revision'])
        if 'applied' in result: print('Applied:', result['applied'])
        for row in result.get('changes', []):
            print(inert(row['change']+' '+row['path']) + (' [variant patch]' if row.get('variant_patch') else ''))
            if row.get('text_diff'): print(inert(row['text_diff']))
        for row in result.get('conflicts', []): print('Conflict:', inert(row['path']), row['reason'])
        for row in result.get('excluded', []): print('Excluded:', inert(row['path']), row['reason'])
        if result.get('state')=='planned': print('Apply this reviewed plan: invariant experiment apply --plan '+result['plan_id'])
        if result.get('recovered') is not None: print(inert(json.dumps(result)))
        print('Project environments/packages are retained in trials; apply writes project files only.')
    elif command in ('stop', 'recover', 'retry', 'delete', 'select'):
        print(inert(json.dumps(result, indent=2)))
    else:
        print(result['kind'].capitalize() + ':', result['id'])
        print('State:', result['state'])
        if result.get('dispatch'):
            print('Controller:', result['dispatch']['state'])
            if result['dispatch'].get('reason'):
                from .errors import recovery_for
                print('Reason:', result['dispatch']['reason'])
                error = result['dispatch'].get('error') or {}
                if error.get('message'):
                    print(inert(error['message']))
                print('Next:', inert(error.get('recovery') or recovery_for(result['dispatch']['reason'])))
        if result.get('reason'):
            print('Reason:', result['reason'])
            if result.get('record', {}).get('recovery'):
                print('Next:', result['record']['recovery'])
        if result['kind'] == 'study':
            if result['frozen'].get('parent'):
                print('Continued from:', result['frozen']['parent']['attempt_id'], 'with a fresh conversation.')
            print('Name:', inert(result['name']))
            print('Trials:', len(result['trials']))
            for trial in result['trials']:
                print(' ', trial['id'], inert(trial['variant_id']), inert(trial['case_id']), trial['state'])
            render_selection(result)
            print('Baseline:', result['baseline_id'])
            print('Request:', inert(result['request_id']))
            print('Inspect: invariant experiment status ' + result['id'])
            if result.get('idempotent_replay'):
                print('Existing study returned; no duplicate was created.')
    if command in ('run', 'execute', 'status', 'continue') and result.get('kind') == 'study' and result['state'] != 'completed':
        print('Execution continues after closing this terminal. Use logs ID --follow, stop ID, or recover STUDY_ID.')


def render_selection(result):
    selection = result.get('selection')
    if selection is not None:
        print('Selected:', selection['attempt_id'] or '(none)', 'revision:', selection['revision'])


def wait_study(engine, identifier):
    import time
    while True:
        result = engine.inspect(identifier)
        dispatch = result.get('dispatch', {})
        if dispatch.get('state') == 'recovery_required':
            raise ExperimentError(dispatch.get('reason') or 'RECOVERY_REQUIRED',
                                  'Detached execution requires attention.', 'Inspect status, then recover STUDY_ID.')
        if dispatch.get('state') == 'completed' or result['state'] == 'completed':
            return result
        time.sleep(.2)
