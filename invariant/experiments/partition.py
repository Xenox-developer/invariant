"""Share a job's existing memory/PID budget with a paused executor, never expand it."""
import copy
import json
import math
import re

from .. import resources,workspace
from .errors import ExperimentError
from .runners import owned_info


def memory_bytes(value):
    match=re.fullmatch(r'([0-9.]+)\s*(B|KiB|MiB|GiB|kB|MB|GB)',value.strip())
    if not match:raise ExperimentError('RESOURCE_UNKNOWN','Docker memory statistics were not interpretable.')
    return math.ceil(float(match[1])*{'B':1,'KiB':1024,'MiB':1024**2,'GiB':1024**3,'kB':1000,'MB':1000**2,'GB':1000**3}[match[2]])


class EvaluationPartition:
    def __init__(self,context,folder,evaluator_name):
        self.context,self.folder,self.evaluator_name=context,folder,evaluator_name
        self.original=context.policy;self.changed=False

    def reserve(self):
        info=owned_info(self.context)
        if not info or not info['State'].get('Paused'):
            raise ExperimentError('RESOURCE_UNKNOWN','Only a paused worker can share its budget.')
        if self.original['resource_mode']=='unlimited':
            contract=resources.execution_contract(self.original)
            workspace.private_write(self.folder/'partition.json',{'schema_version':1,'unlimited':True,
                'worker':self.context.owner_token,'evaluator':self.evaluator_name,'job_contract':contract,
                'worker_contract':contract,'evaluator_contract':contract,'verified':True,
                'observed_worker_host_config':info['HostConfig'],'observed_worker_state':info['State']})
            return copy.deepcopy(self.original)
        stats=json.loads(workspace.docker('stats','--no-stream','--format','{{json .}}',self.context.owner_token))
        try:used=memory_bytes(stats['MemUsage'].split('/')[0]);processes=int(stats['PIDs'])
        except (KeyError,ValueError,TypeError):raise ExperimentError('RESOURCE_UNKNOWN','Paused worker statistics are unavailable.') from None
        total=self.original['limits']['memory_mib']
        reserve=max(128,math.ceil((used/1024**2+64)/64)*64)
        pids=max(32,processes+16)
        if total-reserve<256 or self.original['limits']['processes']-pids<64:
            raise ExperimentError('VALIDATION_RESOURCE_LIMIT','The paused worker leaves insufficient budget for validation.')
        evaluator=copy.deepcopy(self.original);evaluator['limits']['memory_mib']=total-reserve
        evaluator['limits']['processes']-=pids
        held=copy.deepcopy(self.original);held['limits']['memory_mib']=reserve;held['limits']['processes']=pids
        held['limits']['extra_swap_mib']=0
        record={'schema_version':1,'worker':self.context.owner_token,'evaluator':self.evaluator_name,
                'job_contract':resources.execution_contract(self.original),
                'worker_contract':resources.execution_contract(held),'evaluator_contract':resources.execution_contract(evaluator),
                'cpu_exclusion':'Worker paused until evaluator termination is confirmed.'}
        # Persist intended limits before applying them, for crash recovery/audits.
        workspace.private_write(self.folder/'partition.json',record)
        self.changed=True
        workspace.docker('update','--memory='+str(reserve*1024**2),'--memory-swap='+str(reserve*1024**2),
                         '--pids-limit='+str(pids),self.context.owner_token)
        actual=owned_info(self.context);resources.verify_contract(actual,record['worker_contract'])
        if not actual['State'].get('Paused'):raise ExperimentError('UNRESOLVED_EXECUTION','Worker resumed during validation reservation.')
        record['observed_worker_host_config']=actual['HostConfig'];record['observed_worker_state']=actual['State'];record['verified']=True
        workspace.private_write(self.folder/'partition.json',record)
        return evaluator

    def restore(self):
        if not self.changed:return
        limits=self.original['limits'];size=limits['memory_mib']*1024**2
        workspace.docker('update','--memory='+str(size),'--memory-swap='+str(size+limits['extra_swap_mib']*1024**2),
                         '--pids-limit='+str(self.original['limits']['processes']),self.context.owner_token)
        resources.verify_contract(owned_info(self.context),resources.execution_contract(self.original))
        self.changed=False

    @staticmethod
    def verify(record,evaluation,expected):
        if record['job_contract']!=expected or not record.get('verified') or not record['observed_worker_state'].get('Paused'):
            raise ExperimentError('RESOURCE_MISMATCH','Validation resource reservation is not confirmed.')
        worker,child=record['worker_contract'],record['evaluator_contract']
        if record.get('unlimited'):
            if expected.get('mode')!='unlimited' or worker!=expected or child!=expected:
                raise ExperimentError('RESOURCE_MISMATCH','Unlimited validation contract changed.')
            runtime=evaluation.get('runtime',{})
            if runtime.get('resource_contract')!=child:raise ExperimentError('RESOURCE_MISMATCH','Evaluator contract changed.')
            resources.verify_contract({'HostConfig':record['observed_worker_host_config']},worker)
            resources.verify_contract({'HostConfig':runtime.get('host_config',{})},child)
            return
        for key in ('memory_bytes','extra_swap_bytes','processes'):
            if worker['limits'][key]+child['limits'][key]!=expected['limits'][key]:
                raise ExperimentError('RESOURCE_MISMATCH','Validation expanded the aggregate job budget.')
        resources.verify_contract({'HostConfig':record['observed_worker_host_config']},worker)
        runtime=evaluation.get('runtime',{})
        if runtime.get('resource_contract')!=child:
            raise ExperimentError('RESOURCE_MISMATCH','Evaluator resource evidence is missing or changed.')
        resources.verify_contract({'HostConfig':runtime.get('host_config',{})},child)
