"""A worker tool invokes the protected evaluator; receipts bind frozen candidates."""
import json
from pathlib import Path
import time

from .. import workspace
from ..execution import private_directory
from .definitions import digest
from .errors import ExperimentError
from .runners import stop_owned, reclaim_private
from .snapshot_store import EnvironmentSnapshotStore
from ..telemetry import traced,span

TOOL={'type':'function','name':'invariant_validate',
      'description':'Run the fixed acceptance checks through the trusted coordinator. First finish required persistent-state changes and stop application services cleanly. Do not run the full shared suite separately. On failure inspect the returned evidence, fix within this same session, then call again. Success freezes the accepted workspace/environment: finish with a brief final message; further edits require a new validation.',
      'inputSchema':{'type':'object','properties':{},'additionalProperties':False}}


class ValidationSession:
    def __init__(self,controller,study,attempt,context,inputs,intent):
        self.controller,self.study,self.attempt=controller,study,attempt
        self.context,self.inputs,self.intent=context,inputs,intent
        self.root=controller.engine.root/'attempts'/attempt['id']/'validations'
        self.store=EnvironmentSnapshotStore();self.accepted=None
        self.binding=digest({'study':study['id'],'attempt':attempt['id'],'frozen':study['frozen']})

    @traced('validation.total')
    def run(self,call_id,*,cancel,timeout):
        if not isinstance(call_id,str) or not call_id or len(call_id)>512:
            raise ExperimentError('INVALID_TOOL','Validation needs a stable tool call ID.')
        folder=private_directory(self.root)/digest(call_id)
        path=folder/'receipt.json'
        if path.exists():
            record=json.loads(path.read_text())
            if record['binding']!=self.binding:raise ExperimentError('REQUEST_CONFLICT','Validation inputs changed.')
            if record['state']!='completed':raise ExperimentError('VALIDATION_UNCERTAIN','Interrupted validation retained; no automatic replay.')
            return self.compact(record)
        if self.accepted is not None:return self.compact(self.accepted[1])
        if cancel.is_set():raise ExperimentError('DEADLINE_EXCEEDED','Worker budget ended before validation.')
        folder.mkdir(mode=0o700);started=time.monotonic();contexts=[];partition=None
        record={'schema_version':1,'binding':self.binding,'state':'preparing','created_at':time.time(),'call_id':call_id}
        workspace.private_write(path,record)
        try:
            with span('validation.capture'):
                record['snapshot']=self.store.capture(self.context,folder,self.study['frozen']['capture_limits'])
            policy=None
            if record['snapshot'].get('paused'):
                from .partition import EvaluationPartition
                partition=EvaluationPartition(self.context,folder,'invariant-exp-'+self.attempt['id'].removeprefix('attempt-')+'-evaluation')
                policy=partition.reserve()
                record['partition_digest']=digest(json.loads((folder/'partition.json').read_text()))
            record.update(state='evaluating');workspace.private_write(path,record)
            remaining=timeout-(time.monotonic()-started)
            if remaining<=0 or cancel.is_set():raise ExperimentError('DEADLINE_EXCEEDED','Worker budget ended during snapshot.')
            with span('validation.evaluate'):
                record['evaluation']=self.controller.evaluate(self.study,self.attempt,folder,self.inputs,
                    record['snapshot']['image'],record['snapshot']['manifest'],contexts,[],cancel,self.intent,timeout=remaining,policy=policy)
            if partition is not None and record['evaluation'].get('passed') is True:
                from .. import resources
                allocation=json.loads((folder/'partition.json').read_text())
                EvaluationPartition.verify(allocation,record['evaluation'],resources.execution_contract(self.context.policy))
            if record['evaluation'].get('status')=='completed' and record['evaluation'].get('passed') is True:
                self.accepted=(folder,record)
        finally:
            clean=True
            for context in contexts:
                try:
                    if not stop_owned(context):raise RuntimeError('Unconfirmed evaluator stop')
                    reclaim_private(context);workspace.remove_owned(context.owner_token)
                except (OSError,RuntimeError):clean=False
            if not clean:
                self.accepted=None
                record.update(state='uncertain',cleanup_confirmed=False)
                workspace.private_write(path,record)
                raise ExperimentError('UNRESOLVED_EXECUTION','Validation evaluator stop could not be confirmed.')
            if partition is not None:partition.restore()
        if record['snapshot'].get('paused'):
            workspace.docker('unpause',self.context.owner_token)
            if self.accepted is not None or cancel.is_set():
                if not stop_owned(self.context):
                    self.accepted=None
                    raise ExperimentError('UNRESOLVED_EXECUTION','Final worker stop was not confirmed.')
                from .runners import owned_info
                record['snapshot']['stopped_state']=owned_info(self.context)['State']
        record.update(state='completed',finished_at=time.time());workspace.private_write(path,record)
        if self.accepted is None and not cancel.is_set():
            # Keep the exact original writable layer for repairs; the evaluator
            # ran in its own copy and cannot modify the worker's persistent DB.
            if not record['snapshot'].get('paused'):workspace.docker('start',self.context.owner_token)
        return self.compact(record)

    def compact(self,record):
        evaluation=record.get('evaluation',{});metrics=evaluation.get('metrics',{})
        result={'passed':evaluation.get('passed'),'status':evaluation.get('status'),
                'reason':evaluation.get('reason'),'checks':{k:metrics.get(k) for k in ('tests_total','tests_failures','tests_errors')},
                'receipt_digest':digest(record),'frozen':evaluation.get('passed') is True}
        if evaluation.get('passed') is not True:
            folder=self.root/digest(record['call_id'])
            log=folder/'evaluation/artifacts/test-output.txt'
            result['diagnostic']=log.read_text(errors='replace')[-5000:] if log.exists() else 'Inspect retained validation evidence.'
        return result

    @traced('validation.publish')
    def consume(self):
        if self.accepted is None:return None
        folder,record=self.accepted
        if record.get('partition_digest') and digest(json.loads((folder/'partition.json').read_text()))!=record['partition_digest']:
            raise ExperimentError('CORRUPT_STATE','Validation resource reservation changed.')
        if record['binding']!=self.binding or not self.store.matches(self.context,folder,record['snapshot'],self.study['frozen']['capture_limits']):return None
        parent=self.root.parent
        # Publishing the already durable candidate does not commit it again.
        import shutil
        # The exact immutable tree already verified by matches becomes the
        # canonical result with one atomic rename, not another byte copy.
        workspace.private_write(folder/'publication.json',{'state':'prepared','receipt_digest':digest(record),
                                'destination':str(parent/'snapshot')})
        if (parent/'snapshot').exists():raise ExperimentError('CORRUPT_STATE','Result publication already exists')
        source=folder/'snapshot';source.chmod(0o700)
        try:source.rename(parent/'snapshot')
        finally:
            (source if source.exists() else parent/'snapshot').chmod(0o500)
        from .engine import sync_directory
        sync_directory(folder);sync_directory(parent)
        workspace.private_write(folder/'publication.json',{'state':'published','receipt_digest':digest(record),
                                'destination':str(parent/'snapshot')})
        shutil.copytree(folder/'evaluation',parent/'evaluation',symlinks=True)
        checkpoint=record['snapshot']['checkpoint_record']
        workspace.private_write(self.context.checkpoint_path,checkpoint)
        workspace.remove_owned(self.context.owner_token)
        evaluation={**record['evaluation'],'reused_validation':True,'validation_receipt':str(folder/'receipt.json')}
        snapshot=record['snapshot']
        return {'saved':snapshot['manifest'],'evaluation':evaluation,'image':snapshot['image'],
                'receipt_digest':digest(record)}
