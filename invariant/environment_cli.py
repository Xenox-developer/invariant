"""Explicit dependency preparation using the existing isolated environment lifecycle."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys
import threading
import uuid

from . import environment, preferences, resources, workspace
from .execution import ExecutionContext, private_directory, project_lock


def prepare(command, *, project=None, timeout=None, network=None):
    from .experiments.runners import CommandRunner
    project=Path(project or Path.cwd()).resolve()
    policy=workspace.load_project(project)
    if network is not None:
        from .policy import normalize
        policy=normalize({**policy, 'command_network':network})
    if not environment.enabled(policy) or policy['workspace_access']!='read-write':
        raise RuntimeError('Dependency preparation requires the general read-write environment. Review invariant config.')
    if not isinstance(command,str) or not command.strip() or len(command.encode())>8192 or '\0' in command:
        raise ValueError('Supply a nonempty preparation command of at most 8192 bytes.')
    timeout=policy['duration_seconds'] if timeout is None else timeout
    if type(timeout) is not int or timeout<0 or (policy['duration_seconds'] and (not timeout or timeout>policy['duration_seconds'])):
        raise ValueError('Preparation timeout must fit the configured session duration.')
    state=private_directory(workspace.state_for(project))
    lock=project_lock(state)
    session='ws-'+uuid.uuid4().hex;name='invariant-'+session
    context=None;marker=state/'current.json'
    try:
        if marker.exists():
            raise RuntimeError('An interrupted session needs recovery. Run invariant environment recover first; commands are never replayed.')
        workspace.check_project_files(project,allow_symlinks=True,max_entries=200000 if policy['restrictions_enabled'] else 0)
        policy,sizing=resources.select(policy,project,resources.capacity(workspace.docker))
        base=workspace.docker('image','inspect',environment.IMAGE,'--format','{{.Id}}')
        environment.prepare_control(project,base)
        image=environment.selected_image(state,project,base)
        context=ExecutionContext.create(project=project,workspace=project,state=state,image=image,
            owner_token=name,policy=policy,control_directory=environment.control_path(project),
            command=('/bin/sh','-lc',command))
        workspace.private_write(marker,{'session':session,'pid':os.getpid(),'duration_seconds':timeout,'profile':policy['profile']})
        try:
            result=CommandRunner().run(context,timeout=timeout,cancel=threading.Event(),
                log_path=state/(session+'-prepare.log'),limit=1024*1024 if policy['restrictions_enabled'] else None)
        finally:
            context.preserve(name)
            marker.unlink(missing_ok=True)
        result={'schema_version':1,'prepared':result['status']=='completed','session':session,
                'exit_code':result['exit_code'],'reason':result['reason'],'log':result['log'],
                'image':environment.read_record(state/'environment.json')['image'],
                'resources':sizing,'model_turns':0,'network':policy['command_network']}
        workspace.private_write(state/'preparation.json',result)
        return result
    finally:
        lock.close()


def package_command(packages, download_retries=3):
    if (not isinstance(packages, list) or not 1 <= len(packages) <= 128
            or any(not isinstance(p, str) or len(p)>128 or not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::[a-z0-9-]+)?', p) for p in packages)):
        raise ValueError('Supply 1–128 Debian package names, without options or shell syntax.')
    if type(download_retries) is not int or not 0 <= download_retries <= 5:
        raise ValueError('Download retries must be between 0 and 5.')
    script=(workspace.ASSETS/'prepare-packages.py').read_text()
    value=json.dumps({'packages':sorted(set(packages)),'retries':download_retries})
    command='DEBIAN_FRONTEND=noninteractive '+shlex.join(['python3','-I','-c',script,value])
    if len(command.encode())>8192:raise ValueError('Package preparation exceeds the command size limit; use fewer packages.')
    return command


def prepare_packages(packages, *, project=None, timeout=None, allow_network=False, download_retries=3):
    """Retry typed acquisition phases; run maintainer scripts once, offline."""
    command=package_command(packages,download_retries)
    result=prepare(command,project=project,timeout=timeout,network='internet' if allow_network else None)
    return {**result, 'packages':sorted(set(packages)), 'download_retries':download_retries,
            'command_replayed':False}


def recover(project=None):
    project=Path(project or Path.cwd()).resolve();state=workspace.state_for(project)
    lock=project_lock(state)
    try:
        record=environment.read_record(state/'current.json')
        if record:
            if not re.fullmatch(r'ws-[0-9a-f]{32}',record.get('session','')):
                raise RuntimeError('Invalid recovery identity; the record was retained.')
            environment.recover('invariant-'+record['session'],state,project,lambda *a,**k:None)
            (state/'current.json').unlink()
        return {'recovered':True,'replayed_commands':0}
    finally:lock.close()


def main(arguments):
    from .experiments.cli import Parser
    machine='--json' in arguments
    parser=Parser(prog='invariant environment',description='Prepare dependencies inside the persistent Linux environment, without a model turn.')
    parser.add_argument('command',choices=('prepare','packages','recover','budget'))
    parser.add_argument('--command',dest='shell_command',help='explicit Linux shell command; project writes and configured network apply')
    parser.add_argument('--timeout',type=int)
    parser.add_argument('--package',action='append')
    parser.add_argument('--allow-network',action='store_true',help='network for this preparation only; project settings stay unchanged')
    parser.add_argument('--download-retries',type=int,default=3)
    parser.add_argument('--container',help='verify a container against the effective resource budget')
    try:
        args=parser.parse_args([a for a in arguments if a!='--json'])
        if args.command not in ('packages',) and (args.package or args.download_retries!=3):
            parser.error('--package and --download-retries apply only to packages')
        if args.command!='budget' and args.container:parser.error('--container applies only to budget')
        if args.command in ('recover','budget') and (args.shell_command or args.timeout is not None or args.allow_network):
            parser.error('recover/budget take no preparation command, timeout or network override')
        if args.command=='recover':
            result=recover()
        elif args.command=='budget':
            project=Path.cwd().resolve()
            policy,_=resources.select(workspace.load_project(project),project,resources.capacity(workspace.docker),
                                      preferences.memory_hint(workspace.state_for(project),project))
            result=resources.execution_contract(policy)
            if args.container:
                info=json.loads(workspace.docker('inspect',args.container))[0]
                resources.verify_contract(info,result)
                result={**result,'verified':True,'container':args.container}
        elif args.command=='packages':
            if args.shell_command:parser.error('packages takes package names, not a shell command')
            result=prepare_packages(args.package,timeout=args.timeout,allow_network=args.allow_network,
                                    download_retries=args.download_retries)
        else:
            if not args.shell_command:parser.error('prepare requires --command')
            result=prepare(args.shell_command,timeout=args.timeout,network='internet' if args.allow_network else None)
        if machine:print(json.dumps({'schema_version':1,'ok':True,'data':result,'error':None}))
        else:print(json.dumps(result,indent=2))
        return 0 if result.get('prepared',True) else 1
    except KeyboardInterrupt:
        record={'code':'INTERRUPTED','message':'Preparation interrupted; no command will be replayed.'}
        if machine:print(json.dumps({'schema_version':1,'ok':False,'data':None,'error':record}))
        else:print(record['message'],file=sys.stderr)
        return 130
    except (OSError,ValueError,RuntimeError) as error:
        record={'code':getattr(error,'code','ENVIRONMENT_PREPARATION_FAILED'),'message':str(error),
                'recovery':'Check invariant doctor and the saved preparation log. Use environment recover after an interruption; rerun commands explicitly.'}
        if machine:print(json.dumps({'schema_version':1,'ok':False,'data':None,'error':record}))
        else:print(record['message']+'\nNext: '+record['recovery'],file=sys.stderr)
        return 1
