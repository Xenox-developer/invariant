"""File-free inputs for the normal frozen Codex experiment workflow."""
import copy
import json
import os
from pathlib import Path
import shlex
import stat
import sys

from .. import workspace
from .definitions import MAX_DEFINITION, NAME, digest, source_path, text, validate
from .errors import ExperimentError
from .snapshots import protected, regular_bytes, safe_path
from .starters import publish


def collect(args, *, interactive=False):
    """Prompt only for a human wizard; machine callers get actionable missing fields."""
    def ask(value, label, default=None):
        if value is not None:
            return value
        if not interactive:
            return default
        answer = input(label + (' ['+str(default)+']' if default is not None else '')+': ').strip()
        return answer or default

    def words(answer):
        try: return shlex.split(answer)
        except ValueError:
            raise ExperimentError('INVALID_ARGUMENTS','A quoted setup answer is incomplete.',
                                  'Close matching quotes around paths/values containing spaces, then repeat setup.') from None

    task = ask(args.task, 'What coding task should every configuration perform?')
    command = ask(args.test_command, 'Shared test command (runs inside Docker)')
    paths = args.test_path
    if paths is None and interactive:
        paths = words(input('Test files/folders to keep fixed (space-separated; quote spaces): '))
    if not task or not command or not paths:
        raise ExperimentError('INVALID_ARGUMENTS', 'Guided setup requires --task, --test-command and at least one --test-path.',
            'Run invariant experiment init in a terminal, or supply these flags with --json. No files were created.')
    model = args.model
    efforts = args.efforts
    if not args.variant:
        model = ask(model, 'Codex model (Enter for account default)', '') or None
        if efforts is None:
            efforts = words(ask(None, 'Reasoning efforts to compare', 'low medium high'))
    name = ask(args.name, 'Experiment name', 'comparison')
    options = {'task':task, 'test_command':command, 'test_paths':paths, 'model':model,
               'efforts':efforts, 'variants':args.variant, 'name':name,
               'max_parallel':args.max_parallel, 'test_timeout':args.test_timeout}
    return options


def test_files(project, roots):
    """Expand explicit roots without following links or capturing credentials."""
    normalized = []
    files = []
    for root in roots:
        source_path(root, 'test path')
        if protected(root) or any(root==old or root.startswith(old+'/') or old.startswith(root+'/') for old in normalized):
            raise ExperimentError('INVALID_PATH', 'Test roots must be distinct, non-overlapping project paths outside protected state.')
        try:
            fd, name = safe_path(project, root)
            try: info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            finally: os.close(fd)
        except FileNotFoundError:
            raise ExperimentError('INPUT_MISSING','Shared test path does not exist: '+root,
                                  'Choose an existing test file or folder with --test-path.') from None
        if stat.S_ISLNK(info.st_mode):
            raise ExperimentError('INVALID_PATH', 'Shared test roots cannot be symlinks: '+root)
        normalized.append(root)
        paths = [project/root]
        if stat.S_ISDIR(info.st_mode):
            paths = []
            for base, directories, names in os.walk(project/root, followlinks=False,
                                                    onerror=lambda error: (_ for _ in ()).throw(error)):
                for item in directories+names:
                    path = Path(base)/item
                    if path.is_symlink():
                        raise ExperimentError('INVALID_PATH', 'Shared tests cannot traverse symlinks: '+str(path.relative_to(project)))
                paths.extend(Path(base)/name for name in names)
        for path in sorted(paths):
            relative = path.relative_to(project).as_posix()
            if protected(relative):
                raise ExperimentError('INVALID_PATH', 'Protected files cannot be included as shared tests: '+relative)
            _, info = regular_bytes(project, relative)
            if info.st_nlink != 1:
                raise ExperimentError('INVALID_PATH', 'Shared tests cannot be hard-linked files: '+relative)
            files.append({'path':relative, 'mode':info.st_mode & 0o777})
        if len(files)>250:
            raise ExperimentError('CAPTURE_LIMIT', 'Guided setup supports up to 250 shared test files.',
                                  'Choose narrower test paths, or use an advanced experiment definition.')
    if not files:
        raise ExperimentError('INVALID_ARGUMENTS', 'Shared test paths must contain at least one regular file.')
    return normalized, files


def create(engine, path=None, *, task, test_command, test_paths, name='comparison', model=None,
           efforts=None, variants=None, max_parallel=None, test_timeout=120, request_id=None):
    text(task, 'task', maximum=MAX_DEFINITION//4)
    text(test_command, 'test command', maximum=8192)
    text(name, 'experiment name', maximum=64, pattern=NAME)
    if not task.strip() or not test_command.strip():
        raise ExperimentError('INVALID_ARGUMENTS', 'Task and test command cannot be blank.')
    if variants and (model is not None or efforts is not None):
        raise ExperimentError('INVALID_ARGUMENTS', 'Use --variant MODEL EFFORT, or --model with --efforts, not both.')
    choices = ([{'id':'config-'+str(i), 'model':m, 'effort':e} for i,(m,e) in enumerate(variants,1)]
               if variants else [{'id':e, 'effort':e, **({'model':model} if model else {})} for e in (efforts or ['low','medium','high'])])
    if len({(c.get('model'),c.get('effort')) for c in choices})!=len(choices):
        raise ExperimentError('INVALID_ARGUMENTS', 'Choose distinct model/effort configurations.')
    relative = engine._path(path or 'experiments/'+name+'/compare.json')
    target = engine.project/relative
    prompt = target.with_suffix('.task.txt')
    settings = target.with_suffix('.test.json')
    verifier = target.with_suffix('.verify.py')
    guide = target.with_suffix('.guide.md')
    names = [p.relative_to(engine.project).as_posix() for p in (target,prompt,settings,verifier,guide)]
    if len(set(names))!=5:
        raise ExperimentError('INVALID_PATH', 'Definition path collides with a generated support file.')
    roots, files = test_files(engine.project, test_paths)
    if any(n==r or n.startswith(r+'/') for n in names for r in roots):
        raise ExperimentError('INVALID_PATH', 'Generated experiment files cannot overlap shared test paths.')
    if type(test_timeout) is not int or not 1<=test_timeout<=3590:
        raise ExperimentError('INVALID_ARGUMENTS', 'Test timeout must be between 1 and 3590 seconds.')
    definition = {'schema_version':1, 'name':name, 'runner':{'type':'codex'},
        'variants':choices, 'cases':[{'id':'task','prompt_file':names[1]}], 'repetitions':1,
        'workspace':{'include':['**/*'], 'exclude':names},
        'execution':{'max_parallel':len(choices) if max_parallel is None else max_parallel},
        'evaluator':{'argv':['python3','-I','-B',names[3],names[2]],
                     'source_paths':[names[3],names[2]]+[r['path'] for r in files], 'timeout_seconds':test_timeout+5}}
    validate(copy.deepcopy(definition))
    spec = {'command':test_command, 'roots':roots, 'files':files, 'timeout_seconds':test_timeout}
    prompt_text = task.strip()+'\n\nShared evaluation command: '+test_command+'\nKeep these shared test paths unchanged: '+', '.join(roots)+'.\n'
    content = {target:json.dumps(definition,indent=2)+'\n', prompt:prompt_text,
               settings:json.dumps(spec,indent=2)+'\n', verifier:(workspace.ASSETS/'experiment-check.py').read_text(),
               guide:GUIDE}
    result = {'guided':True, 'definition':str(target), 'guide':str(guide), 'prompt':str(prompt),
              'project':str(engine.project), 'configurations':choices, 'test_command':test_command,
              'test_paths':roots, 'test_file_count':len(files), 'requested_parallel':definition['execution']['max_parallel'],
              'dispatch_available':True, 'applied':False}
    try:
        return publish(engine, content, result, request_id=request_id, operation='guided_init')
    except ExperimentError as error:
        if error.code=='ALREADY_EXISTS':
            error.recovery='Inspect invariant experiment list/review STUDY_ID, or choose --name NEW_NAME for another setup. Existing files were kept.'
        raise


def execute(engine, info, *, request_id=None):
    from .cli import wait_study
    key = 'guided-run-'+digest({'definition':info['definition'], 'request_id':request_id})
    study = engine.submit(info['definition'], request_id=key)
    print('Study: '+study['id']+' — results persist after closing this viewer.', file=sys.stderr,flush=True)
    engine.launch(study['id']); wait_study(engine,study['id'])
    return {**info, 'study_id':study['id'], 'results':engine.results(study['id'])}


def inline_prompt(engine, task):
    text(task, 'continuation task', maximum=MAX_DEFINITION//4)
    if not task.strip():
        raise ExperimentError('INVALID_ARGUMENTS','Continuation task cannot be blank.')
    relative = 'experiments/followups/'+digest(task)+'.txt'
    target = engine.project/relative
    # Content-addressed, keyed publication survives lost acknowledgements without overwrites.
    publish(engine,{target:task+'\n'},{'prompt':relative},request_id='inline-prompt-'+digest(task),operation='inline_prompt')
    if regular_bytes(engine.project,relative)[0]!=(task+'\n').encode():
        raise ExperimentError('PROMPT_CONFLICT','The saved inline task was edited; your text was not substituted.',
                              'Preserve the edit and supply a different task or an explicit --prompt-file.')
    return relative


GUIDE = '''# Guided Codex experiment

INVARIANT generated these files from terminal answers or flags. You do not need to edit
them. All configurations start from the same project snapshot and shared image; each has
its own workspace, conversation and writable layer. Frozen studies keep captured inputs.
Shared tests are restored from the frozen inputs in a disposable evaluator workspace,
then the exact test command runs there. Candidate test edits do not replace that suite.
Pass means command exit 0; test counts are not inferred. Logs and exit status are artifacts.
Timeout, output limit, or missing command produce unknown quality, never a pass.

Setup itself is offline. Run/reopen with invariant experiment run DEFINITION --wait
--request-id YOUR_KEY. Inspect results/logs, then use invariant experiment review STUDY_ID
for terminal selection, a new continuation task, and reviewed apply. An assistant uses
results/select/continue --task/diff/apply with --json and the same IDs/revisions.
Continuation starts a new conversation from the exact saved files and environment.
The shared evaluator stays frozen. Apply writes reviewed project files only.
The guided review applies ancestor plans before child plans and preserves conflicts.

Tests run in Linux Docker, never on the host. Supply Linux-compatible commands and test
paths; quote each --test-path containing spaces. Include test configuration/manifests in
those paths if they must stay fixed too. The selected list of test files is generated at
setup; create a new setup to include new test files. Existing tools/dependencies come from
the project's prepared image and captured files. INVARIANT does not guess a language,
install packages or enable networking. Prepare dependencies in invariant run first if
needed. Use invariant config/guardrails to inspect actual project limits and networking.
Run invariant setup and invariant prepare --agent codex if prerequisites are missing.
Reusing a setup request key returns its existing files; it never starts an additional turn
or replaces edited files. Change settings using a new definition path and request key.
'''
