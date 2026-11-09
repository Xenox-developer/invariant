"""Baseline: stock Docker + Codex CLI. No INVARIANT runtime/library imports."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import time

DOCKER=shutil.which('docker') or '/usr/local/bin/docker'
CAPS=('CHOWN','DAC_OVERRIDE','FOWNER','FSETID','SETGID','SETUID','SYS_CHROOT','SETFCAP')


def docker(*args,input=None,check=True,timeout=180):
    result=subprocess.run([DOCKER,*map(str,args)],input=input,capture_output=True,timeout=timeout)
    if check and result.returncode:raise RuntimeError('Docker '+args[0]+': '+result.stderr.decode(errors='replace')[-1200:])
    return result


def archive_files(files):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        for relative,content in files.items():
            entry=tarfile.TarInfo(relative);entry.size=len(content);entry.mode=0o600
            archive.addfile(entry,io.BytesIO(content))
    return stream.getvalue()


def unpack_files(archive,destination):
    for entry in archive:
        parts=PurePosixPath(entry.name)
        if parts.is_absolute() or '..' in parts.parts or not entry.isfile():
            raise RuntimeError('Unexpected archive entry')
        target=Path(destination)/entry.name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(archive.extractfile(entry).read())


class Plain:
    def __init__(self,namespace,*,image,runtime_image,auth_volume,output):
        self.namespace=namespace;self.image=image;self.runtime_image=runtime_image
        self.state=namespace+'-state';self.tools=namespace+'-tools';self.output=Path(output)
        self.auth_volume=auth_volume;self.names=[];self.records=[]
        self.output.mkdir(parents=True,exist_ok=True)

    def helper(self,script,*,input=None):
        return docker('run','--rm','--interactive','--network=none','--cap-drop=ALL',
            '--security-opt=no-new-privileges','--mount',f'type=volume,src={self.state},dst=/state',
            '--entrypoint','python3',self.runtime_image,'-B','-c',script,input=input)

    def prepare(self,seed):
        for volume in (self.state,self.tools):
            assert docker('volume','inspect',volume,check=False).returncode!=0,'Resource namespace already in use'
            docker('volume','create','--label','io.invariant.research=integration-team',volume)
        self.daemon_path=json.loads(docker('volume','inspect',self.state).stdout)[0]['Mountpoint']
        docker('run','--rm','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges',
            '--mount',f'type=volume,src={self.tools},dst=/tools','--entrypoint','/bin/sh',self.runtime_image,
            '-c','cp /__invariant/codex /__invariant/codex-code-mode-host /tools/')
        script='''from pathlib import Path
import shutil
for role in ('trapezoid','simpson','checker'):
    home=Path('/state/homes')/role; (home/'codex').mkdir(parents=True);(home/'home').mkdir()
    shutil.copyfile('/seed/accounts/current/auth.json',home/'codex/auth.json')
    (home/'codex/auth.json').chmod(0o600)
    (Path('/state/work')/role).mkdir(parents=True)
'''
        docker('run','--rm','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges',
            '--mount',f'type=volume,src={self.state},dst=/state',
            '--mount',f'type=volume,src={self.auth_volume},dst=/seed,readonly',
            '--entrypoint','python3',self.runtime_image,'-B','-c',script)
        self.upload(seed,('trapezoid','simpson'))
        self.helper("from pathlib import Path; import shutil; Path('/state/checks').mkdir(); shutil.copyfile('/state/work/trapezoid/fixed_checks.py','/state/checks/fixed_checks.py')")

    def upload(self,seed,roles):
        data=archive_files({p.relative_to(seed).as_posix():p.read_bytes() for p in Path(seed).rglob('*')
            if p.is_file() and (p.relative_to(seed).parts[0]=='inputs' or p.name in ('solution.py','result.json','TASK.md','fixed_checks.py'))})
        script='''import sys,tarfile,io
from pathlib import Path,PurePosixPath
raw=sys.stdin.buffer.read()
for role in ROLES:
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for entry in archive:
            path=PurePosixPath(entry.name)
            if path.is_absolute() or '..' in path.parts or not entry.isfile():raise RuntimeError('Unexpected seed entry')
            target=Path('/state/work')/role/entry.name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.extractfile(entry).read())
'''.replace('ROLES',repr(tuple(roles)))
        self.helper(script,input=data)

    def run_agent(self,role,prompt,model,effort):
        name=self.namespace+'-'+role
        assert docker('inspect',name,check=False).returncode!=0,'Container already exists'
        args=['create','--name',name,'--init','--interactive','--network=bridge','--cap-drop=ALL',
              '--security-opt=no-new-privileges','--tmpfs=/scratch:rw,nosuid,nodev',
              '--label','io.invariant.research=integration-team','--workdir=/workspace',
              '--mount',f'type=bind,src={self.daemon_path}/work/{role},dst=/workspace',
              '--mount',f'type=bind,src={self.daemon_path}/homes/{role},dst=/agent',
              '--mount',f'type=volume,src={self.tools},dst=/tools,readonly',
              '--env','CODEX_HOME=/agent/codex','--env','HOME=/agent/home','--env','TMPDIR=/scratch',
              '--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint=/tools/codex']
        for capability in CAPS:args+=['--cap-add',capability]
        args += [self.image,'exec','--json','--ignore-user-config','--ignore-rules','--skip-git-repo-check',
                 '--dangerously-bypass-approvals-and-sandbox','--model',model,'-c','model_reasoning_effort='+json.dumps(effort),
                 '-c','cli_auth_credentials_store="file"','-c','web_search="disabled"',
                 '-c','features.multi_agent=false','-c','features.code_mode_host=true',
                 '-c','features.skip_host_skill_discovery=true',
                 '-c','shell_environment_policy.inherit="none"',
                 '-c','shell_environment_policy.set={HOME="/root", TMPDIR="/scratch", PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}',
                 '-C','/workspace','-']
        started=time.time();docker(*args);self.names.append(name)
        info=json.loads(docker('inspect',name).stdout)[0]
        assert info['Image']==self.image
        assert not info['HostConfig']['Privileged'] and not info['HostConfig'].get('Devices')
        assert all(m['Source'].startswith(self.daemon_path+'/') or m.get('Name')==self.tools for m in info['Mounts'])
        log=self.output/(role+'.events.jsonl');error=self.output/(role+'.stderr.log')
        with log.open('w') as output,error.open('w') as errors:
            process=subprocess.Popen([DOCKER,'start','--attach','--interactive',name],stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=errors,text=True)
            process.stdin.write(prompt);process.stdin.close()
            events=[]
            for line in process.stdout:
                output.write(line);output.flush()
                try:events.append({'observed_at':time.time(),'event':json.loads(line)})
                except ValueError:pass
            process.stdout.close();exit_code=process.wait()
        finished=time.time()
        completed=[e for e in events if e['event'].get('type')=='turn.completed']
        record={'role':role,'model':model,'effort':effort,'container':name,'started_at':started,'finished_at':finished,
            'duration_seconds':finished-started,'exit_code':exit_code,
            'cli_usage':completed[-1]['event'].get('usage') if completed else None,
            'turn_started_at':next((e['observed_at'] for e in events if e['event'].get('type')=='turn.started'),None),
            'turn_finished_at':completed[-1]['observed_at'] if completed else None,
            'host_config':info['HostConfig'],'image':info['Image']}
        self.records.append(record)
        (self.output/(role+'.execution.json')).write_text(json.dumps(record,indent=2)+'\n')
        if exit_code or not completed:raise RuntimeError('Plain agent failed: '+role+'; inspect '+str(error))
        return record

    def parallel(self,prompts,model,effort):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(self.run_agent,r,prompts[r],model,effort) for r in ('trapezoid','simpson')]
            return [future.result() for future in futures]

    def handoff(self,checker_seed):
        self.upload(checker_seed,('checker',))
        self.helper('''from pathlib import Path
import shutil
for role in ('trapezoid','simpson'):
    target=Path('/state/work/checker/inputs')/role;target.mkdir(parents=True,exist_ok=True)
    for name in ('solution.py','result.json'):shutil.copyfile(Path('/state/work')/role/name,target/name)
shutil.copytree('/state/work/checker/inputs','/state/reference/inputs')
''')

    def collect(self):
        script='''import json,tarfile,sys
from pathlib import Path
reports={}
sys.path.insert(0,'/state/checks')
from fixed_checks import validate
for role in ('trapezoid','simpson','checker'):
    work=Path('/state/work')/role
    reports[role]=validate(role,work,Path('/state/reference') if role=='checker' else None)
    # Only numeric usage observations and model metadata leave the private home.
    sessions=[]
    for path in (Path('/state/homes')/role/'codex/sessions').rglob('*.jsonl'):
        observations=[];model=None;effort=None;previous=None
        for line in path.open():
            item=json.loads(line);payload=item.get('payload',{})
            if item.get('type')=='turn_context':model=payload.get('model',model);effort=payload.get('effort',effort)
            if item.get('type')=='event_msg' and payload.get('type')=='token_count' and payload.get('info'):
                info=payload['info'];total=info.get('total_token_usage');last=info.get('last_token_usage')
                if total and total!=previous:
                    observations.append({'total':total,'last':last,'timestamp':item.get('timestamp')});previous=total
        sessions.append({'model':model,'effort':effort,'observations':observations})
    reports[role]['sessions']=sessions
Path('/state/evidence.json').write_text(json.dumps(reports))
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as archive:
    archive.add('/state/evidence.json',arcname='evidence.json')
    for role in ('trapezoid','simpson','checker'):
        for name in ('solution.py','result.json','build_report.py','comparison.csv','report.md','errors.svg','verification.json'):
            path=Path('/state/work')/role/name
            if path.is_file():archive.add(path,arcname='agents/'+role+'/'+name)
'''
        raw=self.helper(script).stdout
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:unpack_files(archive,self.output)
        evidence=json.loads((self.output/'evidence.json').read_text())
        for record in self.records:
            record['acceptance']=evidence[record['role']]
            (self.output/(record['role']+'.execution.json')).write_text(json.dumps(record,indent=2)+'\n')
        return evidence

    def close(self):
        for name in self.names:docker('rm','--volumes',name,check=False)
        # Named volumes retain private homes and workspaces for continuation.
