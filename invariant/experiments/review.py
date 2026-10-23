"""Readable comparison and human controls over ordinary durable results."""
from pathlib import Path
from ..codex_rpc import inert


def present(result):
    print('Study:',result['study_id'],result['state'])
    print('Choice | configuration | shared tests | agent seconds | tokens | state')
    configurations = {v['variant_id']:v['configuration'] for v in result.get('variants',[])}
    for index,row in enumerate(result['trials'],1):
        seconds = row['runner'].get('duration_seconds')
        tokens = (row.get('token_usage') or {}).get('total',{}).get('totalTokens','unavailable')
        verdict = 'pass' if row['passed'] is True else 'fail' if row['passed'] is False else 'unknown'
        print(index,'|',inert(row['variant_id']),'|',verdict,'|',round(seconds,1) if seconds is not None else 'unavailable','|',tokens,'|',row['state'])
        print('  Attempt:',row['attempt_id'],'metrics:',inert(str(row['metrics'])))
        configuration = configurations.get(row['variant_id'],{})
        if configuration.get('model'):
            print('  Model:',inert(configuration['model']),'effort:',inert(configuration.get('effort') or 'default'))
        if (row.get('capture') or {}).get('workspace') and (row.get('evaluation') or {}).get('artifacts',{}).get('files'):
            print('  Shared test artifacts:',inert(str(Path(row['capture']['workspace']).parents[1]/'evaluation/artifacts')))
    print('Selected:',result['selection']['attempt_id'] or '(none)','revision:',result['selection']['revision'])
    print('Compare runs on this task; no winner is chosen automatically.')
    print('Reopen: invariant experiment review '+result['study_id'])
    print('Logs: invariant experiment logs '+result['study_id']+' --follow')


def interact(engine, identifier):
    from .cli import render, wait_study
    from .guided import inline_prompt
    from .demo import review_apply
    rows = engine.results(identifier)['trials']
    while True:
        answer = input('Choose a displayed result number, or Enter to finish: ').strip()
        if not answer: return
        if answer.isdigit() and 1<=int(answer)<=len(rows):
            attempt = rows[int(answer)-1]['attempt_id']; break
        print('Enter a displayed choice number.')
    study = engine.inspect(identifier)
    engine.select(attempt,revision=study['selection']['revision'])
    while True:
        render('diff',engine.diff(identifier))
        action = input('Continue with a new task [c], review/apply [a], or finish [Enter]: ').strip().lower()
        if not action: return
        if action=='a':
            if review_apply(engine,identifier): return
        elif action=='c':
            task = input('What should the selected configuration do next? ').strip()
            if not task:
                print('No continuation started.'); continue
            prompt = inline_prompt(engine,task)
            child = engine.continue_result(identifier,prompt)
            print('Child study:',child['id'],'— a new conversation from the selected result.',flush=True)
            identifier = child['id']
            engine.launch(identifier); wait_study(engine,identifier)
            result = engine.results(identifier); present(result)
            attempt = result['trials'][0]['attempt_id']
            engine.select(attempt,revision=result['selection']['revision'])
        else:
            print('Choose c, a, or Enter.')
