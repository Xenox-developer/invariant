"""Independently verify received quadrature results and generate four artifacts.

Run: python3 -B build_report.py --inputs inputs --output .
Only Python's standard library is used; input files are never written.
"""
import argparse
import csv
from fractions import Fraction
import hashlib
import importlib.util
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape

NS = (10, 20, 40, 80)
METHODS = ('trapezoid', 'simpson')
FIELDS = ('method', 'power', 'n', 'estimate', 'exact', 'abs_error')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def formula(method, power, n):
    """Signed quadrature errors (positive for these convex polynomials)."""
    if method == 'trapezoid':
        return Fraction(1, 6*n*n) if power == 2 else Fraction(1, 3*n*n) - Fraction(1, 30*n**4)
    return Fraction(0) if power == 2 else Fraction(2, 15*n**4)


def rational_quadrature(method, power, n):
    """Independent exact rational grid sum, without either received integrator."""
    values = [Fraction(i, n)**power for i in range(n+1)]
    if method == 'trapezoid':
        return (values[0]/2 + sum(values[1:-1]) + values[-1]/2)/n
    return sum(v * (1 if i in (0, n) else 4 if i % 2 else 2)
               for i, v in enumerate(values)) / (3*n)


def verify(inputs):
    rows, hashes, evidence = [], {}, []
    for method in METHODS:
        folder = inputs / method
        raw = (folder/'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        doc = json.loads(raw)
        require(doc['method'] == method and doc['interval'] == [0, 1], 'Wrong method or interval')
        require(sorted((r['power'], r['n']) for r in doc['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], 'Incorrect row coverage')
        spec = importlib.util.spec_from_file_location('received_' + method, folder/'solution.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # Execute definitions only, never main or file writes.
        for received in doc['rows']:
            p, n = received['power'], received['n']
            for key in ('estimate', 'exact', 'abs_error'):
                v = received[key]
                require(type(v) in (int, float) and math.isfinite(v), 'Nonfinite or invalid numeric value')
            exact = Fraction(1, p+1)
            theoretical_error = formula(method, p, n)
            independent = rational_quadrature(method, p, n)
            require(independent == exact + theoretical_error, 'Rational sum disagrees with error formula')
            estimate = received['estimate']
            tolerance = 8 * math.ulp(float(exact))
            require(received['exact'] == float(exact), 'Wrong exact reference')
            require(abs(Fraction(estimate) - independent) <= Fraction(tolerance), 'Estimate fails exact rational check')
            require(received['abs_error'] == abs(estimate - received['exact']), 'Reported subtraction error is wrong')
            require(abs(received['abs_error'] - float(theoretical_error)) <= tolerance, 'Error fails mathematical formula')
            executed = module.integrate(lambda x: x**p, 0, 1, n)
            require(math.isfinite(executed) and abs(executed-estimate) <= tolerance, 'Implementation disagrees with received estimate')
            rows.append({'method': method, **{k: received[k] for k in FIELDS[1:]}})
            evidence.append({'method': method, 'power': p, 'n': n,
                             'rational_quadrature': str(independent),
                             'theoretical_signed_error': str(theoretical_error),
                             'executed_estimate': executed,
                             'absolute_tolerance': tolerance})
    ratios = {}
    for method, p, target in (('trapezoid', 2, 4), ('trapezoid', 4, 4), ('simpson', 4, 16)):
        errors = {r['n']: r['abs_error'] for r in rows if r['method'] == method and r['power'] == p}
        values = [errors[n]/errors[n*2] for n in NS[:-1]]
        require(all(abs(v-target) < .015 for v in values), 'Unexpected convergence ratio')
        ratios[f'{method}_x{p}'] = values
    roundoff = all(r['abs_error'] <= 8*math.ulp(1/3) for r in rows if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff and len(rows) == 16, 'Verification incomplete')
    return rows, {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                  'observed_ratios': ratios,
                  'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                  'simpson_x2_exact_within_roundoff': roundoff},
                  'verification': 'Exact Fraction grid sums, exact polynomial error formulas, and execution of both received integrate functions; tolerance eight ulps of the exact reference.',
                  'row_evidence': evidence}


def make_svg(rows):
    # Linear n axis, logarithmic error axis; only strictly positive errors enter log10.
    left, top, width, height = 90, 50, 540, 340
    ymin, ymax = -9, -2
    x = lambda n: left + (n-10)/70*width
    y = lambda e: top + (ymax-math.log10(e))/(ymax-ymin)*height
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="840" height="470" viewBox="0 0 840 470">',
             '<title>Quadrature absolute errors on [0,1]</title>',
             '<desc>Linear subinterval count n; logarithmic absolute error. Simpson x^2 omitted because it is at roundoff.</desc>',
             '<rect width="840" height="470" fill="white"/>',
             '<g font-family="sans-serif" font-size="13" fill="#222">',
             '<text x="90" y="25">Absolute error versus subinterval count</text>']
    for exponent in range(ymin, ymax+1):
        yy = y(10**exponent)
        parts.extend([f'<path d="M {left} {yy:.3f} H {left+width}" stroke="#ddd"/>',
                      f'<text x="78" y="{yy+4:.3f}" text-anchor="end">10^{exponent}</text>'])
    for n in NS:
        xx = x(n)
        parts.extend([f'<path d="M {xx:.3f} {top} V {top+height}" stroke="#eee"/>',
                      f'<text x="{xx:.3f}" y="413" text-anchor="middle">{n}</text>'])
    parts.extend(['<text x="330" y="445">Subintervals n</text>',
                  '<text transform="translate(22 260) rotate(-90)">Absolute error (log10 scale)</text>'])
    for index, (method, p, color) in enumerate((('trapezoid', 2, '#1769aa'), ('trapezoid', 4, '#bb4c00'), ('simpson', 4, '#13824a'))):
        selected = sorted((r for r in rows if r['method'] == method and r['power'] == p), key=lambda r: r['n'])
        require(all(r['abs_error'] > 0 for r in selected), 'Cannot plot nonpositive errors')
        points = ' '.join(f"{x(r['n']):.3f},{y(r['abs_error']):.3f}" for r in selected)
        label = escape(f'{method} x^{p}')
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"><title>{label}</title></polyline>')
        for r in selected:
            parts.append(f'<circle cx="{x(r["n"]):.3f}" cy="{y(r["abs_error"]):.3f}" r="3" fill="{color}"/>')
        yy = 95 + index*30
        parts.extend([f'<path d="M 650 {yy} h 20" stroke="{color}" stroke-width="2"/>',
                      f'<text x="680" y="{yy+4}">{label}</text>'])
    parts.append('</g></svg>\n')
    return '\n'.join(parts)


def make_report(rows, verification):
    ratios = verification['observed_ratios']
    lines = ['# Точность численного интегрирования', '',
             'Это малый детерминированный численный эксперимент: на [0,1] интегрируются x² и x⁴ при n = 10, 20, 40, 80 равных подынтервалах. Точные интегралы равны 1/3 и 1/5 соответственно. Случайные данные не используются.', '',
             'Составной метод трапеций (trapezoid) использует веса 1/2 на концах и 1 внутри, умноженные на h = 1/n. Составной метод Симпсона (simpson), при чётном n, использует веса 1,4,2,…,4,1 и множитель h/3.', '',
             'Все 16 полученных строк проверены независимо: сумма по рациональной сетке вычислена через Fraction и сопоставлена с точными формулами. Обе полученные реализации integrate также исполнены для каждой строки без запуска main. В comparison.csv сохранены именно полученные численные значения, без замены независимыми расчётами.', '',
             'Для знаковой ошибки Q−I точные формулы таковы: trapezoid x²: 1/(6n²); trapezoid x⁴: 1/(3n²)−1/(30n⁴); simpson x²: 0; simpson x⁴: 2/(15n⁴). Рациональные суммы совпали с этими формулами точно. Полученные оценки согласуются с ними в пределах восьми ulp точного эталона; отдельно проверены эталоны и вычисление abs_error = |estimate−exact|.', '',
             'Измеренные отношения ошибок E(n)/E(2n):', '',
             '| Ряд | 10→20 | 20→40 | 40→80 |',
             '|---|---:|---:|---:|']
    for key, values in ratios.items():
        lines.append('| ' + key + ' | ' + ' | '.join(f'{v:.10f}' for v in values) + ' |')
    lines.extend(['', 'Отношения около 4 подтверждают второй порядок метода трапеций. Отношения около 16 подтверждают четвёртый порядок Симпсона для x⁴. Небольшое отклонение трапеций для x⁴ от 4 объясняется членом порядка n⁻⁴. Наблюдения относятся к этим полиномам и данной сетке.', '',
                  'Симпсон математически точен для x², как и для всех полиномов степени не выше трёх. В полученных строках ошибка относительно машинного представления 1/3 составляет ' + ', '.join(f'{r["abs_error"]:.6g}' for r in rows if r['method'] == 'simpson' and r['power'] == 2) + '. Это округление двоичной арифметики, а не погрешность квадратурной формулы; сам эталон 1/3 также представлен приближённо.', '',
                  'В errors.svg показаны три ряда с линейной осью n и логарифмической осью абсолютной ошибки. Почти нулевой ряд simpson x² исключён: он отражает лишь округление и не показывает сходимость метода. Логарифм нуля нигде не вычисляется. verification.json содержит SHA256 исходных байтов result.json, отношения ошибок и подробные свидетельства проверки.', '',
                  'Воспроизведение (только стандартная библиотека Python): python3 -B build_report.py --inputs inputs --output НОВЫЙ_КАТАЛОГ.'])
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    rows, verification = verify(args.inputs.resolve())
    output = args.output.resolve()
    # Refuse output paths that would place generated artifacts inside received inputs.
    require(not output.is_relative_to(args.inputs.resolve()), 'Output must be outside inputs')
    output.mkdir(parents=True, exist_ok=True)
    with (output/'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (output/'report.md').write_text(make_report(rows, verification), encoding='utf-8')
    (output/'errors.svg').write_text(make_svg(rows), encoding='utf-8')
    (output/'verification.json').write_text(json.dumps(verification, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
