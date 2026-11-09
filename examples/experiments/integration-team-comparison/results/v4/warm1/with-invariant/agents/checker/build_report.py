"""Independently verify received quadratures and generate deterministic reports.

Usage: python3 -B build_report.py --inputs inputs --output .
Only Python's standard library is used; received files are never written.
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


def require(condition, message):
    if not condition:
        raise ValueError(message)


def formula_error(method, power, n):
    if method == 'trapezoid':
        return Fraction(1, 6*n*n) if power == 2 else Fraction(1, 3*n*n) - Fraction(1, 30*n**4)
    return Fraction(0) if power == 2 else Fraction(2, 15*n**4)


def rational_quadrature(method, power, n):
    """Independent exact arithmetic sum, using rational nodes i/n."""
    if method == 'trapezoid':
        total = sum((Fraction(i, n)**power for i in range(1, n)), Fraction(0))
        return (total + Fraction(1, 2)) / n
    total = sum(((4 if i % 2 else 2) * Fraction(i, n)**power
                 for i in range(1, n)), Fraction(1))
    return total / (3*n)


def verify(inputs):
    rows, hashes, evidence = [], {}, []
    for method in METHODS:
        directory = inputs / method
        raw = (directory / 'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        document = json.loads(raw)
        require(document['method'] == method and document['interval'] == [0, 1], 'Wrong method/interval')
        require(sorted((r['power'], r['n']) for r in document['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], 'Missing, duplicate or unexpected rows')
        # Execute definitions without invoking main(), which would overwrite inputs.
        implementation = runpy.run_path(str(directory / 'solution.py'), run_name='received_implementation')
        for received in document['rows']:
            power, n = received['power'], received['n']
            context = f'{method} x**{power} n={n}'
            for field in ('estimate', 'exact', 'abs_error'):
                value = received[field]
                require(type(value) in (int, float) and math.isfinite(value), context + ': invalid number')
            exact = Fraction(1, power + 1)
            theoretical_error = formula_error(method, power, n)
            independent = rational_quadrature(method, power, n)
            require(independent - exact == theoretical_error, context + ': exact rational formula mismatch')
            estimate = received['estimate']
            tolerance = 8 * math.ulp(float(exact))
            require(received['exact'] == float(exact), context + ': incorrect reference')
            require(received['abs_error'] == abs(estimate - received['exact']), context + ': incorrect recorded error')
            rational_residual = abs(Fraction.from_float(float(estimate)) - independent)
            require(rational_residual <= Fraction.from_float(tolerance), context + ': estimate disagrees with exact sum')
            require(abs(received['abs_error'] - float(theoretical_error)) <= tolerance,
                    context + ': measured error disagrees with formula')
            executed = implementation['integrate'](lambda x: x**power, 0, 1, n)
            require(executed == estimate, context + ': implementation disagrees with artifact')
            rows.append({'method': method, **received})
            evidence.append({'method': method, 'power': power, 'n': n,
                             'rational_estimate': str(independent),
                             'theoretical_error': str(theoretical_error),
                             'estimate_error_against_rational_integral': float(abs(Fraction.from_float(float(estimate))-exact)),
                             'roundoff_residual_from_exact_quadrature': float(rational_residual),
                             'executed_estimate': executed})
        require((directory / 'result.json').read_bytes() == raw, 'Input changed during execution')
    ratios = {}
    for method, power in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        errors = {r['n']: r['abs_error'] for r in rows if r['method'] == method and r['power'] == power}
        values = [errors[n] / errors[2*n] for n in NS[:-1]]
        target = 4 if method == 'trapezoid' else 16
        require(all(abs(v-target) < .015 for v in values), 'Unexpected convergence')
        ratios[f'{method}_x{power}'] = values
    roundoff = all(r['abs_error'] <= 8*math.ulp(1/3) for r in rows
                   if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic exceeds roundoff tolerance')
    return rows, {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                  'observed_ratios': ratios,
                  'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                  'simpson_x2_exact_within_roundoff': roundoff},
                  'verification_tolerance': '8 ulps of the floating-point exact reference',
                  'row_evidence': evidence}


def svg_graph(rows):
    ns = 'http://www.w3.org/2000/svg'
    ET.register_namespace('', ns)
    root = ET.Element(f'{{{ns}}}svg', {'width': '840', 'height': '530', 'viewBox': '0 0 840 530',
                                     'role': 'img', 'aria-labelledby': 'title description'})
    def node(tag, attrs=None, text=None):
        e = ET.SubElement(root, f'{{{ns}}}{tag}', attrs or {})
        e.text = text
        return e
    node('title', {'id': 'title'}, 'Quadrature absolute errors on [0,1]')
    node('desc', {'id': 'description'}, 'Logarithmic error axis; Simpson x^2 omitted because its error is roundoff.')
    node('rect', {'width': '840', 'height': '530', 'fill': 'white'})
    def label(x, y, text, **attrs):
        node('text', {'x': str(x), 'y': str(y), 'font-family': 'sans-serif', 'font-size': '14', **attrs}, text)
    label(90, 28, 'Quadrature errors: [0,1], n = 10,20,40,80')
    def x(n): return 100 + 160*math.log2(n/10)
    def y(error): return 400 - 58*(math.log10(error)+9)
    for exponent in range(-9, -1):
        yy = y(10**exponent)
        node('line', {'x1': '100', 'x2': '580', 'y1': str(yy), 'y2': str(yy), 'stroke': '#dddddd'})
        label(85, yy+5, f'1e{exponent}', **{'text-anchor': 'end'})
    node('path', {'d': 'M100 52 V400 H580', 'fill': 'none', 'stroke': 'black'})
    for n in NS:
        label(x(n), 424, str(n), **{'text-anchor': 'middle'})
    label(320, 450, 'Subintervals n (log2 scale)')
    label(20, 285, 'Absolute error (log10 scale)', transform='rotate(-90 20 285)')
    for index, (method, power, color) in enumerate((('trapezoid', 2, '#1565c0'),
                                                   ('trapezoid', 4, '#c62828'),
                                                   ('simpson', 4, '#2e7d32'))):
        series = sorted((r for r in rows if r['method'] == method and r['power'] == power), key=lambda r: r['n'])
        require(all(r['abs_error'] > 0 for r in series), 'Logarithm requires positive errors')
        points = ' '.join(f"{x(r['n']):.6f},{y(r['abs_error']):.6f}" for r in series)
        node('polyline', {'points': points, 'fill': 'none', 'stroke': color, 'stroke-width': '2'})
        for r in series:
            node('circle', {'cx': f"{x(r['n']):.6f}", 'cy': f"{y(r['abs_error']):.6f}", 'r': '3', 'fill': color})
        yy = 110 + index*35
        node('line', {'x1': '610', 'x2': '640', 'y1': str(yy), 'y2': str(yy), 'stroke': color, 'stroke-width': '2'})
        label(650, yy+5, f'{method} x^{power}')
    label(100, 490, 'Simpson x^2: roundoff only; omitted from the logarithmic plot.')
    return ET.tostring(root, encoding='unicode') + '\n'


def report(rows, verification):
    text = '''# Точность квадратур на [0,1]

Это малый детерминированный численный эксперимент: исследованы функции x² и x⁴
на равномерных сетках с n = 10, 20, 40, 80 подынтервалами. Точные интегралы,
полученные по первообразной x^(p+1)/(p+1), равны 1/3 и 1/5 соответственно.
Случайность, внешние сервисы и сторонние библиотеки не использовались.

Составная формула trapezoid использует веса 1/2 на концах и 1 внутри,
с множителем h=1/n. Составная формула simpson при чётном n использует
веса 1,4,2,…,4,1 с множителем h/3. Погрешности «квадратура минус интеграл»
в точной арифметике здесь положительны или равны нулю:

- trapezoid, x²: h²/6;
- trapezoid, x⁴: h²/3 − h⁴/30;
- simpson, x²: 0;
- simpson, x⁴: 2h⁴/15.

Формулы проверены независимым суммированием всех взвешенных рациональных
значений (i/n)^p через Fraction. Обе полученные реализации также исполнены
через их функцию integrate без запуска записывающего main. Проверены все
16 оценок, точные ссылки и записанные абсолютные ошибки; исходные числа
перенесены в comparison.csv без подмены независимыми расчётами. Допуск
сравнения с рациональной квадратурой — восемь ulp представления интеграла;
совпадение с повторным исполнением и вычисление записанной ошибки проверены точно.

Отношения соседних полученных ошибок E(n)/E(2n) и наблюдаемые порядки log₂ отношения:

| Метод и функция | Отношения при удвоении n | Порядки |
|---|---|---|
'''
    for key, ratios in verification['observed_ratios'].items():
        text += f"| {key} | {', '.join(f'{v:.10f}' for v in ratios)} | {', '.join(f'{math.log2(v):.8f}' for v in ratios)} |\n"
    text += '''
Таким образом, наблюдается второй порядок у трапеций (отношение около 4)
и четвёртый у Симпсона для x⁴ (около 16). Поправка −h⁴/30 объясняет небольшое
отклонение отношений трапеций для x⁴ от 4. Вывод относится к этим полиномам
и четырём сеткам, а не к произвольным функциям или пределу машинной точности.

Для x² формула Симпсона математически точна. Во всех четырёх полученных строках
оценка равна 0.33333333333333337, а ошибка относительно float(1/3) равна
5.551115123125783e-17. Само число 1/3 также округлено в двоичной арифметике:
расстояние оценки до рационального 1/3 составляет примерно 3.70e-17.
Это округление вычислений, а не ошибка усечения; с уменьшением шага оно
здесь не уменьшается. Для этой серии измерение порядка не имеет смысла.

График errors.svg использует логарифмическую шкалу абсолютной ошибки
и шкалу log₂ для n. Почти нулевая серия simpson x² исключена как уровень
округления, чтобы показать сходимость остальных трёх серий. Нулевые значения
не логарифмируются и не заменяются искусственной положительной величиной.
Хеши исходных result.json и подробные свидетельства проверки сохранены
в verification.json. Повторение: python3 -B build_report.py --inputs inputs --output OUTPUT.
'''
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    rows, verification = verify(args.inputs)
    # Build every artifact before writing a successful verification receipt.
    graph = svg_graph(rows)
    narrative = report(rows, verification)
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'comparison.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=('method', 'power', 'n', 'estimate', 'exact', 'abs_error'))
        writer.writeheader()
        writer.writerows(rows)
    (args.output / 'report.md').write_text(narrative, encoding='utf-8')
    (args.output / 'errors.svg').write_text(graph, encoding='utf-8')
    (args.output / 'verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
