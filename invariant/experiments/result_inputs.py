"""Verified file references between studies, without host staging or shared writes."""
import hashlib
import re

from .definitions import source_path
from .errors import ExperimentError
from .snapshots import protected,regular_bytes


def normalize(values):
    if values is None:return []
    if not isinstance(values,list) or len(values)>256:raise ExperimentError('INVALID_INPUT','Expected a bounded list of result inputs')
    result=[];destinations=set()
    for value in values:
        if not isinstance(value,dict) or set(value)!={'attempt_id','result_digest','files'}:
            raise ExperimentError('INVALID_INPUT','Result input requires attempt_id, result_digest and files')
        if not isinstance(value['attempt_id'],str) or not re.fullmatch(r'attempt-[0-9a-f]{32}',value['attempt_id']):
            raise ExperimentError('INVALID_INPUT','Invalid result attempt identity')
        if not isinstance(value['result_digest'],str) or not re.fullmatch(r'[0-9a-f]{64}',value['result_digest']):
            raise ExperimentError('INVALID_INPUT','Invalid result digest')
        files=value['files']
        if not isinstance(files,dict) or not 1<=len(files)<=256:raise ExperimentError('INVALID_INPUT','Explicit source/destination file mapping required')
        for source,target in files.items():
            source_path(source);source_path(target)
            if protected(source) or protected(target) or target in destinations:
                raise ExperimentError('INVALID_INPUT','Protected or duplicate result input path')
            destinations.add(target)
        result.append({**value,'files':dict(sorted(files.items()))})
    return result


def resolve(engine,values):
    from .results import resolve as saved_result,excluded
    files={}
    for ref in values:
        study,attempt,_,root,_,manifest=saved_result(engine,ref['attempt_id'])
        if attempt['record'].get('evaluation',{}).get('passed') is not True:
            raise ExperimentError('UNVERIFIED_INPUT','Only independently accepted results may become inputs')
        if manifest['content_digest']!=ref['result_digest']:
            raise ExperimentError('RESULT_CONFLICT','Result input digest does not match the saved snapshot')
        for source,target in ref['files'].items():
            row=manifest['entries'].get(source)
            if not row or row['type']!='file' or excluded(source,study):
                raise ExperimentError('UNSAFE_INPUT','Result input must be an allowed regular file: '+source)
            raw,_=regular_bytes(root,source,maximum=row['size'])
            if len(raw)!=row['size'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                raise ExperimentError('CORRUPT_RESULT','Result input changed')
            files[target]=raw
    return files
