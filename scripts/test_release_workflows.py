#!/usr/bin/env python3
"""Real zero-provider checks for onboarding, reports, private restore and retention."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from invariant import environment,workspace
from invariant.experiments.acceptance import sources
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments.examples import create as example
from invariant.experiments.guided import create as guided
from invariant.experiments.snapshots import unseal_tree


def main():
    output=ROOT/'.invariant/release-workflow-checks'/(time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8])
    project=output/'project';project.mkdir(parents=True,mode=0o700)
    report={'schema_version':1,'passed':False,'model_turns':0,'checks':[], 'cleanup_errors':[], 'source_sha256':sources(ROOT)}
    engine=None;prepared=None

    def require(condition,message):
        if not condition:raise AssertionError(message)

    def passed(message):
        report['checks'].append(message);print('PASS '+message,flush=True)

    def cli(*arguments,expected=0):
        result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/invariant'),*arguments,'--json'],
                              cwd=project,capture_output=True,text=True,timeout=1200)
        with (output/'commands.log').open('a') as log:log.write(json.dumps(arguments)+'\n'+result.stdout+'\n'+result.stderr+'\n')
        require(result.returncode==expected,result.stdout+result.stderr)
        reply=json.loads(result.stdout)
        if 'ok' not in reply:return reply  # Legacy configuration JSON remains compatible.
        require(reply['ok'],str(reply))
        return reply['data']

    try:
        subprocess.run([sys.executable,str(ROOT/'scripts/invariant'),'init'],cwd=project,check=True,capture_output=True)
        example('javascript',project/'javascript')
        cli('config','set','command_network','internet')
        prepared=cli('environment','prepare','--command',
            "sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && apt-get -o Acquire::Retries=3 update && DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=3 install -y --no-install-recommends nodejs npm && printf '#!/bin/sh\\nprintf ready\\n' > /usr/local/bin/invariant-release-tool && chmod +x /usr/local/bin/invariant-release-tool && cd javascript && npm ci --ignore-scripts --no-audit --no-fund",'--timeout','600')
        require(prepared['prepared'] and prepared['model_turns']==0,str(prepared))
        cli('config','set','command_network','none')
        passed('explicit dependency preparation persists Node/npm and a custom tool without model calls')
        (project/'answer.txt').write_text('0')
        (project/'tests').mkdir();(project/'tests/check.py').write_text('from pathlib import Path\nassert Path("answer.txt").read_text()=="42"\n')
        js="export function slugify(text) { if (typeof text !== 'string') throw new TypeError('string required'); return text.replace(/[A-Z]/g, x => x.toLowerCase()).replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, ''); }\n"
        (project/'agent.py').write_text('import json,os,subprocess\nfrom pathlib import Path\n'
            'subprocess.run(["invariant-release-tool"],check=True)\n'
            'answer=json.loads(Path(os.environ["INVARIANT_VARIANT_FILE"]).read_text())["parameters"]["answer"]\n'
            'Path("answer.txt").write_text(str(answer))\n'
            'if answer==42: Path("javascript/slug.mjs").write_text('+repr(js)+')\n')
        engine=ExperimentEngine(project)
        studies=[]
        for name,command,paths in [('python','python3 -B tests/check.py',['tests']),
                ('javascript','cd javascript && npm test',['javascript/tests','javascript/package.json','javascript/package-lock.json'])]:
            info=guided(engine,name+'.json',name=name,task='Fix the sample',test_command=command,test_paths=paths)
            recipe=json.loads(Path(info['definition']).read_text())
            recipe['runner']={'type':'command','argv':['python3','agent.py']}
            recipe['variants']=[{'id':'correct','parameters':{'answer':42}},{'id':'wrong','parameters':{'answer':0}}]
            for case in recipe['cases']:case.pop('prompt_file',None)
            recipe['execution']['max_parallel']=2
            Path(info['definition']).write_text(json.dumps(recipe))
            baseline=cli('experiment','preflight',name+'.json','--request-id',name+'-baseline')
            require(baseline['ready'] and baseline['baseline']['quality_failures']==1,str(baseline))
            result=cli('experiment','run',name+'.json','--wait','--request-id',name+'-run')
            comparison=cli('experiment','results',result['id'])
            require(comparison['summary']['runner_successes']==2 and comparison['summary']['quality_passes']==1,str(comparison))
            studies.append(result['id'])
        report['preflight_ready_on_red_baseline']=True
        passed('Python and real npm test commands preflight red baselines and judge correct/wrong private candidates')
        selected=engine.results(studies[0]);attempt=next(r['attempt_id'] for r in selected['trials'] if r['passed'])
        cli('experiment','select',attempt,'--if-revision','0')
        for format,extension in [('markdown','md'),('json','json'),('csv','csv')]:
            destination=(output/('comparison.'+extension)).resolve()
            cli('experiment','export',studies[0],'--format',format,'--output',str(destination))
            require(str(project) not in destination.read_text(),'Default report leaked a private host path')
        report['export_formats']=['markdown','json','csv']
        passed('real comparison exports all formats with private content omitted by default')
        home=engine.root/'attempts'/attempt/'native-home';home.mkdir(exist_ok=True)
        (home/'auth.json').write_text('synthetic-credential-must-not-be-backed-up')
        before=engine.results(studies[0]);archive=(output/'private-backup.tar.gz').resolve()
        saved=cli('experiment','backup','--output',str(archive))
        require(archive.stat().st_mode&0o077==0,'Backup is not private')
        with tarfile.open(archive,'r:gz') as data:require(not any('native-home' in m.name for m in data),'Host auth home leaked into backup')
        restored=cli('experiment','restore',str(archive),'--replace')
        require(restored['restored'] and Path(restored['previous_state']).is_dir(),'Previous state was not retained')
        require(engine.results(studies[0])==before,'Restored comparison/selection changed')
        require(engine.diff(studies[0])['changes'],'Restored snapshot cannot be inspected')
        (project/'next.txt').write_text('Keep behavior and installed tools.')
        child=cli('experiment','continue',studies[0],'--prompt-file','next.txt','--wait')
        require(engine.results(child['id'])['summary']['quality_passes']==1,'Restored files/tools did not continue')
        report['backup_restore_identical']=True
        passed('private backup restores exact images/results/selection and continues with saved installed tools')
        storage=cli('experiment','storage');require(storage['retained_restore_states'],'Retained state missing from storage')
        preview=cli('experiment','cleanup','--older-than-days','0')
        require(any(r['code']=='SELECTED_STATE' for r in preview['skipped']),'Selection was not protected in retention preview')
        cli('experiment','cleanup','--older-than-days','0','--confirm')
        require(engine.inspect(studies[0])['selection']['attempt_id']==attempt,'Cleanup deleted the selected result')
        environment.selected_image(engine.state,engine.project,environment.IMAGE)
        diagnostic=cli('doctor');require(str(project) not in json.dumps(diagnostic),'Support report leaked paths')
        require((project/'answer.txt').read_text()=='0','Experiments changed original files')
        passed('retention protects selected work, diagnostics omit paths and original project files stay unchanged')
        report['passed']=True
    except BaseException as error:
        report['error']=str(error);report['error_type']=type(error).__name__;print('FAIL '+str(error),flush=True)
    finally:
        if engine:
            try:
                if (engine.state/'restore-active.json').exists():
                    from invariant.experiments.backup import restore
                    restore(engine,recover=True)
                for _ in range(3):
                    rows=engine.store.list(100000)
                    if not rows:break
                    for row in rows:
                        try:
                            item=engine.inspect(row['id'])
                            if item['state']!='completed':engine.stop(row['id']);engine.recover(row['id'])
                            choice=engine.inspect(row['id'])['selection']
                            if choice['attempt_id']:engine.select(row['id'],revision=choice['revision'],clear=True)
                            engine.delete(row['id'],confirm=True)
                        except Exception:
                            if _==2:raise
                require(not engine.store.list(),'Fixture studies remain after cleanup')
                if prepared:
                    info=json.loads(workspace.docker('image','inspect',prepared['image']))[0]
                    require(info['Config']['Labels']['io.invariant.checkpoint-project']==environment.project_key(project),'Foreign preparation image')
                    workspace.docker('image','rm','--no-prune',prepared['image'],timeout=120)
                unseal_tree(engine.state);shutil.rmtree(engine.state)
            except Exception as error:report['cleanup_errors'].append(str(error))
        report['source_matches_at_end']=report['source_sha256']==sources(ROOT)
        report['passed'] &= not report['cleanup_errors'] and report['source_matches_at_end']
        workspace.private_write(output/'report.json',report)
        print('Evidence: '+str(output/'report.json'),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
