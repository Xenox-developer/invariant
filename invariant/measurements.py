"""Provider accounting and comparability gates shared by benchmark callers."""
import re
from . import resources
from .experiments.errors import ExperimentError


def usage_totals(calls):
    """Sum distinct calls' final counters; cached/reasoning counters are subsets."""
    fields={'input_tokens':'inputTokens','cached_input_tokens':'cachedInputTokens',
            'output_tokens':'outputTokens','reasoning_output_tokens':'reasoningOutputTokens',
            'total_tokens':'totalTokens'}
    if calls is None:return {'model_calls':None,**{field:None for field in fields},'complete':False}
    if not isinstance(calls,list) or any(not isinstance(call,dict) or 'call_id' not in call for call in calls):
        raise ExperimentError('INVALID_MEASUREMENT','calls must contain identified model-call records.')
    seen=set()
    rows=[]
    for call in calls:
        identifier=call['call_id']
        if not isinstance(identifier,str) or not identifier or identifier in seen:
            raise ExperimentError('DUPLICATE_MEASUREMENT','Each measured model call needs one unique identifier.')
        seen.add(identifier)
        raw=call.get('usage')
        native=isinstance(raw,dict) and isinstance(raw.get('total'),dict)
        raw=raw['total'] if native else raw
        row={}
        for field,alias in fields.items():
            value=raw.get(alias if native else field) if isinstance(raw,dict) else None
            if value is not None and (type(value) is not int or value<0):
                raise ExperimentError('INVALID_MEASUREMENT','Token counters must be nonnegative integers or unavailable.')
            row[field]=value
        if row['total_tokens'] is None and row['input_tokens'] is not None and row['output_tokens'] is not None:
            row['total_tokens']=row['input_tokens']+row['output_tokens']
        for subset,total in (('cached_input_tokens','input_tokens'),('reasoning_output_tokens','output_tokens')):
            if row[subset] is not None and row[total] is not None and row[subset]>row[total]:
                raise ExperimentError('INVALID_MEASUREMENT','Token subset exceeds its containing total.')
        rows.append(row)
    return {'model_calls':len(rows),**{field:sum(row[field] for row in rows)
            if all(row[field] is not None for row in rows) else None for field in fields},
            'complete':all(row['total_tokens'] is not None for row in rows)}


def compare_runs(runs):
    """Keep failed runs as evidence; rank only verified comparable successes."""
    if not isinstance(runs,list) or len(runs)!=2 or not all(isinstance(run,dict) for run in runs):
        raise ExperimentError('INVALID_MEASUREMENT','Provide exactly two measured runs.')
    reasons=[]
    totals=[]
    for run in runs:
        totals.append(usage_totals(run.get('calls')))
        if run.get('state')!='completed' or run.get('passed') is not True:
            reasons.append('incomplete_or_failed_run')
        contract=run.get('contract',{})
        if not isinstance(contract,dict):
            reasons.append('missing_contract')
            continue
        if not all(contract.get(key) for key in ('task_digest','model','effort','image','resources')):
            reasons.append('missing_contract')
            continue
        if (not isinstance(contract['image'],str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',contract['image'])
                or not isinstance(contract['task_digest'],str) or not re.fullmatch(r'[0-9a-f]{64}',contract['task_digest'])):
            reasons.append('nonimmutable_identity')
        budget=contract['resources']
        if not isinstance(budget,dict):
            reasons.append('missing_contract')
            continue
        if budget.get('resolved') is not True:
            reasons.append('unresolved_resources')
        try:resources.verify_contract({'HostConfig':run.get('host_config',{})},budget)
        except (RuntimeError,KeyError,TypeError):reasons.append('resource_mismatch')
        if run.get('model')!=contract['model'] or run.get('effort')!=contract['effort']:
            reasons.append('model_or_effort_mismatch')
        seconds=run.get('wall_seconds')
        if type(seconds) not in (int,float) or not 0<seconds<float('inf'):
            reasons.append('missing_duration')
    if runs[0].get('contract')!=runs[1].get('contract'):reasons.append('contracts_differ')
    reasons=sorted(set(reasons))
    valid=not reasons
    return {'schema_version':1,'comparable':valid,'reasons':reasons,'usage':totals,
            'time_ratio':runs[0]['wall_seconds']/runs[1]['wall_seconds'] if valid else None,
            'token_ratio':totals[0]['total_tokens']/totals[1]['total_tokens']
                if valid and all(t['complete'] for t in totals) and totals[1]['total_tokens'] else None}
