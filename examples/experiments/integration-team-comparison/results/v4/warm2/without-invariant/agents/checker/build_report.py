"""Independently verify received quadrature results and build a Russian report.

Only standard-library modules are used. Inputs are read, never rewritten.
Run: python3 -B build_report.py --inputs inputs --output .
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


def rational_quadrature(method, power, n):
    """Independent exact arithmetic on rational grid points, including weights."""
    samples = [Fraction(i, n) ** power for i in range(n + 1)]
    if method == 'trapezoid':
        return (sum(samples[1:-1]) + (samples[0] + samples[-1]) / 2) / n
    return (samples[0] + samples[-1]
            + 4 * sum(samples[1:-1:2]) + 2 * sum(samples[2:-1:2])) / (3 * n)


def theoretical_error(method, power, n):
    if method == 'trapezoid':
        return (Fraction(1, 6 * n**2) if power == 2 else
                Fraction(1, 3 * n**2) - Fraction(1, 30 * n**4))
    return Fraction(0) if power == 2 else Fraction(2, 15 * n**4)


def verify(inputs):
    rows, hashes, evidence = [], {}, []
    for method in METHODS:
        raw = (inputs / method / 'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        require(data['method'] == method and data['interval'] == [0, 1], method)
        require(sorted((r['power'], r['n']) for r in data['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], 'Unexpected row grid')
        # Execute definitions, without calling either input's file-writing main().
        integrate = runpy.run_path(str(inputs / method / 'solution.py'))['integrate']
        for row in data['rows']:
            power, n = row['power'], row['n']
            for field in ('estimate', 'exact', 'abs_error'):
                value = row[field]
                require(type(value) in (int, float) and math.isfinite(value), field)
            exact = Fraction(1, power + 1)
            rational = rational_quadrature(method, power, n)
            formula = theoretical_error(method, power, n)
            require(rational - exact == formula, 'Exact formula mismatch')
            # Eight ULPs of the integral allow ordinary binary64 evaluation error;
            # this is far smaller than even the smallest nonzero truncation error.
            tolerance = 8 * math.ulp(float(exact))
            estimate = row['estimate']
            require(row['exact'] == float(exact), 'Incorrect exact reference')
            require(row['abs_error'] == abs(estimate - row['exact']),
                    'Incorrect reported floating-point absolute error')
            true_error = abs(Fraction(estimate) - exact)
            require(abs(Fraction(estimate) - rational) <= Fraction(tolerance),
                    'Estimate disagrees with independent rational sum')
            require(abs(true_error - formula) <= Fraction(tolerance),
                    'Error disagrees with mathematical formula')
            executed = integrate(lambda x: x ** power, 0, 1, n)
            require(executed == estimate, 'Input implementation does not reproduce row')
            rows.append({'method': method, **row})
            evidence.append({'method': method, 'power': power, 'n': n,
                             'rational_estimate': str(rational),
                             'theoretical_signed_error': str(formula),
                             'error_against_rational_integral': float(true_error),
                             'executed_estimate': executed,
                             'roundoff_tolerance': tolerance})
    ratios = {}
    for method, power in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        errors = {r['n']: r['abs_error'] for r in rows
                  if r['method'] == method and r['power'] == power}
        measured = [errors[n] / errors[2*n] for n in NS[:-1]]
        target = 4 if method == 'trapezoid' else 16
        require(all(abs(r - target) < .015 for r in measured), 'Unexpected convergence')
        ratios[f'{method}_x{power}'] = measured
    quadratic_roundoff = all(r['abs_error'] <= 8 * math.ulp(1/3)
                             for r in rows if r['method'] == 'simpson' and r['power'] == 2)
    require(quadratic_roundoff, 'Simpson quadratic error exceeds roundoff')
    verification = {
        'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
        'observed_ratios': ratios,
        'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                        'simpson_x2_exact_within_roundoff': quadratic_roundoff},
        'verification_method': 'Exact Fraction quadrature, exact error formulas, '
                               'and execution of both received integrate functions; '
                               'binary64 tolerance: eight ULPs of exact reference.',
        'row_evidence': evidence,
    }
    return rows, verification


def make_svg(rows):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="800" height="520" '
             'viewBox="0 0 800 520" role="img" aria-labelledby="title desc">',
             '<title id="title">Quadrature absolute errors</title>',
             '<desc id="desc">Received errors on a base-ten logarithmic vertical scale; '
             'Simpson x^2 roundoff series omitted.</desc>',
             '<rect width="800" height="520" fill="white"/>',
             '<g font-family="sans-serif" font-size="14" fill="#222">',
             '<text x="80" y="28">Absolute error versus subinterval count</text>']
    def x(n):
        return 90 + (n - 10) / 70 * 640
    def y(error):
        return 65 + (-2 - math.log10(error)) / 7 * 340
    for exponent in range(-2, -10, -1):
        py = y(10**exponent)
        parts.append(f'<path d="M90 {py} H730" stroke="#ddd"/>')
        parts.append(f'<text x="25" y="{py+5}">10^{exponent}</text>')
    for n in NS:
        parts.append(f'<text x="{x(n)-8}" y="430">{n}</text>')
    parts.extend(['<path d="M90 65 V405 H730" fill="none" stroke="#222"/>',
                  '<text x="360" y="455">n (subintervals)</text>',
                  '<text x="18" y="55">error (log10)</text>'])
    for index, (method, power, color) in enumerate((
            ('trapezoid', 2, '#1769aa'), ('trapezoid', 4, '#b54d00'),
            ('simpson', 4, '#15803d'))):
        series = sorted((r for r in rows if r['method'] == method and r['power'] == power),
                        key=lambda r: r['n'])
        require(all(r['abs_error'] > 0 for r in series), 'Cannot plot nonpositive error')
        points = ' '.join(f"{x(r['n']):.3f},{y(r['abs_error']):.3f}" for r in series)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        for r in series:
            parts.append(f'<circle cx="{x(r["n"]):.3f}" cy="{y(r["abs_error"]):.3f}" '
                         f'r="3" fill="{color}"/>')
        lx = 95 + index * 225
        parts.append(f'<path d="M{lx} 490 h25" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="{lx+32}" y="495">{method} x^{power}</text>')
    return '\n'.join(parts + ['</g>', '</svg>', ''])


def make_report(rows, verification):
    lines = [
        '# Проверка точности численного интегрирования',
        '',
        'Это малый детерминированный численный эксперимент на отрезке [0,1]: '
        'для функций x² и x⁴ использованы равномерные сетки n=10,20,40,80. '
        'Точные эталоны получены аналитически: ∫₀¹x²dx=1/3, ∫₀¹x⁴dx=1/5.',
        '',
        'Составная формула трапеций (trapezoid) имеет веса 1/2 на концах '
        'и 1 внутри сетки с множителем h=1/n. Формула Симпсона (simpson) '
        'имеет веса 1,4,2,…,4,1 и множитель h/3; все выбранные n чётны. '
        'Проверены все 16 полученных строк: обе исходные функции integrate '
        'исполнены без запуска main, чтобы не изменять входные файлы. '
        'Независимые суммы вычислены в точной рациональной арифметике Fraction. '
        'Их разности с эталонами точно совпали с математическими формулами ошибок.',
        '',
        'При h=1/n знаковые ошибки Q−I равны: T(x²)−1/3=h²/6; '
        'T(x⁴)−1/5=h²/3−h⁴/30; S(x²)−1/3=0; S(x⁴)−1/5=2h⁴/15. '
        'Все ненулевые теоретические ошибки положительны. Формулы трапеций '
        'следуют из точной для этих полиномов формулы Эйлера—Маклорена; '
        'формулу Симпсона также даёт S(n)=(4T(n)−T(n/2))/3, '
        'где T(k) — результат трапеций на k подынтервалах.',
        '',
        'Отношения полученных абсолютных ошибок E(n)/E(2n):',
        '',
        '| Серия | 10→20 | 20→40 | 40→80 |',
        '|---|---:|---:|---:|',
    ]
    for key, ratios in verification['observed_ratios'].items():
        lines.append('| ' + key + ' | ' + ' | '.join(f'{r:.10f}' for r in ratios) + ' |')
    lines.extend([
        '',
        'Отношения близки к 4 для трапеций и к 16 для Симпсона на x⁴: '
        'измеренные порядки p=log₂(E(n)/E(2n)) близки к 2 и 4 соответственно. '
        'Небольшое отклонение трапеций на x⁴ объясняется членом −h⁴/30.',
        '',
        'Симпсон интегрирует x² точно в рациональной арифметике. Во всех '
        'полученных строках ошибка относительно float(1/3) равна '
        '1.1102230246251565e−16. Это округление binary64, а не ошибка '
        'квадратурной формулы. Сам эталон 1/3 также не представим точно '
        'в binary64; verification.json отдельно содержит ошибки относительно '
        'рационального эталона. Для сопоставления с точными суммами разрешено '
        'не более восьми ULP эталона, то есть менее 4.45e−16.',
        '',
        'В errors.svg вертикальная шкала логарифмическая по основанию 10, '
        'горизонтальная — линейная по n. Серия simpson x² опущена: её '
        'околонулевая ошибка отражает только округление и не показывает '
        'сходимость; логарифм нуля нигде не вычисляется. Три остальные серии '
        'построены непосредственно по полученным ошибкам.',
        '',
        'comparison.csv сохраняет все входные числовые значения. SHA256 '
        'в verification.json относится к фактически прочитанным байтам '
        'result.json. Повторение: python3 -B build_report.py --inputs inputs '
        '--output output. Эксперимент охватывает только две функции и четыре '
        'сетки; общий порядок методов согласуется здесь с точными формулами.',
        '',
    ])
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
    for filename, content in (
            ('verification.json', json.dumps(verification, ensure_ascii=False, indent=2) + '\n'),
            ('errors.svg', make_svg(rows)), ('report.md', make_report(rows, verification))):
        (args.output / filename).write_text(content, encoding='utf-8')


if __name__ == '__main__':
    main()
