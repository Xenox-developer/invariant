#!/usr/bin/env python3
"""Verify received quadratures independently and reproduce four report artifacts.

Usage: python3 -B build_report.py --inputs inputs --output .
Only Python's standard library is required. Received files are never written.
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


def formula_error(method, power, n):
    if method == 'trapezoid':
        return (Fraction(1, 6*n*n) if power == 2 else
                Fraction(1, 3*n*n) - Fraction(1, 30*n**4))
    return Fraction(0) if power == 2 else Fraction(2, 15*n**4)


def rational_quadrature(method, power, n):
    """Sum exact rational samples with integer weights, independent of floats."""
    samples = [Fraction(i, n)**power for i in range(n+1)]
    if method == 'trapezoid':
        return (samples[0] + samples[-1] + 2*sum(samples[1:-1])) / (2*n)
    return (samples[0] + samples[-1] +
            4*sum(samples[1:-1:2]) + 2*sum(samples[2:-1:2])) / (3*n)


def verify(inputs):
    rows, hashes, evidence = [], {}, []
    for method in METHODS:
        folder = inputs / method
        raw = (folder / 'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        require(data['method'] == method and data['interval'] == [0, 1],
                f'{method}: wrong method or interval')
        require(len(data['rows']) == 8, f'{method}: expected eight rows')
        require(sorted((r['power'], r['n']) for r in data['rows']) ==
                sorted((p, n) for p in POWERS for n in NS),
                f'{method}: missing or duplicate grid points')
        # Import without running main(), which would overwrite a received file.
        spec = importlib.util.spec_from_file_location('received_' + method,
                                                       folder / 'solution.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for received in data['rows']:
            p, n = received['power'], received['n']
            label = f'{method} x**{p} n={n}'
            for key in ('estimate', 'exact', 'abs_error'):
                value = received[key]
                require(type(value) in (int, float) and math.isfinite(value),
                        f'{label}: invalid {key}')
            exact = Fraction(1, p+1)
            theoretical_error = formula_error(method, p, n)
            rational_estimate = rational_quadrature(method, p, n)
            require(rational_estimate - exact == theoretical_error,
                    f'{label}: rational sum disagrees with mathematical formula')
            estimate = received['estimate']
            # Eight ulps allow sample, product and summation rounding while
            # remaining much smaller than the smallest nonzero truncation error.
            tolerance = 8 * math.ulp(float(exact))
            require(received['exact'] == float(exact), f'{label}: wrong reference')
            require(received['abs_error'] == abs(estimate - received['exact']),
                    f'{label}: inconsistent reported absolute error')
            require(abs(Fraction(estimate) - rational_estimate) <= Fraction(tolerance),
                    f'{label}: estimate disagrees with exact rational quadrature')
            require(abs(Fraction(received['abs_error']) - theoretical_error) <=
                    Fraction(tolerance), f'{label}: error disagrees with formula')
            executed = module.integrate(lambda x: x**p, 0, 1, n)
            require(executed == estimate, f'{label}: implementation disagrees with artifact')
            rows.append({'method': method, **received})
            evidence.append({'method': method, 'power': p, 'n': n,
                             'rational_estimate': str(rational_estimate),
                             'theoretical_error': str(theoretical_error),
                             'roundoff_tolerance': tolerance,
                             'implementation_matches': True})
        require((folder / 'result.json').read_bytes() == raw,
                f'{method}: input changed during verification')
    ratios = {}
    for method, p in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        errors = [next(r['abs_error'] for r in rows if
                       (r['method'], r['power'], r['n']) == (method, p, n)) for n in NS]
        ratios[f'{method}_x{p}'] = [a/b for a, b in zip(errors, errors[1:])]
        target = 4 if method == 'trapezoid' else 16
        require(all(abs(r-target) < .015 for r in ratios[f'{method}_x{p}']),
                f'{method} x**{p}: unexpected convergence ratios')
    verification = {
        'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
        'observed_ratios': ratios,
        'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                        'simpson_x2_exact_within_roundoff': True},
        'verification_details': evidence,
        'verification_method': 'Exact Fraction quadrature and error formulas; '
                               'execute received integrate; check reported float errors; '
                               'absolute tolerance eight ulps of exact reference.'}
    return rows, verification


def svg_graph(rows):
    """Logarithmic error plot; the near-zero quadratic Simpson series is omitted."""
    left, top, width, height = 95, 60, 540, 350
    xmin, xmax, ymin, ymax = math.log2(10), math.log2(80), -9, -2
    def x(n):
        return left + width*(math.log2(n)-xmin)/(xmax-xmin)
    def y(error):
        require(error > 0, 'Log plot requires strictly positive error')
        return top + height*(ymax-math.log10(error))/(ymax-ymin)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="940" height="510" viewBox="0 0 940 510">',
             '<title>Absolute quadrature errors on [0,1]</title>',
             '<desc>Logarithmic error axis. Simpson x^2 omitted because its residual is roundoff.</desc>',
             '<rect width="940" height="510" fill="white"/>',
             '<g font-family="sans-serif" font-size="14" fill="#222">',
             '<text x="95" y="30" font-size="20">Numerical integration: absolute error</text>']
    for exponent in range(ymin, ymax+1):
        yy = y(10**exponent)
        parts.extend([f'<line x1="{left}" y1="{yy:.3f}" x2="{left+width}" y2="{yy:.3f}" stroke="#ddd"/>',
                      f'<text x="82" y="{yy+5:.3f}" text-anchor="end">1e{exponent}</text>'])
    for n in NS:
        xx = x(n)
        parts.extend([f'<line x1="{xx:.3f}" y1="{top}" x2="{xx:.3f}" y2="{top+height}" stroke="#eee"/>',
                      f'<text x="{xx:.3f}" y="435" text-anchor="middle">{n}</text>'])
    parts.extend(['<text x="360" y="468" text-anchor="middle">n (log2 scale)</text>',
                  '<text transform="translate(22 235) rotate(-90)" text-anchor="middle">Absolute error (log10 scale)</text>'])
    series = [('trapezoid', 2, '#1565c0'), ('trapezoid', 4, '#c62828'),
              ('simpson', 4, '#2e7d32')]
    for index, (method, power, color) in enumerate(series):
        data = sorted((r for r in rows if r['method'] == method and r['power'] == power),
                      key=lambda r: r['n'])
        points = ' '.join(f"{x(r['n']):.3f},{y(r['abs_error']):.3f}" for r in data)
        label = escape(f'{method} x^{power}')
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"><title>{label}</title></polyline>')
        for r in data:
            parts.append(f'<circle cx="{x(r["n"]):.3f}" cy="{y(r["abs_error"]):.3f}" r="4" fill="{color}"/>')
        yy = 100 + index*35
        parts.append(f'<line x1="675" y1="{yy}" x2="710" y2="{yy}" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="720" y="{yy+5}">{label}</text>')
    parts.extend(['<text x="95" y="498">Simpson x^2: roundoff residual; omitted from the error plot.</text>',
                  '</g></svg>'])
    return '\n'.join(parts) + '\n'


def research_report(rows, verification):
    text = '''# Проверка точности численного интегрирования

Это небольшой детерминированный численный эксперимент: вычисляются интегралы
x² и x⁴ на [0, 1] при n = 10, 20, 40, 80 равных подынтервалах, h = 1/n.
Точные эталоны получены по первообразным: ∫x² dx = 1/3, ∫x⁴ dx = 1/5.
Исходные результаты двух независимых исполнителей сохранены без изменений;
comparison.csv содержит все 16 полученных строк с исходными числовыми значениями.

Составная формула трапеций имеет веса 1/2 на концах и 1 внутри, общий множитель h.
Составная формула Симпсона использует веса 1, 4, 2, 4, …, 1 и множитель h/3;
для неё требуется чётное n. Обе полученные реализации просмотрены и их функции
integrate выполнены для всех восьми соответствующих комбинаций параметров.

Независимая проверка использует Fraction: значения (i/n)^p, веса и суммы
вычислены рационально, без округления. Эти суммы в точности совпали с формулами
знаковой ошибки Q − I:

- trapezoid, x²: 1/(6n²);
- trapezoid, x⁴: 1/(3n²) − 1/(30n⁴);
- simpson, x²: 0;
- simpson, x⁴: 2/(15n⁴).

Для трапеций формулы следуют из сумм квадратов и четвёртых степеней либо из
формулы Эйлера — Маклорена. Формулу Симпсона также можно получить как
S_n = (4T_n − T_(n/2))/3: член порядка n⁻² сокращается.
Полученные оценки и ошибки проверены против этих рациональных эталонов с
допуском 8 ulp от машинного представления точного интеграла (около 4.44e−16
для x² и 2.22e−16 для x⁴). Это существенно меньше минимальной ненулевой
ошибки дискретизации. Поле abs_error отдельно проверено как модуль разности
полученной оценки и полученного машинного эталона; подмена расчётов не выполнялась.
Хеши SHA-256 фактических байтов result.json записаны в verification.json.

Отношения последовательных полученных ошибок E(n)/E(2n):

| Метод и функция | 10→20 | 20→40 | 40→80 | Наблюдаемый порядок log₂(отношения) |
|---|---:|---:|---:|---|
'''
    for key, ratios in verification['observed_ratios'].items():
        orders = ', '.join(f'{math.log2(r):.6f}' for r in ratios)
        text += f'| {key} | ' + ' | '.join(f'{r:.10f}' for r in ratios) + f' | {orders} |\n'
    text += '''
Трапеции демонстрируют второй порядок: при удвоении n ошибка уменьшается
примерно в 4 раза. Для x⁴ небольшая поправка n⁻⁴ объясняет отклонение отношения
от 4. Симпсон на x⁴ демонстрирует четвёртый порядок и уменьшение примерно в 16 раз.
Эти выводы относятся к указанным полиномам и диапазону сеток.

Ошибки при n = 10 и n = 80:

| Метод | Функция | E(10) | E(80) |
|---|---|---:|---:|
'''
    for method in METHODS:
        for p in POWERS:
            errors = [next(r['abs_error'] for r in rows if
                           (r['method'], r['power'], r['n']) == (method, p, n)) for n in (10, 80)]
            text += f'| {method} | x^{p} | {errors[0]:.12e} | {errors[1]:.12e} |\n'
    text += '''
Симпсон интегрирует x² точно в рациональной арифметике, поскольку точен для
полиномов степени не выше трёх. Во всех четырёх полученных строках остаток
равен 1.1102230246251565e−16; он связан с округлением узлов, арифметических
операций и представления 1/3 в binary64. Это не ошибка дискретизации и не
основание оценивать порядок по этой серии. Сам эталон 1/3 также округлён.

На errors.svg показаны три серии с логарифмической шкалой абсолютной ошибки
и шкалой log₂ для n. Почти нулевая серия simpson x² опущена, чтобы не смешивать
ошибку округления с ошибкой дискретизации. Логарифм нуля не вычисляется.
Генератор использует только стандартную библиотеку Python; повторный запуск:
`python3 -B build_report.py --inputs inputs --output NEW_DIRECTORY`.
'''
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Prevent imports of received implementations from creating bytecode inputs.
    sys.dont_write_bytecode = True
    rows, verification = verify(args.inputs)
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'comparison.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=('method', 'power', 'n', 'estimate', 'exact', 'abs_error'))
        writer.writeheader()
        writer.writerows(rows)
    (args.output / 'report.md').write_text(research_report(rows, verification), encoding='utf-8')
    (args.output / 'errors.svg').write_text(svg_graph(rows), encoding='utf-8')
    (args.output / 'verification.json').write_text(
        json.dumps(verification, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    print(f'Verified {len(rows)} received rows; wrote four artifacts to {args.output}')


if __name__ == '__main__':
    main()
