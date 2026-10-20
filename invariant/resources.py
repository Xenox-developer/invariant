"""Conservative per-session sizing from Docker capacity; no workload execution."""
import copy
import json
import subprocess
from pathlib import Path

from .policy import DEFAULT_CONFIG, normalize

AUTO_CAPS = {'cpus': 4, 'memory_mib': 4096, 'extra_swap_mib': 0,
             'processes': 256, 'file_descriptors': 1024, 'scratch_mib': 512}


def select(policy, project, capacity=None, memory_hint=None):
    policy = normalize(policy)
    if policy['resource_mode'] == 'manual':
        return policy, {'mode': 'manual', 'limits': copy.deepcopy(policy['limits'])}
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
