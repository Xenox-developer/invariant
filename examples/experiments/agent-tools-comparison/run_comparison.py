"""Run/resume one frozen two-tool comparison with INVARIANT; never apply a result."""
import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

PROJECT=Path(__file__).resolve().parent
sys.path.insert(0,str(PROJECT.parents[2]))
from invariant.experiments.engine import ExperimentEngine
from invariant.experiments.preflight import check_frozen
from invariant.experiments.cli import wait_study
from invariant.experiments.exporting import export
from invariant.usage import UsageLedger

OUTPUT=PROJECT/'results/storage-v2'
OUTPUT.mkdir(parents=True,exist_ok=True)
engine=ExperimentEngine(PROJECT)
study=engine.submit('compare.json',request_id='github-agent-tools-comparison-storage-v2')
print(json.dumps({'stage':'captured','study_id':study['id'],
    'variants':study['frozen']['definition']['variants'],'image':study['frozen']['image']['id']}),flush=True)
(OUTPUT/'study.json').write_text(json.dumps({'study_id':study['id'],'project':str(PROJECT)},indent=2)+'\n')
preflight=check_frozen(engine,study['id'],request_id='github-agent-tools-baseline-storage-v2')
(OUTPUT/'baseline.json').write_text(json.dumps(preflight,indent=2)+'\n')
print(json.dumps({'stage':'baseline','ready':preflight['ready'],'study_id':preflight['study_id']}),flush=True)
if not preflight['ready']:raise SystemExit('Baseline evaluation unavailable; no agent runs dispatched.')
engine.execute(study['id'])
print(json.dumps({'stage':'running','study_id':study['id'],'parallel_trials':2}),flush=True)

results=engine.results(study['id'])
rows=[]
for row in results['trials']:
    runner=row['runner'];evaluation=row['evaluation']
    total=(runner.get('token_usage') or {}).get('total',{})
    rows.append({'tool':row['variant_id'],'attempt_id':row['attempt_id'],'state':row['state'],
        'passed':row['passed'],'reason':row['reason'],'tests':evaluation.get('metrics',{}),
        'agent_seconds':runner.get('duration_seconds'),'wall_seconds':row['duration_seconds'],
        'model':runner.get('native',{}).get('model'),'effort':runner.get('native',{}).get('effort'),
        'provider_usage':total,'inference_usage':runner.get('inference_usage'),
        'workspace_ref':(row.get('capture') or {}).get('workspace_ref'),
        'evaluation_artifacts':evaluation.get('artifacts'),
        'runner_log':runner.get('log'),'validation_reused':evaluation.get('reused_validation',False)})
report={'study_id':study['id'],'state':results['state'],
    'finished_at':datetime.now(ZoneInfo('Europe/Moscow')).isoformat(),
    'task':'Scope-aware JavaScript migration from legacy-log.warn to modern-log.warning',
    'task_sha256':hashlib.sha256((PROJECT/'TASK.md').read_bytes()).hexdigest(),
    'tests_sha256':hashlib.sha256((PROJECT/'tests/acceptance.cjs').read_bytes()).hexdigest(),
    'image':study['frozen']['image']['id'],'baseline_content_digest':study['frozen']['baseline_content_digest'],
    'resources':study['frozen']['effective_policy'],'runs':rows,
    'application':False,'caller_usage':'unknown; shared preparation has no model calls',
    'limitations':['One attempt per tool; measurements are descriptive, not a statistical ranking.',
                   'Shared dependency preparation is excluded from per-agent timings.',
                   'Tests are frozen and identical; agent and executor have no host home or Docker socket.']}
(OUTPUT/'results.json').write_text(json.dumps(report,indent=2)+'\n')
for format,suffix in (('markdown','md'),('json','json'),('csv','csv')):
    destination=OUTPUT/('comparison.'+suffix)
    if not destination.exists():export(engine,study['id'],format=format,output=str(destination))
lines=['# Сравнение инструментов агента','',
    'Задача: безопасная миграция JavaScript API логирования с учётом областей видимости.',
    '',f"INVARIANT study: `{study['id']}`",'',
    '| Инструмент | Модель / effort | Проверки | Время агента, с | Токены |',
    '|---|---|---:|---:|---:|']
for row in rows:
    metrics=row['tests'];passed=metrics.get('tests_passed','?');count=metrics.get('tests_total','?')
    seconds='?' if row['agent_seconds'] is None else f"{row['agent_seconds']:.1f}"
    tokens=row['provider_usage'].get('totalTokens','unknown')
    lines.append(f"| {row['tool']} | {row['model']} / {row['effort']} | {passed}/{count} | {seconds} | {tokens} |")
lines+=['','Один запуск на инструмент. Подготовка общего окружения не включена во время агента.',
        'Результаты сохранены отдельно; автоматического выбора или применения изменений нет.',
        '', 'Инструменты: [ast-grep](https://github.com/ast-grep/ast-grep), [jscodeshift](https://github.com/meta/jscodeshift).',
        '', 'Подробные показатели и пути сохранённых решений: [results.json](results.json).']
(OUTPUT/'REPORT.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'stage':'completed','study_id':study['id'],'runs':rows,'report':str(OUTPUT/'REPORT.md')},indent=2),flush=True)
raise SystemExit(0 if all(row['passed'] is True for row in rows) else 1)
