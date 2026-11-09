"""Read-only, credential-free inspection of container storage boundaries."""
import json
from pathlib import Path

from . import docker_storage as storage


def status():
    path=storage.registry_path()
    registry=json.loads(path.read_text()) if path.exists() else {'schema_version':2,'projects':{}}
    roots=[]
    volumes=storage.docker('volume','ls','--filter','label='+storage.OWNER+'=v2','--format','{{.Name}}').stdout.decode().splitlines()
    for start in range(0,len(volumes),256):
        probe=storage.docker('volume','inspect',*volumes[start:start+256],check=False)
        for info in json.loads(probe.stdout or b'[]'):roots.append((info['Name'],info['Mountpoint']))
    rows=[];issues=[]
    names=storage.docker('ps','--all','--format','{{.Names}}').stdout.decode().splitlines()
    for name in names:
        if not name.startswith('invariant-'):continue
        probe=storage.docker('inspect',name,check=False)
        if probe.returncode:continue
        info=json.loads(probe.stdout)[0];labels=info['Config'].get('Labels') or {}
        if labels.get(storage.OWNER)=='v2' and name.startswith('invariant-manager-'):continue
        if labels.get('io.invariant.role') not in ('agent-runtime','catalog-runtime','codex-workspace','ownership-helper'):continue
        # Historical stopped containers are listed separately; they are never reused.
        if not info['State']['Running']:continue
        mounts=[];unsafe=[]
        for item in info.get('Mounts',[]):
            source=item.get('Source','');matched=next(((v,r) for v,r in roots if source==r or source.startswith(r+'/')),None)
            if matched:
                volume,root=matched;mounts.append({'destination':item['Destination'],'volume':volume,'path':source[len(root):].lstrip('/')})
            elif item['Type']=='tmpfs':mounts.append({'destination':item['Destination'],'temporary':True})
            elif item['Destination']=='/scratch' and item['Type']=='volume':mounts.append({'destination':'/scratch','volume':item.get('Name')})
            else:unsafe.append(item['Destination'])
        host=info['HostConfig']
        if unsafe or host.get('Privileged') or host.get('Devices') or host.get('PortBindings') or host.get('NetworkMode')=='host':
            issues.append({'container':name,'unexpected_mounts':unsafe,'authority_mismatch':True})
        rows.append({'container':name,'role':labels['io.invariant.role'],'mounts':mounts,
                     'privileged':host.get('Privileged'),'published_ports':bool(host.get('PortBindings'))})
    from .storage_migration import owned_runtime_paths,legacy_config_projects
    projects=[Path(row['project']) for row in registry['projects'].values() if row.get('project')]
    covered=[];local_runtime_count=0;local_config_count=0
    for project in sorted(set(projects),key=lambda p:len(p.parts)):
        original=project/'.invariant'
        if not original.is_dir() or any(original.is_relative_to(parent) for parent in covered):continue
        local_runtime_count+=len(owned_runtime_paths(project));local_config_count+=len(legacy_config_projects(project))
        covered.append(original)
    public_projects={}
    for key,row in registry['projects'].items():
        public_projects[key]={k:v for k,v in row.items() if k!='project'}
        public_projects[key]['name']=Path(row.get('project',key)).name
        public_projects[key]['project_ref']={'backend':'docker','volume':row['project_volume'],'path':'.'}
    legacy=Path.home()/'.invariant'
    return {'storage_layout':2,'registry':str(path),'projects':public_projects,'containers':rows,'issues':issues,
            'host_agent_state_present':(legacy/'workspaces').exists(),'host_managed_codex_present':(legacy/'tools').exists(),
            'host_local_runtime_directories':local_runtime_count,'host_local_project_configs':local_config_count,
            'verified':not issues and not local_runtime_count and not local_config_count and not (legacy/'workspaces').exists() and not (legacy/'tools').exists(),
            'volumes':[v for v,r in roots],'migration':registry.get('migrations',{})}
