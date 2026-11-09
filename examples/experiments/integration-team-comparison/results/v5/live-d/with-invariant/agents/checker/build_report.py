#!/usr/bin/env python3
"""Independently verify received quadrature results and build a Russian report.

Usage: python3 -B build_report.py --inputs inputs --output .
Only standard-library modules are used; received files are never written.
"""
import argparse
import csv
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import runpy
import sys
from xml.sax.saxutils import escape

METHODS = ('trapezoid', 'simpson')
NS = (10, 20, 40, 80)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def formula_error(method, power, n):
    if method == 'trapezoid':
        return (Fraction(1, 6*n*n) if power == 2 else
                Fraction(1, 3*n*n) - Fraction(1, 30*n**4))
    return Fraction(0) if power == 2 else Fraction(2, 15*n**4)


def rational_quadrature(method, power, n):
    """Independent exact-arithmetic evaluation of all weighted grid samples."""
    if method == 'trapezoid':
        weights = [1] + [2]*(n-1) + [1]
        divisor = 2*n
    else:
        weights = [1] + [4 if i % 2 else 2 for i in range(1, n)] + [1]
        divisor = 3*n
    return sum((w * Fraction(i, n)**power for i, w in enumerate(weights)),
               Fraction(0)) / divisor


def verify(inputs):
    rows, hashes, checks = [], {}, []
    for method in METHODS:
        path = inputs / method / 'result.json'
        raw = path.read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        require(data['method'] == method and data['interval'] == [0, 1],
                f'{method}: incorrect metadata')
        require(sorted((r['power'], r['n']) for r in data['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], f'{method}: invalid grid')
        # run_name is not __main__: load and call integrate without running the
        # received main(), which would overwrite a received result.json.
        integrate = runpy.run_path(str(inputs / method / 'solution.py'))['integrate']
        for row in data['rows']:
            p, n = row['power'], row['n']
            for key in ('estimate', 'exact', 'abs_error'):
                value = row[key]
                require(type(value) in (int, float) and math.isfinite(value),
                        f'{method}, x^{p}, n={n}: invalid {key}')
            exact = Fraction(1, p+1)
            theoretical_error = formula_error(method, p, n)
            independent = rational_quadrature(method, p, n)
            require(independent - exact == theoretical_error,
                    'Exact rational quadrature disagrees with error formula')
            tolerance = 8 * math.ulp(float(independent))
            require(row['exact'] == float(exact), 'Incorrect exact reference')
            require(abs(Fraction(row['estimate']) - independent) <= Fraction(tolerance),
                    f'{method}, x^{p}, n={n}: estimate fails independent verification')
            require(row['abs_error'] == abs(row['estimate'] - row['exact']),
                    'Reported absolute error is inconsistent')
            require(abs(Fraction(row['abs_error']) - theoretical_error) <= Fraction(tolerance),
                    'Reported error disagrees with mathematical formula')
            actual = integrate(lambda x: x**p, 0, 1, n)
            require(actual == row['estimate'], 'Received implementation disagrees with received data')
            rows.append({'method': method, **row})
            checks.append({'method': method, 'power': p, 'n': n,
                           'rational_estimate': str(independent),
                           'theoretical_signed_error': str(theoretical_error),
                           'executed_estimate': actual,
                           'roundoff_tolerance': tolerance})
        require(path.read_bytes() == raw, 'Input changed during verification')
    ratios = {}
    orders = {}
    for method, p in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        errors = {r['n']: r['abs_error'] for r in rows
                  if r['method'] == method and r['power'] == p}
        key = f'{method}_x{p}'
        ratios[key] = [errors[n] / errors[2*n] for n in NS[:-1]]
        orders[key] = [math.log2(r) for r in ratios[key]]
        target = 4 if method == 'trapezoid' else 16
        require(all(abs(r-target) < .015 for r in ratios[key]), 'Unexpected convergence')
    roundoff = all(r['abs_error'] <= 8*math.ulp(r['exact']) for r in rows
                   if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic error exceeds roundoff')
    return rows, {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                  'observed_ratios': ratios, 'observed_orders': orders,
                  'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                  'simpson_x2_exact_within_roundoff': roundoff},
                  'verification_details': checks}


def make_svg(rows):
    series = [('trapezoid', 2, '#1565c0'), ('trapezoid', 4, '#c62828'),
              ('simpson', 4, '#2e7d32')]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="820" height="520" viewBox="0 0 820 520">',
             '<title>Absolute integration error versus subinterval count</title>',
             '<desc>Logarithmic error axis; Simpson x^2 roundoff series omitted.</desc>',
             '<rect width="820" height="520" fill="white"/>',
             '<g font-family="sans-serif" font-size="14">',
             '<text x="100" y="30">Numerical integration on [0,1]</text>']
    # x is linear in n; y is linear in log10(error), from 10^-9 to 10^-2.
    x = lambda n: 100 + (n-10)*680/70
    y = lambda e: 420 - (math.log10(e)+9)*350/7
    for exponent in range(-9, -1):
        yy = y(10**exponent)
        parts += [f'<line x1="100" y1="{yy}" x2="780" y2="{yy}" stroke="#ddd"/>',
                  f'<text x="30" y="{yy+5}">10^{exponent}</text>']
    parts += ['<path d="M100 70 V420 H780" fill="none" stroke="black"/>',
              '<text x="350" y="475">n (subintervals, linear scale)</text>',
              '<text transform="translate(18 350) rotate(-90)">Absolute error (log10 scale)</text>']
    for n in NS:
        parts.append(f'<text x="{x(n)-9}" y="445">{n}</text>')
    for index, (method, power, color) in enumerate(series):
        values = sorted((r for r in rows if r['method'] == method and r['power'] == power),
                        key=lambda r: r['n'])
        require(all(r['abs_error'] > 0 for r in values), 'Nonpositive log-plot error')
        points = ' '.join(f'{x(r["n"]):.6f},{y(r["abs_error"]):.6f}' for r in values)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        for r in values:
            parts.append(f'<circle cx="{x(r["n"]):.6f}" cy="{y(r["abs_error"]):.6f}" r="3" fill="{color}"/>')
        yy = 90 + 24*index
        label = escape(f'{method} x^{power}')
        parts += [f'<line x1="490" y1="{yy}" x2="520" y2="{yy}" stroke="{color}" stroke-width="2"/>',
                  f'<text x="530" y="{yy+5}">{label}</text>']
    return '\n'.join(parts + ['</g>', '</svg>', ''])


def make_report(rows, verification):
    ratios = verification['observed_ratios']
    text = '''# Точность численного интегрирования

Это малый детерминированный численный эксперимент: интегрируются функции x**2 и
x**4 на [0,1] при n = 10, 20, 40, 80 равных подынтервалах, h = 1/n.
Точные эталоны получены аналитически: интеграл x**p равен 1/(p+1), то есть 1/3
и 1/5. Случайные данные и внешние библиотеки не используются.

Составная формула трапеций использует половинные веса концов и единичные веса
внутренних узлов. Составная формула Симпсона при чётном n использует веса
1,4,2,...,4,1 и множитель h/3. Обе полученные реализации изучены и выполнены
через функцию integrate для всех восьми своих строк без запуска main и без
перезаписи входных файлов. Все 16 полученных оценок сохранены в comparison.csv;
их вычисления не заменены независимыми результатами проверяющего.

Независимая проверка использует Fraction: точные рациональные узлы, веса и суммы.
Дополнительно проверены точные формулы знаковой ошибки Q−I:
для trapezoid x**2 это 1/(6n²), для trapezoid x**4 —
1/(3n²)−1/(30n⁴), для simpson x**4 — 2/(15n⁴), для simpson x**2 — ноль.
Рациональные суммы совпали с этими формулами точно. Полученные числа с плавающей
точкой согласуются с рациональным результатом в пределах восьми ulp;
поле abs_error каждой строки в точности равно abs(estimate−exact).
Эталон exact в файлах является ближайшим двоичным представлением точной дроби.

Отношения последовательных полученных ошибок E(n)/E(2n):

'''
    for key, values in ratios.items():
        text += f'- {key}: ' + ', '.join(f'{v:.10f}' for v in values) + '.\n'
    text += '''
При удвоении n ошибка трапеций уменьшается примерно в 4 раза, что подтверждает
второй порядок. Ошибка Симпсона для x**4 уменьшается примерно в 16 раз,
подтверждая четвёртый порядок; наблюдаемый порядок вычисляется как log2 отношения.
Небольшое отклонение отношения трапеций для x**4 от 4 объясняется членом n⁻⁴.

Для x**2 формула Симпсона математически точна, поскольку она точна для всех
многочленов степени не выше трёх. Во всех четырёх полученных строках ошибка
относительно двоичного эталона равна 1.1102230246251565e-16: это округление
арифметики float, а не ошибка квадратуры. Поэтому simpson x**2 исключена из
errors.svg: она отражает уровень округления и не показывает сходимость метода.
График содержит три остальные серии; ось n линейная, ось абсолютной ошибки
логарифмическая. Логарифм нуля нигде не вычисляется.

verification.json содержит SHA256 исходных байтов result.json, результаты
построчной проверки и измеренные отношения. Выводы ограничены этими функциями
и сетками: эксперимент подтверждает ожидаемые порядки здесь, но не доказывает
их для произвольных функций. Для воспроизведения в новом каталоге:
`python3 -B build_report.py --inputs inputs --output new_report`.
'''
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Avoid creating bytecode files while loading received implementations.
    sys.dont_write_bytecode = True
    rows, verification = verify(args.inputs)
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['method', 'power', 'n', 'estimate', 'exact', 'abs_error'])
        writer.writeheader()
        writer.writerows(rows)
    (args.output / 'report.md').write_text(make_report(rows, verification), encoding='utf-8')
    (args.output / 'errors.svg').write_text(make_svg(rows), encoding='utf-8')
    (args.output / 'verification.json').write_text(
        json.dumps(verification, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
