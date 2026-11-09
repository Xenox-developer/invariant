"""Explain the measured gap using existing evidence and metadata-only profiling."""
import json
import math
from pathlib import Path

HERE=Path(__file__).resolve().parent
RUN=HERE/'results/pair-20261107-a'
D=RUN/'diagnostics'


def main():
    comparison=json.loads((RUN/'comparison.json').read_text())
    sessions=json.loads((D/'sessions.json').read_text())
    setup=json.loads((D/'setup-profile.json').read_text())
    stages=json.loads((D/'stage-times.json').read_text())
    profile=json.loads((D/'native-profile.json').read_text())
    a=comparison['arms']['with_invariant'];b=comparison['arms']['without_invariant']
    deltas={k:a['timeline_seconds'][k]-b['timeline_seconds'][k] for k in a['timeline_seconds']}
    assert math.isclose(sum(deltas.values()),a['wall_seconds']-b['wall_seconds'],abs_tol=1e-6)
    roles=[]
    base_hashes=set()
    for role in ('trapezoid','simpson','checker'):
        native=next(s['sessions'][0] for s in sessions if s['arm']=='invariant' and s['role']==role)
        plain=next(s['sessions'][0] for s in sessions if s['arm']=='plain' and s['role']==role)
        nr=next(x for x in a['agents'] if x['role']==role);pr=next(x for x in b['agents'] if x['role']==role)
        base_hashes.update((native['meta']['base_instruction_sha256'],plain['meta']['base_instruction_sha256']))
        ni=native['usages'][0]['last']['input_tokens'];pi=plain['usages'][0]['last']['input_tokens']
        waits=[]
        calls={c.get('id'):c for c in native['calls'] if c.get('id')}
        for call in native['calls']:
            parent=calls.get(call.get('output_for'))
            if parent and 'tools.invariant_validate' in parent.get('argument_start',''):
                from datetime import datetime
                start=datetime.fromisoformat(parent['timestamp'].replace('Z','+00:00')).timestamp()
                end=datetime.fromisoformat(call['timestamp'].replace('Z','+00:00')).timestamp()
                waits.append(end-start)
        roles.append({'role':role,'first_input_native':ni,'first_input_plain':pi,'first_input_difference':ni-pi,
            'responses_native':nr['provider_responses'],'responses_plain':pr['provider_responses'],
            'total_token_difference':nr['usage']['total_tokens']-pr['usage']['total_tokens'],
            'native_validation_call_seconds':waits,
            'native_output_chars':sum(c.get('output_chars',0) for c in native['calls']),
            'plain_output_chars':sum(c.get('output_chars',0) for c in plain['calls'])})
    assert len(base_hashes)==1
    token_delta={k:a['usage'][k]-b['usage'][k] for k in ('input_tokens','output_tokens','total_tokens')}
    assert token_delta['input_tokens']+token_delta['output_tokens']==token_delta['total_tokens']
    preflights=[]
    for stage in stages:
        s=next(x for x in stage['studies'] if x['evaluation_only']);r=s['attempts'][0]
        preflights.append({'stage':stage['stage'],'before_attempt_seconds':r['started_at']-s['created_at'],
            'total_created_to_finished_seconds':r['finished_at']-s['created_at'],
            'actual_evaluator_seconds':r['evaluation']['duration_seconds']})
    report={'wall_difference_seconds':a['wall_seconds']-b['wall_seconds'],'timeline_differences_seconds':deltas,
        'outside_agent_window_difference_seconds':a['outside_agent_windows_seconds']-b['outside_agent_windows_seconds'],
        'inside_agent_window_difference_seconds':deltas['parallel_agents']+deltas['checker_agent'],
        'token_differences':token_delta,'roles':roles,'common_base_instruction_sha256':next(iter(base_hashes)),
        'base_instruction_identical':True,'original_preflights':preflights,
        'nonmodel_setup_profile':setup,'native_home_profile':profile,
        'plain_overrides':{'web_search':'disabled','features.multi_agent':False},
        'model_turns_in_diagnostics':0,
        'source_findings':[
            'The application creates two new INVARIANT projects; runtime preparation and baseline preflight repeat for the checker.',
            'prepare_control copies and hashes the Codex/inspector binaries in each project-specific runtime cache.',
            'LocalRuntime.models caches only within an instance; each new manager engine request constructs another LocalRuntime.',
            'ContainerEngine initializes/syncs; submit syncs again. CLI init syncs and exports logs too.',
            'ContainerEngine execute and results each export both logs and results; the benchmark calls both, discarding the first return.',
            'Unrestricted codex_configuration enables live web search and agents; baseline overrides web search and multi_agent.',
            'All three validations were reused; no duplicate post-run full evaluation or semantic reviewer explains the gap.',
            'Baseline agents also spent calls discovering absent invariant_validate and trying unavailable rg, so it is not a minimal baseline.'],
        'limits':[
            'Original logs do not time each Docker/RPC command; the disposable setup replay demonstrates current costs, not exact past per-command attribution.',
            'Session logs do not expose the fully serialized tools/model request or component tokenizer counts; the exact split of the ~3K initial-token gap is unknown.',
            'Cold-cache reasons are not visible in local logs. The measured cache rates affect price, not whether cached input is counted as input tokens.',
            'One fixed-order pair cannot establish steady-state speed or causal cache effects.']}
    (D/'diagnosis.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    lines=['# Почему запуск с INVARIANT оказался дольше и потребовал больше токенов','',
        'Разобраны журналы исходной пары. Новых исследовательских модельных запусков не было. Дополнительно измерены только подготовка runtime и чтение метаданных аккаунта/моделей, с программным запретом создавать модельный turn.',
        '', '## Время: основная прибавка находится в подготовке и переходе к третьему агенту','',
        '| Этап | С INVARIANT, с | Прямой запуск, с | Разница, с |','|---|---:|---:|---:|']
    labels={'before_first_agent':'До начала первых двух агентов','parallel_agents':'Окно двух параллельных агентов',
            'handoff_and_checker_preparation':'Переход и подготовка третьего агента','checker_agent':'Окно третьего агента','after_checker':'После третьего агента'}
    for key,label in labels.items():lines.append(f"| {label} | {a['timeline_seconds'][key]:.2f} | {b['timeline_seconds'][key]:.2f} | {deltas[key]:+.2f} |")
    lines += [f"| Итого | {a['wall_seconds']:.2f} | {b['wall_seconds']:.2f} | {report['wall_difference_seconds']:+.2f} |",'',
        f"Вне окон агентов получилось {a['outside_agent_windows_seconds']:.2f} против {b['outside_agent_windows_seconds']:.2f} с: прибавка {report['outside_agent_window_difference_seconds']:.2f} с. Окна агентов, напротив, суммарно короче на {-report['inside_agent_window_difference_seconds']:.2f} с. Их времена включают запуск контейнеров и инструменты, поэтому это не чистое время генерации модели.",
        '', 'В прикладном сценарии для проверяющего создан второй новый проект INVARIANT. Для него повторены создание менеджера/подготовка, проверка каталога моделей, создание защищённого runtime и baseline preflight. Прямой вариант передал готовые файлы уже подготовленному третьему контейнеру.',
        '', 'Исходные предварительные проверки:', '', '| Этап | От создания baseline до начала попытки, с | До завершения baseline, с | Сам evaluator, с |','|---|---:|---:|---:|']
    for p in preflights:lines.append(f"| {p['stage']} | {p['before_attempt_seconds']:.2f} | {p['total_created_to_finished_seconds']:.2f} | {p['actual_evaluator_seconds']:.2f} |")
    lines += ['', 'Диагностическое воспроизведение без модели подтвердило конкретные расходы:',
        f"- `environment.prepare_control` переносит {setup['control_bytes']:,} байт исполняемых файлов и проверяет их хеши. Новая копия заняла {setup['cold_control_seconds']:.2f} с, из них Docker cp — {sum(e['seconds'] for e in setup['cold_control_docker_calls'] if e['operation']=='cp'):.2f} с. Проверка готовой копии — {setup['warm_control_seconds']:.2f} с.",
        f"- `LocalRuntime.models` занял {setup['catalog_seconds']:.2f} с. Повторный вызов на том же экземпляре — {setup['catalog_same_instance_seconds']:.3f} с. Кеш существует только в памяти экземпляра; при новом процессе/проекте подготовка повторяется.",
        '- Отдельно запускался app-server для чтения аккаунта и каталога; оплачиваемый turn не создавался. В профиле видны initialize, account/read и model/list.',
        '- Есть повторная работа обвязки: init/конструктор/submit синхронизируют исходники; execute и следующий results оба выгружают результаты и логи. Первый ответ execute сценарий не использовал.',
        '', 'Замер подготовки выполнен позже на временной копии, которая затем удалена. Это подтверждение механизмов и их масштаба, а не точная разбивка всех секунд исторического запуска.',
        '', '## Токены: больше начальный контекст, а не больше обращений','',
        '| Показатель | С INVARIANT | Прямой запуск | Разница |','|---|---:|---:|---:|',
        f"| Входные токены | {a['usage']['input_tokens']:,} | {b['usage']['input_tokens']:,} | {token_delta['input_tokens']:+,} |",
        f"| Выходные токены | {a['usage']['output_tokens']:,} | {b['usage']['output_tokens']:,} | {token_delta['output_tokens']:+,} |",
        f"| Всего | {a['usage']['total_tokens']:,} | {b['usage']['total_tokens']:,} | {token_delta['total_tokens']:+,} |",
        f"| Ответы модели | {a['provider_responses']} | {b['provider_responses']} | {a['provider_responses']-b['provider_responses']:+d} |",'',
        '| Роль | Первый вход с INVARIANT | Первый вход напрямую | Разница | Изменение всех токенов роли |','|---|---:|---:|---:|---:|']
    for r in roles:lines.append(f"| {r['role']} | {r['first_input_native']:,} | {r['first_input_plain']:,} | {r['first_input_difference']:+,} | {r['total_token_difference']:+,} |")
    lines += ['',
        'Общие базовые инструкции Codex во всех шести сессиях совпадают по SHA256. Дополнительный контекст INVARIANT включает инструкции Linux-окружения, сети и сохранения состояния, перечень файлов/команд, удалённое окружение и инструмент invariant_validate. Он участвует и в следующих запросах по той же истории. Поэтому даже несколько тысяч начальных токенов дают десятки тысяч суммарных входных токенов за несколько ответов.',
        'Точное распределение начальных ~3 тысяч токенов между сериализованными схемами инструментов и отдельными сообщениями журнал не содержит. Не следует выдавать число токенов каждого компонента за измеренный факт.',
        '', 'Выявлено также различие настроек, которое было недостаточно явно выделено в предыдущем сравнении: профиль INVARIANT при restrictions_enabled=false задаёт web_search=live и agents.enabled=true; прямой CLI явно задавал web_search=disabled и features.multi_agent=false. Разрешения ресурсов были одинаковыми, но профили инструментов строго не выровнены. Веб-поиск и дополнительные агенты фактически не использовались.',
        '', 'Траектории тоже различались: агент трапеций через INVARIANT сделал отдельное чтение fixed_checks и отдельный запуск/validation; напрямую часть действий была объединена. У Симпсона и проверяющего через INVARIANT было по одному ответу меньше. Избыточного числа агентов или массового чтения логов в контекст не обнаружено.',
        '', 'Сам прямой сценарий также не минимален: все три агента искали отсутствующий invariant_validate, затем пытались использовать отсутствующий rg. Это следствие общего условного промпта и отсутствия готового списка команд в прямом варианте.',
        '', '## Проверки и кеш','',
        'Каждый invariant_validate выполнял сохранение, снимок окружения и отдельную проверку. Вызовы заняли примерно 12.9/13.5/7.4 с, тогда как непосредственно evaluator — 1.69/1.62/0.81 с. Это цена жизненного цикла и сохранения состояния; повторной полной проверки после успеха не было — все три receipts переиспользованы.',
        'Кешированный вход входит в общий счётчик токенов. Разница долей кеша 69.9% и 90.6% объясняет часть разницы кредитного эквивалента, но не является причиной роста числа токенов. Причину каждого cache miss локальные журналы не раскрывают.',
        '', '## Что имеет смысл оптимизировать','',
        '1. В прикладном сценарии переиспользовать один проект/менеджер и подготовленное окружение между этапами; исключить повторные синхронизации и выгрузки.',
        '2. В INVARIANT переиспользовать неизменяемые исполняемые файлы по digest образа между проектами, сохранив контроль целостности; не переносить 263 МБ для каждого нового этапа.',
        '3. Переиспользовать подключение/кеш метаданных моделей в пределах одного запуска с явной проверкой актуальности, вместо полного нового app-server для каждого этапа.',
        '4. Отделить отсутствие ресурсных ограничений от включения широкого набора инструментов. Использовать одинаковый компактный профиль инструментов в обоих вариантах.',
        '5. Для контрольного сравнения дать прямому агенту точный список доступных команд и локальную команду проверки; убрать поиск несуществующего инструмента.',
        '', 'Эти изменения в ходе диагностики не вносились. Ядро и исходные измерения сохранены. Дополнительных платных исследовательских модельных запусков не было.',
        '', 'Данные: [diagnosis.json](diagnosis.json), [sessions.json](sessions.json), [stage-times.json](stage-times.json), [setup-profile.json](setup-profile.json), [native-profile.json](native-profile.json).']
    (D/'DIAGNOSIS.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'time_delta_seconds':report['wall_difference_seconds'],'outside_delta_seconds':report['outside_agent_window_difference_seconds'],
        'inside_delta_seconds':report['inside_agent_window_difference_seconds'],'token_delta':token_delta,
        'first_prompt_deltas':[r['first_input_difference'] for r in roles],'paid_diagnostic_turns':0},indent=2))


if __name__=='__main__':main()
