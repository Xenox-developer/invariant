"""Deterministic job runtime: model-free preparation/wait/apply, one semantic review.

A tool host can call Coordinator.execute and await it without invoking a model
while it runs. The CLI is an adapter to the same interface, not a second manager.
"""
from pathlib import Path
import time

from . import task_results, workspace
from .tasks import TaskRunner
from .task_review import Reviewer
from .experiments.definitions import digest
from .experiments.errors import ExperimentError
from .measurements import usage_totals


class Coordinator:
    def __new__(cls, project=None, *, runner=None, reviewer=None):
        from .docker_storage import managed, ContainerCoordinator, ContainerTaskRunner
        if reviewer is None and not managed() and (runner is None or isinstance(runner,ContainerTaskRunner)):
            return ContainerCoordinator(runner.project if runner is not None else project)
        return super().__new__(cls)

    def __init__(self,project=None,*,runner=None,reviewer=None):
        self.project=Path(project or Path.cwd()).resolve()
        self.runner=runner or TaskRunner(self.project)
        self.reviewer=reviewer or Reviewer(self.project)

    def execute(self,key,request,*,apply=False,timeout=None):
        if not isinstance(request,dict) or set(request)-{'operation','options','parent','task','evaluation'}:
            raise ExperimentError('INVALID_ARGUMENTS','Invalid coordinator request.')
        operation=request.get('operation','run');deadline=time.monotonic()+timeout if timeout is not None else float('inf')
        self.reviewer.ready(self.runner.root/'review-runtime')
        if operation=='run':record=self.runner.run(key,request.get('options'))
        elif operation=='continue':record=self.runner.continue_task(key,request.get('parent'),request.get('task'),evaluation=request.get('evaluation'))
        else:raise ExperimentError('INVALID_ARGUMENTS','Unknown coordinator operation.')
        if record['state']=='blocked':return task_results.compact_record(self.runner,record)
        record=task_results.wait(self.runner,key,timeout=max(0,int(deadline-time.monotonic())) if timeout is not None else None)
        execution=record.get('execution',{})
        if execution.get('state') not in task_results.STUDY_TERMINAL:return task_results.compact_record(self.runner,record)
        if len(execution.get('trials',[]))!=1:
            result=task_results.compact_record(self.runner,record);result['next']='Choose a comparison result explicitly; no automatic winner or application.';return result
        if execution['trials'][0]['passed'] is not True:
            result=task_results.compact_record(self.runner,record);result['next']='Inspect failed or unknown evaluation; no automatic model retry or application.';return result
        record=task_results.review(self.runner,key)
        package=record['review']
        if package['conflicts'] or package['requires_full_review']:
            result=task_results.compact_record(self.runner,record,include_review=True)
            result['next']='Explicit review is required; conflicts and full-content checks are retained.';return result
        engine=self.runner.backend.engine();study=engine.inspect(record['study_id'])
        trial=next(t for t in study['trials'] if any(a['id']==package['attempt_id'] for a in t['attempts']))
        variant=next(v for v in study['frozen']['definition']['variants'] if v['id']==trial['variant_id'])
        options=record['options']
        folder=self.runner.root/(digest(key)+'-reviews')/package['review_digest']
        payload={'task':options['task'],'review':package,'evidence':execution.get('receipts',[])}
        decision=self.reviewer.decide(folder,payload,model=variant['model'],effort=variant.get('effort'),
                                     timeout=max(1,min(180,int(deadline-time.monotonic()))) if timeout is not None else 0)
        if decision['decision']['approved'] and apply:
            record=task_results.apply(self.runner,key,package['review_digest'])
        result=task_results.compact_record(self.runner,record)
        result['execution']=task_results.compact_execution(execution)
        result['decision']={k:decision[k] for k in ('state','decision','model','effort','usage','inference_usage')}
        result['completion_state']='applied' if result.get('application',{}).get('applied') else 'reviewed' if decision['decision']['approved'] else 'rejected'
        result['decision_path']=str(folder/'decision.json')
        calls=execution.get('calls',[])+[{'call_id':'review:'+decision['thread_id'],'usage':decision['usage']}]
        result['usage']=usage_totals(calls)
        workspace.private_write(self.runner.root/(digest(key)+'-completion.json'),result)
        return result
