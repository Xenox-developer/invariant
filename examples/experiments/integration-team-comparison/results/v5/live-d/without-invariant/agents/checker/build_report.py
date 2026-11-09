"""Independently verify received quadrature artifacts and build a Russian report.

Usage: python3 -B build_report.py --inputs inputs --output .
Only Python's standard library is required. Inputs are read without modification.
"""
import argparse
import csv
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import runpy

NS = (10, 20, 40, 80)
METHODS = ('trapezoid', 'simpson')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rational_sum(method, power, n):
    """Independent exact arithmetic quadrature on rational nodes."""
    values = [Fraction(i, n) ** power for i in range(n + 1)]
    if method == 'trapezoid':
        return (values[0] / 2 + sum(values[1:-1]) + values[-1] / 2) / n
    return (values[0] + values[-1]
            + 4 * sum(values[1:-1:2]) + 2 * sum(values[2:-1:2])) / (3 * n)


def formula_error(method, power, n):
    if method == 'trapezoid':
        return Fraction(1, 6 * n**2) if power == 2 else Fraction(1, 3 * n**2) - Fraction(1, 30 * n**4)
    return Fraction(0) if power == 2 else Fraction(2, 15 * n**4)


def verify(inputs):
    rows, hashes, details = [], {}, []
    for method in METHODS:
        folder = inputs / method
        raw = (folder / 'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        document = json.loads(raw)
        require(document['method'] == method and document['interval'] == [0, 1], 'Invalid method or interval')
        require(sorted((r['power'], r['n']) for r in document['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], 'Invalid row coverage')
        # __main__ is deliberately not executed: it writes result.json.
        # Invoke the original integrator directly, leaving the received files intact.
        integrate = runpy.run_path(str(folder / 'solution.py'))['integrate']
        for source in document['rows']:
            p, n = source['power'], source['n']
            for key in ('estimate', 'exact', 'abs_error'):
                require(type(source[key]) in (int, float) and math.isfinite(source[key]), 'Invalid numeric field')
            exact = Fraction(1, p + 1)
            quadrature = rational_sum(method, p, n)
            error = formula_error(method, p, n)
            require(quadrature - exact == error, 'Exact rational sum disagrees with mathematical error formula')
            require(source['exact'] == float(exact), 'Incorrect exact reference')
            reproduced = integrate(lambda x: x**p, 0, 1, n)
            require(reproduced == source['estimate'], 'Original integrator does not reproduce received estimate')
            budget = 8 * math.ulp(float(exact))
            discrepancy = abs(Fraction.from_float(float(source['estimate'])) - quadrature)
            require(discrepancy <= Fraction.from_float(budget), 'Estimate disagrees with rational quadrature')
            require(source['abs_error'] == abs(source['estimate'] - source['exact']), 'Incorrect reported error')
            require(abs(source['abs_error'] - float(error)) <= budget, 'Reported error disagrees with formula')
            rows.append({'method': method, **{k: source[k] for k in ('power', 'n', 'estimate', 'exact', 'abs_error')}})
            details.append({'method': method, 'power': p, 'n': n,
                            'rational_estimate': str(quadrature), 'formula_error': str(error),
                            'estimate_roundoff': float(discrepancy), 'roundoff_budget': budget})
        require((folder / 'result.json').read_bytes() == raw, 'Input changed during verification')
    ratios = {}
    for method, p in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        errors = {r['n']: r['abs_error'] for r in rows if r['method'] == method and r['power'] == p}
        ratios[f'{method}_x{p}'] = [errors[n] / errors[2*n] for n in NS[:-1]]
        target = 4 if method == 'trapezoid' else 16
        require(all(abs(r - target) < .015 for r in ratios[f'{method}_x{p}']), 'Unexpected measured convergence')
    roundoff = all(r['abs_error'] <= 8 * math.ulp(1/3) for r in rows if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic exceeds roundoff')
    return rows, {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                  'observed_ratios': ratios,
                  'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                  'simpson_x2_exact_within_roundoff': roundoff},
                  'verification_method': 'Original integrate functions executed; independent Fraction sums and exact signed error formulas; eight ulps absolute tolerance for floating-point roundoff.',
                  'row_checks': details}


def svg_graph(rows):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="850" height="540" viewBox="0 0 850 540">',
             '<title>Quadrature absolute errors</title>',
             '<desc>Logarithmic error scale; near-zero Simpson x^2 omitted.</desc>',
             '<rect width="850" height="540" fill="white"/>',
             '<g font-family="sans-serif" font-size="14" fill="#222">',
             '<text x="100" y="28">Absolute error vs number of subintervals</text>']
    def x(n):
        return 100 + math.log2(n / 10) * 200
    def y(error):
        require(error > 0, 'Cannot plot nonpositive error')
        return 440 - (math.log10(error) + 9) * 60
    for exponent in range(-9, -1):
        yy = y(10**exponent)
        parts.append(f'<path d="M100 {yy} H700" stroke="#ddd"/><text x="25" y="{yy+5}">1e{exponent}</text>')
    for n in NS:
        parts.append(f'<text x="{x(n)-10}" y="465">{n}</text>')
    parts.extend(['<path d="M100 20 V440 H710" stroke="#222" fill="none"/>',
                  '<text x="310" y="495">n (doubling steps)</text>',
                  '<text transform="translate(17 340) rotate(-90)">absolute error (log10 scale)</text>'])
    for i, (method, power, color) in enumerate((('trapezoid', 2, '#0066bb'), ('trapezoid', 4, '#bb4400'), ('simpson', 4, '#228833'))):
        data = sorted((r for r in rows if r['method'] == method and r['power'] == power), key=lambda r: r['n'])
        points = ' '.join(f"{x(r['n']):.3f},{y(r['abs_error']):.3f}" for r in data)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        for r in data:
            parts.append(f'<circle cx="{x(r["n"]):.3f}" cy="{y(r["abs_error"]):.3f}" r="3" fill="{color}"/>')
        parts.append(f'<text x="{110+i*235}" y="525" fill="{color}">{method} x^{power}</text>')
    parts.append('</g></svg>\n')
    return '\n'.join(parts)


def report(rows, verification):
    text = '''# Точность численного интегрирования

Проведён малый детерминированный численный эксперимент: функции x² и x⁴
интегрировались на [0, 1] при n = 10, 20, 40, 80 равных подынтервалах.
Точные эталоны получены из первообразной x^(p+1)/(p+1): I₂ = 1/3, I₄ = 1/5.
Метод trapezoid заменяет функцию отрезками и суммирует площади трапеций;
составной метод simpson использует параболы и веса 1, 4, 2, …, 4, 1 при чётном n.

Все 16 полученных строк сохранены в comparison.csv без замены исходных чисел.
Проверяющий генератор исполняет функции integrate из обеих исходных программ,
не вызывая записывающие файлы main. Независимый расчёт с Fraction суммирует
рациональные значения в узлах и сравнивается с точными формулами знаковой ошибки
Q − I: для трапеций E₂ = 1/(6n²), E₄ = 1/(3n²) − 1/(30n⁴);
для Симпсона E₂ = 0, E₄ = 2/(15n⁴). Все ненулевые ошибки здесь положительны.
Дополнительно проверены эталоны, разности estimate − exact и воспроизводимость
полученных оценок. Допуск округления равен восьми ulp машинного эталона;
он значительно меньше наименьшей ненулевой ошибки дискретизации.
Хеши SHA256 исходных байтов result.json и подробности проверки находятся в verification.json.

Отношения последовательных полученных абсолютных ошибок E(n)/E(2n):

| Ряд | 10 → 20 | 20 → 40 | 40 → 80 | Наблюдаемые порядки log₂ отношения |
|---|---:|---:|---:|---|
'''
    for key, ratios in verification['observed_ratios'].items():
        text += '| ' + key + ' | ' + ' | '.join(f'{r:.10f}' for r in ratios) + ' | ' + ', '.join(f'{math.log2(r):.6f}' for r in ratios) + ' |\n'
    quadratic = [r['abs_error'] for r in rows if r['method'] == 'simpson' and r['power'] == 2]
    text += f'''
Уменьшение шага вдвое снижает ошибки трапеций примерно в 4 раза (порядок 2),
а ошибки Симпсона для x⁴ — примерно в 16 раз (порядок 4).
Небольшое отклонение отношения для трапеций на x⁴ от четырёх объясняется членом n⁻⁴.
Эти выводы относятся к данным полиномам и сеткам; эксперимент не является
статистическим исследованием или доказательством порядка для произвольной функции.

Симпсон интегрирует x² точно в рациональной арифметике. Полученные машинные
ошибки лежат от {min(quadratic):.16g} до {max(quadratic):.16g}; это округление двоичных
узлов, сумм и представления 1/3, а не ошибка аппроксимации. Поэтому для этого
ряда порядок по отношениям ошибок не оценивается. Его почти нулевые значения
исключены из errors.svg: они скрывали бы масштаб содержательных ошибок.
На графике показаны три оставшихся ряда с логарифмической осью ошибок;
логарифм нуля нигде не вычисляется. Подписи n соответствуют удвоениям сетки.

Воспроизведение (только стандартная библиотека Python):
`python3 -B build_report.py --inputs inputs --output fresh_output`
'''
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows, verification = verify(args.inputs)
    # Complete verification before emitting a successful report.
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=('method', 'power', 'n', 'estimate', 'exact', 'abs_error'))
        writer.writeheader()
        writer.writerows(rows)
    (args.output / 'report.md').write_text(report(rows, verification), encoding='utf-8')
    (args.output / 'errors.svg').write_text(svg_graph(rows), encoding='utf-8')
    (args.output / 'verification.json').write_text(json.dumps(verification, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
