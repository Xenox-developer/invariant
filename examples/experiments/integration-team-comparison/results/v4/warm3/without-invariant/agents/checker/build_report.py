"""Independently verify received quadrature artifacts and generate a report.

Usage: python3 -B build_report.py --inputs inputs --output .
Only the standard library is needed; received artifacts are never rewritten.
"""

import argparse
import csv
from fractions import Fraction
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from xml.sax.saxutils import escape

METHODS = ('trapezoid', 'simpson')
NS = (10, 20, 40, 80)
POWERS = (2, 4)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rational_quadrature(method, power, n):
    """Independent sums on exact rational nodes, without floating arithmetic."""
    samples = [Fraction(i, n) ** power for i in range(n + 1)]
    if method == 'trapezoid':
        return (sum(samples[1:-1]) + (samples[0] + samples[-1]) / 2) / n
    return (samples[0] + samples[-1] +
            4 * sum(samples[1:-1:2]) + 2 * sum(samples[2:-1:2])) / (3 * n)


def theoretical_error(method, power, n):
    # Exact polynomial identities (quadrature minus integral), not bounds.
    if method == 'trapezoid':
        return (Fraction(1, 6 * n**2) if power == 2 else
                Fraction(1, 3 * n**2) - Fraction(1, 30 * n**4))
    return Fraction(0) if power == 2 else Fraction(2, 15 * n**4)


def verify(inputs):
    rows, hashes, details = [], {}, []
    sys.dont_write_bytecode = True
    for method in METHODS:
        folder = inputs / method
        raw = (folder / 'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        document = json.loads(raw)
        require(document['method'] == method and document['interval'] == [0, 1],
                f'{method}: wrong method or interval')
        require(sorted((r['power'], r['n']) for r in document['rows']) ==
                [(p, n) for p in POWERS for n in NS], f'{method}: wrong row set')
        spec = importlib.util.spec_from_file_location(f'received_{method}', folder / 'solution.py')
        implementation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(implementation)  # main() is deliberately not run.
        for row in document['rows']:
            power, n = row['power'], row['n']
            label = f'{method} x**{power} n={n}'
            for key in ('estimate', 'exact', 'abs_error'):
                require(type(row[key]) in (int, float) and math.isfinite(row[key]),
                        f'{label}: invalid {key}')
            exact = Fraction(1, power + 1)
            independent = rational_quadrature(method, power, n)
            formula_error = theoretical_error(method, power, n)
            require(independent - exact == formula_error, f'{label}: rational identity failed')
            require(row['exact'] == float(exact), f'{label}: wrong exact reference')
            tolerance = 8 * math.ulp(float(independent))
            require(abs(row['estimate'] - float(independent)) <= tolerance,
                    f'{label}: estimate disagrees with independent rational sum')
            require(row['abs_error'] == abs(row['estimate'] - row['exact']),
                    f'{label}: inconsistent reported error')
            require(abs(row['abs_error'] - float(formula_error)) <= tolerance,
                    f'{label}: error disagrees with polynomial identity')
            executed = implementation.integrate(lambda x: x**power, 0, 1, n)
            require(executed == row['estimate'], f'{label}: implementation disagrees with artifact')
            rows.append({'method': method, **row})
            details.append({'method': method, 'power': power, 'n': n,
                            'rational_estimate': str(independent),
                            'rational_error': str(formula_error),
                            'executed_estimate': executed,
                            'absolute_tolerance': tolerance})
    ratios = {}
    for method, power in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        errors = {r['n']: r['abs_error'] for r in rows
                  if r['method'] == method and r['power'] == power}
        values = [errors[n] / errors[2 * n] for n in NS[:-1]]
        target = 4 if method == 'trapezoid' else 16
        require(all(abs(value - target) < .015 for value in values),
                f'{method} x**{power}: unexpected convergence')
        ratios[f'{method}_x{power}'] = values
    roundoff = all(r['abs_error'] <= 8 * math.ulp(r['exact']) for r in rows
                   if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic error exceeds roundoff')
    return rows, {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                  'observed_ratios': ratios,
                  'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                  'simpson_x2_exact_within_roundoff': roundoff},
                  'verification': details}


def graph(rows):
    """Log10(error) versus log2(n); only strictly positive resolved errors."""
    left, right, top, bottom = 100, 580, 55, 380
    def x(n):
        return left + math.log2(n / 10) / 3 * (right - left)
    def y(error):
        require(error > 0, 'Nonpositive error cannot be plotted on log scale')
        return top + (-2 - math.log10(error)) / 7 * (bottom - top)
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="820" height="460" viewBox="0 0 820 460">',
           '<title>Quadrature absolute error on [0,1]</title>',
           '<desc>Logarithmic error axis, logarithmic n axis. Simpson x squared is omitted because its error is roundoff.</desc>',
           '<rect width="820" height="460" fill="white"/>',
           '<g font-family="sans-serif" font-size="13" fill="#222">',
           '<text x="100" y="25">Absolute integration error on [0,1]</text>']
    for exponent in range(-2, -10, -1):
        yy = y(10**exponent)
        svg += [f'<path d="M {left} {yy:.3f} H {right}" stroke="#ddd"/>',
                f'<text x="45" y="{yy + 4:.3f}">1e{exponent}</text>']
    for n in NS:
        xx = x(n)
        svg += [f'<path d="M {xx:.3f} {top} V {bottom}" stroke="#ddd"/>',
                f'<text x="{xx - 8:.3f}" y="405">{n}</text>']
    svg += ['<text x="225" y="440">n subintervals (log2 scale)</text>',
            '<text transform="translate(20 290) rotate(-90)">absolute error (log10 scale)</text>']
    for index, (method, power, color) in enumerate((('trapezoid', 2, '#1261a0'),
                                                  ('trapezoid', 4, '#b34700'),
                                                  ('simpson', 4, '#16813b'))):
        series = sorted((r for r in rows if r['method'] == method and r['power'] == power),
                        key=lambda r: r['n'])
        points = ' '.join(f"{x(r['n']):.3f},{y(r['abs_error']):.3f}" for r in series)
        svg.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        yy = 90 + index * 30
        svg += [f'<path d="M 605 {yy} h 25" stroke="{color}" stroke-width="2"/>',
                f'<text x="638" y="{yy + 4}">{escape(method)} x^{power}</text>']
    return '\n'.join(svg + ['</g>', '</svg>', ''])


def report(rows, verification):
    lines = ['# Проверка численного интегрирования', '',
             'Это малый детерминированный численный эксперимент: интегрируются x² и x⁴ '
             'на [0,1] при n = 10, 20, 40, 80 равных подынтервалах, h = 1/n. '
             'Точные математические эталоны — 1/3 и 1/5 соответственно.', '',
             'Составная формула трапеций (trapezoid) использует половинные веса концов '
             'и единичные внутренние веса. Формула Симпсона (simpson) при чётном n '
             'использует веса 1, 4, 2, …, 4, 1 с множителем h/3. '
             'Обе полученные реализации изучены и выполнены через integrate; '
             'все 16 исходных оценок, эталонов и ошибок проверены. '
             'Исходные файлы не изменялись, CSV сохраняет именно полученные числа.', '',
             'Независимая проверка сумм проведена на рациональных узлах i/n с Fraction. '
             'В точной арифметике ошибки Q−I равны: для трапеций на x² — 1/(6n²), '
             'на x⁴ — 1/(3n²)−1/(30n⁴); для Симпсона на x² — 0, '
             'на x⁴ — 2/(15n⁴). Рациональные суммы совпали с этими тождествами точно. '
             'Отклонение полученных float от рациональных оценок и ошибок ограничено '
             'восемью ulp оценки; ошибки также сверены с abs(estimate−exact).', '',
             '| Серия | E(10)/E(20) | E(20)/E(40) | E(40)/E(80) |',
             '|---|---:|---:|---:|']
    for name, values in verification['observed_ratios'].items():
        lines.append('| ' + name + ' | ' + ' | '.join(f'{v:.10f}' for v in values) + ' |')
    lines += ['', 'Измеренные отношения показывают второй порядок трапеций '
              '(ошибка уменьшается примерно в 4 раза) и четвёртый порядок Симпсона '
              'на x⁴ (примерно в 16 раз). Малое отклонение трапеций на x⁴ от 4 '
              'объясняется членом порядка n⁻⁴.', '',
              'Симпсон интегрирует x² точно в рациональной арифметике. '
              'В полученных данных ошибка относительно float(1/3) равна '
              '1.1102230246251565e-16 при каждом n: это округление, а не ошибка метода. '
              'Сам эталон float(1/3) тоже округлён. Поэтому эта почти нулевая серия '
              'опущена на графике; её отношения не характеризуют сходимость. '
              'На errors.svg изображены три положительные серии с логарифмической '
              'шкалой ошибки и шкалой log2(n); log(0) не вычисляется.', '',
              'Полные исходные численные строки находятся в comparison.csv, '
              'хеши SHA-256 фактических байтов result.json, точные рациональные '
              'значения и результаты проверок — в verification.json. '
              'Выводы относятся к этим двум полиномам и указанным сеткам; '
              'случайных испытаний в эксперименте нет.', '',
              'Воспроизведение: `python3 -B build_report.py --inputs inputs --output OUTPUT_DIR`.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows, verification = verify(args.inputs)
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'comparison.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['method', 'power', 'n', 'estimate', 'exact', 'abs_error'])
        writer.writeheader()
        writer.writerows(rows)
    (args.output / 'report.md').write_text(report(rows, verification), encoding='utf-8')
    (args.output / 'errors.svg').write_text(graph(rows), encoding='utf-8')
    (args.output / 'verification.json').write_text(
        json.dumps(verification, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
