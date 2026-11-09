"""Verify received quadrature artifacts and build a deterministic research report.

Usage: python3 -B build_report.py --inputs inputs --output .
Only the Python standard library is used; input files are never written.
"""
import argparse
import csv
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import runpy
from xml.sax.saxutils import escape

NS = (10, 20, 40, 80)
METHODS = ('trapezoid', 'simpson')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def theoretical_error(method, power, n):
    if method == 'trapezoid':
        return Fraction(1, 6*n*n) if power == 2 else Fraction(1, 3*n*n) - Fraction(1, 30*n**4)
    return Fraction(0) if power == 2 else Fraction(2, 15*n**4)


def rational_quadrature(method, power, n):
    """Independent exact-arithmetic weighted sum at rational grid points."""
    total = Fraction(0)
    for i in range(n + 1):
        if method == 'trapezoid':
            weight = Fraction(1, 2) if i in (0, n) else 1
        else:
            weight = 1 if i in (0, n) else (4 if i % 2 else 2)
        total += weight * Fraction(i, n)**power
    return total / (n if method == 'trapezoid' else 3*n)


def verify(inputs):
    rows, hashes, evidence = [], {}, []
    for method in METHODS:
        raw = (inputs / method / 'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        document = json.loads(raw)
        require(document['method'] == method and document['interval'] == [0, 1], 'Wrong method or interval')
        received = document['rows']
        require(sorted((r['power'], r['n']) for r in received) == [(p, n) for p in (2, 4) for n in NS], 'Wrong row coverage')
        # A non-main run executes definitions, then we call the received integrator.
        # Neither implementation's file-writing main() is invoked.
        integrate = runpy.run_path(str(inputs / method / 'solution.py'))['integrate']
        for row in received:
            p, n = row['power'], row['n']
            for key in ('estimate', 'exact', 'abs_error'):
                value = row[key]
                require(type(value) in (int, float) and math.isfinite(value), 'Nonfinite or nonnumeric input')
            exact = Fraction(1, p + 1)
            formula = theoretical_error(method, p, n)
            rational = rational_quadrature(method, p, n)
            label = f'{method} x**{p}, n={n}'
            require(rational - exact == formula, label + ': rational sum disagrees with formula')
            tolerance = 8 * math.ulp(float(exact))
            require(row['exact'] == float(exact), label + ': wrong exact reference')
            require(row['abs_error'] == abs(row['estimate'] - row['exact']), label + ': wrong reported subtraction')
            # Fraction.from_float compares against the true rational integral,
            # avoiding loss of the distinction between 1/3 and its float value.
            signed_error = Fraction.from_float(float(row['estimate'])) - exact
            require(abs(signed_error - formula) <= Fraction.from_float(tolerance), label + ': incorrect mathematical error')
            require(abs(row['abs_error'] - float(formula)) <= tolerance, label + ': incorrect error magnitude')
            executed = integrate(lambda x: x**p, 0, 1, n)
            require(executed == row['estimate'], label + ': implementation does not reproduce received estimate')
            rows.append({'method': method, **row})
            evidence.append({'method': method, 'power': p, 'n': n,
                             'formula_error': str(formula), 'rational_estimate': str(rational),
                             'executed_estimate': executed,
                             'error_against_true_integral': float(abs(signed_error)),
                             'roundoff_tolerance': tolerance})
    ratios = {}
    for method, p, target in (('trapezoid', 2, 4), ('trapezoid', 4, 4), ('simpson', 4, 16)):
        data = {r['n']: r['abs_error'] for r in rows if r['method'] == method and r['power'] == p}
        values = [data[n] / data[2*n] for n in NS[:-1]]
        require(all(abs(v-target) < .015 for v in values), 'Unexpected convergence ratio')
        ratios[f'{method}_x{p}'] = values
    roundoff = all(r['abs_error'] <= 8*math.ulp(1/3) for r in rows if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic error exceeds roundoff')
    verification = {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                    'observed_ratios': ratios,
                    'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                    'simpson_x2_exact_within_roundoff': roundoff},
                    'verification_details': evidence}
    return rows, verification


def svg_graph(rows):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="540" viewBox="0 0 900 540">',
             '<title>Absolute quadrature errors on [0,1]</title>',
             '<desc>Logarithmic error axis; quadratic Simpson roundoff series omitted.</desc>',
             '<rect width="900" height="540" fill="white"/>',
             '<g font-family="sans-serif" font-size="14" fill="#222">',
             '<text x="90" y="30" font-size="20">Numerical integration: absolute error</text>']
    def x(n):
        return 95 + 570*math.log2(n/10)/3
    def y(error):
        return 70 + (-2-math.log10(error))*350/7
    for exponent in range(-9, -1):
        yy = y(10**exponent)
        parts.append(f'<path d="M95 {yy:.3f} H665" stroke="#ddd"/>')
        parts.append(f'<text x="80" y="{yy+5:.3f}" text-anchor="end">10^{exponent}</text>')
    for n in NS:
        xx = x(n)
        parts.append(f'<path d="M{xx:.3f} 70 V420" stroke="#eee"/>')
        parts.append(f'<text x="{xx:.3f}" y="445" text-anchor="middle">{n}</text>')
    parts += ['<path d="M95 70 V420 H665" fill="none" stroke="#222"/>',
              '<text x="380" y="482" text-anchor="middle">Subintervals n (log2 scale)</text>',
              '<text transform="translate(23 250) rotate(-90)" text-anchor="middle">Absolute error (log10 scale)</text>']
    for index, (method, p, color) in enumerate((('trapezoid', 2, '#1763a6'), ('trapezoid', 4, '#c65315'), ('simpson', 4, '#168348'))):
        data = sorted((r for r in rows if r['method'] == method and r['power'] == p), key=lambda r: r['n'])
        require(all(r['abs_error'] > 0 for r in data), 'Logarithmic graph needs positive errors')
        points = ' '.join(f"{x(r['n']):.3f},{y(r['abs_error']):.3f}" for r in data)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for r in data:
            parts.append(f'<circle cx="{x(r["n"]):.3f}" cy="{y(r["abs_error"]):.3f}" r="4" fill="{color}"/>')
        ly = 100 + 32*index
        parts.append(f'<path d="M690 {ly} H720" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="730" y="{ly+5}">{escape(method)} x^{p}</text>')
    parts += ['<text x="95" y="518">Simpson x^2: roundoff only; omitted from the error graph.</text>', '</g></svg>']
    return '\n'.join(parts) + '\n'


def report(rows, verification):
    text = '''# Проверка точности квадратур

Это малый детерминированный численный эксперимент: на отрезке [0,1] интегрируются x² и x⁴ при n = 10, 20, 40, 80 равных подынтервалах, h = 1/n. Точные эталоны получены аналитически: ∫x² dx = 1/3, ∫x⁴ dx = 1/5.

Составная формула трапеций использует половинные веса на концах и единичные внутри сетки. Составная формула Симпсона при чётном n использует веса 1,4,2,…,4,1 и множитель h/3. Все 16 полученных строк сохранены в comparison.csv без замены исходных чисел. Обе полученные реализации исполнены через integrate; их функции main, записывающие файлы, не вызывались. Результаты исполнения совпали с полученными оценками.

Независимая проверка использует Fraction: узлы i/n, веса и суммы вычисляются точно в рациональной арифметике. Для знаковой ошибки Q−I получены формулы:

- trapezoid, x²: h²/6;
- trapezoid, x⁴: h²/3 − h⁴/30;
- simpson, x²: 0;
- simpson, x⁴: 2h⁴/15.

Рациональные суммы совпали с формулами точно. Полученные оценки и ошибки проверены с абсолютным допуском восемь ulp машинного представления эталона (не более 4.45·10⁻¹⁶). Также отдельно проверены поле exact и вычисление abs(estimate−exact). verification.json содержит SHA256 фактических байтов result.json, отношения ошибок и свидетельства проверки каждой строки.

При удвоении n наблюдаются следующие отношения E(n)/E(2n) и порядки log₂ отношения:

| Метод, степень | 10→20 | 20→40 | 40→80 | Наблюдаемые порядки |
|---|---:|---:|---:|---|
'''
    for key, ratios in verification['observed_ratios'].items():
        text += '| ' + key + ' | ' + ' | '.join(f'{v:.10f}' for v in ratios) + ' | ' + ', '.join(f'{math.log2(v):.7f}' for v in ratios) + ' |\n'
    text += '''
Трапеции показывают второй порядок: ошибка уменьшается примерно в 4 раза. Для x⁴ поправка четвёртого порядка объясняет небольшое отличие отношения от 4. Симпсон для x⁴ показывает четвёртый порядок и уменьшение ошибки примерно в 16 раз. Эти выводы относятся к указанным гладким полиномам и сеткам.

Для x² формула Симпсона математически точна. Во всех четырёх полученных строках ошибка относительно машинного значения 1/3 равна 5.551115123125783e-17: это округление двоичной арифметики, а не ошибка квадратуры. Само значение float(1/3) также отличается от истинного рационального числа; проверка через Fraction учитывает это различие. Отношения таких малых ошибок не характеризуют порядок сходимости.

На errors.svg ось ошибки логарифмическая (log10), ось n имеет шкалу log2. Показаны три серии: trapezoid x², trapezoid x⁴ и simpson x⁴. Почти нулевая серия simpson x² исключена, чтобы округление не смешивалось с ошибкой дискретизации; log(0) нигде не вычисляется.

Воспроизведение средствами только стандартной библиотеки Python:

```sh
python3 -B build_report.py --inputs inputs --output .
```

Вместо точки можно указать новый каталог. Генератор не изменяет входные артефакты и завершится с ошибкой при неуспешной проверке, до создания отчётных файлов.
'''
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows, verification = verify(args.inputs)
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=('method', 'power', 'n', 'estimate', 'exact', 'abs_error'))
        writer.writeheader()
        writer.writerows(rows)
    (args.output / 'verification.json').write_text(json.dumps(verification, indent=2) + '\n', encoding='utf-8')
    (args.output / 'errors.svg').write_text(svg_graph(rows), encoding='utf-8')
    (args.output / 'report.md').write_text(report(rows, verification), encoding='utf-8')


if __name__ == '__main__':
    main()
