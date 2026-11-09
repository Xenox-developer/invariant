"""Coordinator-owned observation, review and guarded application.

No model is invoked here. Waiting never dispatches/retries work. Complete
artifacts stay on disk; the model receives bounded decision information.
"""
import time
import json

from . import workspace
from .execution import project_lock
from .experiments.definitions import digest, text
from .experiments.errors import ExperimentError
from .experiments.store import TERMINAL
from .measurements import usage_totals

STUDY_TERMINAL=TERMINAL | {'completed'}


def compact_execution(value):
    rows=value.get('trials',[])
    return {'study_id':value.get('study_id'),'state':value.get('state'),
            'receipts':value.get('receipts',[])[:20],
            'summary':{k:v for k,v in value.get('summary',{}).items() if k!='metrics'},'selection':value.get('selection'),
            'trials':[{k:row.get(k) for k in ('attempt_id','variant_id','state','passed','duration_seconds','reason')}
                      for row in rows[:20]],'trials_omitted':max(0,len(rows)-20),
            'usage':usage_totals(value.get('calls',[{'call_id':r['attempt_id'],'usage':r.get('token_usage')} for r in rows]))}


def compact_record(runner,record,*,include_review=False):
    from .docker_storage import ContainerTaskRunner
    if isinstance(runner,ContainerTaskRunner):return runner.result('compact_record',[record['request_id']],{'include_review':include_review})
    result={k:record[k] for k in ('request_id','state','active_stage','study_id','resume_required') if k in record}
    result['record_path']=str(runner.path(record['request_id']))
    if 'error' in record:
        result['error']={k:v for k,v in record['error'].items() if k!='details'}
    if 'execution' in record:result['execution']=compact_execution(record['execution'])
    if 'review' in record:
        package=record['review']
        result['review']=package if include_review else {k:v for k,v in package.items() if k not in ('changes','conflicts')}
    if 'application' in record:result['application']=record['application']
    result['completed_stages']=list(record.get('completed',{}))
    return result


def wait(runner,key,*,timeout=None,interval=2,clock=time.monotonic,sleep=time.sleep):
    from .docker_storage import ContainerTaskRunner
    if isinstance(runner,ContainerTaskRunner):return runner.result('wait',[key],{'timeout':timeout})
    if timeout is not None and (type(timeout) is not int or not 0<=timeout<=86400):
        raise ExperimentError('INVALID_ARGUMENTS','Wait timeout must be 0–86400 seconds.')
    deadline=clock()+timeout if timeout is not None else float('inf')
    while True:
        record=runner.status(key)
        execution=record.get('execution')
        if record['state']=='blocked' or not execution or execution['state'] in STUDY_TERMINAL:return record
        if clock()>=deadline:return record  # Observation deadline is not execution failure.
        sleep(min(interval,max(0,deadline-clock())))


def review(runner,key,attempt_id=None,*,revision=None):
    from .docker_storage import ContainerTaskRunner
    if isinstance(runner,ContainerTaskRunner):return runner.result('review',[key,attempt_id],{'revision':revision})
    if revision is not None and (type(revision) is not int or not 0<=revision<=1000000):
        raise ExperimentError('INVALID_ARGUMENTS','Review revision must be 0–1000000.')
    with project_lock(runner.root,reconcile=False):
        record=runner.status(key)
        if not record.get('study_id'):raise ExperimentError('NOT_SUBMITTED','The task has no submitted study.')
        report=record['execution']
        revision=record.get('review',{}).get('review_revision',0) if revision is None else revision
        if report['state'] not in STUDY_TERMINAL:raise ExperimentError('TASK_RUNNING','Wait for terminal results before review.')
        engine=runner.backend.engine()
        if attempt_id is None:
            if len(report['trials'])!=1:
                raise ExperimentError('CHOICE_REQUIRED','Choose an explicit attempt for a comparison; no winner is inferred.')
            attempt_id=report['trials'][0]['attempt_id']
        study=engine.inspect(record['study_id'])
        attempts={a['id'] for t in study['trials'] for a in t['attempts']}
        if attempt_id not in attempts:raise ExperimentError('FOREIGN_RESULT','The attempt does not belong to this task.')
        selection=study['selection']
        selection_revision=selection['revision']
        if selection['attempt_id']!=attempt_id:
            # A previous explicit selection is a decision, not coordinator bookkeeping.
            if selection['attempt_id'] is not None:
                raise ExperimentError('CHOICE_CHANGED','Another result is selected; review that choice explicitly.')
            engine.select(attempt_id,revision=selection_revision,request_id='task-select-'+digest([key,attempt_id,selection_revision]))
        plan=engine.plan_apply(attempt_id,request_id='task-plan-'+digest([key,attempt_id,engine.inspect(study['id'])['selection']['revision'],revision]))
        full=engine.root/'apply-plans'/plan['plan_id']/'plan.json'
        budget=24000;changes=[];truncated=False
        for row in plan['changes'][:100]:
            patch=row.get('text_diff')
            shown=None if patch is None else patch[:budget]
            if shown is not None:budget-=len(shown)
            omitted=bool(row.get('text_diff_truncated') or patch is not None and shown!=patch)
            # Binary/large files need explicit full inspection too.
            omitted=omitted or any(e and e['type']=='file' for e in (row['before'],row['after'])) and patch is None
            item={'path':row['path'],'change':row['change'],'text_diff':shown,'requires_full_review':bool(omitted)}
            if len(json.dumps(changes+[item]))>32000:
                truncated=True;break
            changes.append(item)
            truncated=truncated or bool(omitted)
        truncated=truncated or len(plan['changes'])>len(changes)
        package={'review_revision':revision,'plan_id':plan['plan_id'],'review_digest':digest(plan),'attempt_id':attempt_id,
                 'state':plan['state'],'conflicts':plan['conflicts'][:20],
                 'conflicts_omitted':max(0,len(plan['conflicts'])-20),
                 'changed_paths':len(plan['changes']),'excluded_paths':len(plan['excluded']),
                 'changes':changes,'requires_full_review':truncated,'full_review_path':str(full),
                 'desired_files_path':str(full.parent/'desired'),
                 'snapshot_path':str(engine.root/'attempts'/attempt_id/'snapshot/workspace'),
                 'environment_applied':False}
        record['review']=package
        runner.save({k:v for k,v in record.items() if k!='execution'})
        return record


def apply(runner,key,review_digest,*,reviewed_full=False):
    from .docker_storage import ContainerTaskRunner
    if isinstance(runner,ContainerTaskRunner):return runner.result('apply',[key,review_digest],{'reviewed_full':reviewed_full})
    text(review_digest,'review digest',maximum=64,pattern=r'[a-f0-9]{64}')
    with project_lock(runner.root,reconcile=False):
        record=runner.read(key)
        if record is None:raise ExperimentError('NOT_FOUND','Unknown task request.')
        package=record.get('review')
        if not package or package['review_digest']!=review_digest:
            raise ExperimentError('REVIEW_REQUIRED','Supply the digest from this task’s reviewed concrete plan.')
        if package['requires_full_review'] and not reviewed_full:
            raise ExperimentError('FULL_REVIEW_REQUIRED','Inspect full_review_path, then attest using --reviewed-full.')
        from .experiments.applying import load
        engine=runner.backend.engine()
        plan,_=load(engine.root,package['plan_id'])
        if digest(plan)!=review_digest:raise ExperimentError('CORRUPT_PLAN','Reviewed plan changed.')
        # Existing apply engine validates destination, ancestor order, selection,
        # journals and conflicts. A lost acknowledgement replays this operation.
        result=engine.apply(package['plan_id'],request_id='task-apply-'+digest([key,review_digest]))
        record['application']={k:result.get(k) for k in ('plan_id','attempt_id','applied','verification')}
        runner.save(record)
        return record
