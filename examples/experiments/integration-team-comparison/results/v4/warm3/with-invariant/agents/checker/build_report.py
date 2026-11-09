"""Independently verify received quadrature data and generate a Russian report.

Usage: python3 -B build_report.py --inputs inputs --output .
Only the Python standard library is required. Inputs are read, never rewritten.
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


def rational_quadrature(method, power, n):
    """Independent weighted sum on an exact rational grid (no float samples)."""
    if method == 'trapezoid':
        weights = [1] + [2] * (n - 1) + [1]
        divisor = 2 * n
    else:
        weights = [1] + [4 if i % 2 else 2 for i in range(1, n)] + [1]
        divisor = 3 * n
    return sum((w * Fraction(i, n) ** power
                for i, w in enumerate(weights)), Fraction(0)) / divisor


def theoretical_error(method, power, n):
    # Exact signed errors Q-I from polynomial sums / Euler--Maclaurin.
    if method == 'trapezoid':
        return (Fraction(1, 6 * n**2) if power == 2 else
                Fraction(1, 3 * n**2) - Fraction(1, 30 * n**4))
    return Fraction(0) if power == 2 else Fraction(2, 15 * n**4)


def verify(inputs):
    rows, hashes, evidence = [], {}, []
    for method in METHODS:
        raw = (inputs / method / 'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        require(data['method'] == method and data['interval'] == [0, 1],
                f'{method}: invalid metadata')
        require(sorted((r['power'], r['n']) for r in data['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], f'{method}: invalid rows')
        # Execute definitions, without invoking either main() or writing inputs.
        integrate = runpy.run_path(str(inputs / method / 'solution.py'))['integrate']
        for r in data['rows']:
            p, n = r['power'], r['n']
            exact = Fraction(1, p + 1)
            rational = rational_quadrature(method, p, n)
            formula = theoretical_error(method, p, n)
            require(rational - exact == formula, f'{method}/{p}/{n}: rational identity')
            for key in ('estimate', 'exact', 'abs_error'):
                require(type(r[key]) in (int, float) and math.isfinite(r[key]),
                        f'{method}/{p}/{n}: invalid {key}')
            # Eight ulps allow only floating arithmetic noise, far below every
            # nonzero truncation error in this experiment (minimum 3.25e-9).
            tolerance = 8 * math.ulp(float(exact))
            require(r['exact'] == float(exact), 'incorrect exact reference')
            require(abs(r['estimate'] - float(rational)) <= tolerance,
                    f'{method}/{p}/{n}: estimate disagrees with rational sum')
            require(r['abs_error'] == abs(r['estimate'] - r['exact']),
                    f'{method}/{p}/{n}: inconsistent reported error')
            require(abs(r['abs_error'] - float(formula)) <= tolerance,
                    f'{method}/{p}/{n}: error disagrees with formula')
            executed = integrate(lambda x: x**p, 0, 1, n)
            require(math.isfinite(executed) and abs(executed - r['estimate']) <= tolerance,
                    f'{method}/{p}/{n}: implementation disagrees with artifact')
            rows.append(dict(method=method, **r))
            evidence.append({'method': method, 'power': p, 'n': n,
                             'rational_estimate': str(rational),
                             'exact_signed_error': str(formula),
                             'executed_estimate': executed,
                             'absolute_tolerance': tolerance})
    ratios = {}
    for method, p in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        errors = {r['n']: r['abs_error'] for r in rows
                  if r['method'] == method and r['power'] == p}
        values = [errors[n] / errors[2*n] for n in NS[:-1]]
        target = 4 if method == 'trapezoid' else 16
        require(all(abs(v - target) < .015 for v in values), 'unexpected convergence')
        ratios[f'{method}_x{p}'] = values
    quadratic_ok = all(r['abs_error'] <= 8 * math.ulp(1/3) for r in rows
                       if r['method'] == 'simpson' and r['power'] == 2)
    require(quadratic_ok, 'Simpson quadratic exceeds roundoff')
    return rows, {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                  'observed_ratios': ratios,
                  'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                  'simpson_x2_exact_within_roundoff': quadratic_ok},
                  'row_evidence': evidence}


def make_svg(rows):
    root = ET.Element('svg', xmlns='http://www.w3.org/2000/svg',
                      width='850', height='510', viewBox='0 0 850 510')
    def element(tag, **attrs):
        return ET.SubElement(root, tag, {k: str(v) for k, v in attrs.items()})
    def label(x, y, text, **attrs):
        element('text', x=x, y=y, fill='#17212b', **attrs).text = text
    element('rect', width=850, height=510, fill='white')
    label(80, 30, 'Absolute integration errors on [0,1]', **{'font-size': 20})
    # Coordinates are linear in log2(n) and log10(error).
    xcoord = lambda n: 90 + 650 * math.log2(n / 10) / 3
    ycoord = lambda e: 410 - (math.log10(e) + 9) * 350 / 7
    for exponent in range(-9, -1):
        y = ycoord(10**exponent)
        element('line', x1=90, y1=y, x2=740, y2=y, stroke='#dddddd')
        label(15, y+4, f'1e{exponent}')
    for n in NS:
        x = xcoord(n)
        element('line', x1=x, y1=60, x2=x, y2=410, stroke='#eeeeee')
        label(x-8, 432, str(n))
    element('path', d='M90 60 V410 H740', fill='none', stroke='black')
    label(350, 458, 'n (log2 scale)')
    label(95, 53, 'absolute error (log10 scale)')
    for index, (method, p, color) in enumerate((('trapezoid', 2, '#1565c0'),
                                               ('trapezoid', 4, '#c62828'),
                                               ('simpson', 4, '#228438'))):
        series = sorted((r for r in rows if r['method'] == method and r['power'] == p),
                        key=lambda r: r['n'])
        require(all(r['abs_error'] > 0 for r in series), 'log plot requires positive errors')
        element('polyline', points=' '.join(f"{xcoord(r['n']):.5f},{ycoord(r['abs_error']):.5f}"
                                          for r in series),
                fill='none', stroke=color, **{'stroke-width': 2})
        for r in series:
            element('circle', cx=xcoord(r['n']), cy=ycoord(r['abs_error']), r=3, fill=color)
        x = 95 + index * 230
        element('line', x1=x, y1=487, x2=x+25, y2=487, stroke=color,
                **{'stroke-width': 3})
        label(x+32, 492, f'{method} x**{p}')
    return ET.tostring(root, encoding='unicode') + '\n'


def make_report(rows, verification):
    lines = []
    for key, values in verification['observed_ratios'].items():
        orders = [math.log2(v) for v in values]
        lines.append(f"| {key} | " + ', '.join(f'{v:.9f}' for v in values) +
                     ' | ' + ', '.join(f'{v:.7f}' for v in orders) + ' |')
    quadratic_error = max(r['abs_error'] for r in rows
                          if r['method'] == 'simpson' and r['power'] == 2)
    return '''# Точность численного интегрирования

Это малый детерминированный численный эксперимент: исследованы функции x**2
и x**4 на [0,1] при n = 10, 20, 40, 80 равных подынтервалах, h = 1/n.
Точные интегралы по первообразной x**(p+1)/(p+1) равны 1/3 и 1/5.

Составная формула трапеций (trapezoid) использует половинные веса на концах
и единичные внутри сетки: T = h[f(0)/2 + Σ f(ih) + f(1)/2].
Составная формула Симпсона (simpson) при чётном n использует веса
1,4,2,4,...,2,4,1 и множитель h/3. Она точна для многочленов до степени 3.

Независимая проверка всех 16 строк выполнена суммированием рациональных
чисел Fraction на точной сетке. Дополнительно исполнены функции integrate
из обеих полученных реализаций без вызова main и без записи во входные файлы.
Рациональные суммы проверены по точным формулам знаковой ошибки Q−I:
для трапеций на x**2 это 1/(6n²), на x**4 — 1/(3n²)−1/(30n⁴);
для Симпсона на x**2 — ноль, на x**4 — 2/(15n⁴).
Таким образом, обе ненулевые ошибки положительны: оценки завышают интеграл.
Сравнение полученных float с рациональной формулой допускает 8 ulp точного
эталона: этот допуск намного меньше минимальной ненулевой ошибки дискретизации.
Проверены также эталоны, вычисление abs_error, полнота сетки и конечность чисел.
SHA256 полученных байтов result.json сохранены в verification.json.

Измеренные отношения E(n)/E(2n) и порядки log2(E(n)/E(2n)):

| Ряд | Три отношения ошибок | Три измеренных порядка |
|---|---|---|
''' + '\n'.join(lines) + f'''

Ошибки трапеций уменьшаются примерно в 4 раза при удвоении n (порядок 2),
ошибка Симпсона на x**4 — в 16 раз (порядок 4). Небольшое отклонение
отношений для трапеций на x**4 объясняется членом n⁻⁴.
Для Симпсона на x**2 полученная ошибка относительно float-эталона равна
{quadratic_error:.17g} для каждого n: это округление двоичной арифметики,
а не ошибка квадратурной формулы. Само число 1/3 также представимо только
приближённо; abs_error во входных данных вычислено относительно этого float.
Поэтому нельзя измерять порядок сходимости по этому практически нулевому ряду.

График errors.svg использует логарифмическую шкалу ошибок и log2-шкалу n.
Почти нулевой ряд simpson x**2 опущен: он показывает предел округления,
а не сходимость. Логарифм нуля нигде не вычисляется. Три показанных ряда
используют именно полученные abs_error. В comparison.csv все 16 полученных
числовых строк сохранены без замены оценками независимой проверки.
Выводы относятся к этому малому опыту с двумя многочленами и четырьмя сетками;
они не являются универсальным измерением точности для произвольных функций.

Воспроизведение: `python3 -B build_report.py --inputs inputs --output .`.
Генератор использует только стандартную библиотеку Python; дополнительный
каталог результатов задаётся параметром --output. Точные рациональные
проверочные значения и результаты исполнения сохранены в row_evidence.
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows, verification = verify(args.inputs)
    svg = make_svg(rows)
    report = make_report(rows, verification)
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'comparison.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['method', 'power', 'n', 'estimate', 'exact', 'abs_error'])
        writer.writeheader()
        writer.writerows(rows)
    (args.output / 'report.md').write_text(report, encoding='utf-8')
    (args.output / 'errors.svg').write_text(svg, encoding='utf-8')
    (args.output / 'verification.json').write_text(
        json.dumps(verification, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
