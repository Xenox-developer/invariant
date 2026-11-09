"""Paired measured outcomes, immutable fixed suite and independent negative controls."""
import csv,hashlib,json,shutil,statistics
from pathlib import Path
from analyze import normalize,response_overlap
from audit_checker import SCRIPT
from baseline_v3 import docker
HERE=Path(__file__).resolve().parent;BASE=HERE/'results/v4'
def read(path):return json.loads(path.read_text())
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def main():
    result={};negative={};csvs=[];protocol=read(BASE/'protocol.json')
    assert protocol['checks_sha256']==read(HERE/'results/v3/protocol.json')['checks_sha256']
    labels=['first','warm1','warm2','warm3']
    if (BASE/'release/with-invariant/summary.json').exists() and (BASE/'release/without-invariant/summary.json').exists():labels.append('release')
    for label in labels:
        result[label]={};negative[label]={}
        for arm in ('with-invariant','without-invariant'):
            root=BASE/label/arm;raw=read(root/'summary.json');value=normalize(raw)
            assert value['tests_passed']==value['tests']==85
            value['parallel_model_evidence']=response_overlap(BASE/label,raw)
            result[label][arm]=value
            if arm=='with-invariant':
                assert raw['container_internal_handoff'] and raw['source_digest']==protocol.get('source_digests',{}).get(label,protocol['source_digest'])
                for phase in ('methods','checker'):
                    dispatch=read(root/phase/'dispatch.json');assert dispatch['baseline_preflight']['ready']
                    execution=read(root/phase/'execution.json')
                    if phase=='checker':assert len(dispatch['frozen']['result_inputs'])==2
                    for r in execution['trials']:
                        runner=r['runner'];assert runner['completion_source']=='validation'
                        assert runner['native']['turn_status'] in ('interrupted','completed')
                        assert r['evaluation']['reused_validation'] and runner['inference_usage']['complete']
            files={'build_report.py':(root/'agents/checker/build_report.py').read_text(),'fixed_checks.py':(HERE/'fixed_checks.py').read_text()}
            for role in ('trapezoid','simpson'):
                target=root/'agents/checker/inputs'/role;target.mkdir(parents=True,exist_ok=True)
                for name in ('solution.py','result.json'):
                    source=root/'agents'/role/name;shutil.copyfile(source,target/name);files[f'inputs/{role}/{name}']=source.read_text()
            output=docker('run','--rm','--interactive','--network=none','--read-only','--cap-drop=ALL',
                '--security-opt=no-new-privileges','--tmpfs','/audit:rw,nosuid,nodev','--env','PYTHONDONTWRITEBYTECODE=1',
                '--entrypoint','python3',raw['starting_image'],'-B','-c',SCRIPT,input=json.dumps({'files':files}).encode()).stdout
            negative[label][arm]=json.loads(output)
            with (root/'agents/checker/comparison.csv').open() as stream:rows=list(csv.DictReader(stream))
            values={(r['method'],int(r['power']),int(r['n'])):float(r['estimate']) for r in rows};assert len(values)==16;csvs.append(values)
    difference=max(abs(v[k]-csvs[0][k]) for v in csvs for k in v);assert difference<1e-12
    summaries={}
    for arm in ('with-invariant','without-invariant'):
        samples=[result[label][arm] for label in ('warm1','warm2','warm3')]
        summaries[arm]={'samples':3,'wall_seconds':{'median':statistics.median(x['wall_seconds'] for x in samples),
              'min':min(x['wall_seconds'] for x in samples),'max':max(x['wall_seconds'] for x in samples)},
            'usage':{k:statistics.median(x['usage'][k] for x in samples) for k in samples[0]['usage']},
            'standard_credit_equivalent':statistics.median(x['standard_credit_equivalent'] for x in samples),
            'provider_responses':statistics.median(x['provider_responses'] for x in samples),
            'outside_agent_windows_seconds':statistics.median(x['outside_agent_windows_seconds'] for x in samples)}
    warm_only=summaries
    if 'release' in result:
        summaries={}
        for arm in ('with-invariant','without-invariant'):
            samples=[result[label][arm] for label in ('warm1','warm2','warm3','release')]
            summaries[arm]={'samples':4,'wall_seconds':{'median':statistics.median(x['wall_seconds'] for x in samples),'min':min(x['wall_seconds'] for x in samples),'max':max(x['wall_seconds'] for x in samples)},
                'usage':{k:statistics.median(x['usage'][k] for x in samples) for k in samples[0]['usage']},
                'standard_credit_equivalent':statistics.median(x['standard_credit_equivalent'] for x in samples),
                'provider_responses':statistics.median(x['provider_responses'] for x in samples),
                'outside_agent_windows_seconds':statistics.median(x['outside_agent_windows_seconds'] for x in samples)}
    old=read(HERE/'results/v3/measurements.json')['rounds']['warm']['with-invariant'];new=summaries['with-invariant'];direct=summaries['without-invariant']
    comparisons={'versus_previous_invariant':{'wall_reduction_percent':100*(1-new['wall_seconds']['median']/old['wall_seconds']),
        'tokens_reduction_percent':100*(1-new['usage']['total_tokens']/old['usage']['total_tokens']),
        'credits_reduction_percent':100*(1-new['standard_credit_equivalent']/old['standard_credit_equivalent'])},
        'versus_new_direct':{'wall_extra_percent':100*(new['wall_seconds']['median']/direct['wall_seconds']['median']-1),
        'tokens_extra_percent':100*(new['usage']['total_tokens']/direct['usage']['total_tokens']-1),
        'credits_extra_percent':100*(new['standard_credit_equivalent']/direct['standard_credit_equivalent']-1)}}
    write(BASE/'measurements.json',{'rounds':result,'warm_medians':warm_only,'repeat_medians':summaries,'comparisons':comparisons,'max_estimate_difference':difference,
       'checks_sha256':protocol['checks_sha256'],'previous_warm_invariant':old,'actual_billed_usd':None,
       'outer_chat_usage':None,'pricing':'Frozen Standard credit equivalent: uncached input 50, cached 2.5, output 250 per million tokens',
       'first_run_notice':protocol['first_run_notice']})
    write(BASE/'checker-negative-control.json',negative)
    print(json.dumps({'warm_medians':warm_only,'repeat_medians':summaries,'comparisons':comparisons},indent=2))
if __name__=='__main__':main()
