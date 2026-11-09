"""Per-response provider observations and reconciliation, without token estimates."""
import json
import os
from pathlib import Path
import time

from .measurements import usage_totals

FIELDS={'input_tokens':'inputTokens','cached_input_tokens':'cachedInputTokens',
        'output_tokens':'outputTokens','reasoning_output_tokens':'reasoningOutputTokens','total_tokens':'totalTokens'}


def counters(raw):
    if not isinstance(raw,dict):return {key:None for key in FIELDS}
    result={key:(value if type(value:=raw.get(key,raw.get(alias))) is int and value>=0 else None)
            for key,alias in FIELDS.items()}
    if result['total_tokens'] is None and result['input_tokens'] is not None and result['output_tokens'] is not None:
        result['total_tokens']=result['input_tokens']+result['output_tokens']
    return result


class UsageLedger:
    """An increasing cumulative observation is one response ONLY if last == delta.

    Duplicate notifications are ignored. Missing/mismatched observations remain
    explicit gaps, never split into invented inference calls or replaced by zero.
    """
    def __init__(self,path=None):
        self.path=Path(path) if path else None
        self.rows=[];self.previous={key:0 for key in FIELDS};self.problems=[]

    def observe(self,total,last,*,phase='execution',timestamp=None,identity=None):
        total,last=counters(total),counters(last)
        if total==self.previous:return
        delta={key:total[key]-self.previous[key] if total[key] is not None and self.previous[key] is not None else None for key in FIELDS}
        exact=all(delta[key] is not None and delta[key]>=0 and delta[key]==last[key] for key in FIELDS)
        row={'sequence':len(self.rows)+1,'response_id':str(len(self.rows)+1),'source_event_id':identity,'phase':phase,'timestamp':timestamp or time.time(),
             'usage':last,'cumulative':total,'attribution':'one_response' if exact else 'unattributed_change'}
        if not exact:self.problems.append('missing_or_nonconsecutive_provider_observation')
        self.previous=total;self.rows.append(row)
        if self.path:
            self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'a') as out:out.write(json.dumps(row,allow_nan=False)+'\n');out.flush()

    def summary(self,final):
        total=counters(final.get('total') if isinstance(final,dict) and 'total' in final else final)
        rows=[{'call_id':str(r['sequence']),'usage':r['usage']} for r in self.rows]
        summed=usage_totals(rows) if rows else usage_totals(None)
        match=bool(rows) and all(summed[key]==total[key] and total[key] is not None for key in FIELDS)
        return {'response_count':len(rows),'complete':match and not self.problems,
                'reconciled':match,'problems':sorted(set(self.problems+([] if match else ['final_total_not_reconciled']))),
                'path':str(self.path) if self.path else None,'final':total}


def from_rollout(path,final,*,phase='execution'):
    ledger=UsageLedger()
    with Path(path).open() as stream:
        for line in stream:
            record=json.loads(line);payload=record.get('payload',{})
            if record.get('type')!='event_msg' or payload.get('type')!='token_count':continue
            info=payload.get('info')
            if isinstance(info,dict):ledger.observe(info.get('total_token_usage'),info.get('last_token_usage'),
                phase=phase,timestamp=record.get('timestamp'),identity=record.get('ordinal'))
    return {'observations':ledger.rows,**ledger.summary(final)}
