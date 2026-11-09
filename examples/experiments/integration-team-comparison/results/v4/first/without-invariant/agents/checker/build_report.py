"""Verify received quadrature data and generate a deterministic report (stdlib only)."""

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


def rational_quadrature(method, power, n):
    """Independent summation on an exact rational grid, without input code."""
    samples = [Fraction(i, n) ** power for i in range(n + 1)]
    if method == 'trapezoid':
        return (samples[0] / 2 + sum(samples[1:-1]) + samples[-1] / 2) / n
    return sum(value * (1 if i in (0, n) else 4 if i % 2 else 2)
               for i, value in enumerate(samples)) / (3 * n)


def theoretical_error(method, power, n):
    """Signed quadrature-minus-integral errors, exact for these polynomials."""
    if method == 'trapezoid':
        return (Fraction(1, 6 * n**2) if power == 2 else
                Fraction(1, 3 * n**2) - Fraction(1, 30 * n**4))
    return Fraction(0) if power == 2 else Fraction(2, 15 * n**4)


def verify(inputs):
    rows, hashes, details = [], {}, []
    for method in METHODS:
        directory = inputs / method
        raw = (directory / 'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        document = json.loads(raw)
        require(document['method'] == method, 'Incorrect method')
        require(document['interval'] == [0, 1], 'Incorrect interval')
        require(sorted((r['power'], r['n']) for r in document['rows']) ==
                sorted((p, n) for p in (2, 4) for n in NS), 'Incorrect row set')
        # Execute definitions only: calling either main would overwrite an artifact.
        implementation = runpy.run_path(str(directory / 'solution.py'),
                                       run_name='received_' + method)['integrate']
        for row in document['rows']:
            power, n = row['power'], row['n']
            for key in ('estimate', 'exact', 'abs_error'):
                value = row[key]
                require(type(value) in (int, float) and math.isfinite(value),
                        f'{method} {power} {n}: invalid {key}')
            exact = Fraction(1, power + 1)
            rational_estimate = rational_quadrature(method, power, n)
            formula_error = theoretical_error(method, power, n)
            require(rational_estimate - exact == formula_error,
                    'Independent rational sum disagrees with error formula')
            computed = implementation(lambda x: x**power, 0, 1, n)
            label = f'{method} x**{power} n={n}'
            require(computed == row['estimate'], label + ': implementation mismatch')
            require(row['exact'] == float(exact), label + ': reference mismatch')
            require(row['abs_error'] == abs(row['estimate'] - row['exact']),
                    label + ': reported error mismatch')
            # Eight ulps of the estimate allow accumulated binary64 rounding;
            # this is far smaller than the smallest nonzero discretization error.
            tolerance = 8 * math.ulp(float(rational_estimate))
            true_error = Fraction.from_float(float(row['estimate'])) - exact
            estimate_difference = abs(Fraction.from_float(float(row['estimate'])) -
                                      rational_estimate)
            require(estimate_difference <= Fraction.from_float(tolerance),
                    label + ': estimate disagrees with exact quadrature')
            require(abs(Fraction.from_float(float(row['abs_error'])) - formula_error)
                    <= Fraction.from_float(tolerance),
                    label + ': error disagrees with mathematical formula')
            rows.append({'method': method, **row})
            details.append({'method': method, 'power': power, 'n': n,
                            'rational_quadrature': str(rational_estimate),
                            'formula_error': str(formula_error),
                            'true_abs_error_against_rational_integral': float(abs(true_error)),
                            'rounding_tolerance': tolerance})
    ratios = {}
    for method, power in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        errors = {r['n']: r['abs_error'] for r in rows
                  if r['method'] == method and r['power'] == power}
        ratios[f'{method}_x{power}'] = [errors[n] / errors[2*n] for n in NS[:-1]]
    roundoff = all(r['abs_error'] <= 8 * math.ulp(r['exact']) for r in rows
                   if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic exceeds roundoff tolerance')
    for name, values in ratios.items():
        target = 16 if name == 'simpson_x4' else 4
        require(all(abs(v - target) < .015 for v in values), 'Convergence mismatch')
    verification = {
        'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
        'observed_ratios': ratios,
        'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                        'simpson_x2_exact_within_roundoff': roundoff},
        'verification_method': 'Exact Fraction grid sums, analytic error formulas, '
                               'received integrate execution, and binary64 error checks',
        'row_checks': details,
    }
    return rows, verification


def svg_graph(rows):
    left, top, width, height = 95, 55, 570, 340
    def x(n):
        return left + math.log2(n / 10) / 3 * width
    def y(error):
        require(error > 0, 'Logarithm requires positive error')
        return top + (-2 - math.log10(error)) / 7 * height
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="850" height="520" '
             'viewBox="0 0 850 520" role="img" aria-labelledby="title desc">',
             '<title id="title">Quadrature absolute errors</title>',
             '<desc id="desc">Logarithmic error scale; n doubles at each tick. '
             'Simpson x**2 is omitted because its errors are at roundoff level.</desc>',
             '<rect width="850" height="520" fill="white"/>',
             '<g font-family="sans-serif" font-size="13" fill="#222">',
             '<text x="95" y="25">Absolute error vs. subinterval count</text>']
    for exponent in range(-2, -10, -1):
        yp = y(10**exponent)
        parts.extend([f'<path d="M {left} {yp:.3f} h {width}" stroke="#ddd"/>',
                      f'<text x="30" y="{yp+4:.3f}">1e{exponent}</text>'])
    for n in NS:
        xp = x(n)
        parts.append(f'<text x="{xp:.3f}" y="418" text-anchor="middle">{n}</text>')
    parts.extend(['<text x="380" y="445">n (log2 scale)</text>',
                  '<text x="18" y="300" transform="rotate(-90 18 300)">abs_error (log10 scale)</text>'])
    for index, (method, power, color) in enumerate((('trapezoid', 2, '#1565c0'),
                                                   ('trapezoid', 4, '#c45300'),
                                                   ('simpson', 4, '#208040'))):
        selected = sorted((r for r in rows if r['method'] == method and
                           r['power'] == power), key=lambda r: r['n'])
        points = ' '.join(f"{x(r['n']):.3f},{y(r['abs_error']):.3f}" for r in selected)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        for r in selected:
            parts.append(f'<circle cx="{x(r["n"]):.3f}" cy="{y(r["abs_error"]):.3f}" r="3" fill="{color}"/>')
        ly = 80 + index * 30
        parts.extend([f'<path d="M 690 {ly} h 25" stroke="{color}" stroke-width="2"/>',
                      f'<text x="725" y="{ly+4}">{method} x**{power}</text>'])
    parts.append('</g></svg>\n')
    return '\n'.join(parts)


def report_text(rows, verification):
    ratios = verification['observed_ratios']
    lines = [
        '# Точность численного интегрирования\n',
        'Это малый детерминированный численный эксперимент: интегрируются x**2 и '
        'x**4 на [0,1] при n = 10, 20, 40, 80 равных подынтервалах. '
        'Точные эталоны следуют из первообразной x**(p+1)/(p+1): 1/3 и 1/5.\n',
        'Составная формула трапеций использует веса 1/2 на концах и 1 внутри '
        'с множителем h=1/n. Составная формула Симпсона использует веса '
        '1,4,2,…,4,1 с множителем h/3; все выбранные n чётны. '
        'Полученные файлы не изменялись: обе реализации изучены и выполнены через '
        'integrate, без запуска записывающих main. Все 16 оценок совпали с их '
        'исходными значениями. CSV сохраняет полученные числа, включая ошибки.\n',
        'Независимая проверка выполнена точными рациональными суммами Fraction '
        'на сетке i/n. Для ошибки Q−I формулы трапеций дают 1/(6n²) для x**2 '
        'и 1/(3n²)−1/(30n⁴) для x**4. Они следуют из сумм степеней '
        '(или формулы Эйлера–Маклорена, которая здесь обрывается). '
        'Для Симпсона S_n=(4T_n−T_(n/2))/3: член n⁻² сокращается, '
        'поэтому ошибки равны 0 и 2/(15n⁴). Рациональные суммы точно совпали '
        'с этими формулами во всех случаях. Допуск для округления оценок '
        'составил восемь ulp; отдельно проверены эталоны и вычитания ошибок.\n',
        'Измеренные отношения E(n)/E(2n):\n',
        '| Серия | 10→20 | 20→40 | 40→80 |\n|---|---:|---:|---:|',
    ]
    for name, values in ratios.items():
        lines.append('| ' + name + ' | ' + ' | '.join(f'{v:.10f}' for v in values) + ' |')
    lines.extend([
        '\nОтношения около 4 подтверждают второй порядок трапеций; около 16 — '
        'четвёртый порядок Симпсона для x**4. Соответствующие наблюдаемые '
        'порядки log2(E(n)/E(2n)) равны:',
        *[f'- {name}: ' + ', '.join(f'{math.log2(v):.8f}' for v in values)
          for name, values in ratios.items()],
        '\nДля x**2 Симпсон математически точен. Во всех четырёх полученных '
        'строках ошибка относительно float(1/3) равна 5.551115123125783e-17 '
        '(одна ulp): это округление двоичной арифметики, а не ошибка квадратуры. '
        'Сам float(1/3) тоже приближён; verification.json дополнительно содержит '
        'ошибку относительно точной рациональной дроби. Отношения этих почти '
        'нулевых ошибок не характеризуют порядок метода.\n',
        'На errors.svg показаны три серии с положительными ошибками; ось ошибок '
        'логарифмическая (log10), ось n — log2. Серия simpson x**2 опущена, '
        'поскольку находится на уровне округления; логарифм нуля не вычисляется. '
        'Выводы относятся к этим полиномам и четырём сеткам.\n',
        'Воспроизведение: `python3 -B build_report.py --inputs inputs --output .`. '
        'Использована только стандартная библиотека Python. Контрольные SHA256 '
        'вычислены по исходным байтам result.json и записаны в verification.json.\n',
    ])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    rows, verification = verify(args.inputs.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['method', 'power', 'n', 'estimate', 'exact', 'abs_error'])
        writer.writeheader()
        writer.writerows(rows)
    (args.output / 'verification.json').write_text(
        json.dumps(verification, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (args.output / 'errors.svg').write_text(svg_graph(rows), encoding='utf-8')
    (args.output / 'report.md').write_text(report_text(rows, verification), encoding='utf-8')


if __name__ == '__main__':
    main()
