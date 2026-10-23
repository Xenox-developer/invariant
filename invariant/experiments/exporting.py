"""Allowlisted comparison reports; private records are never dumped wholesale."""
import csv
import copy
import html
import io
import json
import math
import os
from pathlib import Path
import re

from .. import __version__
from .errors import ExperimentError


def number(value):
    return value if type(value) in (int,float) and math.isfinite(value) else None


def label(value):
    return value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value) else None


def document(engine, identifier, *, include_task=False, include_diff=False, include_logs=False):
    item=engine.inspect(identifier)
    study=item if item['kind']=='study' else engine.inspect(item['study_id'])
    frozen=study['frozen'];definition=frozen['definition']
    variants={v['id']:v for v in definition['variants']}
    rows=[]
    for trial in study['trials']:
        variant=variants[trial['variant_id']]
        for attempt in sorted(trial['attempts'],key=lambda a:a['number']):
            record=attempt['record'];runner=record.get('runner',{});evaluation=record.get('evaluation',{})
            native=runner.get('native') or {}
            usage=runner.get('token_usage') or {}
            total=usage.get('total') or {}
            metrics={k:number(v) for k,v in evaluation.get('metrics',{}).items()
                     if re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}',k)}
            rows.append({'trial_id':trial['id'],'attempt_id':attempt['id'],'attempt_number':attempt['number'],
                'comparison_attempt':attempt['number']==1,'variant':trial['variant_id'],'case':trial['case_id'],
                'repetition':trial['repetition'],'state':attempt['state'],'error_code':label(attempt.get('reason')),
                'requested_model':label(variant.get('model')),'requested_effort':label(variant.get('effort')),
                'observed_model':label(native.get('model')),'observed_effort':label(native.get('effort')),
                'runner_status':label(runner.get('status')),'evaluation_status':label(evaluation.get('status')),
                'passed':evaluation.get('passed') if type(evaluation.get('passed')) is bool else None,
                'agent_seconds':number(runner.get('duration_seconds')),'total_tokens':number(total.get('totalTokens')),
                'metrics':metrics})
    report={'schema_version':1,'kind':'invariant-comparison','invariant_version':__version__,
        'study_id':study['id'],'state':study['state'],'runner':definition['runner']['type'],
        'attempt_policy':'first_attempt','repetitions':definition['repetitions'],
        'selection':{k:study.get('selection',{}).get(k) for k in ('attempt_id','revision')},
        'baseline_digest':frozen.get('baseline_content_digest'),'image_id':frozen['image']['id'],
        'attempts':rows,'summary':copy.deepcopy(engine.results(study['id'])['summary']),
        'limitations':['Frozen local inputs do not make model responses or external services deterministic.',
            'Only first attempts contribute to comparison; all retries are listed separately.',
            'Missing quality, timing or token values remain unknown. No automatic winner or statistical ranking.']}
    # Summary metric keys are also user supplied; retain only the same bounded names.
    report['summary']['metrics']={k:v for k,v in report['summary'].get('metrics',{}).items()
                                  if re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}',k)}
    if include_task:
        from .snapshots import regular_bytes
        base=engine.root/'baselines'/study['baseline_id']/'inputs'
        report['tasks']=[{'case':case['id'],'text':regular_bytes(base,case['prompt_file'],maximum=1024*1024)[0].decode('utf-8')}
                         for case in definition['cases'] if case.get('prompt_file')]
    if include_diff:report['diff']=engine.diff(study['id'])
    if include_logs:report['logs']=engine.logs(study['id'],tail_bytes=65536)
    return report


def cell(value):
    if value is None:return ''
    text=str(value)
    return "'"+text if text.startswith(('=','+','-','@','\t','\r','\n')) else text


def render(report, format):
    if format=='json':return json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False)+'\n'
    if format=='csv':
        stream=io.StringIO();fields=[k for k in report['attempts'][0] if k!='metrics'] if report['attempts'] else ['attempt_id']
        writer=csv.DictWriter(stream,fieldnames=fields+['metrics']);writer.writeheader()
        for row in report['attempts']:
            writer.writerow({**{k:cell(row[k]) for k in fields},'metrics':json.dumps(row['metrics'],allow_nan=False)})
        return stream.getvalue()
    def safe(value):return html.escape(str(value if value is not None else 'unknown')).replace('|','&#124;').replace('\n',' ')
    lines=['# INVARIANT comparison','',f"Study: `{report['study_id']}` · INVARIANT {report['invariant_version']}",
           '',f"State: {safe(report['state'])}. Comparison uses first attempts; retries are shown separately.",
           '', '| Configuration | Case | Attempt | Requested / observed | Quality | Seconds | Tokens | State / error |',
           '|---|---|---:|---|---|---:|---:|---|']
    for row in report['attempts']:
        quality='pass' if row['passed'] is True else 'fail' if row['passed'] is False else 'unknown'
        requested='/'.join(safe(row[k]) for k in ('requested_model','requested_effort'))
        observed='/'.join(safe(row[k]) for k in ('observed_model','observed_effort'))
        lines.append('| '+' | '.join([safe(row['variant']),safe(row['case']),str(row['attempt_number']),requested+' → '+observed,
            quality,safe(row['agent_seconds']),safe(row['total_tokens']),safe(row['state'])+' / '+safe(row['error_code'])])+' |')
    lines+=['','## Summary','', '```json',json.dumps(report['summary'],indent=2,allow_nan=False),'```',
            '', 'Selected attempt: '+safe(report['selection']['attempt_id']),
            '',f"Image: `{report['image_id']}`",'',*['- '+s for s in report['limitations']]]
    if any(k in report for k in ('tasks','diff','logs')):
        # Fence length prevents included workload content from closing the block.
        payload=json.dumps({k:report[k] for k in ('tasks','diff','logs') if k in report},indent=2,ensure_ascii=False)
        fence='`'*max(3,1+max([len(m.group()) for m in re.finditer(r'`+',payload)] or [0]))
        lines+=['','## Explicitly included private content','',fence+'json',payload,fence]
    return '\n'.join(lines)+'\n'


def export(engine, identifier, *, format='markdown', output=None, include_task=False, include_diff=False, include_logs=False):
    if format=='csv' and (include_task or include_diff or include_logs):
        raise ExperimentError('INVALID_ARGUMENTS','CSV contains comparison rows only. Use JSON or Markdown to include task/diff/log content.')
    report=document(engine,identifier,include_task=include_task,include_diff=include_diff,include_logs=include_logs)
    content=render(report,format)
    if output:
        path=Path(output).expanduser().absolute()
        # An explicit report destination must never silently replace source or follow a link.
        try:fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        except FileExistsError:
            raise ExperimentError('OUTPUT_EXISTS','The report destination already exists.','Choose a new --output path; existing files are never replaced.') from None
        with os.fdopen(fd,'w') as stream:stream.write(content)
        return {'format':format,'output':str(path),'bytes':len(content.encode()),'study_id':report['study_id']}
    return {'format':format,'content':content,'study_id':report['study_id']}
