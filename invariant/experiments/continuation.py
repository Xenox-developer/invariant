"""A continuation is a new frozen study, never a replay or a mutable parent fork."""
import copy
import hashlib
import json
from pathlib import Path
import uuid

from .. import environment, workspace
from ..execution import private_directory, project_lock
from .definitions import digest, expand, MAX_DEFINITION
from .errors import ExperimentError
from .files import materialize, write_file
from .lifecycle import references, request_key
from .results import resolve, safe_links
from .snapshots import regular_bytes, seal_tree, protected


def create(engine, identifier, prompt_file, *, request_id=None):
    from .engine import sync_tree, sync_directory
    request_id=request_key(request_id) or str(uuid.uuid4())
    relative=engine._path(prompt_file)
    with project_lock(engine.root/'mutations',timeout=3):
        with engine._lock(reconcile=False):
            if (engine.state/'current.json').exists():
                raise ExperimentError('WORKSPACE_RECOVERY_REQUIRED','Finish or recover the ordinary chat before capturing a continuation prompt.')
            prompt,_=regular_bytes(engine.project,relative,maximum=MAX_DEFINITION)
            try:
                if not prompt.decode('utf-8').strip(): raise ValueError()
            except (UnicodeError,ValueError):raise ExperimentError('INVALID_INPUT','Continuation prompt must be nonempty UTF-8 text.') from None
            fingerprint=digest({'operation':'continue','identifier':identifier,'prompt_file':relative,'prompt_digest':hashlib.sha256(prompt).hexdigest()})
            existing=engine.store.lookup_request(request_id,fingerprint)
            if existing:return {**engine.inspect(existing),'idempotent_replay':True}
            study,attempt,base,saved,_,manifest=resolve(engine,identifier)
            folder=engine.root/'attempts'/attempt['id']
            checkpoint=attempt['record']['checkpoint']
            selected=environment.selected_image(folder/'control',engine.project,study['frozen']['image']['id'],owner=attempt['id']+'-runner')
            if selected!=checkpoint['image']:
                raise ExperimentError('INCOMPLETE_RESULT','The exact parent checkpoint is unavailable; no replacement was made.')
            # Preserve the complete private workspace, including generated .env
            # files and Linux link text. Materialization never dereferences links;
            # host apply has its own stricter exclusion and link checks.
            entries=copy.deepcopy(manifest['entries'])
            child_id='study-'+uuid.uuid4().hex
            staging=private_directory(engine.root/'staging')/('continuation-'+uuid.uuid4().hex);staging.mkdir(mode=0o700)
            materialize(saved,staging/'workspace',entries)
            inputs=staging/'inputs';inputs.mkdir(mode=0o700)
            frozen=copy.deepcopy(study['frozen']);definition=frozen['definition']
            trial=next(t for t in study['trials'] if t['id']==attempt['trial_id'])
            variant=copy.deepcopy(next(v for v in definition['variants'] if v['id']==trial['variant_id']))
            variant.pop('patch_file',None)  # Already present in the saved parent workspace.
            case=copy.deepcopy(next(c for c in definition['cases'] if c['id']==trial['case_id']))
            case['fixtures']=[]  # Do not reset files modified by the parent.
            prompt_name='continuation/'+uuid.uuid4().hex+'.txt';case['prompt_file']=prompt_name
            definition.update(name='continue-'+attempt['id'][-16:],variants=[variant],cases=[case],repetitions=1)
            definition['execution']['max_parallel']=1
            for path,expected in frozen['inputs'].items():
                data,_=regular_bytes(base/'inputs',path,maximum=frozen['capture_limits']['source_bytes'])
                if hashlib.sha256(data).hexdigest()!=expected:raise ExperimentError('CORRUPT_STATE','Parent input changed: '+path)
                write_file(inputs/path,data,0o400)
            write_file(inputs/prompt_name,prompt,0o400)
            frozen['inputs'][prompt_name]=hashlib.sha256(prompt).hexdigest()
            combined={**entries,**{'@input/'+path:{'type':'file','mode':0o400,'size':len(regular_bytes(inputs,path)[0]),'sha256':sha}
                                      for path,sha in frozen['inputs'].items()}}
            capture={'schema_version':1,'entries':combined,'content_digest':digest(combined),
                'source_bytes':sum(r.get('size',0) for r in combined.values()),'excluded':[],
                'host_exclusions':study['_baseline_manifest'].get('host_exclusions',study['_baseline_manifest'].get('excluded',[])),
                'mandatory_exclusions':manifest.get('mandatory_exclusions',[])}
            if capture['source_bytes']>frozen['capture_limits']['source_bytes']:
                raise ExperimentError('CAPTURE_LIMIT','Child workspace and captured inputs exceed the frozen capture limit.')
            image={**frozen['image'],'id':selected,'repo_digests':[]}
            baseline={'schema_version':1,'project':str(engine.project),'manifest':capture,'image':image,'policy':frozen['effective_policy']}
            baseline_id='baseline-'+digest(baseline)
            parent={'study_id':study['id'],'trial_id':trial['id'],'attempt_id':attempt['id'],
                    'result_digest':manifest['content_digest'],'image':selected,'selection_revision':study['selection']['revision'],
                    'new_conversation':True,'prompt_source':relative}
            frozen.update(milestone='M5',parent=parent,baseline_id=baseline_id,baseline_content_digest=capture['content_digest'],
                image=image,definition_digest=digest(definition),requested_definition=copy.deepcopy(definition),
                input_checks={'variant_changed_paths':{variant['id']:[]}})
            frozen['execution'].update(max_parallel=1,requested_max_parallel=1)
            frozen['execution']['trial_policy']=copy.deepcopy(frozen['effective_policy'])
            workspace.private_write(staging/'manifest.json',capture);workspace.private_write(staging/'baseline.json',baseline)
            sync_tree(staging)
            target=private_directory(engine.root/'baselines')/baseline_id
            staging.rename(target);seal_tree(target);sync_directory(target.parent)
            destination=private_directory(engine.root/'studies')/child_id;destination.mkdir(mode=0o700)
            workspace.private_write(destination/'definition.json',frozen);sync_tree(destination);seal_tree(destination);sync_directory(destination.parent)
            engine.store.create(study_id=child_id,baseline={'id':baseline_id,'content_digest':capture['content_digest'],'path':str(target)},
                frozen=frozen,trials=expand(definition),request_id=request_id,fingerprint=fingerprint,project=engine.project)
            saved_refs=references(engine,study)
            if saved_refs:workspace.private_write(engine.root/'references'/(child_id+'.json'),saved_refs)
            return {**engine.inspect(child_id),'idempotent_replay':False,'new_conversation':True}
