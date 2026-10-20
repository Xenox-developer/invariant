"""Resolve complete saved results and describe content changes without project writes."""
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath

from .definitions import digest, source_path
from .errors import ExperimentError
from .files import diff_entries
from .snapshots import protected, regular_bytes
from .store import TERMINAL


def excluded(path, study):
    from .snapshots import glob_match
    if protected(path.casefold()): return True
    parts = PurePosixPath(path)
    candidates = [path, *(p.as_posix() for p in parts.parents if str(p) != '.')]
    rules = study['frozen']['definition']['workspace']['exclude']
    baseline = study.get('_baseline_manifest', {})
    omissions=baseline.get('host_exclusions',baseline.get('excluded', []))+study.get('_host_secret_paths',[])
    return any(glob_match(p, rule) or glob_match(p+'/', rule) for p in candidates for rule in rules) or any(
        path.casefold() == p.casefold() or path.casefold().startswith(p.casefold()+'/') for p in omissions)


def safe_links(entries):
    """Resolve only manifest links, never host links. Return target dependencies."""
    dependencies = set()
    for path, row in entries.items():
        if row['type'] != 'symlink': continue
        pending = list(PurePosixPath(path).parts); stack=[]; followed=set()
        while pending:
            part=pending.pop(0)
            if part in ('', '.'): continue
            if part == '..':
                if not stack: raise ExperimentError('UNSAFE_LINK', 'Saved link escapes the project: '+path)
                stack.pop(); continue
            stack.append(part); current='/'.join(stack); value=entries.get(current)
            if protected(current.casefold()): raise ExperimentError('UNSAFE_LINK', 'Saved link targets protected state: '+path)
            dependencies.add(current)
            if value and value['type']=='symlink':
                target=value['target']
                if current in followed or len(followed)>=40 or not target or '\0' in target or target.startswith('/'):
                    raise ExperimentError('UNSAFE_LINK', 'Saved link is absolute, cyclic or invalid: '+path)
                followed.add(current);stack.pop();pending=target.split('/')+pending
        if stack and '/'.join(stack) not in entries:
            raise ExperimentError('UNSAFE_LINK', 'Saved link target is not included in the result: '+path)
    return dependencies


def verify_tree(root, entries):
    names={}
    for path,row in entries.items():
        source_path(path)
        if path.startswith('@input/'): continue
        folded=path.casefold()
        if folded in names and names[folded]!=path:
            raise ExperimentError('CASE_COLLISION', 'Case-colliding result paths cannot be applied portably.')
        names[folded]=path
        for parent in PurePosixPath(path).parents:
            if str(parent)!='.' and entries.get(str(parent),{}).get('type','directory')!='directory':
                raise ExperimentError('CORRUPT_RESULT', 'Saved result traverses a non-directory: '+path)
        if row['type']=='file':
            data,_=regular_bytes(root,path,maximum=row['size'])
            if len(data)!=row['size'] or hashlib.sha256(data).hexdigest()!=row['sha256']:
                raise ExperimentError('CORRUPT_RESULT', 'Saved content changed: '+path)
        elif row['type']=='symlink':
            import os
            if not (root/path).is_symlink() or os.readlink(root/path)!=row['target']:
                raise ExperimentError('CORRUPT_RESULT', 'Saved link changed: '+path)
        elif row['type']=='directory':
            from .snapshots import safe_path
            import os
            fd,name=safe_path(root,path)
            try:
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(child)
            finally:os.close(fd)
        else: raise ExperimentError('CORRUPT_RESULT', 'Unknown saved entry type.')


def resolve(engine, identifier):
    item=engine.inspect(identifier)
    if item['kind']=='study':
        chosen=item['selection']['attempt_id']
        if not chosen: raise ExperimentError('NO_SELECTION', 'This study has no selected result.', 'Select a complete attempt first, or pass an explicit trial/attempt ID.')
        attempt=engine.store.inspect(chosen)
    elif item['kind']=='trial':
        if len(item['attempts'])!=1:
            raise ExperimentError('AMBIGUOUS_ATTEMPT', 'This trial has multiple attempts; pass an explicit attempt ID.')
        attempt={**item['attempts'][0], 'study_id':item['study_id']}
    else: attempt=item
    study=engine.inspect(attempt['study_id']);record=attempt['record']
    if (attempt['state'] not in TERMINAL or not record.get('cleanup_confirmed') or
        not record.get('capture',{}).get('complete') or not record.get('checkpoint',{}).get('complete')):
        raise ExperimentError('INCOMPLETE_RESULT', 'A complete stopped workspace and environment are required.', 'Recover retained work or explicitly retry; no fresh environment will be substituted.')
    folder=engine.root/'attempts'/attempt['id'];root=folder/'snapshot/workspace'
    raw,_=regular_bytes(folder,'snapshot/manifest.json');manifest=json.loads(raw)
    if digest(manifest['entries'])!=manifest['content_digest'] or manifest['content_digest']!=record['capture']['content_digest']:
        raise ExperimentError('CORRUPT_RESULT', 'Saved result manifest changed.')
    verify_tree(root,manifest['entries'])
    base=engine.root/'baselines'/study['baseline_id']
    before=json.loads(regular_bytes(base,'manifest.json')[0])
    verify_tree(base/'workspace',{p:r for p,r in before['entries'].items() if not p.startswith('@input/')})
    study['_baseline_manifest']=before
    from .lifecycle import references
    study['_host_secret_paths']=[]
    for value in references(engine,study).values():
        try:study['_host_secret_paths'].append(Path(value).resolve().relative_to(engine.project).as_posix())
        except ValueError:pass  # Outside-project credentials cannot be an apply destination.
    return study,attempt,base,root,before,manifest


def describe(engine, identifier):
    study,attempt,base,root,before,after=resolve(engine,identifier)
    changes=[];omitted=[];remaining=2*1024**2
    patch_paths=study['frozen']['input_checks'].get('variant_changed_paths',{}).get(
        next(t['variant_id'] for t in study['trials'] if t['id']==attempt['trial_id']),[])
    for row in diff_entries(before['entries'],after['entries']):
        path=row['path']
        if excluded(path,study): omitted.append({'path':path,'reason':'protected_or_excluded'});continue
        row={**row,'variant_patch':path in patch_paths,'text_diff':None,'text_diff_truncated':False}
        texts=[]
        for tree,entry in ((base/'workspace',row['before']),(root,row['after'])):
            if entry and entry['type']!='file' or entry and entry.get('size',0)>65536:break
            try:texts.append(regular_bytes(tree,path,maximum=65536)[0].decode('utf-8') if entry else '')
            except UnicodeError:break
        if len(texts)==2 and remaining>0:
            value=''.join(difflib.unified_diff(texts[0].splitlines(True),texts[1].splitlines(True),fromfile='before/'+path,tofile='after/'+path))
            row['text_diff']=value[:remaining];row['text_diff_truncated']=len(value)>remaining;remaining-=len(row['text_diff'])
        changes.append(row)
    desired={p:r for p,r in after['entries'].items() if not excluded(p,study)}
    safe_links(desired)
    return {'study_id':study['id'],'trial_id':attempt['trial_id'],'attempt_id':attempt['id'],
            'baseline_id':study['baseline_id'],'result_digest':after['content_digest'],
            'selection_revision':study['selection']['revision'],'parent':study['frozen'].get('parent'),
            'changes':changes,'excluded':omitted,'environment':{'image':attempt['record']['checkpoint']['image'],
                'applied_to_host':False},'project_files_changed':False}
