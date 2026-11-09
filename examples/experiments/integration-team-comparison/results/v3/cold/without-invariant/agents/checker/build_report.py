"""Independently verify received quadrature data and generate a Russian report.

Usage: python3 -B build_report.py --inputs inputs --output .
Only Python's standard library is used. Input files are never written.
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


def rational_quadrature(method, power, n):
    """Independent quadrature with exact rational nodes and arithmetic."""
    values = [Fraction(i, n) ** power for i in range(n + 1)]
    if method == 'trapezoid':
        return (sum(values[1:-1]) + (values[0] + values[-1]) / 2) / n
    return (values[0] + values[-1]
            + 4 * sum(values[1:-1:2]) + 2 * sum(values[2:-1:2])) / (3 * n)


def theoretical_error(method, power, n):
    """Signed quadrature minus integral, exact for the assigned polynomials."""
    if method == 'trapezoid':
        return (Fraction(1, 6 * n**2) if power == 2
                else Fraction(1, 3 * n**2) - Fraction(1, 30 * n**4))
    return Fraction(0) if power == 2 else Fraction(2, 15 * n**4)


def verify(inputs):
    rows, checks, hashes = [], [], {}
    for method in METHODS:
        folder = inputs / method
        raw = (folder / 'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        document = json.loads(raw)
        require(document['method'] == method, 'Wrong method')
        require(document['interval'] == [0, 1], 'Wrong interval')
        require(sorted((r['power'], r['n']) for r in document['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], 'Missing or duplicate rows')
        # Execute function definitions without invoking main(), which writes result.json.
        integrate = runpy.run_path(str(folder / 'solution.py'))['integrate']
        for received in document['rows']:
            p, n = received['power'], received['n']
            label = f'{method}, x**{p}, n={n}'
            for key in ('estimate', 'exact', 'abs_error'):
                value = received[key]
                require(type(value) in (int, float) and math.isfinite(value), label + ': invalid ' + key)
            exact = Fraction(1, p + 1)
            quadrature = rational_quadrature(method, p, n)
            formula = theoretical_error(method, p, n)
            require(quadrature - exact == formula, label + ': rational formula mismatch')
            estimate = received['estimate']
            tolerance = 8 * math.ulp(float(exact))
            require(received['exact'] == float(exact), label + ': wrong exact reference')
            require(received['abs_error'] == abs(estimate - received['exact']), label + ': wrong reported error')
            require(abs(Fraction(estimate) - quadrature) <= Fraction(tolerance), label + ': wrong estimate')
            require(abs(Fraction(received['abs_error']) - abs(formula)) <= Fraction(tolerance),
                    label + ': wrong error formula')
            executed = integrate(lambda x: x ** p, 0, 1, n)
            require(executed == estimate, label + ': received implementation disagrees')
            rows.append({'method': method, **{k: received[k] for k in
                        ('power', 'n', 'estimate', 'exact', 'abs_error')}})
            checks.append({'method': method, 'power': p, 'n': n,
                           'rational_quadrature': str(quadrature),
                           'exact_integral': str(exact), 'signed_error_formula': str(formula),
                           'executed_estimate': executed,
                           'actual_error_against_rational_integral': float(abs(Fraction(estimate) - exact)),
                           'roundoff_tolerance': tolerance, 'passed': True})
        require((folder / 'result.json').read_bytes() == raw, 'Input changed during verification')
    ratios = {}
    for method, p in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        errors = {r['n']: r['abs_error'] for r in rows if r['method'] == method and r['power'] == p}
        ratios[f'{method}_x{p}'] = [errors[n] / errors[2*n] for n in NS[:-1]]
    roundoff = all(r['abs_error'] <= 8 * math.ulp(r['exact'])
                   for r in rows if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic error exceeds roundoff')
    for key, values in ratios.items():
        target = 16 if key == 'simpson_x4' else 4
        require(all(abs(v - target) < .015 for v in values), 'Unexpected convergence')
    return rows, {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                  'observed_ratios': ratios,
                  'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                  'simpson_x2_exact_within_roundoff': roundoff},
                  'verification_details': checks,
                  'verification_policy': 'Exact Fraction quadrature and error identities; received floats within 8 ulps of integral; execute both integrate functions without main.'}


def svg_graph(rows):
    # A base-10 logarithmic error axis; only strictly positive series are plotted.
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="850" height="510" viewBox="0 0 850 510">',
             '<title>Quadrature absolute errors on [0,1]</title>',
             '<desc>Log10 error scale; Simpson x^2 omitted because its error is roundoff.</desc>',
             '<rect width="850" height="510" fill="white"/>',
             '<g font-family="sans-serif" font-size="13" fill="#222">',
             '<text x="95" y="28">Absolute integration error: log10 scale</text>']
    def x(n):
        return 95 + 470 * math.log2(n / 10) / 3
    def y(error):
        return 65 + (-2 - math.log10(error)) * 42
    for exponent in range(-9, -1):
        yy = y(10 ** exponent)
        parts.append(f'<path d="M95 {yy} H565" stroke="#ddd"/>')
        parts.append(f'<text x="30" y="{yy + 4}">10^{exponent}</text>')
    for n in NS:
        xx = x(n)
        parts.append(f'<path d="M{xx} 65 V370" stroke="#eee"/>')
        parts.append(f'<text x="{xx-8}" y="393">{n}</text>')
    parts.extend(['<path d="M95 65 V370 H565" fill="none" stroke="black"/>',
                  '<text x="225" y="425">n (subintervals, log2 spacing)</text>',
                  '<text x="20" y="460">Absolute error = |received estimate - received exact reference|</text>'])
    for index, (method, power, color) in enumerate((('trapezoid', 2, '#1565c0'),
                                                   ('trapezoid', 4, '#c62828'),
                                                   ('simpson', 4, '#2e7d32'))):
        series = sorted((r for r in rows if r['method'] == method and r['power'] == power), key=lambda r: r['n'])
        require(all(r['abs_error'] > 0 for r in series), 'Cannot plot nonpositive error')
        points = ' '.join(f"{x(r['n']):.3f},{y(r['abs_error']):.3f}" for r in series)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for r in series:
            parts.append(f'<circle cx="{x(r["n"]):.3f}" cy="{y(r["abs_error"]):.3f}" r="3" fill="{color}"/>')
        yy = 95 + index * 35
        parts.append(f'<path d="M595 {yy} h28" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="632" y="{yy+4}">{escape(method)} x^{power}</text>')
    parts.append('</g></svg>\n')
    return '\n'.join(parts)


def report_text(rows, verification):
    lines = ['# Точность составных квадратур на [0, 1]', '',
             'Это малый детерминированный численный эксперимент: исследуются функции x² и x⁴ '
             'на равномерных сетках с n = 10, 20, 40, 80 подынтервалами. '
             'Точные интегралы получены по первообразной x^(p+1)/(p+1): соответственно 1/3 и 1/5.', '',
             'Метод trapezoid использует половинные веса на концах и единичные внутри, '
             'умножая сумму на h = 1/n. Метод simpson при чётном n использует веса '
             '1, 4, 2, …, 4, 1 и множитель h/3. Оба исходных файла изучены; генератор '
             'исполняет функции integrate без запуска main, чтобы не переписывать входные файлы.', '',
             'Для независимой проверки все узлы и квадратурные суммы вычислены заново '
             'в точной рациональной арифметике Fraction. Проверены все 16 полученных строк: '
             'оценки, точные ссылки, абсолютные ошибки и совпадение с исполненными функциями. '
             'Точные знаковые ошибки Q−I для trapezoid равны 1/(6n²) для x² и '
             '1/(3n²)−1/(30n⁴) для x⁴. Для simpson они равны нулю для x² '
             'и 2/(15n⁴) для x⁴. Все ненулевые ошибки здесь положительны. '
             'Рациональные суммы точно совпали с этими формулами; отклонения полученных '
             'чисел проверены с допуском 8 ulp значения интеграла.', '',
             '| Метод и функция | E(10)/E(20) | E(20)/E(40) | E(40)/E(80) |',
             '|---|---:|---:|---:|']
    for key, values in verification['observed_ratios'].items():
        lines.append('| ' + key + ' | ' + ' | '.join(f'{v:.10f}' for v in values) + ' |')
    lines += ['', 'При удвоении n ошибки trapezoid уменьшаются примерно в 4 раза: '
              'наблюдаемый порядок близок к 2. Для simpson на x⁴ уменьшение примерно '
              'в 16 раз соответствует порядку 4. Небольшое отклонение отношений trapezoid '
              'для x⁴ от 4 объясняется членом n⁻⁴; отношения стремятся к 4.', '',
              'Формула Симпсона точна для многочленов степени до трёх, поэтому для x² '
              'ошибка дискретизации строго равна нулю. В полученных данных абсолютная '
              'ошибка относительно float(1/3) составляет 1.1102230246251565e-16 на всех '
              'сетках: это округление двоичной арифметики, а не сходимость четвёртого порядка. '
              'Само число 1/3 также не представимо точно в float. В verification.json '
              'дополнительно записана ошибка относительно рационального интеграла.', '',
              'На errors.svg показаны три серии с логарифмической шкалой ошибки по основанию 10 '
              'и логарифмическим расположением n по основанию 2. Серия simpson x² исключена '
              'как близкая к нулю и определяемая округлением; log(0) нигде не вычисляется. '
              'В comparison.csv сохранены именно полученные числовые значения без замены '
              'независимыми расчётами. SHA256 исходных байтов result.json и подробности '
              'проверок находятся в verification.json. Выводы относятся к этим двум '
              'полиномам и четырём сеткам, а не ко всем возможным функциям.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows, verification = verify(args.inputs.resolve())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=('method', 'power', 'n', 'estimate', 'exact', 'abs_error'))
        writer.writeheader()
        writer.writerows(rows)
    (output / 'report.md').write_text(report_text(rows, verification), encoding='utf-8')
    (output / 'errors.svg').write_text(svg_graph(rows), encoding='utf-8')
    (output / 'verification.json').write_text(json.dumps(verification, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(f'Verified {len(rows)} rows; wrote four outputs to {output}')


if __name__ == '__main__':
    main()
