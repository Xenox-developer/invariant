"""Explicit dependency preparation using the existing isolated environment lifecycle."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import threading
import uuid

from . import environment, resources, workspace
from .execution import ExecutionContext, private_directory, project_lock


def prepare(command, *, project=None, timeout=None):
    from .experiments.runners import CommandRunner
    project=Path(project or Path.cwd()).resolve()
    policy=workspace.load_project(project)
    if not environment.enabled(policy) or policy['workspace_access']!='read-write':
        raise RuntimeError('Dependency preparation requires the general read-write environment. Review invariant config.')
    if not isinstance(command,str) or not command.strip() or len(command.encode())>8192 or '\0' in command:
        raise ValueError('Supply a nonempty preparation command of at most 8192 bytes.')
    timeout=policy['duration_seconds'] if timeout is None else timeout
    if type(timeout) is not int or not 1<=timeout<=policy['duration_seconds']:
        raise ValueError('Preparation timeout must fit the configured session duration.')
    state=private_directory(workspace.state_for(project))
    lock=project_lock(state)
    session='ws-'+uuid.uuid4().hex;name='invariant-'+session
    context=None;marker=state/'current.json'
    try:
        if marker.exists():
            raise RuntimeError('An interrupted session needs recovery. Run invariant environment recover first; commands are never replayed.')
        workspace.check_project_files(project,allow_symlinks=True)
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
                log_path=state/(session+'-prepare.log'),limit=1024*1024)
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
    parser.add_argument('command',choices=('prepare','recover'))
    parser.add_argument('--command',dest='shell_command',help='explicit Linux shell command; project writes and configured network apply')
    parser.add_argument('--timeout',type=int)
    try:
        args=parser.parse_args([a for a in arguments if a!='--json'])
        if args.command=='recover':
            if args.shell_command or args.timeout is not None:parser.error('recover takes no preparation command or timeout')
            result=recover()
        else:
            if not args.shell_command:parser.error('prepare requires --command')
            result=prepare(args.shell_command,timeout=args.timeout)
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
