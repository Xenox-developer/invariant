"""Human and JSON adapters over the same experiment engine."""
import argparse
import base64
import json
import sqlite3
import subprocess
import sys
import tarfile

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
    demo = commands.add_parser('demo', help='create a three-configuration Codex coding demo in its own project')
    demo.add_argument('directory', nargs='?', default='invariant-demo')
    demo.add_argument('--model', help='one supported model for all three configurations; omit for account default')
    demo.add_argument('--efforts', nargs=3, metavar='LEVEL', help='three distinct supported efforts; default: low medium high')
    demo.add_argument('--run', action='store_true', help='run/reopen the saved demo; up to three real model turns on its first run')
    exporting=commands.add_parser('export',help='export a summary report; private task/diff/log content requires explicit flags')
    exporting.add_argument('identifier')
    exporting.add_argument('--format',choices=('markdown','json','csv'),default='markdown')
    exporting.add_argument('--output',help='new output file; existing files are never overwritten')
    for field in ('task','diff','logs'):exporting.add_argument('--include-'+field,action='store_true')
    commands.add_parser('storage',help='show study, shared baseline and Docker image sizes')
    cleaning=commands.add_parser('cleanup',help='preview retention cleanup; selection, references and running work are protected')
    cleaning.add_argument('--older-than-days',type=float,default=30)
    cleaning.add_argument('--confirm',action='store_true')
    backing=commands.add_parser('backup',help='private backup of stopped experiments and exact Docker images; host authentication omitted')
    backing.add_argument('--output',required=True,help='new private archive outside the project and live state')
    restoring=commands.add_parser('restore',help='restore your private backup at the same project/state path')
    restoring.add_argument('archive',nargs='?')
    restoring.add_argument('--replace',action='store_true',help='retain old state in a private sibling before replacement')
    restoring.add_argument('--recover',action='store_true',help='reconcile an interrupted state swap without executing work')
    for name in ('init', 'validate', 'run', 'preflight'):
        command = commands.add_parser(name)
        command.add_argument('file', nargs='?' if name=='init' else None, help='project-relative experiment JSON file; omit for guided setup')
        if name == 'init':
            command.add_argument('--request-id', help='idempotency key for starter creation')
            command.add_argument('--runner', choices=('command', 'codex'))
            command.add_argument('--guided',action='store_true',help='answer setup prompts; --json always requires flags instead')
            command.add_argument('--task',help='coding task shared by all configurations')
            command.add_argument('--test-command',help='shared shell command executed inside Docker only')
            command.add_argument('--test-path',action='append',help='project-relative test file/folder to freeze; repeat for more paths')
            command.add_argument('--model',help='one model for all configurations; omit for account default')
            command.add_argument('--efforts',nargs='+',help='reasoning efforts to compare; default: low medium high')
            command.add_argument('--variant',nargs=2,action='append',metavar=('MODEL','EFFORT'),help='explicit model/effort pair; repeat to compare different models')
            command.add_argument('--name',help='experiment name; default: comparison')
            command.add_argument('--max-parallel',type=int,help='requested parallelism; default: number of configurations')
            command.add_argument('--test-timeout',type=int,default=120,help='shared test deadline in seconds; default: 120')
            command.add_argument('--run',action='store_true',help='run/reopen the generated study and wait for its comparison')
        if name == 'run':
            command.add_argument('--request-id', help='idempotency key for durable study creation')
            command.add_argument('--create-only', action='store_true', help='freeze/queue without executing')
            command.add_argument('--wait', action='store_true', help='wait for the detached study; Ctrl+C only closes the viewer')
        if name == 'preflight':command.add_argument('--request-id')
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
            task = command.add_mutually_exclusive_group(required=True)
            task.add_argument('--prompt-file', help='project-relative new task; starts a fresh conversation')
            task.add_argument('--task',help='new task text; no prompt file required')
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
    reviewing = commands.add_parser('review',help='compare saved runs, choose one, continue or review/apply; JSON never prompts')
    reviewing.add_argument('identifier')
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
        engine = None if args.command == 'demo' else ExperimentEngine(allow_restore=True) if args.command=='restore' else ExperimentEngine()
        if args.command == 'demo':
            from . import demo
            result = demo.create(args.directory, model=args.model, efforts=args.efforts)
            if args.run:
                result = demo.execute(result)
        elif args.command == 'backup':
            from .backup import backup
            result=backup(engine,args.output)
        elif args.command == 'restore':
            from .backup import restore
            if args.recover and (args.archive or args.replace):raise ExperimentError('INVALID_ARGUMENTS','--recover takes no archive or replacement flag.')
            result=restore(engine,args.archive,replace=args.replace,recover=args.recover)
        elif args.command == 'storage':
            from .storage import overview
            result=overview(engine)
        elif args.command == 'cleanup':
            from .storage import cleanup
            result=cleanup(engine,older_than_days=args.older_than_days,confirm=args.confirm)
        elif args.command == 'export':
            from .exporting import export
            result=export(engine,args.identifier,format=args.format,output=args.output,
                include_task=args.include_task,include_diff=args.include_diff,include_logs=args.include_logs)
        elif args.command == 'preflight':
            from .preflight import check
            result=check(engine,args.file,request_id=args.request_id)
        elif args.command == 'init':
            guided = args.file is None or args.guided or any(getattr(args,k) is not None for k in
                ('task','test_command','test_path','model','efforts','variant','name','max_parallel')) or args.run or args.test_timeout!=120
            if guided:
                from .guided import collect, create, execute
                if args.runner not in (None,'codex'):
                    raise ExperimentError('INVALID_ARGUMENTS','Guided setup currently uses Codex; use init FILE --runner command for a command starter.')
                wizard = not machine and sys.stdin.isatty() and (args.guided or not all((args.task,args.test_command,args.test_path)))
                options = collect(args,interactive=wizard)
                result = create(engine,args.file,request_id=args.request_id,**options)
                if wizard and not args.run:
                    render('init',result)
                    args.run = input('Start '+str(len(result['configurations']))+' real Codex runs now? [y/N]: ').strip().lower()=='y'
                if args.run:
                    result = execute(engine,result,request_id=args.request_id)
            else:
                result = engine.init_definition(args.file, request_id=args.request_id, runner=args.runner or 'command')
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
        elif args.command == 'review':
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
            prompt = args.prompt_file
            if args.task is not None:
                from .guided import inline_prompt
                prompt = inline_prompt(engine,args.task)
            result = engine.continue_result(args.identifier, prompt, request_id=args.request_id)
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
            if not (args.command=='init' and locals().get('wizard') and not args.run):
                render(args.command, result)
            if args.command == 'demo' and args.run and sys.stdin.isatty():
                from .demo import interact
                interact(result)
            elif sys.stdin.isatty() and (args.command=='review' or args.command=='init' and result.get('study_id')):
                from .review import interact
                interact(engine,result.get('study_id') or args.identifier)
        return 1 if result.get('errors') or result.get('ready') is False or result.get('termination_confirmed') is False or result.get('unresolved_attempt_ids') else 0
    except EOFError:
        return 0  # Closing the guided demo prompt never discards saved work.
    except (KeyboardInterrupt, BrokenPipeError):
        if not machine and 'frame' in locals() and not isinstance(sys.exc_info()[1], BrokenPipeError):
            print('\nResume logs: invariant experiment logs ' + args.identifier + ' --follow --after ' + frame['cursor'])
        return 130
    except (ExperimentError, OSError, RuntimeError, sqlite3.Error, subprocess.SubprocessError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        if isinstance(error,tarfile.TarError):
            error=ExperimentError('INVALID_BACKUP','The backup archive is incomplete or invalid.','Restore an intact private archive; current state was kept.')
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
    if command=='export':
        print(result.get('content') or ('Saved '+result['format']+' report: '+result['output']),end='' if result.get('content') else '\n')
    elif command == 'demo':
        from .demo import present
        present(result)
    elif command == 'init':
        print('Created definition:', inert(result['definition']))
        print('Edit guide:', inert(result['guide']))
        if result.get('guided'):
            print('Configurations:',len(result['configurations']),'requested parallelism:',result['requested_parallel'])
            for config in result['configurations']:
                print(' ',inert(config['id']),inert(config.get('model') or 'account default'),'effort:',inert(config.get('effort') or 'default'))
            print('Shared test command (Docker):',inert(result['test_command']))
            print('Fixed test files:',result['test_file_count'],'from',inert(', '.join(result['test_paths'])))
            print('Requires prepared Docker and host Codex ChatGPT sign-in. Setup itself uses no model turns.')
            if result.get('results'):
                from .review import present
                present(result['results'])
            else:
                import shlex
                print('Run: invariant experiment run '+shlex.quote(result['definition'])+' --wait --request-id YOUR_KEY')
    elif command == 'review':
        from .review import present
        present(result)
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
