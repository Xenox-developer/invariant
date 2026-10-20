"""Experiment release evidence. Missing live/platform evidence never means accepted."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid

SCHEMA = 1
REQUIRED = {f'P{i:02d}' for i in range(1, 22)}
CASE_STAGES = {
    'P01': ('units','creation'), 'P02': ('units','creation','execution','lifecycle'),
    'P03': ('execution',), 'P04': ('execution','apply'),
    'P05': ('quickstart','codex','apply'), 'P06': ('units','creation','lifecycle','codex'),
    'P07': ('units','execution','codex'), 'P08': ('execution','lifecycle'),
    'P09': ('lifecycle',), 'P10': ('lifecycle',),
    'P11': ('units','execution','lifecycle'), 'P12': ('lifecycle',),
    'P13': ('units','execution'), 'P14': ('units','execution'),
    'P15': ('units','apply','quickstart'), 'P16': ('units','apply'),
    'P17': ('apply',), 'P18': ('units','creation','lifecycle','apply'),
    'P19': ('units','sdk','legacy','workspace','configuration','guardrails','easy','internet','general','resume','codex'),
    'P20': ('codex',), 'P21': ('quickstart',),
}
LIVE = {'codex','resume'}
ALL_STAGES = set(t for tags in CASE_STAGES.values() for t in tags)|{'prepare','inventory'}
REQUIRED_UNIT_SUFFIXES = (
    'test_strict_invalid_definitions','test_source_changed_during_capture_is_not_committed',
    'test_active_chat_lock_and_stale_recovery_block_capture','test_secret_references_are_files_and_never_captured',
    'test_native_errors_do_not_replay_model_turn','test_selection_cas_race_only_one_wins_replay_does_not_increment',
    'test_result_validation','test_every_write_intent_recovers_without_partial_files_or_temporary_residue',
    'test_external_edit_during_crash_recovery_is_preserved_and_blocks_writes',
    'test_unsafe_link_and_case_collision_are_rejected_before_writes',
    'test_m1_database_migrates_without_mutating_frozen_definition',
    'test_concurrent_stop_and_claim_never_claims_after_commit',
)


def validate_stage(tag, data):
    if not isinstance(data,dict):raise ValueError('Malformed stage evidence: '+tag)
    if data.get('passed',data.get('accepted')) is not True or data.get('cleanup_errors'):
        raise ValueError('Nonpassing evidence: '+tag)
    if tag=='units':
        tests=data.get('tests',{})
        if len(tests)!=data.get('test_count') or len(tests)<219 or any(v.get('status')!='passed' for v in tests.values()):
            raise ValueError('Incomplete unit outcomes')
        if any(not any(k.endswith('.'+name) for k in tests) for name in REQUIRED_UNIT_SUFFIXES):
            raise ValueError('Required fault/concurrency unit coverage missing')
    if tag=='execution':
        if data.get('variant_count')!=10 or data.get('trial_count')!=40 or data.get('maximum_reserved')!=2 or not data.get('non_git',{}).get('passed'):
            raise ValueError('Ten-variant/multiple-case/repetition/non-Git observations missing')
        if len(data.get('parallel_receipts',[]))!=40 or not data.get('original_source_unchanged'):
            raise ValueError('Independent receipts or source observations missing')
    if tag=='codex' and (data.get('model_turns')!=2 or data.get('native_results',{}).get('summary',{}).get('quality_passes')!=2):
        raise ValueError('Two evaluated real native turns required')
    if tag=='resume' and (data.get('model_turns')!=3 or data.get('resumed_runtime',{}).get('network')!='none'):
        raise ValueError('Three real resumed-chat turns and inspected boundary evidence required')
    if tag=='creation':
        ids=data.get('concurrent_submission',{}).get('study_ids',[])
        if len(ids)!=2 or ids[0]!=ids[1]:raise ValueError('Concurrent CLI creation evidence missing')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def containerized():
    if any(Path(p).exists() for p in ('/.dockerenv','/run/.containerenv')):return True
    try:control=Path('/proc/1/cgroup').read_text()
    except OSError:return False
    return any(marker in control for marker in ('docker/','/docker-','kubepods','/lxc/'))


def emit(data, *, machine, error=None):
    if machine:
        print(json.dumps({'schema_version':1,'ok':error is None,'data':data if error is None else None,'error':error},allow_nan=False))
    else:print(str(error or data))


class GateParser(argparse.ArgumentParser):
    def error(self,message):
        if self.machine:
            emit(None,machine=True,error={'code':'INVALID_ARGUMENTS','message':message,
                'recovery':'Use invariant experiment acceptance --help.'})
            self.exit(2)
        super().error(message)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
    path.chmod(0o600)


def sources(root):
    paths = []
    for directory in ('invariant','scripts','sdk','crates','docker','examples','docs/experiments','.github'):
        paths.extend(p for p in (root/directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    paths.extend(root/name for name in ('.dockerignore','.gitignore','Cargo.toml','Cargo.lock','pyproject.toml','MVP_CONTRACT.md','README.md',
        'LICENSE','NOTICE','CONTRIBUTING.md','docs/LICENSING.md','docs/IMPLEMENTATION_PLAN.md','docs/LEGACY_MVP_CONTRACT.md') if (root/name).is_file())
    return {str(p.relative_to(root)): sha(p) for p in sorted(set(paths))}


def requirements(root):
    rows = re.findall(r'^\| (P\d\d) \| (.+?) \|$', (root/'MVP_CONTRACT.md').read_text(), re.M)
    result = dict(rows)
    if len(rows) != 21 or set(result) != REQUIRED:
        raise ValueError('Contract must declare exactly P01–P21')
    return result


def matrix_passed(report):
    """Never trust a top-level accepted flag or a partial/duplicated matrix."""
    if not isinstance(report, dict) or type(report.get('schema_version')) is not int or report['schema_version'] != SCHEMA:
        return False
    cases = report.get('cases')
    if not isinstance(cases, list) or len(cases) != 21 or not all(isinstance(c,dict) for c in cases):
        return False
    if {c.get('id') for c in cases} != REQUIRED:
        return False
    return all(c.get('status') == 'passed' and c.get('error') is None and c.get('expected')
        and isinstance(c.get('evidence'), list) and c['evidence'] for c in cases)


def artifact_path(base, relative):
    p = PurePosixPath(relative)
    if not relative or p.is_absolute() or '..' in p.parts or '\\' in relative:
        raise ValueError('Unsafe evidence path')
    path = base.joinpath(*p.parts)
    for parent in (path,*path.parents):
        if parent == base: break
        if parent.is_symlink(): raise ValueError('Evidence may not traverse symlinks')
    return path


def verify_bundle(path, *, expected_source=None, _depth=0):
    """Check a local evidence bundle; this is integrity checking, not attestation."""
    if _depth>4:raise ValueError('Platform evidence nesting too deep')
    path = Path(path).resolve()
    if path.stat().st_size>32*1024**2:raise ValueError('Report exceeds size limit')
    d = json.loads(path.read_text())
    if not isinstance(d,dict):raise ValueError('Acceptance report must be an object')
    if type(d.get('schema_version')) is not int or d['schema_version'] != SCHEMA or d.get('kind') != 'invariant-experiment-acceptance':
        raise ValueError('Unsupported acceptance report')
    if d.get('report_digest') != hashlib.sha256(canonical({k:v for k,v in d.items() if k!='report_digest'})).hexdigest():
        raise ValueError('Acceptance report checksum mismatch')
    source = d.get('source_sha256')
    if not isinstance(source,dict) or not source or d.get('source_digest') != hashlib.sha256(canonical(source)).hexdigest():
        raise ValueError('Missing or invalid source manifest')
    if expected_source is not None and source != expected_source:
        raise ValueError('Platform evidence was built from different source')
    artifacts = d.get('artifacts')
    if not isinstance(artifacts,dict) or not artifacts or len(artifacts)>10000: raise ValueError('Missing or excessive evidence artifacts')
    for relative, digest in artifacts.items():
        file = artifact_path(path.parent,relative)
        if not file.is_file() or sha(file) != digest: raise ValueError('Evidence changed or missing: '+relative)
    stages = d.get('stages',{})
    if not isinstance(stages,dict) or set(stages)!=ALL_STAGES: raise ValueError('Missing or unknown stages')
    for name, stage in stages.items():
        if not isinstance(stage,dict) or stage.get('status') not in ('passed','failed','blocked'):raise ValueError('Malformed stage')
        if stage.get('status') == 'passed':
            if stage.get('exit_code') != 0 or stage.get('report') not in artifacts:
                raise ValueError('Unbacked passing stage: '+name)
            evidence=json.loads(artifact_path(path.parent,stage['report']).read_text())
            validate_stage(name,evidence)
    cases=d.get('cases',[])
    if not isinstance(cases,list) or len(cases)!=21 or {c.get('id') for c in cases if isinstance(c,dict)}!=REQUIRED:
        raise ValueError('Missing/duplicate acceptance cases')
    for case in cases:
        if not isinstance(case.get('evidence'),list):raise ValueError('Missing case evidence')
        if case['status']=='passed':
            required=CASE_STAGES[case['id']]
            if any(stages.get(tag,{}).get('status')!='passed' for tag in required):
                raise ValueError('Case claims a pass without its required stages')
            if not case.get('expected') or case.get('error') is not None or not case['evidence']:
                raise ValueError('Incomplete passing case')
            if any(ref not in artifacts for ref in case['evidence']):raise ValueError('Unknown evidence reference')
    fixtures=all(v.get('status')=='passed' for k,v in stages.items() if k not in LIVE)
    if d.get('local_fixture_passed') is True and not fixtures:raise ValueError('False fixture success claim')
    observed={d.get('host',{}).get('system')} if fixtures and d.get('source_unchanged') and d.get('host',{}).get('containerized') is False else set()
    for peer in d.get('imported_platforms',[]):
        peer_path=artifact_path(path.parent,peer['report'])
        if peer['report'] not in artifacts or sha(peer_path)!=peer['report_sha256']:raise ValueError('Missing platform bundle')
        other=verify_bundle(peer_path,expected_source=source,_depth=_depth+1)
        if not other.get('local_fixture_passed') or not other.get('source_unchanged') or other['host'].get('containerized') is not False or other['host']['system']!=peer['system']:
            raise ValueError('Invalid platform evidence')
        observed.add(peer['system'])
    if set(d.get('platforms_verified',[]))!=observed:raise ValueError('Unbacked platform claim')
    if d.get('accepted') is True and (not matrix_passed(d) or d.get('blockers') or not d.get('source_unchanged')):
        raise ValueError('False release acceptance claim')
    if any(c['id']=='P21' and c['status']=='passed' for c in cases) and not {'Darwin','Linux'} <= set(d.get('platforms_verified',[])):
        raise ValueError('Missing actual host platform evidence')
    return d


def classify(stages, expected, platforms):
    cases=[]
    for identifier in sorted(REQUIRED):
        relevant=[stages.get(tag,{'status':'blocked','error':'Required stage did not run'}) for tag in CASE_STAGES[identifier]]
        errors=[r.get('error','Required stage unavailable') for r in relevant if r['status']!='passed']
        status='failed' if any(r['status']=='failed' for r in relevant) else 'blocked' if errors else 'passed'
        if identifier=='P21' and not {'Darwin','Linux'} <= platforms:
            status='blocked' if status!='failed' else status;errors.append('Actual macOS and Linux host evidence is required; Docker guest OS does not count.')
        cases.append({'id':identifier,'status':status,'expected':expected[identifier],
            'evidence':[r['report'] for r in relevant if r.get('report')], 'error':'; '.join(errors) if errors else None})
    return cases


def main(arguments=None, *, root=None):
    parser=GateParser(prog='invariant experiment acceptance',description=__doc__)
    parser.machine='--json' in (sys.argv[1:] if arguments is None else arguments)
    parser.add_argument('--live',metavar='MODEL',help='authorize real native model calls, including legacy resume and continuation')
    parser.add_argument('--effort',default='medium')
    parser.add_argument('--json',action='store_true')
    parser.add_argument('--verify',type=Path,metavar='REPORT',help='validate a saved evidence bundle without running workloads')
    parser.add_argument('--platform-report',action='append',default=[],type=Path,help='evidence report from another actual host, for the same source')
    parser.add_argument('--checkout',type=Path,help='source checkout to verify (needed when launched from an installed wheel)')
    args=parser.parse_args(arguments)
    if args.verify:
        try:
            report=verify_bundle(args.verify)
            data={'valid':True,'accepted':report['accepted'],'report':str(args.verify.resolve()),'blockers':report['blockers']}
            data['status']=report.get('status','blocked')
            code=0 if report['accepted'] else 1 if data['status']=='failed' else 2
        except (ValueError,OSError,TypeError,KeyError) as error:
            data={'valid':False,'accepted':False,'error':str(error)};code=1
        emit(data,machine=args.json,error=None if data['valid'] else {'code':'INVALID_EVIDENCE','message':data['error'],
            'recovery':'Restore the complete unchanged report and artifact bundle; rerun verification if necessary.'})
        return code
    root=Path(args.checkout or root or Path(__file__).resolve().parents[2]).resolve()
    if not (root/'scripts/test_experiments_acceptance.py').is_file():
        emit(None,machine=args.json,error={'code':'CHECKOUT_REQUIRED','message':'Source checkout required.',
            'recovery':'Run from the repository or pass --checkout PATH.'});return 2
    os.umask(0o077)
    out=root/'.invariant/experiment-acceptance'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-')+uuid.uuid4().hex[:8])
    out.mkdir(parents=True)
    snapshot=sources(root);stages={};platforms=set();blockers=[]
    report={'schema_version':SCHEMA,'kind':'invariant-experiment-acceptance','accepted':False,
        'created_at':datetime.now(timezone.utc).isoformat(),'host':{'system':platform.system(),'release':platform.release(),
            'machine':platform.machine(),'python':platform.python_version(),'containerized':containerized()},'source_sha256':snapshot,
        'source_digest':hashlib.sha256(canonical(snapshot)).hexdigest(),'source_unchanged':False,
        'contract_sha256':sha(root/'MVP_CONTRACT.md'),'stages':stages,'live_requested':args.live,
        'limitations':['Reports are local evidence, not signed CI attestations.','Real-provider output is nondeterministic.',
            'Injected provider/Docker failures are identified separately from real Docker fault tests.']}

    def announce(message):
        print(message,file=sys.stderr if args.json else sys.stdout,flush=True)

    def run(tag, script, *, extra=(), group=None, timeout=1800, direct=None, native=False):
        if native and not args.live:
            stages[tag]={'status':'blocked','error':'Real provider not requested; rerun with --live MODEL.'};return
        log=out/(tag+'.log');before=set((root/'.invariant'/group).glob('*/report.json')) if group else set()
        command=direct or [sys.executable,'-B',str(root/'scripts'/script),*extra]
        announce('Checking '+tag+'…')
        stage={'status':'failed','command':command,'log':log.name};stages[tag]=stage;started=time.monotonic()
        try:
            with log.open('w') as stream:
                result=subprocess.run(command,cwd=root,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
            stage['exit_code']=result.returncode
            if group:
                after=set((root/'.invariant'/group).glob('*/report.json'))-before
                if len(after)!=1:raise ValueError('Expected exactly one fresh '+tag+' report')
                proof=after.pop()
            elif tag=='legacy':
                candidates=[p for p in (root/'.invariant/acceptance').glob('*/summary.json') if p.stat().st_mtime>=started_wall[0]]
                if len(candidates)!=1:raise ValueError('Expected a fresh legacy report')
                proof=candidates[0]
            else:
                proof=out/(tag+'-results.json')
                if tag=='prepare':save(proof,{'schema_version':1,'passed':result.returncode==0,'command':command})
            data=json.loads(proof.read_text())
            stage['report']='evidence/'+tag+'.json';save(out/stage['report'],data)
            if tag=='legacy':
                shutil.copytree(proof.parent,out/'evidence/legacy-details')
            if data.get('cleanup_errors'):raise ValueError('Fixture cleanup was not confirmed')
            if result.returncode or data.get('passed',data.get('accepted')) is not True:
                codes=json.dumps(data)
                if (native or (tag=='apply' and args.live)) and any(c in codes for c in ('AUTH_REQUIRED','THROTTLED','MODEL_UNAVAILABLE','EFFORT_UNSUPPORTED','provider')):
                    stage['status']='blocked'
                raise ValueError(data.get('error') or 'Check failed; inspect '+log.name)
            if data.get('source_matches') is False or data.get('source_matches_at_end') is False:raise ValueError('Source changed during stage')
            for name,digest in data.get('source_sha256',{}).items():
                if not (root/name).is_file() or sha(root/name)!=digest:raise ValueError('Stale source in '+tag)
            validate_stage(tag,data)
            if tag=='apply' and args.live and data.get('model_turns')!=2:raise ValueError('Native continuation evidence missing')
            if tag=='units' and (data.get('test_count',0)<219 or not all(v['status']=='passed' for v in data.get('tests',{}).values())):
                raise ValueError('Missing or nonpassing unit evidence')
            stage['status']='passed'
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            stage['error']=str(error)
        stage['elapsed_seconds']=round(time.monotonic()-started,3)
        announce(tag+': '+stage['status'])

    started_wall=[time.time()]
    try:
        docker=subprocess.run(['docker','info','--format','{{json .}}'],capture_output=True,text=True,timeout=15)
        info=json.loads(docker.stdout or '{}')
        if docker.returncode or not info.get('ServerVersion') or info.get('OSType')!='linux':raise ValueError('Responsive Linux Docker engine required')
        report['docker']={k:info.get(k) for k in ('ServerVersion','OSType','Architecture','NCPU','MemTotal','Driver')}
        run('units',None,direct=[sys.executable,str(root/'scripts/test_release_units.py'),'--report',str(out/'units-results.json')],timeout=180)
        run('sdk',None,direct=[sys.executable,str(root/'scripts/test_release_units.py'),'--suite','sdk','--report',str(out/'sdk-results.json')],timeout=60)
        run('prepare',None,direct=[sys.executable,str(root/'scripts/prepare_release.py')],timeout=1200)
        run('legacy','test_mvp_acceptance.py',timeout=2400)
        run('creation','test_experiments_creation.py',group='experiment-creation-checks')
        run('execution','test_experiments_execution.py',extra=['--ten-variants'],group='experiment-execution-checks')
        run('lifecycle','test_experiments_lifecycle.py',group='experiment-lifecycle-checks')
        run('apply','test_experiments_apply.py',extra=['--live',args.live,'--effort',args.effort] if args.live else [],group='experiment-apply-checks')
        run('codex','test_experiments_codex.py',extra=['--live',args.live or '', '--effort',args.effort],group='experiment-codex-checks',native=True)
        for tag,script,group in [('workspace','test_codex_workspace.py','workspace-checks'),
                ('configuration','test_configuration_e2e.py','configuration-checks'),('guardrails','test_guardrails_e2e.py','guardrails-checks'),
                ('easy','test_easy_workflow_e2e.py','easy-workflow-checks'),('internet','test_internet_e2e.py','internet-checks'),
                ('general','test_general_environment_e2e.py','general-environment-checks')]:
            run(tag,script,group=group,extra=['--oom'] if tag=='easy' else [])
        run('resume','test_codex_resume.py',extra=['--live',args.live or '', '--effort',args.effort],group='resume-checks',native=True)
        run('quickstart','test_experiments_quickstart.py',group='experiment-quickstart-checks')
        run('inventory','release_inventory.py',direct=[sys.executable,str(root/'scripts/release_inventory.py'),'--report',str(out/'inventory-results.json')],timeout=300)
    except (OSError,ValueError,subprocess.SubprocessError) as error:blockers.append(str(error))
    # Never infer a missing stage from another passing gate.
    for tag in ALL_STAGES:
        stages.setdefault(tag,{'status':'blocked','error':'Prerequisites failed; stage did not run.'})
    local_fixtures=all(v['status']=='passed' for k,v in stages.items() if k not in LIVE)
    if local_fixtures and not report['host']['containerized']:platforms.add(platform.system())
    report['imported_platforms']=[]
    for path in args.platform_report:
        try:
            other=verify_bundle(path,expected_source=snapshot)
            if not other.get('source_unchanged') or not other.get('local_fixture_passed') or other['host'].get('containerized') is not False:
                raise ValueError('Other platform fixtures did not pass on an actual host')
            system=other['host']['system']
            if system not in ('Darwin','Linux'):raise ValueError('Unsupported host platform')
            peer=out/('platform-'+system+'-'+sha(path)[:12]);peer.mkdir()
            shutil.copyfile(path,peer/'report.json')
            for relative in other['artifacts']:
                target=artifact_path(peer,relative);target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(artifact_path(path.resolve().parent,relative),target)
            platforms.add(system);report['imported_platforms'].append({'system':system,'report':str((peer/'report.json').relative_to(out)),'report_sha256':sha(path)})
        except (OSError,ValueError,KeyError,TypeError) as error:blockers.append('Platform evidence: '+str(error))
    report['source_unchanged']=snapshot==sources(root)
    if not report['source_unchanged']:blockers.append('Source changed during verification')
    report['cases']=classify(stages,requirements(root),platforms)
    inventory=stages.get('inventory',{})
    if inventory.get('status')!='passed':blockers.append('Dependency/notice inventory incomplete')
    for case in report['cases']:
        if case['status']!='passed':blockers.append(case['id']+': '+str(case['error']))
    report['platforms_verified']=sorted(platforms);report['local_fixture_passed']=local_fixtures
    report['blockers']=blockers;report['accepted']=matrix_passed(report) and not blockers and report['source_unchanged']
    report['status']='accepted' if report['accepted'] else 'failed' if any(s['status']=='failed' for s in stages.values()) else 'blocked'
    report['artifacts']={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}
    report['report_digest']=hashlib.sha256(canonical(report)).hexdigest()
    save(out/'report.json',report)
    result={'schema_version':1,'accepted':report['accepted'],'status':report['status'],'report':str(out/'report.json'),
        'passed_cases':sum(c['status']=='passed' for c in report['cases']),'required_cases':21,'blockers':blockers}
    if args.json:emit(result,machine=True)
    else:print('Experiment acceptance '+report['status'].upper()+'\nEvidence: '+str(out/'report.json')+'\n'+'\n'.join(blockers))
    return 0 if report['accepted'] else 1 if report['status']=='failed' else 2
