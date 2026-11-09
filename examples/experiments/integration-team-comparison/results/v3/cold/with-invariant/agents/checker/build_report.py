"""Independently verify received quadrature data and build a deterministic report.

Usage: python3 -B build_report.py --inputs inputs --output .
Only Python's standard library is required. Received artifacts are read only.
"""
import argparse
import csv
from fractions import Fraction
import hashlib
import io
import json
import math
from pathlib import Path
import runpy
from xml.sax.saxutils import escape

NS = (10, 20, 40, 80)
METHODS = ('trapezoid', 'simpson')
OUTPUTS = ('comparison.csv', 'report.md', 'errors.svg', 'verification.json')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def formula_error(method, power, n):
    if method == 'trapezoid':
        return Fraction(1, 6*n*n) if power == 2 else Fraction(1, 3*n*n) - Fraction(1, 30*n**4)
    return Fraction(0) if power == 2 else Fraction(2, 15*n**4)


def rational_quadrature(method, power, n):
    """Independent exact arithmetic over rational nodes and integer weights."""
    total = Fraction(0)
    for i in range(n + 1):
        if method == 'trapezoid':
            weight = 1 if i in (0, n) else 2
        else:
            weight = 1 if i in (0, n) else (4 if i % 2 else 2)
        total += weight * Fraction(i, n)**power
    return total / ((2 if method == 'trapezoid' else 3) * n)


def verify(inputs):
    rows, hashes, evidence = [], {}, []
    for method in METHODS:
        data_path = inputs / method / 'result.json'
        raw = data_path.read_bytes()
        source_path = inputs / method / 'solution.py'
        source_bytes = source_path.read_bytes()
        # Use a non-main name: execute definitions, never the received file writer.
        implementation = runpy.run_path(str(source_path), run_name='received_' + method)
        integrate = implementation['integrate']
        data = json.loads(raw)
        require(data['method'] == method and data['interval'] == [0, 1], 'Wrong method or interval')
        require(sorted((r['power'], r['n']) for r in data['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], 'Missing or duplicate rows')
        hashes[method] = hashlib.sha256(raw).hexdigest()
        for row in data['rows']:
            p, n = row['power'], row['n']
            for key in ('estimate', 'exact', 'abs_error'):
                value = row[key]
                require(type(value) in (int, float) and math.isfinite(value), 'Nonfinite or nonnumeric value')
            exact = Fraction(1, p + 1)
            error = formula_error(method, p, n)
            rational = rational_quadrature(method, p, n)
            require(rational - exact == error, 'Exact quadrature disagrees with error formula')
            computed = integrate(lambda x: x**p, 0, 1, n)
            require(computed == row['estimate'], 'Received implementation does not reproduce estimate')
            require(row['exact'] == float(exact), 'Incorrect exact reference')
            require(row['abs_error'] == abs(row['estimate'] - row['exact']), 'Incorrect reported error')
            tolerance = 8 * math.ulp(float(exact))
            true_error = Fraction.from_float(float(row['estimate'])) - exact
            require(abs(row['estimate'] - float(rational)) <= tolerance, 'Incorrect quadrature estimate')
            require(abs(float(true_error - error)) <= tolerance, 'Incorrect mathematical error')
            require(abs(row['abs_error'] - float(error)) <= tolerance, 'Reported error disagrees with formula')
            rows.append({'method': method, **row})
            evidence.append({'method': method, 'power': p, 'n': n,
                             'formula_signed_error': str(error),
                             'rational_quadrature': str(rational),
                             'estimate_minus_exact_rational': str(true_error),
                             'roundoff_tolerance': tolerance,
                             'implementation_reproduced': True})
        require(data_path.read_bytes() == raw and source_path.read_bytes() == source_bytes,
                'Received artifacts changed during verification')
    ratios = {}
    for method, p, target in (('trapezoid', 2, 4), ('trapezoid', 4, 4), ('simpson', 4, 16)):
        errors = {r['n']: r['abs_error'] for r in rows if r['method'] == method and r['power'] == p}
        values = [errors[n] / errors[2*n] for n in NS[:-1]]
        require(all(abs(v - target) < .015 for v in values), 'Unexpected convergence')
        ratios[f'{method}_x{p}'] = values
    roundoff = all(r['abs_error'] <= 8*math.ulp(1/3) for r in rows
                   if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic is not exact within roundoff')
    verification = {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                    'observed_ratios': ratios,
                    'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                    'simpson_x2_exact_within_roundoff': roundoff},
                    'verification_details': evidence,
                    'verification_policy': 'Exact Fraction quadrature and signed error formulas; execute received integrate without main; tolerate at most eight ulps of exact reference.'}
    return rows, verification


def svg_graph(rows):
    # Horizontal positions follow log2(n); vertical positions follow log10(error).
    def x(n):
        return 90 + math.log2(n/10)*160
    def y(error):
        require(error > 0, 'Cannot plot nonpositive error')
        return 370 - (math.log10(error)+9)*44
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="820" height="460" viewBox="0 0 820 460" role="img" aria-labelledby="title desc">',
             '<title id="title">Quadrature absolute errors</title>',
             '<desc id="desc">Logarithmic error axis, logarithmic n axis. Simpson x^2 omitted because its error is roundoff.</desc>',
             '<rect width="820" height="460" fill="white"/>',
             '<g font-family="sans-serif" font-size="13" fill="#222">',
             '<text x="90" y="25" font-size="18">Integration on [0,1]: measured absolute error</text>']
    for exponent in range(-9, -1):
        yy = y(10**exponent)
        parts.append(f'<path d="M90 {yy:.3f} H570" stroke="#ddd"/><text x="35" y="{yy+4:.3f}">1e{exponent}</text>')
    for n in NS:
        xx = x(n)
        parts.append(f'<path d="M{xx:.3f} 62 V370" stroke="#ddd"/><text x="{xx-8:.3f}" y="393">{n}</text>')
    parts += ['<path d="M90 62 V370 H570" fill="none" stroke="black"/>',
              '<text x="245" y="425">n subintervals (log2 scale)</text>',
              '<text transform="translate(18,310) rotate(-90)">Absolute error (log10 scale)</text>']
    for index, (method, p, color) in enumerate((('trapezoid', 2, '#1764ab'), ('trapezoid', 4, '#b54b00'), ('simpson', 4, '#19804b'))):
        series = sorted((r for r in rows if r['method'] == method and r['power'] == p), key=lambda r: r['n'])
        points = ' '.join(f"{x(r['n']):.3f},{y(r['abs_error']):.3f}" for r in series)
        label = escape(f'{method} x^{p}')
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"><title>{label}</title></polyline>')
        for r in series:
            parts.append(f'<circle cx="{x(r["n"]):.3f}" cy="{y(r["abs_error"]):.3f}" r="3" fill="{color}"/>')
        yy = 100 + index*30
        parts.append(f'<path d="M600 {yy} h25" stroke="{color}" stroke-width="2"/><text x="635" y="{yy+4}">{label}</text>')
    return '\n'.join(parts + ['</g>', '</svg>', ''])


def report(rows, verification):
    lines = ['# Точность численного интегрирования', '',
             'Это небольшой детерминированный численный эксперимент: интегрируем x² и x⁴ на [0,1] при n = 10, 20, 40, 80 равных подынтервалах. Точные эталоны: ∫x² dx = 1/3 и ∫x⁴ dx = 1/5 на этом отрезке. Все вычисления и проверка используют только стандартную библиотеку Python.', '',
             'Составная формула трапеций использует шаг h = 1/n и веса 1/2 на концах, 1 внутри. Составная формула Симпсона использует чётное n и веса 1,4,2,…,4,1 с множителем h/3. Обе полученные реализации выполнены через integrate без запуска main, который записывает файлы. Все 16 исходных оценок воспроизведены без изменения входных файлов. Дополнительно выполнено независимое суммирование с точными рациональными узлами и весами (Fraction).', '',
             'Для знаковой ошибки Q−I точные формулы здесь: трапеции, x²: 1/(6n²); трапеции, x⁴: 1/(3n²)−1/(30n⁴); Симпсон, x²: 0; Симпсон, x⁴: 2/(15n⁴). Рациональные суммы совпали с этими формулами точно. Полученные числа и ошибки согласуются с ними в пределах восьми ulp эталонного float (не более 4.45·10⁻¹⁶).', '',
             'Отношения E(n)/E(2n), вычисленные именно из полученных abs_error, и наблюдаемые порядки log₂ отношения:', '',
             '| Метод и функция | Отношения для 10→20, 20→40, 40→80 | Наблюдаемые порядки |',
             '|---|---|---|']
    for key, values in verification['observed_ratios'].items():
        lines.append('| ' + key + ' | ' + ', '.join(f'{v:.10f}' for v in values) + ' | ' + ', '.join(f'{math.log2(v):.8f}' for v in values) + ' |')
    lines += ['', 'При удвоении n ошибка трапеций уменьшается примерно в 4 раза (порядок 2), ошибка Симпсона для x⁴ — примерно в 16 раз (порядок 4). Отклонение отношения трапеций для x⁴ от 4 объясняется членом n⁻⁴. Это подтверждение на четырёх сетках для двух полиномов; общий порядок метода не выводится только из этого эксперимента.', '',
              'Симпсон интегрирует x² точно в рациональной арифметике. В полученных данных ошибка для всех четырёх n равна 1.1102230246251565·10⁻¹⁶ и обусловлена округлением двоичных float. Поле abs_error измеряет разность с float(1/3), а не с бесконечно точным рациональным 1/3; эти величины могут различаться на округление эталона. Точные рациональные отклонения сохранены в verification.json. Отношение таких почти нулевых ошибок не показывает порядок сходимости.', '',
              'На графике errors.svg вертикальная ось логарифмическая (log10 ошибки), горизонтальная — log2(n). Серия simpson x² исключена как находящаяся на уровне машинного округления; log(0) нигде не вычисляется. Три остальные серии используют непосредственно полученные ошибки. comparison.csv сохраняет все 16 исходных числовых строк без подмены расчётов.', '',
              'Воспроизведение: `python3 -B build_report.py --inputs inputs --output .` (либо новый каталог вместо точки). SHA-256 исходных result.json и подробности независимых проверок находятся в verification.json.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Check everything before writing a successful receipt or any report.
    rows, verification = verify(args.inputs)
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=('method', 'power', 'n', 'estimate', 'exact', 'abs_error'))
    writer.writeheader()
    writer.writerows(rows)
    contents = {'comparison.csv': stream.getvalue(), 'report.md': report(rows, verification),
                'errors.svg': svg_graph(rows),
                'verification.json': json.dumps(verification, ensure_ascii=False, indent=2, allow_nan=False) + '\n'}
    args.output.mkdir(parents=True, exist_ok=True)
    for name in OUTPUTS:
        (args.output / name).write_text(contents[name], encoding='utf-8', newline='')
    print(f'Verified {len(rows)} received rows; wrote four outputs to {args.output}')


if __name__ == '__main__':
    main()
