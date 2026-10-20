"""Read-only policy display and live Docker/kernel inspection for one project."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import tomllib

from .codex_rpc import inert
from .resources import AUTO_CAPS
from . import packages
from . import environment as machine
from .policy import PROFILE, DEFAULT_CONFIG, normalize, resource_limits, docker_network
from .workspace import docker, load_project, operator, state_for, verify_container, PROXY_VARIABLES

# -I prevents a writable project or PYTHONPATH from replacing these stdlib
# modules and forging inspection results. No project files or credentials read.
KERNEL_PROBE = '''import errno,json,os,pathlib,resource,socket
cg=pathlib.Path('/sys/fs/cgroup')
def limit(name):
    value=(cg/name).read_text().strip()
    return None if value=='max' else int(value)
quota,period=(cg/'cpu.max').read_text().split()
status=dict(line.split(':',1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line)
network={}
for label,family in [('ipv4',socket.AF_INET),('ipv6',socket.AF_INET6)]:
    try: s=socket.socket(family,socket.SOCK_STREAM)
    except OSError as e: network[label]='denied' if e.errno in (errno.EPERM,errno.EACCES) else 'unverified'
    else: s.close();network[label]='allowed'
scratch=os.statvfs('/scratch')
print(json.dumps({'cpus':None if quota=='max' else int(quota)/int(period),
 'memory_bytes':limit('memory.max'),'extra_swap_bytes':limit('memory.swap.max'),
 'processes':limit('pids.max'),'file_descriptors':list(resource.getrlimit(resource.RLIMIT_NOFILE)),
 'scratch_bytes':scratch.f_blocks*scratch.f_frsize,'uid':os.getuid(),
 'capabilities_effective':int(status['CapEff'].strip(),16),'capabilities_bounding':int(status['CapBnd'].strip(),16),
 'no_new_privileges':status['NoNewPrivs'].strip()=='1','seccomp_filter':status['Seccomp'].strip()=='2',
 'root_read_only':bool(os.statvfs('/').f_flag&os.ST_RDONLY),
 'workspace_read_only':bool(os.statvfs('/workspace').f_flag&os.ST_RDONLY),
 'policy_read_only':bool(os.statvfs('/workspace/.invariant').f_flag&os.ST_RDONLY),'network_sockets':network}))
'''


def read_small(path, limit=8192):
    with open(path, 'rb', opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW | os.O_NONBLOCK)) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > limit:
            raise RuntimeError('Unsafe or oversized inspection metadata')
        return source.read(limit + 1)


def launch_record(state, session):
    # Lifecycle metadata only; never open Codex history or auth.json.
    with open(state / (session + '.jsonl'), 'rb',
              opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW | os.O_NONBLOCK)) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Unsafe session launch record')
        for _ in range(16):
            line = source.readline(8193)
            if not line or len(line) > 8192:
                break
            row = json.loads(line)
            if row.get('event') == 'session_start' and row.get('session') == session:
                return row
    raise RuntimeError('The trusted session launch record is unavailable')


def docker_observations(info):
    host = info['HostConfig']
    memory, combined = host.get('Memory'), host.get('MemorySwap')
    environment = dict(row.split('=', 1) for row in info['Config'].get('Env') or [] if '=' in row)
    general = (info['Config'].get('Labels') or {}).get('io.invariant.environment') == machine.PROFILE
    expected_environment = machine.ENVIRONMENT if general else packages.ENVIRONMENT
    return {'image': info['Image'], 'container': info['Id'], 'network': host.get('NetworkMode'),
            'project_dependencies_configured': all(environment.get(key) == value for key, value in expected_environment.items()),
            'networks': sorted((info.get('NetworkSettings') or {}).get('Networks', {})),
            'root_read_only': host.get('ReadonlyRootfs'), 'privileged': host.get('Privileged'),
            'user': info['Config'].get('User'), 'cpus': host['NanoCpus'] / 1_000_000_000 if host.get('NanoCpus') else None,
            'memory_bytes': memory, 'memory_and_swap_bytes': combined,
            'extra_swap_bytes': combined - memory if memory and combined and combined >= memory else None,
            'processes': host.get('PidsLimit'), 'file_descriptors': host.get('Ulimits'),
            'scratch': host.get('Tmpfs'),
            'proxy_environment_nonempty': sorted(row.partition('=')[0] for row in info['Config'].get('Env') or []
                                                if row.partition('=')[0] in PROXY_VARIABLES and row.partition('=')[2]),
            'mounts': sorted([{key: mount.get(key) for key in ('Source', 'Destination', 'Type', 'RW')}
                              for mount in info['Mounts']], key=lambda mount: (str(mount['Destination']), str(mount['Source'])))}


def kernel_issues(kernel, policy=None):
    policy = normalize(policy or DEFAULT_CONFIG)
    limits = resource_limits(policy)
    general = machine.enabled(policy)
    expected = {**limits, 'file_descriptors': [limits['file_descriptors']] * 2,
                'uid': 0 if general else os.getuid(),
                'capabilities_effective': machine.CAPABILITY_MASK if general else 0,
                'capabilities_bounding': machine.CAPABILITY_MASK if general else 0,
                'no_new_privileges': True, 'seccomp_filter': True, 'root_read_only': not general or policy['workspace_access'] == 'read-only',
                'policy_read_only': True, 'workspace_read_only': policy['workspace_access'] == 'read-only', 'network_sockets': {family: 'allowed' if policy['command_network'] == 'internet' else 'denied' for family in ('ipv4', 'ipv6')}}
    if general:
        expected['control_read_only'] = True
    return [f'Kernel {key}: observed {kernel.get(key)!r}; required {value!r}'
            for key, value in expected.items() if kernel.get(key) != value]


def inspect(project=None):
    project = (project or Path.cwd()).resolve()
    policy = load_project(project)
    general = machine.enabled(policy)
    limits = resource_limits(policy)
    if policy["resource_mode"] == "auto":
        limits = {key: None for key in limits}
    result = {'schema': 1, 'project': str(project), 'profile': policy['profile'], 'status': 'inactive',
              'verified': False, 'configured': {**limits, 'default_duration_seconds': policy['duration_seconds'],
                'workspace_access': policy['workspace_access'], 'resource_mode': policy['resource_mode'],
                'automatic_caps': AUTO_CAPS if policy['resource_mode'] == 'auto' else None,
                'network': policy['command_network'], 'docker_network': docker_network(policy), 'writable_host_mount': str(project) if policy['workspace_access'] == 'read-write' else None, 'read_only_policy': str(project / '.invariant'),
                'project_disk_quota_bytes': None, 'model_disclosure': 'allowed', 'local_execution_fallback': 'disabled',
                'scope': 'command container; Codex authentication/model connection run on the trusted host'},
              'dependencies': ({'persistence': 'whole Linux filesystem and home; project-bound Docker checkpoints',
                                'administrator_inside_environment': True, 'language_restrictions': None,
                                'processes_restored': False, 'temporary_directory': '/scratch'} if general else
                               {'python_user_base': packages.USER_BASE, 'project_directory': str(project / packages.DIRECTORY),
                                'persistence': 'project files; no project disk quota'}),
              'session': None, 'runtime': None, 'configuration_changed': False, 'issues': []}
    state = state_for(project)
    current_path = state / 'current.json'
    try:
        before = read_small(current_path)
    except FileNotFoundError:
        return result
    except (OSError, ValueError, RuntimeError) as error:
        result.update(status='unverified', issues=['Cannot read current-session metadata: ' + str(error)])
        return result
    result['status'] = 'unverified'
    try:
        current = json.loads(before)
        session = current.get('session', '')
        if not isinstance(session, str) or not re.fullmatch(r'ws-[0-9a-f]{32}', session):
            raise RuntimeError('Invalid current-session identity')
        result['session'] = session
        reply = operator('status', session, quiet=True)
        if not reply.get('running') or not reply.get('ready'):
            raise RuntimeError('The session is starting or stopping; try guardrails again when ready')
        name = 'invariant-' + session
        info = json.loads(docker('inspect', name, timeout=10))[0]
        labels = info['Config'].get('Labels') or {}
        project_mounts = [m for m in info['Mounts'] if m.get('Destination') == '/workspace']
        if (labels.get('io.invariant.role') != 'codex-workspace' or labels.get('io.invariant.session') != name
                or len(project_mounts) != 1 or project_mounts[0].get('Source') != str(project)):
            raise RuntimeError('The recorded container does not belong to this project/session; it was not probed')
        if not info.get('State', {}).get('Running'):
            raise RuntimeError('The recorded container is not running')
        observed = docker_observations(info)
        result['runtime'] = {'docker': observed, 'kernel': None, 'launcher': None}
        record = launch_record(state, session)
        if record.get('project') != str(project):
            raise RuntimeError('The session launch record does not match this project policy')
        launched_policy = normalize(record['profile'])
        result['runtime']['policy'] = launched_policy
        result['configuration_changed'] = policy != normalize(record.get('project_policy', record['profile']))
        result['runtime']['resources'] = record.get('resources', {'mode': 'manual', 'limits': launched_policy['limits']})
        try:
            verify_container(name, project, record['image'], info=info, policy=launched_policy)
        except RuntimeError as error:
            result['issues'].append('Docker configuration mismatch: ' + str(error))
        # Read the live executor kernel, not documentation or a remembered test.
        probe = (machine.INSPECTOR,) if machine.enabled(launched_policy) else ('/usr/local/bin/python3', '-I', '-c', KERNEL_PROBE)
        kernel = json.loads(docker('exec', info['Id'], *probe, timeout=10))
        if not isinstance(kernel, dict):
            raise RuntimeError('Invalid kernel inspection response')
        result['runtime']['kernel'] = kernel
        result['issues'].extend(kernel_issues(kernel, launched_policy))
        home = state / 'codex'
        environments = tomllib.loads(read_small(home / 'environments.toml').decode())
        config = tomllib.loads(read_small(home / 'config.toml').decode())
        entries = environments.get('environments', [])
        fallback_disabled = (environments.get('include_local') is False and environments.get('default') == 'invariant'
            and len(entries) == 1 and entries[0].get('id') == 'invariant'
            and entries[0].get('args') == ['start', '--attach', '--interactive', name])
        duration = current.get('duration_seconds')
        if type(duration) is not int or not 1 <= duration <= 86400:
            duration = None
        dependency_environment = config.get('shell_environment_policy', {}).get('set', {})
        expected_environment = machine.ENVIRONMENT if machine.enabled(launched_policy) else packages.ENVIRONMENT
        dependencies_configured = all(dependency_environment.get(key) == value for key, value in expected_environment.items())
        result['runtime']['launcher'] = {'local_execution_fallback_configured': not fallback_disabled,
                                         'project_dependencies_configured': dependencies_configured,
                                         'web_search_configured': config.get('web_search'), 'duration_seconds': duration}
        if not fallback_disabled or config.get('web_search') != 'disabled' or not dependencies_configured:
            result['issues'].append('Launcher configuration does not match the isolated profile')
        final = json.loads(docker('inspect', info['Id'], timeout=10))[0]
        if (not final.get('State', {}).get('Running') or final.get('Id') != info['Id']
                or final.get('State', {}).get('StartedAt') != info.get('State', {}).get('StartedAt')
                or docker_observations(final) != observed):
            raise RuntimeError('The container changed or stopped during inspection; retry guardrails')
        if not operator('status', session, quiet=True).get('ready'):
            raise RuntimeError('The session stopped being ready during inspection')
        if read_small(current_path) != before:
            raise RuntimeError('The session changed during inspection; retry guardrails')
        result['status'] = 'mismatch' if result['issues'] else 'active'
        result['verified'] = not result['issues']
        result['observed_at'] = time.time()
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, RuntimeError, subprocess.SubprocessError) as error:
        result['issues'].append('Live inspection unavailable: ' + str(error))
    return result


def size(value):
    return f'{value / (1024 * 1024):g} MiB' if type(value) in (int, float) else 'unlimited or unavailable'


def render(report):
    print('INVARIANT guardrails')
    print('Project:', inert(report['project']))
    print('Profile:', report['profile'])
    if report['status'] == 'inactive':
        print('State: no active session — configured policy below; live enforcement is not currently verified.')
    elif report['verified']:
        print('State: ACTIVE — Docker configuration and executor kernel verified now.')
    else:
        print('State:', report['status'].upper(), '— live guardrails are NOT verified.')
    if report['session']:
        print('Session:', report['session'])
    runtime = report['runtime']
    if runtime:
        observed, kernel = runtime['docker'], runtime['kernel']
        print('\nObserved container settings:')
        print('  Resources:', runtime.get('resources', {}).get('mode', 'manual'))
        print('  Command network:', observed['network'])
        print('  CPU limit:', observed['cpus'] if observed['cpus'] is not None else 'unlimited or unavailable', 'CPU')
        print('  Memory:', size(observed['memory_bytes']) if observed['memory_bytes'] else 'unlimited or unavailable')
        print('  Additional swap:', size(observed['extra_swap_bytes']))
        print('  Processes:', observed['processes'] if observed['processes'] and observed['processes'] > 0 else 'unlimited or unavailable')
        print('  Container system files:', 'read-only' if observed['root_read_only'] else 'WRITABLE')
        print('  Privileged container:', observed['privileged'])
        print('  Container user:', observed['user'])
        for mount in observed['mounts']:
            print('  Mount:', inert(str(mount['Source'])), '->', inert(str(mount['Destination'])),
                  '(writable)' if mount['RW'] else '(read-only)')
        print('  Image:', observed['image'])
        if kernel:
            print('\nObserved inside the running executor:')
            print('  CPU:', kernel.get('cpus'), 'CPU; memory:', size(kernel.get('memory_bytes')),
                  '; extra swap:', size(kernel.get('extra_swap_bytes')))
            print('  Processes:', kernel.get('processes'), '; file descriptors (soft/hard):', kernel.get('file_descriptors'))
            print('  Temporary scratch:', size(kernel.get('scratch_bytes')))
            print('  IPv4/IPv6 socket creation:', kernel.get('network_sockets'))
            print('  Linux capabilities (effective/bounding):', kernel.get('capabilities_effective'), '/', kernel.get('capabilities_bounding'))
            print('  No new privileges:', kernel.get('no_new_privileges'), '; seccomp filter:', kernel.get('seccomp_filter'))
            print('  Project read-only:', kernel.get('workspace_read_only'))
            print('  Policy directory read-only:', kernel.get('policy_read_only'))
        if runtime['launcher']:
            launcher = runtime['launcher']
            print('\nLauncher settings:')
            print('  Session duration:', str(launcher['duration_seconds']) + ' seconds' if launcher['duration_seconds'] is not None
                  else 'unavailable for this session (started before duration reporting)')
            print('  Local execution fallback configured:', launcher['local_execution_fallback_configured'])
            print('  Web search configured:', launcher['web_search_configured'])
    else:
        policy = report['configured']
        print('\nConfigured for the next launch (not live measurements):')
        print('  Command network:', policy['network'], '; .invariant policy: read-only')
        print('  Project mount:', inert(report['project']), '-> /workspace (' + policy['workspace_access'] + ')')
        print('  Host home, authentication store and Docker socket: not mounted')
        if report['profile'] == machine.PROFILE:
            print('  Environment: administrator inside isolated Linux; package-management capabilities only; no-new-privileges; filtered sockets')
            print('  Environment filesystem:', 'read-only' if policy['workspace_access'] == 'read-only' else 'writable and saved across sessions')
            print('  Protected executor and static guardrail inspector: read-only host-managed mount')
        else:
            print('  Privileges: non-root, no capabilities, no-new-privileges, filtered socket families')
        if policy['resource_mode'] == 'auto':
            print('  Resources: managed automatically at launch; exact live limits are not selected yet.')
            print('  Automatic maximums:', json.dumps(policy['automatic_caps']))
        else:
            print('  CPU:', policy['cpus'], '; memory:', size(policy['memory_bytes']), '; extra swap:', size(policy['extra_swap_bytes']))
            print('  Processes:', policy['processes'], '; file descriptors:', policy['file_descriptors'], '; scratch:', size(policy['scratch_bytes']))
        print('  Default session duration:', policy['default_duration_seconds'], 'seconds (override with --duration)')
    if report['configuration_changed']:
        print('\nProject configuration changed. The running session keeps its recorded policy.')
        print('Next run/resume settings:', json.dumps(report['configured']))
    print('\nPolicy scope:')
    profile = runtime.get('policy', {}).get('profile') if runtime else report['profile']
    if profile == machine.PROFILE:
        print('  Persistence: whole environment filesystem, home, installed tools and configuration. Scratch and processes are temporary.')
        print('  Technologies: any Linux-compatible tools within Docker, network and resource limits; no package-manager-specific paths.')
    else:
        print('  Saved Python dependencies:', packages.USER_BASE, '(project storage; available on run/resume).')
    access = (runtime.get('policy', {}).get('workspace_access') if runtime else None) or report['configured']['workspace_access']
    print('  Project edits and deletion:', 'allowed.' if access == 'read-write' else 'denied (read-only).', 'Project disk quota: none.')
    network = (runtime.get('policy', {}).get('command_network') if runtime else None) or report['configured']['network']
    if network == 'internet':
        print('  Internet mode: outbound connections to reachable internet and local-network services; no domain allowlist or published ports.')
        print('  Socket permission is verified; internet reachability and package registries are not tested by guardrails.')
    else:
        print('  Command network: disabled (IPv4/IPv6 sockets denied).')
    print('  Sending project content and tool results to OpenAI: allowed.')
    print('  Limits apply to commands in Docker. Codex authentication/model communication run on the trusted host.')
    for issue in report['issues']:
        print('  WARNING:', inert(issue))
    if report['status'] == 'inactive':
        print('\nStart a workspace, then run invariant guardrails in another terminal to inspect live enforcement.')


def main(*, machine=False):
    report = inspect()
    if machine:
        print(json.dumps(report))
    else:
        render(report)
    return 0 if report['status'] in ('inactive', 'active') else 1
