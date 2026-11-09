"""Conservative per-session sizing from Docker capacity; no workload execution."""
import copy
import json
import subprocess
from pathlib import Path

from .policy import DEFAULT_CONFIG, normalize, resource_limits, scratch_options

AUTO_CAPS = {'cpus': 4, 'memory_mib': 4096, 'extra_swap_mib': 0,
             'processes': 256, 'file_descriptors': 1024, 'scratch_mib': 512}


def execution_contract(policy):
    """One resource description for creation, verification and comparisons."""
    policy=normalize(policy)
    limits=resource_limits(policy)
    if policy['resource_mode'] == 'unlimited':
        return {'schema_version':1, 'resolved':True, 'mode':'unlimited', 'profile':policy['profile'],
                'limits':limits, 'host_config':{'NanoCpus':0, 'Memory':0, 'MemorySwap':0},
                'docker_args':['--pids-limit=-1', '--volume=/scratch']}
    memory=limits['memory_bytes']
    swap=memory+limits['extra_swap_bytes']
    nofile=limits['file_descriptors']
    scratch={'/scratch':scratch_options(policy)}
    return {'schema_version':1,'resolved':policy['resource_mode']=='manual','profile':policy['profile'],'limits':limits,
            'host_config':{'NanoCpus':round(limits['cpus']*1000000000),'Memory':memory,'MemorySwap':swap,
                           'PidsLimit':limits['processes'],'Ulimits':[{'Name':'nofile','Hard':nofile,'Soft':nofile}],
                           'Tmpfs':scratch},
            'docker_args':[f"--cpus={limits['cpus']}",f'--memory={memory}',f'--memory-swap={swap}',
                           f"--pids-limit={limits['processes']}",f'--ulimit=nofile={nofile}:{nofile}',
                           '--tmpfs=/scratch:'+scratch['/scratch']]}


def verify_contract(info, contract):
    # Treat imported benchmark manifests as data, not as authority to omit limits.
    try:
        limits=contract['limits']
        if contract.get('mode') == 'unlimited':
            policy=normalize({**DEFAULT_CONFIG,'profile':contract['profile'],'resource_mode':'unlimited'})
            canonical=execution_contract(policy)
            if contract != canonical:raise ValueError()
            host=info.get('HostConfig',{})
            if any(host.get(key)!=value for key,value in canonical['host_config'].items()) or host.get('PidsLimit') not in (-1,0,None):
                raise RuntimeError('Container resource contract mismatch: unlimited resources')
            if host.get('Ulimits') or host.get('Tmpfs'):
                raise RuntimeError('Unexpected resource limits on an unlimited container')
            return True
        for key in ('memory_bytes','extra_swap_bytes','scratch_bytes'):
            if type(limits[key]) is not int or limits[key]%(1024*1024):raise ValueError()
        policy=normalize({**DEFAULT_CONFIG,'profile':contract['profile'],'resource_mode':'manual',
            'limits':{'cpus':limits['cpus'],'memory_mib':limits['memory_bytes']//(1024*1024),
                      'extra_swap_mib':limits['extra_swap_bytes']//(1024*1024),'processes':limits['processes'],
                      'file_descriptors':limits['file_descriptors'],'scratch_mib':limits['scratch_bytes']//(1024*1024)}})
        canonical=execution_contract(policy)
        if (type(contract.get('schema_version')) is not int or contract['schema_version']!=1 or type(contract.get('resolved')) is not bool
                or contract['host_config']!=canonical['host_config'] or limits!=canonical['limits']):raise ValueError()
    except (KeyError,TypeError,ValueError,RuntimeError):
        raise RuntimeError('Invalid container resource contract') from None
    host=info.get('HostConfig',{})
    if not isinstance(host,dict):raise RuntimeError('Missing container resource evidence')
    changed=[key for key,value in contract['host_config'].items() if host.get(key)!=value]
    if changed:
        raise RuntimeError('Container resource contract mismatch: '+', '.join(changed))
    return True


def execution_evidence(info,policy):
    contract=execution_contract(policy)
    return {'resource_contract':contract,'host_config':{
        key:info['HostConfig'].get(key) for key in contract['host_config']}}


def select(policy, project, capacity=None, memory_hint=None):
    policy = normalize(policy)
    if policy['resource_mode'] in ('manual', 'unlimited'):
        return policy, {'mode': policy['resource_mode'], 'limits': resource_limits(policy) if policy['resource_mode']=='unlimited' else copy.deepcopy(policy['limits'])}
    capacity = capacity if isinstance(capacity, dict) else {}
    cpu, memory = capacity.get('cpus'), capacity.get('memory_bytes')
    cpu = cpu if type(cpu) is int and 1 <= cpu <= 65536 else None
    memory = memory if type(memory) is int and 0 < memory < 2**63 else None
    cpu_cap = min(4, max(1, cpu // 2)) if cpu else 1
    memory_cap = min(4096, (memory // (1024 * 1024) // 2 // 64) * 64) if memory else 512
    if memory_cap < 64:
        raise RuntimeError('Docker has too little memory for a workspace. Increase Docker memory in its settings, then run invariant run again.')
    baseline = min(1024, memory_cap)
    hint = memory_hint if type(memory_hint) is int and 64 <= memory_hint <= 4096 else baseline
    chosen = max(baseline, min(hint, memory_cap))
    limits = {'cpus': min(2, cpu_cap), 'memory_mib': chosen, 'extra_swap_mib': 0,
              'processes': 256 if memory else 128, 'file_descriptors': 1024 if memory else 256,
              'scratch_mib': min(512, max(16, chosen // 4))}
    effective = copy.deepcopy(policy)
    effective.update(resource_mode='manual', limits=limits)
    plan = {'mode': 'auto', 'limits': limits, 'memory_ceiling_mib': memory_cap,
            'capacity': {'cpus': cpu, 'memory_bytes': memory}, 'project_kind': 'general',
            'conservative_fallback': cpu is None or memory is None}
    return normalize(effective), plan


def capacity(docker):
    try:
        result = json.loads(docker('info', '--format', '{"cpus":{{.NCPU}},"memory_bytes":{{.MemTotal}}}', timeout=10))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return {}


PRESSURE_PROBE = "import json,pathlib;print(json.dumps(dict((k,int(v)) for k,v in (line.split() for line in pathlib.Path('/sys/fs/cgroup/memory.events').read_text().splitlines()))))"


def pressure(docker, container, *, persistent=False):
    try:
        command = ('/__invariant/inspect', 'pressure') if persistent else ('/usr/local/bin/python3', '-I', '-c', PRESSURE_PROBE)
        result = json.loads(docker('exec', container, *command, timeout=5))
        if isinstance(result, dict) and type(result.get('oom_kill')) is int:
            return result['oom_kill']
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        pass
    try:
        result = json.loads(docker('inspect', container, '--format', '{{json .State}}', timeout=5))
        return 1 if isinstance(result, dict) and result.get('OOMKilled') is True else None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None
