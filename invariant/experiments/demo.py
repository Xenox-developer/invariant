"""A real three-configuration coding study over the existing experiment interface."""
import contextlib
import io
import json
from pathlib import Path
import re
import shlex
import sys

from .. import environment, workspace
from ..codex_rpc import inert
from .engine import ExperimentEngine
from .errors import ExperimentError
from .definitions import validate

EFFORTS = ('low', 'medium', 'high')
REQUEST = 'guided-codex-demo-v1'
FILES = {'solution.py': 'demo-solution.py', 'task.md': 'demo-task.md',
         'followup.md': 'demo-followup.md', 'suite.json': 'demo-suite.json', 'verify.py': 'demo-verify.py'}


def create(directory, *, model=None, efforts=None):
    path = Path(directory).expanduser().absolute()
    if path.is_symlink():
        raise ExperimentError('INVALID_PATH', 'The demo directory cannot be a symlink.')
    path = path.resolve()
    marker = path/'.invariant/demo.json'
    if path.exists():
        if not path.is_dir() or marker.is_symlink() or marker.parent.is_symlink():
            raise ExperimentError('ALREADY_EXISTS', 'Demo destination is occupied or redirected; nothing was replaced.')
        saved = environment.read_record(marker)
        if not saved or saved.get('kind') != 'codex-demo-v1' or saved.get('project') != str(path):
            raise ExperimentError('ALREADY_EXISTS', 'Demo destination already exists; its files were kept.',
                                  'Choose a new directory. Existing marked demos can be reopened with the same command.')
        workspace.load_project(path)
        if (model is not None and model != saved['model']) or (efforts is not None and list(efforts) != saved['efforts']):
            raise ExperimentError('DEMO_CONFLICT', 'This demo already has different settings.',
                                  'Use a new directory for new settings, or edit compare.json and submit a new study explicitly.')
        return {'project': str(path), 'definition': 'compare.json', 'reused': True, 'model': saved['model'], 'efforts': saved['efforts']}
    efforts = list(EFFORTS if efforts is None else efforts)
    if len(efforts) != 3 or len(set(efforts)) != 3 or any(not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', e) for e in efforts):
        raise ExperimentError('INVALID_ARGUMENTS', 'Choose exactly three distinct reasoning efforts.')
    definition = {'schema_version': 1, 'name': 'calendar-codex-demo', 'runner': {'type': 'codex'},
        'variants': [{'id': effort, 'effort': effort, **({'model': model} if model else {})} for effort in efforts],
        'cases': [{'id': 'merge-windows', 'prompt_file': 'task.md'}], 'repetitions': 1,
        'workspace': {'include': ['solution.py'], 'exclude': ['__pycache__', '**/__pycache__/**']},
        'execution': {'max_parallel': 3, 'trial_cpus': .5, 'trial_memory_mib': 512, 'trial_timeout_seconds': 300},
        'evaluator': {'argv': ['python3', '-I', '-B', 'verify.py'], 'source_paths': ['verify.py', 'suite.json'], 'timeout_seconds': 60}}
    validate(json.loads(json.dumps(definition)))
    # Never overwrite even an empty existing directory or unrelated project.
    path.mkdir(parents=True, mode=0o700)
    for target, asset in FILES.items():
        with (path/target).open('xb') as output:
            output.write((workspace.ASSETS/asset).read_bytes())
    with (path/'compare.json').open('x') as output:
        output.write(json.dumps(definition, indent=2)+'\n')
    with contextlib.redirect_stdout(io.StringIO()):
        workspace.init(path)
    workspace.private_write(marker, {'kind': 'codex-demo-v1', 'project': str(path), 'model': model, 'efforts': efforts})
    (path/'README.md').write_text(guide())
    return {'project': str(path), 'definition': 'compare.json', 'reused': False, 'model': model, 'efforts': efforts}


def execute(info):
    from .cli import wait_study
    engine = ExperimentEngine(info['project'])
    study = engine.submit(info['definition'], request_id=REQUEST)
    print('Demo study: '+study['id']+' — up to three Codex turns; results persist if this viewer closes.', file=sys.stderr, flush=True)
    print('Project: '+inert(info['project']), file=sys.stderr, flush=True)
    print('Inspect: invariant experiment status '+study['id'], file=sys.stderr, flush=True)
    engine.launch(study['id'])
    wait_study(engine, study['id'])
    return {**info, 'study_id': study['id'], 'results': engine.results(study['id']),
            'concurrency': study['frozen']['execution'], 'applied': False}


def present(info):
    print('Codex experiment demo:', inert(info['project']))
    if 'results' not in info:
        print('Created one coding task, three configurations and 23 shared checks.')
        print('Run: invariant experiment demo '+shlex.quote(info['project'])+' --run')
        print('Requires Docker, a prepared image and host Codex ChatGPT sign-in.')
        print('Setup: invariant setup; invariant prepare --agent codex')
        return
    result = info['results']
    print('Study:', result['study_id'])
    if 'concurrency' in info:
        print('Admitted parallel trials:', info['concurrency']['max_parallel'], 'of 3 requested')
    print('Choice | effort | tests passed | agent seconds | tokens | state')
    for index, row in enumerate(result['trials'], 1):
        metrics = row['metrics']
        score = str(metrics.get('tests_passed', '?'))+'/'+str(metrics.get('tests_total', '?'))
        seconds = row['runner'].get('duration_seconds')
        usage = row.get('token_usage') or {}
        tokens = usage.get('total', {}).get('totalTokens', 'unavailable')
        print(index, '|', inert(row['variant_id']), '|', score, '|',
              round(seconds, 1) if seconds is not None else 'unavailable', '|', tokens, '|', row['state'])
        print('  Attempt:', row['attempt_id'], 'quality:', row['passed'], 'reason:', row['reason'])
        if row['passed'] is None:
            print('  Quality unavailable; inspect this attempt rather than treating it as a pass.')
    print('One task and one attempt per configuration; all may pass. Choose the result you prefer.')
    print('No result is selected or applied automatically.')
    print('From: cd '+shlex.quote(info['project']))
    print('Compare: invariant experiment results '+result['study_id'])
    print('Select: invariant experiment select ATTEMPT_ID --if-revision '+str(result['selection']['revision']))
    print('Continue: invariant experiment continue '+result['study_id']+' --prompt-file followup.md --wait')
    print('Review/apply: invariant experiment diff '+result['study_id'])
    print('  invariant experiment apply '+result['study_id']+' --dry-run')
    print('  invariant experiment apply --plan PLAN_ID')


def interact(info):
    """Human-only adapter; every mutation uses the ordinary durable engine."""
    from .cli import render, wait_study
    engine = ExperimentEngine(info['project'])
    rows = info['results']['trials']
    while True:
        answer = input('Choose result 1–3, or Enter to leave it for later: ').strip()
        if not answer:
            return
        if answer.isdigit() and 1 <= int(answer) <= len(rows):
            attempt = rows[int(answer)-1]['attempt_id']; break
        print('Enter a displayed choice number.')
    study = engine.inspect(info['study_id'])
    engine.select(attempt, revision=study['selection']['revision'])
    identifier = study['id']
    while True:
        render('diff', engine.diff(identifier))
        action = input('Continue selected result [c], review/apply [a], or finish [Enter]: ').strip().lower()
        if not action:
            return
        if action == 'c':
            child = engine.continue_result(identifier, 'followup.md', request_id='demo-continue-'+attempt)
            print('Child study:', child['id'], '— a new conversation using the selected files and tools.', flush=True)
            engine.launch(child['id']); wait_study(engine, child['id'])
            present({'project': info['project'], 'results': engine.results(child['id'])})
            child = engine.inspect(child['id'])
            attempt = child['trials'][0]['attempts'][0]['id']
            engine.select(attempt, revision=child['selection']['revision'])
            identifier = child['id']
        elif action == 'a':
            if review_apply(engine, identifier):
                return
        else:
            print('Choose c, a, or Enter.')


def review_apply(engine, identifier, *, confirm=None):
    """Review concrete ancestor/child plans; never hide a merge or conflict."""
    from .cli import render
    from .tree import entry
    confirm = confirm or input
    lineage = []
    report = engine.diff(identifier)
    while True:
        if report['attempt_id'] in lineage or len(lineage) >= 64:
            raise ExperimentError('CORRUPT_STATE', 'Invalid or excessive continuation lineage.')
        lineage.append(report['attempt_id'])
        if not report['parent']:
            break
        report = engine.diff(report['parent']['attempt_id'])
    for attempt in reversed(lineage):
        report = engine.diff(attempt)
        if all(entry(engine.project, row['path']) == row['after'] for row in report['changes']):
            print('Project files already match result:', attempt)
            continue
        print('Review result:', attempt, '(ancestors are applied before their continuation)')
        plan = engine.plan_apply(attempt)
        render('apply', plan)
        if plan['conflicts']:
            print('Conflicts were kept. Resolve them and create a fresh plan; this step was not applied.')
            return False
        if confirm('Apply exactly this reviewed plan to the demo project? [y/N]: ').strip().lower() != 'y':
            return False
        render('apply', engine.apply(plan['plan_id']))
    return True


def guide():
    return '''# Three Codex configurations, one coding task

Implement a calendar interval-merging function. Each configuration starts from the same
stub in a private workspace and Docker layer. Only reasoning effort differs by default.
The shared 23-case evaluator is captured separately from candidate code; it checks valid
inputs, errors, output types and input immutability. Details are in task.md and suite.json.
No packages or command networking are needed. One small task is illustrative, not a
statistical model benchmark. All three configurations may pass.

Run `invariant experiment demo . --run` to compare them. On a terminal, choose a result
and continue it or review/apply its changes. With `--json` or piped input, there are no
prompts: use the ordinary commands below. Running the same demo command again finds
the same study and never replays completed model turns. Ctrl+C closes only the viewer.

```sh
invariant experiment status --request-id guided-codex-demo-v1
invariant experiment results STUDY_ID --json
invariant experiment logs STUDY_ID --follow
invariant experiment select ATTEMPT_ID --if-revision REVISION
invariant experiment diff STUDY_ID
invariant experiment continue STUDY_ID --prompt-file followup.md --wait
invariant experiment apply STUDY_ID --dry-run
invariant experiment apply --plan PLAN_ID
```

Selection does not change project files. Continuation creates a new study and conversation
from the exact selected files/environment, evaluated by the same captured checks. Select
its result before using its study ID for apply. Apply changes files in this demo project;
it never installs the candidate Docker image on the host. Edit followup.md before continuing
if you want a different next task. Use explicit retries/new request IDs for new attempts.
A child's diff is relative to its parent: apply the parent plan before the child plan.
The guided terminal flow reviews these steps in order, with a separate concrete plan
for each, and skips ancestors whose changed files already match the saved result.

Models and efforts must be supported by your account; nothing silently substitutes them.
If preparation fails, run `invariant setup` and `invariant prepare --agent codex`, then retry.
If a controller crashes, inspect status and use `invariant experiment recover STUDY_ID`;
interrupted model turns are not replayed. `stop STUDY_ID` stops this study's work.
Use `delete STUDY_ID` to preview cleanup. Clear selections and delete children before parents.
'''
