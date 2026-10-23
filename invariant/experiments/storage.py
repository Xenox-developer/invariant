"""Honest disk accounting and reference-aware retention using existing deletion."""
import json
from pathlib import Path
import os
import shutil
import stat
import time

from .. import workspace
from .errors import ExperimentError


def usage(root):
    logical=allocated=0;seen=set()
    if not Path(root).exists():return {'logical_bytes':0,'allocated_bytes':0}
    for base,dirs,files in os.walk(root,followlinks=False):
        for name in dirs+files:
            try:info=(Path(base)/name).lstat()
            except FileNotFoundError:continue
            identity=(info.st_dev,info.st_ino)
            if identity in seen:continue
            seen.add(identity)
            if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):logical+=info.st_size
            allocated+=getattr(info,'st_blocks',0)*512
    return {'logical_bytes':logical,'allocated_bytes':allocated}


def images(engine, studies):
    identities=set()
    for row in studies:
        study=engine.inspect(row['id']);identities.add(study['frozen']['image']['id'])
        for trial in study['trials']:
            for attempt in trial['attempts']:
                image=attempt['record'].get('checkpoint',{}).get('image')
                if image:identities.add(image)
    return sorted(identities)


def overview(engine):
    studies=engine.store.list(100000)
    rows=[]
    for row in studies:
        study=engine.inspect(row['id']);size=usage(engine.root/'studies'/row['id'])
        for trial in study['trials']:
            for attempt in trial['attempts']:
                extra=usage(engine.root/'attempts'/attempt['id'])
                for key in size:size[key]+=extra[key]
        rows.append({'study_id':row['id'],'state':row['state'],'created_at':row['created_at'],
                     'selected':row['selection']['attempt_id'] is not None,'baseline_id':row['baseline_id'],**size})
    baselines={row['baseline_id']:usage(engine.root/'baselines'/row['baseline_id']) for row in studies}
    image_rows=[]
    for image in images(engine,studies):
        try:
            info=json.loads(workspace.docker('image','inspect',image))[0]
            image_rows.append({'id':image,'logical_bytes':info['Size'],'available':True})
        except (OSError,RuntimeError,ValueError,IndexError,KeyError):
            image_rows.append({'id':image,'logical_bytes':None,'available':False})
    existing=engine.state
    while not existing.exists():existing=existing.parent
    return {'schema_version':1,'studies':rows,'shared_baselines':baselines,'state':usage(engine.root),
            'total_project_private_state':usage(engine.state),
            'retained_restore_states':[{'path':str(p),**usage(p)} for p in engine.state.glob('restore-before-*')
                                       if p.is_dir() and not p.is_symlink()],
            'host_free_bytes':shutil.disk_usage(existing).free,'images':image_rows,'daemon_free_bytes':None,
            'hard_disk_quota':False,'image_sizes_include_shared_layers':True,
            'notice':'Study sizes exclude shared baselines and Docker images. Image logical sizes are not reclaimable physical totals.'}


def cleanup(engine, *, older_than_days=30, confirm=False, now=None):
    if type(older_than_days) not in (int,float) or not 0<=older_than_days<=36500:
        raise ExperimentError('INVALID_ARGUMENTS','Retention days must be between 0 and 36500.')
    cutoff=(time.time() if now is None else now)-older_than_days*86400
    result={'confirmed':confirm,'older_than_days':older_than_days,'candidates':[],'deleted':[],'skipped':[],'errors':[]}
    # Children are newer than parents. Existing delete rechecks references, selection,
    # process ownership and leases at each mutation; no global Docker prune is used.
    for row in engine.store.list(100000):
        if row['created_at']>cutoff:continue
        try:
            plan=engine.delete(row['id'],confirm=False)
            result['candidates'].append({'study_id':row['id'],'paths':plan['paths'],'image_count':len(plan['images'])})
            if confirm:
                engine.delete(row['id'],confirm=True)
                result['deleted'].append(row['id'])
        except ExperimentError as error:
            if error.code in ('SELECTED_STATE','REFERENCED_STATE','ACTIVE_STATE','CONTROLLER_BUSY','PROJECT_BUSY'):
                result['skipped'].append({'study_id':row['id'],'code':error.code})
            else:result['errors'].append({'study_id':row['id'],'code':error.code})
    return result
