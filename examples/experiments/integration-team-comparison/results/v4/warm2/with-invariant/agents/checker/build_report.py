#!/usr/bin/env python3
"""Independently verify received quadratures and generate deterministic reports.

Usage: python3 -B build_report.py --inputs inputs --output .
Only Python's standard library is used. Received artifacts are read-only.
"""
import argparse
import csv
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

NS = (10, 20, 40, 80)
METHODS = ('trapezoid', 'simpson')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def formula_error(method, power, n):
    if method == 'trapezoid':
        return Fraction(1, 6*n*n) if power == 2 else Fraction(1, 3*n*n)-Fraction(1, 30*n**4)
    return Fraction(0) if power == 2 else Fraction(2, 15*n**4)


def rational_quadrature(method, power, n):
    # Exact rational grid, powers, weights and sum: independent of float code.
    values = [Fraction(i, n)**power for i in range(n+1)]
    if method == 'trapezoid':
        return (sum(values[1:-1], Fraction(0))+(values[0]+values[-1])/2)/n
    return (values[0]+values[-1]+sum(
        ((4 if i % 2 else 2)*values[i] for i in range(1, n)), Fraction(0)))/(3*n)


def verify(inputs):
    rows, hashes, evidence = [], {}, []
    for method in METHODS:
        folder = inputs / method
        received = (folder/'result.json').read_bytes()
        hashes[method] = hashlib.sha256(received).hexdigest()
        doc = json.loads(received)
        require(doc['method'] == method and doc['interval'] == [0, 1], 'Invalid metadata')
        require(len(doc['rows']) == 8, 'Expected eight rows per method')
        require(sorted((r['power'], r['n']) for r in doc['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], 'Invalid or duplicate grid')
        # Execute the actual implementation definitions without invoking main(),
        # which would overwrite result.json. No import cache is created.
        source = folder/'solution.py'
        namespace = {'__name__': 'received_'+method, '__file__': str(source)}
        exec(compile(source.read_bytes(), str(source), 'exec'), namespace)
        integrate = namespace['integrate']
        for row in doc['rows']:
            p, n = row['power'], row['n']
            for key in ('estimate', 'exact', 'abs_error'):
                require(type(row[key]) in (int, float) and math.isfinite(row[key]), 'Nonfinite number')
            exact = Fraction(1, p+1)
            error = formula_error(method, p, n)
            independent = rational_quadrature(method, p, n)
            require(independent == exact+error, 'Rational sum contradicts error formula')
            tolerance = 8*math.ulp(float(exact))
            require(row['exact'] == float(exact), 'Incorrect exact reference')
            require(abs(row['estimate']-float(independent)) <= tolerance, 'Incorrect estimate')
            require(row['abs_error'] == abs(row['estimate']-row['exact']), 'Incorrect reported error')
            # Compare the binary float estimate to the truly exact fraction too.
            true_signed_error = Fraction.from_float(float(row['estimate']))-exact
            require(abs(true_signed_error-error) <= Fraction.from_float(tolerance), 'Error formula mismatch')
            computed = integrate(lambda x: x**p, 0, 1, n)
            require(computed == row['estimate'], 'Received implementation does not reproduce estimate')
            rows.append({'method': method, **row})
            evidence.append({'method': method, 'power': p, 'n': n,
                             'rational_estimate': str(independent),
                             'theoretical_error': str(error),
                             'true_signed_float_error': str(true_signed_error),
                             'implementation_reproduced': True,
                             'absolute_tolerance': tolerance})
    ratios = {}
    for method, p in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        errors = {r['n']: r['abs_error'] for r in rows if r['method'] == method and r['power'] == p}
        ratios[f'{method}_x{p}'] = [errors[n]/errors[2*n] for n in NS[:-1]]
        target = 4 if method == 'trapezoid' else 16
        require(all(abs(r-target) < .015 for r in ratios[f'{method}_x{p}']), 'Unexpected convergence')
    roundoff = all(r['abs_error'] <= 8*math.ulp(r['exact']) for r in rows
                   if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic exceeds roundoff')
    return rows, {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                  'observed_ratios': ratios,
                  'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                  'simpson_x2_exact_within_roundoff': roundoff},
                  'verification_details': evidence}


def svg_graph(rows):
    uri = 'http://www.w3.org/2000/svg'
    ET.register_namespace('', uri)
    root = ET.Element('{'+uri+'}svg', {'width': '860', 'height': '520', 'viewBox': '0 0 860 520',
                                      'role': 'img', 'aria-labelledby': 'title desc'})
    def element(tag, attrs, text=None):
        node = ET.SubElement(root, '{'+uri+'}'+tag, {k: str(v) for k, v in attrs.items()})
        node.text = text
        return node
    element('title', {'id': 'title'}, 'Integration errors on [0,1]')
    element('desc', {'id': 'desc'}, 'Absolute errors on a logarithmic vertical scale. Simpson x^2 omitted: roundoff only.')
    element('rect', {'width': 860, 'height': 520, 'fill': 'white'})
    def label(x, y, text, **attrs):
        return element('text', {'x': x, 'y': y, 'font-family': 'sans-serif', 'font-size': 14, **attrs}, text)
    def xcoord(n): return 100+math.log2(n/10)*155
    def ycoord(error): return 420-(math.log10(error)+9)*60
    label(100, 28, 'Absolute integration error on [0,1]')
    for exponent in range(-9, -1):
        y = ycoord(10**exponent)
        element('line', {'x1': 100, 'y1': y, 'x2': 565, 'y2': y, 'stroke': '#ddd'})
        label(85, y+5, f'1e{exponent}', **{'text-anchor': 'end'})
    element('path', {'d': 'M100 0 V420 H565', 'transform': 'translate(0 0)', 'stroke': '#333', 'fill': 'none'})
    for n in NS:
        label(xcoord(n), 444, str(n), **{'text-anchor': 'middle'})
    label(310, 475, 'n (subintervals; log2 scale)', **{'text-anchor': 'middle'})
    label(20, 230, 'abs_error (log10 scale)', transform='rotate(-90 20 230)', **{'text-anchor': 'middle'})
    for index, (method, power, color) in enumerate((('trapezoid', 2, '#1967b3'), ('trapezoid', 4, '#c53b25'), ('simpson', 4, '#16803c'))):
        data = sorted((r for r in rows if r['method'] == method and r['power'] == power), key=lambda r: r['n'])
        require(all(r['abs_error'] > 0 for r in data), 'Cannot plot nonpositive logarithmic error')
        points = ' '.join(f"{xcoord(r['n']):.3f},{ycoord(r['abs_error']):.3f}" for r in data)
        element('polyline', {'points': points, 'stroke': color, 'stroke-width': 2, 'fill': 'none'})
        for r in data:
            element('circle', {'cx': f"{xcoord(r['n']):.3f}", 'cy': f"{ycoord(r['abs_error']):.3f}", 'r': 4, 'fill': color})
        y = 90+index*32
        element('line', {'x1': 600, 'y1': y, 'x2': 625, 'y2': y, 'stroke': color, 'stroke-width': 2})
        label(635, y+5, f'{method} x^{power}')
    return ET.tostring(root, encoding='unicode')+'\n'


def report(rows, verification):
    text = '''# Точность численного интегрирования на [0,1]

Это малый детерминированный численный эксперимент, а не статистическое исследование.
Исследованы функции x² и x⁴ на равномерных сетках с n = 10, 20, 40, 80
подынтервалами. Точные эталоны из первообразных: ∫₀¹x² dx = 1/3 и
∫₀¹x⁴ dx = 1/5. Использована только стандартная библиотека Python.

Составной метод трапеций (trapezoid) интегрирует линейную интерполяцию:
Tₙ = h[(f(0)+f(1))/2 + Σᵢ₌₁ⁿ⁻¹ f(ih)], h=1/n.
Составной метод Симпсона (simpson) использует параболы на парах интервалов:
Sₙ = (h/3)[f(0)+f(1)+4Σнечёт f(ih)+2Σчёт f(ih)], n чётно.

Независимая проверка выполнена точными рациональными суммами Fraction на сетке i/n.
Для каждого из 16 значений проверены точный эталон, оценка, записанная абсолютная
ошибка и совпадение с запуском функции integrate из полученной реализации.
Главные программы входных файлов не запускались: они перезаписывают result.json.
Полученные данные сохранены без изменений в comparison.csv; SHA256 исходных байтов
и подробности проверки находятся в verification.json.

Знаковые ошибки в точной арифметике положительны (кроме точного нуля):
Tₙ(x²)−1/3 = 1/(6n²), Tₙ(x⁴)−1/5 = 1/(3n²)−1/(30n⁴),
Sₙ(x²)−1/3 = 0, Sₙ(x⁴)−1/5 = 2/(15n⁴).
Проверка оценок и формул допускает лишь восемь единиц последнего разряда
плавающего эталона; равенство записанной ошибки abs(estimate−exact) проверено точно.

## Наблюдаемые отношения ошибок E(n)/E(2n)

| Метод и функция | 10→20 | 20→40 | 40→80 | Измеренный порядок log₂ отношения |
|---|---:|---:|---:|---|
'''
    for key, ratios in verification['observed_ratios'].items():
        text += '| '+key+' | '+' | '.join(f'{r:.10f}' for r in ratios)+' | '+', '.join(f'{math.log2(r):.6f}' for r in ratios)+' |\n'
    text += '''
Отношения около 4 подтверждают второй порядок трапеций, около 16 — четвёртый
порядок Симпсона для x⁴. Небольшое отклонение трапеций для x⁴ объясняется членом n⁻⁴.
Вывод ограничен этими полиномами и четырьмя сетками; он согласуется с точными формулами.

Симпсон интегрирует x² точно в рациональной арифметике, поскольку точен для
полиномов степени не выше трёх. Во всех четырёх полученных строках ошибка
относительно float(1/3) равна 5.551115123125783e-17 (один ulp).
Это округление двоичных чисел, узлов, степеней и сумм, а не ошибка дискретизации.
Сам float(1/3) также отличается от рационального 1/3; проверка учитывает это отдельно
через Fraction.from_float. Порядок сходимости по такому шуму определять нельзя.

На errors.svg вертикальная ось логарифмическая, горизонтальная — log₂(n) с метками n.
Показаны trapezoid x², trapezoid x⁴ и simpson x⁴. Почти нулевая серия simpson x²
опущена, поскольку отражает лишь округление и лежит далеко ниже остальных ошибок;
логарифм нуля нигде не вычисляется.

Воспроизведение: `python3 -B build_report.py --inputs inputs --output .`.
Для отдельного каталога замените значение --output. При любой ошибке проверки
генератор завершится с исключением до записи результатов и не отметит passed=true.
'''
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    rows, verification = verify(args.inputs.resolve())
    graph = svg_graph(rows)
    narrative = report(rows, verification)
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output/'comparison.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=('method', 'power', 'n', 'estimate', 'exact', 'abs_error'))
        writer.writeheader()
        writer.writerows(rows)
    (args.output/'report.md').write_text(narrative, encoding='utf-8')
    (args.output/'errors.svg').write_text(graph, encoding='utf-8')
    (args.output/'verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
