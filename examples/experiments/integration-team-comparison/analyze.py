"""Produce a measured report, preserving unknown billing/outer-chat information."""
from datetime import datetime
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

HERE=Path(__file__).resolve().parent
FIELDS=('input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens','total_tokens')
ALIASES=dict(zip(FIELDS,('inputTokens','cachedInputTokens','outputTokens','reasoningOutputTokens','totalTokens')))


def tokens(raw):return {name:raw.get(name,raw.get(alias)) for name,alias in ALIASES.items()}


def credit_equivalent(usage):
    i,c,o=(usage[k] for k in ('input_tokens','cached_input_tokens','output_tokens'))
    assert all(type(n) is int and n>=0 for n in (i,c,o)) and c<=i
    return ((i-c)*50+c*2.5+o*250)/1_000_000


def epoch(value):
    return float(value) if isinstance(value,(int,float)) else datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def normalize(arm):
    records=[]
    for agent in arm['agents']:
        if arm['backend']=='invariant':
            usage=tokens(agent['provider_usage']['total'])
            ledger=agent['inference_usage']
            checks=agent['tests'];count=checks['tests_total'];passed=checks['tests_passed']
            assert agent['model']=='gpt-6.1-sol' and agent['effort']=='medium'
            responses=ledger['response_count'];reconciled=ledger['reconciled'];model_observed=True
        else:
            acceptance=agent['acceptance'];count=acceptance['tests'];passed=count-acceptance['failures']
            sessions=acceptance['sessions'];assert len(sessions)==1,sessions
            session=sessions[0];observations=session['observations'];assert observations
            usage=tokens(observations[-1]['total'])
            summed={k:sum(tokens(row['last'])[k] for row in observations) for k in FIELDS}
            reconciled=summed==usage
            responses=len(observations)
            model_observed=session['model']=='gpt-6.1-sol' and session['effort']=='medium'
            assert model_observed,session
            cli=agent['cli_usage']
            for k in ('input_tokens','cached_input_tokens','output_tokens'):assert cli[k]==usage[k],(k,cli,usage)
        assert reconciled and count==passed
        records.append({'role':agent['role'],'seconds':agent['duration_seconds'],'started_at':agent['started_at'],
            'finished_at':agent['finished_at'],'tests':count,'tests_passed':passed,'usage':usage,
            'provider_responses':responses,'usage_reconciled':reconciled,'observed_model_and_effort_match':model_observed,
            'standard_credit_equivalent':credit_equivalent(usage)})
    totals={k:sum(r['usage'][k] for r in records) for k in FIELDS}
    first=[r for r in records if r['role']!='checker'];third=next(r for r in records if r['role']=='checker')
    overlap=max(0,min(r['finished_at'] for r in first)-max(r['started_at'] for r in first))
    assert len(records)==3 and len(first)==2 and overlap>0
    assert third['started_at']>=max(r['finished_at'] for r in first)
    first_start=min(r['started_at'] for r in first)
    first_finish=max(r['finished_at'] for r in first)
    windows={'before_first_agent':first_start-arm['started_at'],
        'parallel_agents':first_finish-first_start,'handoff_and_checker_preparation':third['started_at']-first_finish,
        'checker_agent':third['finished_at']-third['started_at'],'after_checker':arm['finished_at']-third['finished_at']}
    assert all(v>=0 for v in windows.values())
    assert math.isclose(sum(windows.values()),arm['wall_seconds'],abs_tol=.001)
    return {'wall_seconds':arm['wall_seconds'],'setup_seconds':arm['setup_seconds'],
        'parallel_stage_seconds':arm['parallel_stage_seconds'],'checker_stage_seconds':arm['checker_stage_seconds'],
        'agents':records,'usage':totals,'provider_responses':sum(r['provider_responses'] for r in records),
        'standard_credit_equivalent':credit_equivalent(totals),'actual_billed_usd':None,
        'credit_equivalent_if_no_input_cache':(totals['input_tokens']*50+totals['output_tokens']*250)/1_000_000,
        'cached_input_fraction':totals['cached_input_tokens']/totals['input_tokens'],
        'timeline_seconds':windows,'outside_agent_windows_seconds':sum(windows[k] for k in ('before_first_agent','handoff_and_checker_preparation','after_checker')),
        'tests':sum(r['tests'] for r in records),'tests_passed':sum(r['tests_passed'] for r in records),
        'parallel_overlap_seconds':overlap,'checker_after_both_methods':True}


def response_overlap(run,arm):
    windows=[]
    if arm['backend']=='invariant':
        execution=json.loads((run/'with-invariant/methods/execution.json').read_text())
        logs=Path(execution['exported_logs'])
        for row in execution['trials']:
            path=logs/'experiments/attempts'/row['attempt_id']/'runner.usage.jsonl'
            observations=[json.loads(line) for line in path.read_text().splitlines()]
            dates=[epoch(row['timestamp']) for row in observations]
            windows.append((min(dates),max(dates)))
    else:
        for agent in arm['agents']:
            if agent['role']=='checker':continue
            observations=agent['acceptance']['sessions'][0]['observations']
            dates=[epoch(row['timestamp']) for row in observations]
            windows.append((min(dates),max(dates)))
    overlap=max(0,min(w[1] for w in windows)-max(w[0] for w in windows))
    assert overlap>0,windows
    return {'response_window_overlap_seconds':overlap,'response_windows':windows}


def main():
    namespace=sys.argv[1];run=HERE/'results'/namespace
    arms=[json.loads((run/folder/'summary.json').read_text()) for folder in ('with-invariant','without-invariant')]
    assert arms[0]['starting_image']==arms[1]['starting_image']
    results={name:normalize(arm) for name,arm in zip(('with_invariant','without_invariant'),arms)}
    for name,arm in zip(results,arms):results[name]['parallel_model_evidence']=response_overlap(run,arm)
    csvs=[]
    for folder in ('with-invariant','without-invariant'):
        # Bundle the actual handoff so exported reports can be reproduced directly.
        for method in ('trapezoid','simpson'):
            target=run/folder/'agents/checker/inputs'/method;target.mkdir(parents=True,exist_ok=True)
            for name in ('solution.py','result.json'):shutil.copyfile(run/folder/'agents'/method/name,target/name)
        with (run/folder/'agents/checker/comparison.csv').open(newline='') as stream:rows=list(csv.DictReader(stream))
        csvs.append({(r['method'],int(r['power']),int(r['n'])):r for r in rows})
    assert csvs[0].keys()==csvs[1].keys() and len(csvs[0])==16
    difference=max(abs(float(csvs[0][k]['estimate'])-float(csvs[1][k]['estimate'])) for k in csvs[0])
    assert difference<1e-12
    core=json.loads((run/'core-proof.json').read_text());assert core['unchanged']
    # Verify both INVARIANT receipts really ran in unlimited containers. Baseline
    # uses the same unbounded CPU/RAM policy; architecture is intentionally different.
    actual=[]
    for arm in arms:
        for agent in arm['agents']:
            host=agent.get('host_config') or (agent.get('runtime') or {}).get('host_config')
            assert host is not None
            assert host.get('Memory',0)==0 and host.get('NanoCpus',0)==0
            actual.append({'backend':arm['backend'],'role':agent['role'],
                'memory_bytes':host.get('Memory',0),'nano_cpus':host.get('NanoCpus',0),
                'privileged':host.get('Privileged'),'privilege_field_reported':'Privileged' in host})
    report={'namespace':namespace,'model':'gpt-6.1-sol','effort':'medium','starting_image':arms[0]['starting_image'],
        'result_equivalent':True,'max_estimate_difference':difference,'core_unchanged':core,
        'resource_observations':actual,'arms':results,
        'checker_negative_control':json.loads((run/'checker-negative-control.json').read_text()),
        'execution_audit':json.loads((run/'execution-audit.json').read_text()),
        'failed_setup':json.loads((run/'plain-preparation-failure.json').read_text()) if (run/'plain-preparation-failure.json').exists() else None,
        'cost_basis':json.loads((HERE/'results/pricing.json').read_text()),
        'ratios':{'time_invariant_over_plain':results['with_invariant']['wall_seconds']/results['without_invariant']['wall_seconds'],
            'tokens_invariant_over_plain':results['with_invariant']['usage']['total_tokens']/results['without_invariant']['usage']['total_tokens'],
            'credit_equivalent_invariant_over_plain':results['with_invariant']['standard_credit_equivalent']/results['without_invariant']['standard_credit_equivalent']},
        'limitations':['One pair, fixed order: INVARIANT first, plain second; not a statistical benchmark.',
            'No API dollar tariff is substituted for subscription billing. Published Standard credit equivalents are estimates, not actual debits.',
            'Outer coordinating chat tokens/cost and shared one-time experiment authoring time are unavailable and excluded.',
            'Common images were already built. Per-arm setup, preflight/validation, file transfer and export are included in wall time.',
            'INVARIANT uses app-server with separate executor/validation containers; baseline uses Codex exec in one container per agent.',
            'Logical task/roles/checks, Codex version, selected model/effort, compute image and unlimited CPU/RAM policy match; wrapper instructions/tool schemas differ.',
            'Provider cache warmth, network latency and nondeterministic code generation may affect this one pair.']}
    (run/'comparison.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    a,b=results.values()
    lines=['# Два параллельных исследователя и независимый проверяющий','',
        'Исследование интегралов x² и x⁴ на [0,1]: методы трапеций и Симпсона, 10/20/40/80 шагов.',
        '',f'Модель: `{MODEL_LABEL}`. Три агента на вариант; исходный Linux-образ одинаков.',
        '', '| Показатель | С INVARIANT | Без INVARIANT |','|---|---:|---:|',
        f"| Проверки | {a['tests_passed']}/{a['tests']} | {b['tests_passed']}/{b['tests']} |",
        f"| Время всего, с | {a['wall_seconds']:.2f} | {b['wall_seconds']:.2f} |",
        f"| Окно двух параллельных агентов, с | {a['timeline_seconds']['parallel_agents']:.2f} | {b['timeline_seconds']['parallel_agents']:.2f} |",
        f"| Окно третьего агента, с | {a['timeline_seconds']['checker_agent']:.2f} | {b['timeline_seconds']['checker_agent']:.2f} |",
        f"| Время вне окон агентов, с | {a['outside_agent_windows_seconds']:.2f} | {b['outside_agent_windows_seconds']:.2f} |",
        f"| Входные токены (включая кеш) | {a['usage']['input_tokens']:,} | {b['usage']['input_tokens']:,} |",
        f"| Из них кешированные | {a['usage']['cached_input_tokens']:,} | {b['usage']['cached_input_tokens']:,} |",
        f"| Доля кешированного входа | {a['cached_input_fraction']:.1%} | {b['cached_input_fraction']:.1%} |",
        f"| Выходные токены | {a['usage']['output_tokens']:,} | {b['usage']['output_tokens']:,} |",
        f"| Всего токенов | {a['usage']['total_tokens']:,} | {b['usage']['total_tokens']:,} |",
        f"| Ответы модели | {a['provider_responses']} | {b['provider_responses']} |",
        f"| Кредитный эквивалент Standard | {a['standard_credit_equivalent']:.6f} | {b['standard_credit_equivalent']:.6f} |",
        f"| Перекрытие окон ответов двух моделей, с | {a['parallel_model_evidence']['response_window_overlap_seconds']:.2f} | {b['parallel_model_evidence']['response_window_overlap_seconds']:.2f} |",
        '',f"Все 16 численных результатов совпадают с максимальным расхождением {difference:.3g}.",
        'В обоих вариантах третий агент начал работу после завершения первых двух.',
        'Дополнительный отрицательный контроль: оба проверяющих приняли исходные данные и отвергли численную строку с намеренно испорченной оценкой. Дополнительных модельных вызовов для этого не было.',
        '', '## Проверка отсутствия изменений INVARIANT','',
        f"Проверенные {core['files_checked']} файла ядра, SDK, Docker-конфигураций, skill и служебных скриптов совпадают с отпечатками до запуска.",
        'Добавлены только файлы этого исследовательского примера. Через INVARIANT использованы существующие init, ExperimentEngine.submit/execute/results и check_frozen; новых методов, операций RPC, плагинов или исправлений ядра нет.',
        'Прямой backend использует стандартную библиотеку, Docker CLI и codex exec. В его рабочем образе отсутствует Python-пакет INVARIANT; бинарники Codex в обоих вариантах совпадают по SHA256. Проверено по одной сессии на роль, без дополнительных дочерних агентов.',
        '', '## Стоимость и границы сравнения','',
        'Расход относится к шести рабочим агентам. Кешированные входные токены — часть входных, reasoning — часть выходных; повторно они не прибавляются. Наблюдения ответов сверены с итоговыми счётчиками сессий.',
        'По [опубликованному тарифу Codex](https://developers.openai.com/codex/pricing/#token-rates) для GPT-6.1 Sol Standard: 50 кредитов за миллион некешированных входных токенов, 2,5 за миллион кешированных и 250 за миллион выходных.',
        'Это сопоставимый кредитный эквивалент, а не фактическое списание по подписке. Фактическая сумма в долларах и изменение подписочного лимита для отдельного запуска недоступны. API-цены для их подмены не использованы.',
        f"Разница зависит от кеша: доля кешированного входа — {a['cached_input_fraction']:.1%} и {b['cached_input_fraction']:.1%}. Если условно считать весь вход некешированным, оценки были бы {a['credit_equivalent_if_no_input_cache']:.5f} и {b['credit_equivalent_if_no_input_cache']:.5f} кредита. Этот сценарий не является фактическим расходом.",
        '', 'Время вне окон агентов включает подготовку, preflight, сохранение/передачу/выгрузку и прочую координацию; проверки внутри invariant_validate входят в окно агента. Это не замер чистого процессорного overhead.',
        '', (f"Отдельно сохранён первоначальный сбой подготовки прямого запуска: около {report['failed_setup']['log_capture_span_seconds_approx']:.1f} с по временам файла журнала, до обращения к модели, 0 модельных вызовов. Причина — несовместимый параметр распаковки в скрипте примера; исправлен только пример. Это время не включено в последующий успешный запуск и не скрыто из отчёта." if report['failed_setup'] else 'Сбоев подготовки в этом запуске не зафиксировано.'),
        '',
        '- Одна пара запусков в фиксированном порядке: сначала INVARIANT, затем прямой вариант. Это описательное сравнение.',
        '- Внешний чат, который подготовил эксперимент, имеет общий для двух вариантов расход; его токены и стоимость недоступны и не включены в итоги.',
        '- Общие образы были собраны заранее. В измерение входят подготовка конкретного запуска, проверки, передача и выгрузка файлов.',
        '- INVARIANT использует app-server и отдельные контейнеры агента/исполнителя/проверки; прямой вариант — Codex CLI в контейнере каждого агента.',
        '- Совпадают задача, роли, проверки, бинарник Codex, модель/effort, вычислительный образ и отсутствие квот CPU/RAM. Служебные инструкции и схемы инструментов различаются.',
        '- На значения могут влиять кеширование, задержки сервиса и различия в сгенерированном коде.',
        '', '## Артефакты','',
        '- [Полные измерения](comparison.json)',
        '- [Отчёт агента с INVARIANT](with-invariant/agents/checker/report.md)',
        '- [График с INVARIANT](with-invariant/agents/checker/errors.svg)',
        '- [Отчёт агента без INVARIANT](without-invariant/agents/checker/report.md)',
        '- [График без INVARIANT](without-invariant/agents/checker/errors.svg)',
        '- [Отпечатки ядра после запуска](core-proof.json)']
    (run/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'passed':True,'results':results,'ratios':report['ratios']},ensure_ascii=False,indent=2))


MODEL_LABEL='gpt-6.1-sol / medium, Codex 0.163.0'
if __name__=='__main__':main()
