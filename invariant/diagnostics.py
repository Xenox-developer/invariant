"""Read-only support diagnostics with no project content, credentials or raw logs."""
import json
import os
from pathlib import Path
import platform
import re
import sys

from . import __version__, dependencies, environment, workspace


def report(project=None):
    project=Path(project or Path.cwd()).resolve()
    dependencies_state=dependencies.inspect_dependencies()
    data={'schema_version':1,'kind':'invariant-diagnostics','invariant_version':__version__,
        'platform':{'system':platform.system(),'architecture':platform.machine(),'python':platform.python_version()},
        'supported':dependencies_state['supported'],'tools':{},'project':{'initialized':False},'issues':[]}
    for tool in ('docker','codex'):
        state=dependencies_state[tool]
        data['tools'][tool]={'status':state['status']}
        if tool=='codex':
            version=state.get('version','')
            data['tools'][tool]['version']=version if re.fullmatch(r'codex-cli [0-9A-Za-z.+-]{1,40}',version) else None
        if state['status']!='ready':
            data['issues'].append({'code':tool.upper()+'_UNAVAILABLE','action':'Run invariant setup --check, then invariant setup in a terminal.'})
    if not data['supported']:data['issues'].append({'code':'UNSUPPORTED_HOST','action':'Use a supported macOS or Linux host.'})
    if (project/'.invariant/config.json').exists():
        data['project']['initialized']=True
        try:
            policy=workspace.load_project(project);state=workspace.state_for(project)
            data['project'].update({'profile':policy['profile'],'network':policy['command_network'],
                                    'workspace_access':policy['workspace_access']})
            for filename,code,action in [('current.json','WORKSPACE_RECOVERY_REQUIRED','Finish the active session, or use invariant environment recover.'),
                    ('experiments/apply-active.json','APPLY_RECOVERY_REQUIRED','Run invariant experiment apply --recover.'),
                    ('restore-active.json','RESTORE_RECOVERY_REQUIRED','Run invariant experiment restore --recover.')]:
                if (state/filename).exists():data['issues'].append({'code':code,'action':action})
            if dependencies_state['docker']['status']=='ready':
                image=environment.selected_image(state,project,environment.IMAGE)
                identity=json.loads(workspace.docker('image','inspect',image))[0]
                data['project']['image_id']=identity['Id']
        except (OSError,RuntimeError,ValueError,KeyError,IndexError):
            data['issues'].append({'code':'PROJECT_OR_IMAGE_UNAVAILABLE','action':'Review invariant config; prepare the base image or restore its exact saved checkpoint.'})
    data['ready']=not data['issues']
    data['privacy']='No auth, environment-variable values, prompts, source files, private paths or raw logs are included.'
    return data


def main(arguments):
    from .experiments.cli import Parser
    machine='--json' in arguments
    parser=Parser(prog='invariant doctor',description='Read-only support diagnostics without credentials or project content.')
    parser.add_argument('--output',help='new report file; never overwrites an existing path')
    args=parser.parse_args([a for a in arguments if a!='--json'])
    data=report()
    if args.output:
        fd=os.open(Path(args.output).expanduser(),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as stream:json.dump(data,stream,indent=2)
    if machine:print(json.dumps({'schema_version':1,'ok':True,'data':data,'error':None}))
    else:
        print('INVARIANT',data['invariant_version'],'—','ready' if data['ready'] else 'needs attention')
        for item in data['issues']:print(item['code']+': '+item['action'])
        print(data['privacy'])
    return 0 if data['ready'] else 1
