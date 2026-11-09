"""JSON and terminal adapters to the durable task protocol."""
import contextlib
import json
import sys

from .experiments.cli import Parser
from .experiments.errors import ExperimentError
from .tasks import TaskRunner


def main(arguments):
    machine='--json' in arguments
    parser=Parser(prog='invariant task',description='Coordinate preparation, execution, waiting, review and guarded application with durable recovery.')
    parser.add_argument('action',choices=('run','status','wait','review','apply','continue','execute'))
    parser.add_argument('--request-id',required=True)
    parser.add_argument('--input',help='JSON job specification for execute')
    parser.add_argument('--apply',action='store_true',help='apply after passing checks and semantic approval (default in Docker)')
    parser.add_argument('--no-apply',action='store_true',help='export a reviewed result without changing the original project')
    parser.add_argument('--details',action='store_true',help='full durable state; default is bounded model response')
    parser.add_argument('--wait',action='store_true',help='wait in the coordinator after dispatch')
    parser.add_argument('--timeout',type=int,default=None,help='observer timeout; omitted waits until completion')
    parser.add_argument('--attempt')
    parser.add_argument('--review-revision',type=int)
    parser.add_argument('--parent')
    parser.add_argument('--review-digest')
    parser.add_argument('--reviewed-full',action='store_true')
    parser.add_argument('--apply-include',action='append')
    parser.add_argument('--apply-exclude',action='append')
    parser.add_argument('--task')
    parser.add_argument('--name')
    parser.add_argument('--test-command')
    parser.add_argument('--test-report')
    parser.add_argument('--test-path',action='append')
    parser.add_argument('--model')
    parser.add_argument('--effort')
    parser.add_argument('--efforts',nargs='+')
    parser.add_argument('--variant',nargs=2,action='append',metavar=('MODEL','EFFORT'))
    parser.add_argument('--max-parallel',type=int)
    parser.add_argument('--package',action='append')
    parser.add_argument('--prepare-command')
    parser.add_argument('--allow-network',action='store_true',default=None)
    parser.add_argument('--preparation-timeout',type=int)
    parser.add_argument('--test-timeout',type=int)
    parser.add_argument('--retry-preparation',action='store_true')
    try:
        args=parser.parse_args([a for a in arguments if a!='--json'])
        mapping={'task':'task','name':'name','test_command':'test_command','test_path':'test_paths','test_report':'test_report',
                 'model':'model','effort':'effort','efforts':'efforts','variant':'variants','max_parallel':'max_parallel',
                 'package':'packages','prepare_command':'prepare_command',
                 'apply_include':'apply_include','apply_exclude':'apply_exclude','allow_network':'allow_network','preparation_timeout':'preparation_timeout','test_timeout':'test_timeout'}
        value={target:getattr(args,source) for source,target in mapping.items() if getattr(args,source) is not None}
        if args.timeout is not None and not 0<=args.timeout<=86400:parser.error('--timeout must be 0–86400 seconds')
        from . import task_results
        if args.action not in ('run','continue') and (value or args.retry_preparation or args.wait):
            parser.error('Observation/review/apply takes no task setup options')
        if args.action=='continue' and (set(value)-{'task','test_command','test_paths','test_timeout','test_report'} or not args.parent or 'task' not in value):
            parser.error('continue requires --parent and --task; inherits frozen settings')
        if args.action!='continue' and args.parent:parser.error('--parent requires continue')
        if args.action!='review' and (args.attempt or args.review_revision is not None):parser.error('--attempt requires review')
        if args.action!='apply' and (args.review_digest or args.reviewed_full):parser.error('Review approval flags require apply')
        if args.action!='run' and args.retry_preparation:parser.error('--retry-preparation requires run')
        if args.action=='execute':
            if not args.input or value or args.wait or args.retry_preparation:parser.error('execute requires --input and no inline task options')
        elif args.input or args.apply:parser.error('--input/--apply require execute')
        runner=TaskRunner()
        # Existing image/setup helpers may print; keep stdout machine-readable.
        with contextlib.redirect_stdout(sys.stderr):
            if args.action=='execute':
                from .task_runtime import Coordinator
                from .experiments.snapshots import regular_bytes
                from .experiments.definitions import parse_json
                source=runner.project/args.input
                relative=source.relative_to(runner.project).as_posix()
                request=parse_json(regular_bytes(runner.project,relative,maximum=256*1024)[0],'job specification')
                from .docker_storage import managed
                result=Coordinator(runner=runner).execute(args.request_id,request,apply=(args.apply or managed()) and not args.no_apply,timeout=args.timeout)
            elif args.action=='run':result=runner.run(args.request_id,value or None,retry_preparation=args.retry_preparation)
            elif args.action=='continue':result=runner.continue_task(args.request_id,args.parent,args.task,evaluation={k:v for k,v in value.items() if k!='task'} or None)
            elif args.action=='wait':result=task_results.wait(runner,args.request_id,timeout=args.timeout)
            elif args.action=='review':result=task_results.review(runner,args.request_id,args.attempt,revision=args.review_revision)
            elif args.action=='apply':result=task_results.apply(runner,args.request_id,args.review_digest,reviewed_full=args.reviewed_full)
            else:result=runner.status(args.request_id)
            if args.wait and result['state']=='submitted':result=task_results.wait(runner,args.request_id,timeout=args.timeout)
        if not args.details and args.action!='execute':result=task_results.compact_record(runner,result,include_review=args.action=='review')
        print(json.dumps({'schema_version':1,'ok':True,'data':result,'error':None}) if machine else json.dumps(result,indent=2))
        return 1 if result['state']=='blocked' or result.get('completion_state')=='rejected' else 0
    except KeyboardInterrupt:
        error={'code':'INTERRUPTED','message':'Viewer/process interrupted; inspect task status before resuming. Preparation is never assumed not to have run.'}
        print(json.dumps({'schema_version':1,'ok':False,'data':None,'error':error}) if machine else error['message'])
        return 130
    except (OSError,ValueError,RuntimeError) as exception:
        error=exception.record() if isinstance(exception,ExperimentError) else {
            'code':'TASK_FAILED','message':str(exception),'recovery':'Inspect invariant task status using the original request ID.'}
        print(json.dumps({'schema_version':1,'ok':False,'data':None,'error':error}) if machine else error['message'])
        return 2 if error['code']=='INVALID_ARGUMENTS' else 1
