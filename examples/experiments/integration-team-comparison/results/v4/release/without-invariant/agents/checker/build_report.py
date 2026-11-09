"""Independently check received quadrature data and build a deterministic report.

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
import xml.etree.ElementTree as ET

NS = (10, 20, 40, 80)
METHODS = ('trapezoid', 'simpson')
SERIES = (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rational_quadrature(method, power, n):
    """Independent quadrature on an exact rational grid, without float samples."""
    values = [Fraction(i, n) ** power for i in range(n + 1)]
    if method == 'trapezoid':
        return (values[0] / 2 + sum(values[1:-1]) + values[-1] / 2) / n
    return (values[0] + values[-1] + 4 * sum(values[1:-1:2])
            + 2 * sum(values[2:-1:2])) / (3 * n)


def signed_error_formula(method, power, n):
    if method == 'trapezoid':
        return Fraction(1, 6 * n**2) if power == 2 else (
            Fraction(1, 3 * n**2) - Fraction(1, 30 * n**4))
    return Fraction(0) if power == 2 else Fraction(2, 15 * n**4)


def verify(inputs):
    rows, hashes, details = [], {}, []
    for method in METHODS:
        raw = (inputs / method / 'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        doc = json.loads(raw)
        require(doc['method'] == method and doc['interval'] == [0, 1], 'Bad metadata')
        require(sorted((r['power'], r['n']) for r in doc['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], 'Missing or duplicate rows')
        # run_path executes definitions, but not the writers guarded by __main__.
        implementation = runpy.run_path(str(inputs / method / 'solution.py'))['integrate']
        for received in doc['rows']:
            p, n = received['power'], received['n']
            label = f'{method} x**{p} n={n}'
            for field in ('estimate', 'exact', 'abs_error'):
                value = received[field]
                require(type(value) in (int, float) and math.isfinite(value), label + ' invalid number')
            exact = Fraction(1, p + 1)
            error = signed_error_formula(method, p, n)
            independent = rational_quadrature(method, p, n)
            require(independent - exact == error, label + ' rational formula mismatch')
            reference = float(exact)
            tolerance = 8 * math.ulp(reference)
            require(received['exact'] == reference, label + ' wrong exact reference')
            require(received['abs_error'] == abs(received['estimate'] - reference), label + ' wrong stored error')
            require(abs(Fraction(received['estimate']) - independent) <= Fraction(tolerance),
                    label + ' estimate disagrees with exact rational quadrature')
            require(abs(received['abs_error'] - float(error)) <= tolerance,
                    label + ' error disagrees with mathematical formula')
            executed = implementation(lambda x: x**p, 0, 1, n)
            require(math.isfinite(executed) and abs(executed - received['estimate']) <= tolerance,
                    label + ' implementation disagrees with received data')
            rows.append({'method': method, **received})
            details.append({'method': method, 'power': p, 'n': n,
                            'rational_estimate': str(independent),
                            'formula_signed_error': str(error),
                            'executed_estimate': executed,
                            'roundoff_tolerance': tolerance})
    data = {(r['method'], r['power'], r['n']): r for r in rows}
    ratios = {}
    for method, p in SERIES:
        values = [data[method, p, n]['abs_error'] for n in NS]
        require(all(v > 0 for v in values), 'Nonpositive convergence error')
        measured = [a / b for a, b in zip(values, values[1:])]
        target = 4 if method == 'trapezoid' else 16
        require(all(abs(r - target) < 0.015 for r in measured), 'Unexpected convergence')
        ratios[f'{method}_x{p}'] = measured
    roundoff = all(data['simpson', 2, n]['abs_error'] <= 8 * math.ulp(1 / 3) for n in NS)
    require(roundoff and len(rows) == 16, 'Final verification failed')
    return rows, {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                  'observed_ratios': ratios,
                  'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                  'simpson_x2_exact_within_roundoff': roundoff},
                  'verification_basis': 'Exact Fraction grid sums, exact signed error formulas, '
                                        'float error arithmetic and execution of both received integrators; '
                                        'tolerance is eight ulps of the exact reference.',
                  'row_checks': details}


def make_svg(rows):
    namespace = 'http://www.w3.org/2000/svg'
    ET.register_namespace('', namespace)
    root = ET.Element(f'{{{namespace}}}svg', {'width': '850', 'height': '500',
                       'viewBox': '0 0 850 500', 'role': 'img',
                       'aria-label': 'Absolute quadrature errors on a logarithmic scale'})

    def element(tag, attrs, text=None):
        node = ET.SubElement(root, f'{{{namespace}}}{tag}', {k: str(v) for k, v in attrs.items()})
        node.text = text
        return node

    element('rect', {'width': 850, 'height': 500, 'fill': 'white'})
    element('text', {'x': 80, 'y': 30, 'font-size': 20}, 'Integration of x^2 and x^4 on [0,1]')
    # Equal horizontal spacing represents doublings of n; errors use log10.
    def x(n):
        return 90 + 150 * math.log2(n / 10)

    def y(error):
        require(error > 0, 'Cannot plot nonpositive error')
        return 420 - (math.log10(error) + 9) * 52

    for exponent in range(-9, -1):
        yy = y(10**exponent)
        element('line', {'x1': 90, 'x2': 540, 'y1': yy, 'y2': yy, 'stroke': '#ddd'})
        element('text', {'x': 22, 'y': yy + 5, 'font-size': 13}, f'1e{exponent}')
    element('line', {'x1': 90, 'x2': 540, 'y1': 420, 'y2': 420, 'stroke': 'black'})
    element('line', {'x1': 90, 'x2': 90, 'y1': 56, 'y2': 420, 'stroke': 'black'})
    for n in NS:
        element('text', {'x': x(n) - 8, 'y': 445}, str(n))
    element('text', {'x': 175, 'y': 480}, 'Subintervals n (log2 scale)')
    element('text', {'x': 25, 'y': 52}, 'Absolute error (log10)')
    for j, ((method, power), color) in enumerate(zip(SERIES, ('#1769aa', '#b34b00', '#16803a'))):
        selected = sorted((r for r in rows if r['method'] == method and r['power'] == power),
                          key=lambda r: r['n'])
        points = ' '.join(f"{x(r['n']):.3f},{y(r['abs_error']):.3f}" for r in selected)
        element('polyline', {'points': points, 'fill': 'none', 'stroke': color, 'stroke-width': 2})
        for r in selected:
            element('circle', {'cx': x(r['n']), 'cy': y(r['abs_error']), 'r': 3, 'fill': color})
        element('line', {'x1': 590, 'x2': 625, 'y1': 100 + j * 35,
                         'y2': 100 + j * 35, 'stroke': color, 'stroke-width': 2})
        element('text', {'x': 635, 'y': 105 + j * 35}, f'{method} x^{power}')
    return ET.tostring(root, encoding='unicode', xml_declaration=True) + '\n'


def make_report(rows, verification):
    ratios = verification['observed_ratios']
    ratio_lines = '\n'.join('| ' + name + ' | ' + ' | '.join(f'{v:.9f}' for v in values) + ' |'
                            for name, values in ratios.items())
    last = {(r['method'], r['power']): r['abs_error'] for r in rows if r['n'] == 80}
    return f'''# Проверка точности численного интегрирования

Это малый детерминированный численный эксперимент: исследованы функции x² и x⁴ на [0,1] при n = 10, 20, 40, 80 равных подынтервалах. Точные эталоны получены из первообразных: ∫x² dx = 1/3 и ∫x⁴ dx = 1/5 на указанном отрезке. Случайных данных и внешних библиотек нет.

Составной метод trapezoid использует веса 1/2 на концах и 1 внутри сетки с шагом h=1/n. Составной метод simpson при чётном n использует веса 1,4,2,…,4,1 и множитель h/3. Обе полученные реализации изучены и выполнены через функцию integrate для всех восьми соответствующих строк; их функции записи файлов не вызывались. Все 16 исходных строк сохранены в comparison.csv без замены чисел независимыми расчётами.

Независимая проверка использует точные рациональные узлы i/n и суммы с Fraction. Для знаковой ошибки «оценка минус интеграл» точные формулы таковы: trapezoid(x²) = h²/6; trapezoid(x⁴) = h²/3 − h⁴/30; simpson(x²) = 0; simpson(x⁴) = 2h⁴/15. Рациональные суммы в точности совпали с этими формулами. Проверены также эталоны, арифметика записанной абсолютной ошибки и согласие с выполненными реализациями. Допуск для сравнения с рациональным результатом — восемь ulp машинного эталона; подробности и SHA256 исходных байтов находятся в verification.json.

Измеренные отношения E(n)/E(2n), рассчитанные именно из полученных abs_error:

| Серия | 10→20 | 20→40 | 40→80 |
|---|---:|---:|---:|
{ratio_lines}

Отношения около 4 подтверждают второй порядок метода трапеций; отношения около 16 подтверждают четвёртый порядок Симпсона для x⁴. Для трапеций на x⁴ небольшое отклонение от 4 объясняется членом −h⁴/30. При n=80 ошибки составили {last['trapezoid', 2]:.12g} для trapezoid x², {last['trapezoid', 4]:.12g} для trapezoid x⁴ и {last['simpson', 4]:.12g} для simpson x⁴.

Метод Симпсона математически точен для многочленов степени не выше трёх. Для x² все четыре записанные ошибки равны {last['simpson', 2]:.17g}: это округление двоичной арифметики и представления 1/3, а не ошибка квадратурной формулы. Поэтому порядок по этой серии не оценивается. Серия simpson x² с почти нулевой ошибкой опущена на errors.svg; нулевой аргумент логарифма нигде не вычисляется. Вертикальная ось графика логарифмическая (log10 абсолютной ошибки), горизонтальная — log2(n). Числа в таблице отражают машинные оценки, поэтому ошибки относительно округлённого float-эталона могут слегка отличаться от ошибок относительно точных рациональных интегралов. Выводы относятся к этим двум гладким многочленам и четырём сеткам.
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows, verification = verify(args.inputs.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'comparison.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=('method', 'power', 'n', 'estimate', 'exact', 'abs_error'))
        writer.writeheader()
        writer.writerows(rows)
    (args.output / 'verification.json').write_text(
        json.dumps(verification, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (args.output / 'report.md').write_text(make_report(rows, verification), encoding='utf-8')
    (args.output / 'errors.svg').write_text(make_svg(rows), encoding='utf-8')


if __name__ == '__main__':
    main()
