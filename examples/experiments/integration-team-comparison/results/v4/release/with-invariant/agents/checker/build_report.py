"""Verify received quadrature artifacts and create a deterministic research report.

Usage: python3 -B build_report.py --inputs inputs --output .
Only Python's standard library is required. Received files are never rewritten.
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
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
NS = (10, 20, 40, 80)
METHODS = ('trapezoid', 'simpson')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rational_quadrature(method, power, n):
    """Independent exact arithmetic on rational nodes and integer weights."""
    samples = [Fraction(i, n) ** power for i in range(n + 1)]
    if method == 'trapezoid':
        return (samples[0] + samples[-1] + 2 * sum(samples[1:-1])) / (2 * n)
    weights = [1 if i in (0, n) else 4 if i % 2 else 2 for i in range(n + 1)]
    return sum(w * v for w, v in zip(weights, samples)) / (3 * n)


def error_formula(method, power, n):
    """Signed quadrature minus integral, exact for these two polynomials."""
    if method == 'trapezoid':
        if power == 2:
            return Fraction(1, 6 * n ** 2)
        return Fraction(1, 3 * n ** 2) - Fraction(1, 30 * n ** 4)
    return Fraction(0) if power == 2 else Fraction(2, 15 * n ** 4)


def verify(inputs):
    rows, hashes, evidence = [], {}, []
    for method in METHODS:
        path = inputs / method / 'result.json'
        raw = path.read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        doc = json.loads(raw)
        require(doc['method'] == method and doc['interval'] == [0, 1], 'Wrong method or interval')
        require(len(doc['rows']) == 8, 'Expected eight received rows per method')
        require(sorted((r['power'], r['n']) for r in doc['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], 'Unexpected row keys')
        # Import without calling main(): execute the received integrate function,
        # preserving both original source and JSON bytes.
        spec = importlib.util.spec_from_file_location('received_' + method, inputs / method / 'solution.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for r in doc['rows']:
            p, n = r['power'], r['n']
            for key in ('estimate', 'exact', 'abs_error'):
                require(type(r[key]) in (int, float) and math.isfinite(r[key]), 'Nonfinite numeric data')
            exact = Fraction(1, p + 1)
            formula = error_formula(method, p, n)
            independent = rational_quadrature(method, p, n)
            require(independent - exact == formula, 'Rational sum disagrees with error formula')
            # Eight ulps of the integral cover node, sum and final multiplication
            # rounding, while remaining tiny relative to all nonzero method errors.
            tolerance = 8 * math.ulp(float(exact))
            require(r['exact'] == float(exact), 'Incorrect exact reference')
            require(abs(r['estimate'] - float(independent)) <= tolerance, 'Incorrect estimate')
            require(r['abs_error'] == abs(r['estimate'] - r['exact']), 'Incorrect reported absolute error')
            require(abs(r['abs_error'] - float(formula)) <= tolerance, 'Incorrect error versus formula')
            rerun = module.integrate(lambda x: x ** p, 0, 1, n)
            require(rerun == r['estimate'], 'Received implementation does not reproduce estimate')
            true_error = abs(Fraction.from_float(r['estimate']) - exact)
            require(abs(true_error - formula) <= Fraction.from_float(tolerance), 'Error against rational integral failed')
            rows.append({'method': method, **r})
            evidence.append({'method': method, 'power': p, 'n': n,
                             'exact_quadrature': str(independent), 'formula_error': str(formula),
                             'error_against_rational_integral': str(true_error),
                             'absolute_tolerance': tolerance, 'implementation_reproduced': True})
    ratios = {}
    for method, p in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        errors = [next(r['abs_error'] for r in rows if
                       (r['method'], r['power'], r['n']) == (method, p, n)) for n in NS]
        ratios[f'{method}_x{p}'] = [a / b for a, b in zip(errors, errors[1:])]
        target = 4 if method == 'trapezoid' else 16
        require(all(abs(value - target) < .015 for value in ratios[f'{method}_x{p}']), 'Unexpected convergence')
    roundoff = all(r['abs_error'] <= 8 * math.ulp(r['exact']) for r in rows
                   if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic exceeds roundoff')
    return rows, {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                  'observed_ratios': ratios, 'conclusions': {'trapezoid_order': 2,
                  'simpson_order': 4, 'simpson_x2_exact_within_roundoff': roundoff},
                  'verification': evidence}


def make_svg(rows):
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    def tag(name):
        return '{http://www.w3.org/2000/svg}' + name
    root = ET.Element(tag('svg'), {'width': '840', 'height': '510', 'viewBox': '0 0 840 510',
                                  'role': 'img', 'aria-labelledby': 'title desc'})
    ET.SubElement(root, tag('title'), {'id': 'title'}).text = 'Absolute quadrature error on [0,1]'
    ET.SubElement(root, tag('desc'), {'id': 'desc'}).text = 'Logarithmic error axis; Simpson x^2 is omitted because it is at roundoff.'
    ET.SubElement(root, tag('rect'), {'width': '840', 'height': '510', 'fill': 'white'})
    def label(x, y, text, **attrs):
        ET.SubElement(root, tag('text'), {'x': str(x), 'y': str(y), 'font-family': 'sans-serif',
                                        'font-size': '14', **attrs}).text = text
    def line(x1, y1, x2, y2, color):
        ET.SubElement(root, tag('line'), dict(x1=str(x1), y1=str(y1), x2=str(x2), y2=str(y2), stroke=color))
    def xpos(n):
        return 90 + 440 * (n - 10) / 70
    def ypos(error):
        require(error > 0, 'Cannot plot nonpositive logarithmic error')
        return 420 - (math.log10(error) + 9) * 55
    label(90, 30, 'Integration error: x^2 and x^4 on [0,1]')
    for exponent in range(-9, -1):
        y = ypos(10 ** exponent)
        line(90, y, 550, y, '#dddddd')
        label(20, y + 5, f'1e{exponent}')
    line(90, 35, 90, 420, 'black')
    line(90, 420, 550, 420, 'black')
    for n in NS:
        line(xpos(n), 420, xpos(n), 426, 'black')
        label(xpos(n), 445, str(n), **{'text-anchor': 'middle'})
    label(280, 480, 'n (subintervals)')
    label(16, 20, 'abs_error (log10)')
    for index, (method, p, color) in enumerate((('trapezoid', 2, '#1769aa'),
                                                ('trapezoid', 4, '#c05200'), ('simpson', 4, '#188038'))):
        data = sorted((r for r in rows if r['method'] == method and r['power'] == p), key=lambda r: r['n'])
        points = ' '.join(f"{xpos(r['n']):.3f},{ypos(r['abs_error']):.3f}" for r in data)
        ET.SubElement(root, tag('polyline'), {'points': points, 'fill': 'none', 'stroke': color, 'stroke-width': '2'})
        for r in data:
            ET.SubElement(root, tag('circle'), {'cx': f"{xpos(r['n']):.3f}", 'cy': f"{ypos(r['abs_error']):.3f}", 'r': '4', 'fill': color})
        y = 100 + index * 35
        line(585, y - 5, 615, y - 5, color)
        label(625, y, f'{method} x^{p}')
    return ET.tostring(root, encoding='unicode', xml_declaration=True) + '\n'


def make_report(rows, verification):
    ratios = verification['observed_ratios']
    lines = [
        '# Точность численного интегрирования', '',
        'Это малый детерминированный численный эксперимент: интегрируются x² и x⁴ на [0,1] '
        'при n = 10, 20, 40, 80 равных подынтервалах. Точные эталоны — 1/3 и 1/5; '
        'случайные данные и внешние библиотеки не используются.', '',
        'Составной метод трапеций (trapezoid) использует половинные веса концов и единичные '
        'веса внутренних узлов. Составной метод Симпсона (simpson) при чётном n использует '
        'веса 1, 4, 2, …, 4, 1 с множителем h/3, где h = 1/n.', '',
        'Независимая проверка всех 16 строк выполнена рациональной арифметикой Fraction: '
        'точные взвешенные суммы сопоставлены с интегралами и формулами ошибки Q−I. '
        'Для трапеций они равны h²/6 для x² и h²/3−h⁴/30 для x⁴. Для Симпсона '
        'получаются 0 для x² и 2h⁴/15 для x⁴. Все ненулевые теоретические ошибки положительны. '
        'Обе полученные реализации также импортированы и исполнены через integrate; '
        'каждое значение воспроизведено без изменения входных файлов.', '',
        'Измеренные отношения E(n)/E(2n), вычисленные непосредственно из полученных ошибок:', '',
        '| Метод и функция | 10→20 | 20→40 | 40→80 |',
        '|---|---:|---:|---:|']
    for key, values in ratios.items():
        lines.append('| ' + key + ' | ' + ' | '.join(f'{value:.10f}' for value in values) + ' |')
    lines += ['', 'Отношения около 4 подтверждают второй порядок трапеций; отношения около 16 '
              'подтверждают четвёртый порядок Симпсона для x⁴. Небольшое отличие отношений '
              'для трапеций x⁴ от 4 объясняет член −h⁴/30. Эти выводы относятся к выбранным '
              'полиномам и сеткам, а не являются статистической оценкой.', '',
              'Симпсон интегрирует x² точно в рациональной арифметике. В полученных данных '
              'ошибка относительно машинного представления 1/3 равна '
              f"{next(r['abs_error'] for r in rows if r['method'] == 'simpson' and r['power'] == 2):.17g}. "
              'Это округление двоичных чисел, а не ошибка дискретизации. Проверка использует '
              'допуск восьми ulp эталона и отдельно сравнивает оценку с рациональным интегралом; '
              'сам эталон 1/3 тоже не представим точно в float.', '',
              'На графике errors.svg показаны три ряда с логарифмической шкалой абсолютной '
              'ошибки и линейной шкалой n. Почти нулевой ряд simpson x² исключён, поскольку '
              'характеризует округление, а не сходимость; логарифм нуля нигде не вычисляется.', '',
              'comparison.csv сохраняет все исходные числовые значения. verification.json содержит '
              'SHA256 фактических байтов входных result.json, отношения ошибок и подробности '
              'рациональной проверки. Воспроизведение: '
              '`python3 -B build_report.py --inputs inputs --output OUTPUT_DIR`.']
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows, verification = verify(args.inputs)
    # Finish every validation before producing a successful verification receipt.
    svg = make_svg(rows)
    report = make_report(rows, verification)
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'comparison.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=('method', 'power', 'n', 'estimate', 'exact', 'abs_error'))
        writer.writeheader()
        writer.writerows(rows)
    (args.output / 'report.md').write_text(report, encoding='utf-8')
    (args.output / 'errors.svg').write_text(svg, encoding='utf-8')
    (args.output / 'verification.json').write_text(json.dumps(verification, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
