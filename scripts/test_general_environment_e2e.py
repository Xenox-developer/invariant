#!/usr/bin/env python3
"""General Linux toolchains, immutable inspection and whole-environment resume."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from invariant import environment
from invariant.workspace import docker, private_write, remove_owned, state_for, container_command, verify_container


def sources():
    paths = [p for p in (ROOT/'invariant').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    paths += [Path(__file__), ROOT/'docs/LEGACY_MVP_CONTRACT.md', ROOT/'scripts/invariant']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',metavar='MODEL',help='use two real Codex turns across offline native resume')
    parser.add_argument('--effort',default='medium')
    args=parser.parse_args()
    output=ROOT/'.invariant/general-environment-checks'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8])
    project=output/'project';project.mkdir(parents=True,mode=0o700)
    report={'passed':False,'checks':[],'source_sha256':sources(),'project':str(project),'live_model':args.live,'cleanup_errors':[]}
    child=None;sequence=0;chat=None;containers=[]
    nonce='environment-'+uuid.uuid4().hex

    def require(ok,message):
        if not ok: raise AssertionError(message)

    def passed(name):
        report['checks'].append(name);print('PASS '+name,flush=True)

    def cli(*commands,cwd=None):
        r=subprocess.run([str(ROOT/'scripts/invariant'),*commands],cwd=cwd or project,capture_output=True,text=True,timeout=45)
        require(r.returncode==0,r.stderr+r.stdout);return r.stdout

    def wait(condition,log,seconds=240):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if condition(): return
            if child.poll() is not None: raise AssertionError('Launcher exited: '+log.read_text()[-5000:])
            time.sleep(.2)
        raise AssertionError('Timed out: '+log.read_text()[-5000:])

    def start(label,*,resume=False):
        nonlocal child,sequence
        sequence+=1;log=output/(str(sequence)+'-'+label+'.log')
        command=['resume',chat] if resume else ['run']
        if not resume and args.live: command+=['--model',args.live]
        if args.live: command+=['--effort',args.effort]
        command+=['--duration','900']
        with log.open('w') as stream:
            child=subprocess.Popen([str(ROOT/'scripts/invariant'),*command],cwd=project,stdin=subprocess.PIPE,
                                   stdout=stream,stderr=subprocess.STDOUT,text=True)
        wait(lambda:'\nYou> ' in log.read_text(),log,120)
        return log

    def close(log):
        nonlocal child
        child.stdin.write('/exit\n');child.stdin.flush()
        require(child.wait(timeout=210)==0,log.read_text()[-5000:]);child.stdin.close();child=None
        require(not (state_for(project)/'current.json').exists(),'Active record survived successful checkpoint')

    def message(text,log):
        before=log.read_text().count('\nYou> ')
        child.stdin.write(text+'\n');child.stdin.flush()
        wait(lambda:log.read_text().count('\nYou> ')>before,log)

    def inspect(label):
        r=json.loads(cli('guardrails','--json'));require(r['verified'],str(r['issues']))
        name='invariant-'+r['session']
        if name not in containers: containers.append(name)
        private_write(output/(label+'.json'),r);return r,name

    def command(name,text,label,timeout=90):
        result=docker('exec',name,'bash','-lc',text,timeout=timeout)
        (output/(label+'.txt')).write_text(result);return result

    def use_tools(name,label):
        text=command(name,"""set -eu
test "$(cat /root/environment-marker)" = saved
test "$(cat /opt/environment-marker)" = saved
test "$(hello-environment)" = profile-restored
test -L /workspace/.venv/bin/python
/workspace/.venv/bin/python -c "import packaging;assert packaging.__version__=='24.2'"
node -e "if(require('/workspace/js/node_modules/is-odd')(3)!==true)process.exit(1)"
test "$(node -p \"require('/usr/local/lib/node_modules/semver/package.json').version\")" = 7.7.2
test "$(semver -r '^1' 1.2.3)" = 1.2.3
test "$(/usr/local/bin/environment-rust)" = rust-ok
test "$(/usr/local/bin/environment-c)" = c-ok
rustc /workspace/probe.rs -o /scratch/recompiled-rust
cc /workspace/probe.c -o /scratch/recompiled-c
test "$(/scratch/recompiled-rust)" = rust-ok
test "$(/scratch/recompiled-c)" = c-ok
cargo --version
test "$(cargo run --quiet --offline --manifest-path /workspace/cargo-app/Cargo.toml)" = cargo-ok
node --version
printf 'all-toolchains-restored\n'
""",label,120)
        require('all-toolchains-restored' in text,'Tools did not survive: '+text)

    try:
        cli('init');policy=json.loads(cli('config','--json'))
        require(policy['profile']==environment.PROFILE,'New project selected a language-specific profile')
        log=start('offline-initial');r,name=inspect('offline-initial')
        k=r['runtime']['kernel']
        require(k['uid']==0 and not k['root_read_only'] and k['control_read_only'] and k['policy_read_only'],'Incorrect administrator/control isolation')
        require(k['network_sockets']=={'ipv4':'denied','ipv6':'denied'},'Offline sockets not blocked')
        passed('default_general_environment_and_static_kernel_guardrails')
        command(name,"""set -eu
python3 -m venv /workspace/.venv
printf saved >/root/environment-marker
printf saved >/opt/environment-marker
printf temporary >/scratch/disappears
""",'initial-files',90)
        cli('config','set','command_network','internet');close(log)
        checkpoint=json.loads((state_for(project)/'environment.json').read_text())
        report['first_checkpoint']=checkpoint['image']
        passed('whole_filesystem_and_symlinked_venv_checkpointed_before_container_removal')

        log=start('install-toolchains');r,name=inspect('install-toolchains')
        require(r['runtime']['docker']['image']==checkpoint['image'],'Saved image was not selected')
        command(name,"""set -eu
test "$(cat /root/environment-marker)" = saved
test ! -e /scratch/disappears
test -L /workspace/.venv/bin/python
sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources
apt-get -o Acquire::Retries=3 update
DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=3 install -y --no-install-recommends nodejs npm rustc cargo build-essential
mkdir -p /workspace/js /root/custom-tools
cd /workspace/js
npm install --no-audit --no-fund is-odd@3.0.1
npm install --global --no-audit --no-fund semver@7.7.2
/workspace/.venv/bin/python -m pip install --disable-pip-version-check --index-url https://pypi.org/simple packaging==24.2
printf 'fn main(){println!("rust-ok");}\n' >/workspace/probe.rs
printf '#include <stdio.h>\nint main(void){puts("c-ok");}\n' >/workspace/probe.c
rustc /workspace/probe.rs -o /usr/local/bin/environment-rust
cc /workspace/probe.c -o /usr/local/bin/environment-c
cargo new --vcs none --bin /workspace/cargo-app
printf 'fn main(){println!("cargo-ok");}\n' >/workspace/cargo-app/src/main.rs
cargo build --offline --manifest-path /workspace/cargo-app/Cargo.toml
printf '#!/bin/sh\necho profile-restored\n' >/root/custom-tools/hello-environment
chmod +x /root/custom-tools/hello-environment
printf '\nexport PATH="$HOME/custom-tools:$PATH"\n' >>/root/.profile
""",'normal-installers',420)
        use_tools(name,'installed-tools')
        passed('ordinary_apt_npm_pip_toolchains_and_global_home_system_installs')
        passed('c_rust_javascript_python_and_custom_login_profile_work_without_adapters')

        if args.live:
            message('Remember this phrase for our next conversation turn: '+nonce+'. Do not write the phrase to a file. '
                    'Run the existing C and Rust programs /usr/local/bin/environment-c and /usr/local/bin/environment-rust, '
                    'run node to check /workspace/js/node_modules/is-odd returns true for 3, and use /workspace/.venv/bin/python '
                    'to check packaging version 24.2. Run hello-environment in a login shell. Do not install anything. '
                    'When all succeed write exactly verified plus a newline to /workspace/agent-first.txt.',log)
            require((project/'agent-first.txt').read_text()=='verified\n','Codex did not use the installed technologies')
            chats=json.loads(cli('chats','--json'));require(len(chats)==1,'Native chat not saved')
            chat=chats[0]['id'];report['chat']=chat
            passed('live_codex_uses_the_general_environment')

        # The writable environment Python cannot forge guardrail measurements.
        command(name,"printf '#!/bin/sh\necho forged-inspection\n' >/usr/local/bin/python3; chmod +x /usr/local/bin/python3",'fake-interpreter')
        forged,_=inspect('mutable-python-ignored')
        require(forged['runtime']['kernel']['uid']==0,'Mutable Python forged inspection')
        docker('exec',name,'/bin/rm','/usr/local/bin/python3')
        passed('replaced_environment_interpreter_cannot_forge_static_inspector')
        boundary="""import errno,os,pathlib,socket
assert not any(k in os.environ for k in ('OPENAI_API_KEY','AWS_SECRET_ACCESS_KEY'))
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not pathlib.Path('/Users/nikchin/.codex/auth.json').exists()
for p in ('/__invariant/inspect','/__invariant/codex','/workspace/.invariant/config.json'):
    try: pathlib.Path(p).write_text('escape')
    except OSError as e: assert e.errno in (errno.EROFS,errno.EACCES)
    else: raise AssertionError('control writable: '+p)
for family,kind in ((socket.AF_PACKET,socket.SOCK_RAW),(socket.AF_INET,socket.SOCK_RAW)):
    try: s=socket.socket(family,kind)
    except OSError as e: assert e.errno in (errno.EPERM,errno.EACCES)
    else: s.close();raise AssertionError('raw socket allowed')
print('isolation-preserved')
"""
        require(docker('exec',name,'/usr/bin/python3','-I','-c',boundary)=='isolation-preserved','Isolation probe failed')
        passed('administrator_cannot_modify_control_policy_or_access_host_credentials_docker_raw_sockets')
        cli('config','set','command_network','none');close(log)

        log=start('offline-resume',resume=bool(chat));r,name=inspect('offline-resume')
        require(r['runtime']['kernel']['network_sockets']=={'ipv4':'denied','ipv6':'denied'},'Offline policy not applied to restored filesystem')
        use_tools(name,'restored-tools')
        if args.live:
            require('Resumed chat: '+chat in log.read_text(),'Chat identity changed')
            message('Write the exact phrase I asked you to remember to /workspace/remembered.txt followed by a newline. '
                    'Without downloading or installing anything, run the existing C and Rust programs and use node to check '
                    '/workspace/js/node_modules/is-odd and /workspace/.venv/bin/python to check packaging version 24.2. '
                    'Write exactly verified plus a newline to /workspace/agent-resumed.txt when all checks succeed. Networking is disabled.',log)
            require((project/'remembered.txt').read_text()==nonce+'\n','Native chat context was not restored')
            require((project/'agent-resumed.txt').read_text()=='verified\n','Resumed Codex could not use toolchains offline')
        passed('offline_native_resume_preserves_all_toolchains_home_and_project_state')
        docker('exec',name,'/bin/sh','-c','printf recovered >/root/crash-marker')
        previous=r['session'];child.kill();child.wait(timeout=15);child.stdin.close();child=None
        log=start('crash-recovery',resume=bool(chat));r,name=inspect('crash-recovery')
        require('Recovering the previous workspace' in log.read_text(),'Missing crash recovery guidance')
        require(docker('exec',name,'cat','/root/crash-marker')=='recovered','Uncheckpointed orphan filesystem lost')
        use_tools(name,'crash-recovered-tools')
        gone=subprocess.run(['docker','inspect','invariant-'+previous],capture_output=True)
        require(gone.returncode!=0,'Recovered stale container was not removed')
        passed('killed_launcher_checkpoints_exact_orphan_before_recovery_without_replay')
        close(log)

        original=(state_for(project)/'environment.json').read_bytes()
        cli('config','set','workspace_access','read-only')
        log=start('read-only',resume=bool(chat));r,name=inspect('read-only')
        require(r['runtime']['kernel']['root_read_only'] and r['runtime']['kernel']['workspace_read_only'],'Read-only mode failed')
        require(docker('exec',name,'/usr/local/bin/environment-rust')=='rust-ok','Read-only environment lost tools')
        denied="""import errno,pathlib
for p in ('/opt/environment-marker','/root/environment-marker','/workspace/denied'):
    try:pathlib.Path(p).write_text('changed')
    except OSError as e:assert e.errno in (errno.EROFS,errno.EACCES)
    else:raise AssertionError('write allowed')
pathlib.Path('/scratch/allowed').write_text('temporary')
"""
        docker('exec',name,'/usr/bin/python3','-I','-c',denied);close(log)
        require((state_for(project)/'environment.json').read_bytes()==original,'Read-only session overwrote saved state')
        passed('read_only_environment_uses_saved_tools_without_mutating_checkpoint')

        other=output/'other-project';other.mkdir();cli('init',cwd=other)
        base=docker('image','inspect',environment.IMAGE,'--format','{{.Id}}')
        environment.prepare_control(other,base)
        other_name='invariant-general-separation-'+uuid.uuid4().hex
        try:
            docker(*container_command(other_name,other,base));verify_container(other_name,other,base);docker('start',other_name)
            docker('exec',other_name,'/bin/sh','-c','test ! -e /root/environment-marker && test ! -e /usr/local/bin/environment-rust && ! command -v node && ! command -v rustc')
        finally: remove_owned(other_name)
        passed('separate_project_has_independent_environment_filesystem')
        if args.live:
            records=[json.loads(line) for path in state_for(project).glob('ws-*.jsonl') for line in path.read_text().splitlines()]
            require(sum(r['event']=='turn_completed' and r['status']=='completed' for r in records)==2,'Live model turns did not complete')
            require(sum(r['event']=='environment_saved' for r in records)>=3,'Missing persistence lifecycle evidence')
        require(sources()==report['source_sha256'],'Sources changed during verification')
        report['passed']=True
    except BaseException as error:
        report['error']=str(error);print('FAIL '+str(error),file=sys.stderr,flush=True)
    finally:
        if child:
            if child.poll() is None: child.terminate();child.wait(timeout=210)
            child.stdin.close()
        current=state_for(project)/'current.json'
        if current.exists():
            session=json.loads(current.read_text()).get('session','')
            if session.startswith('ws-') and len(session)==35: containers.append('invariant-'+session)
        for name in set(containers):
            try: remove_owned(name)
            except Exception as error: report['cleanup_errors'].append(str(error))
        if report['cleanup_errors']: report['passed']=False
        private_write(output/'report.json',report);print(output/'report.json',flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__': sys.exit(main())
