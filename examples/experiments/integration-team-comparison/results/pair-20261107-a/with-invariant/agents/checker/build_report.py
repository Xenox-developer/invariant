"""Verify received quadrature artifacts and generate a deterministic research report.

Uses rational arithmetic for independent quadrature sums and closed-form errors.
Never invokes either received main() or changes the received artifacts.
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


def require(condition, message):
    if not condition:
        raise ValueError(message)


def formula_error(method, power, n):
    if method == 'trapezoid':
        return Fraction(1, 6*n*n) if power == 2 else Fraction(1, 3*n*n)-Fraction(1, 30*n**4)
    return Fraction(0) if power == 2 else Fraction(2, 15*n**4)


def rational_quadrature(method, power, n):
    # Independent integer-grid summation: no floating point sample locations.
    if method == 'trapezoid':
        numerator = n**power + 2*sum(i**power for i in range(1, n))
        return Fraction(numerator, 2*n**(power+1))
    numerator = n**power + sum((4 if i % 2 else 2)*i**power for i in range(1, n))
    return Fraction(numerator, 3*n**(power+1))


def verify(inputs):
    rows, details, hashes = [], [], {}
    for method in METHODS:
        raw = (inputs/method/'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        require(data['method'] == method and data['interval'] == [0, 1], 'Wrong method or interval')
        require(len(data['rows']) == 8, 'Expected eight rows per method')
        require(sorted((r['power'], r['n']) for r in data['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], 'Unexpected or duplicate rows')
        spec = importlib.util.spec_from_file_location('received_'+method, inputs/method/'solution.py')
        implementation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(implementation)
        for r in data['rows']:
            p, n = r['power'], r['n']
            for field in ('estimate', 'exact', 'abs_error'):
                require(type(r[field]) in (int, float) and math.isfinite(r[field]), 'Nonfinite numeric field')
            exact = Fraction(1, p+1)
            error = formula_error(method, p, n)
            rational_sum = rational_quadrature(method, p, n)
            require(rational_sum == exact+error, 'Rational sum disagrees with error formula')
            tolerance = 16*math.ulp(float(exact))
            require(r['exact'] == float(exact), 'Incorrect exact reference')
            require(r['abs_error'] == abs(r['estimate']-r['exact']), 'Incorrect reported error')
            require(abs(Fraction(r['estimate'])-rational_sum) <= Fraction(tolerance), 'Incorrect estimate')
            require(abs(Fraction(r['abs_error'])-error) <= Fraction(tolerance), 'Incorrect formula error')
            actual = implementation.integrate(lambda x: x**p, 0, 1, n)
            require(actual == r['estimate'], 'Received implementation does not reproduce estimate')
            rows.append(dict(method=method, **r))
            details.append(dict(method=method, power=p, n=n,
                                rational_exact=str(exact), rational_quadrature=str(rational_sum),
                                rational_signed_error=str(error),
                                true_abs_error=float(abs(Fraction(r['estimate'])-exact)),
                                implementation_reproduced=True))
    ratios = {}
    for method, p, target in (('trapezoid', 2, 4), ('trapezoid', 4, 4), ('simpson', 4, 16)):
        series = sorted((r for r in rows if r['method'] == method and r['power'] == p), key=lambda r:r['n'])
        values = [a['abs_error']/b['abs_error'] for a, b in zip(series, series[1:])]
        require(all(abs(v-target) < .015 for v in values), 'Unexpected convergence')
        ratios[f'{method}_x{p}'] = values
    roundoff = all(r['abs_error'] <= 16*math.ulp(r['exact']) for r in rows
                   if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic error exceeds roundoff')
    verification = dict(passed=True, rows_checked=len(rows), input_sha256=hashes,
                        observed_ratios=ratios,
                        conclusions=dict(trapezoid_order=2, simpson_order=4,
                                         simpson_x2_exact_within_roundoff=roundoff),
                        checks=details, tolerance='16 ulp of the rounded exact integral')
    return rows, verification


def svg_graph(rows):
    left, top, width, height = 85, 45, 520, 330
    def x(n): return left + math.log2(n/10)/3*width
    def y(e): return top + (-2-math.log10(e))/7*height
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="450" viewBox="0 0 900 450">',
             '<title>Quadrature absolute errors on [0,1]</title>',
             '<desc>Logarithmic error axis; Simpson x^2 omitted because it is at roundoff.</desc>',
             '<rect width="900" height="450" fill="white"/>',
             '<g font-family="sans-serif" font-size="13" fill="#222">',
             '<text x="85" y="24">Absolute error versus subinterval count</text>']
    for exponent in range(-2, -10, -1):
        yy = y(10**exponent)
        parts += [f'<path d="M {left} {yy:.3f} h {width}" stroke="#ddd"/>',
                  f'<text x="25" y="{yy+4:.3f}">10^{exponent}</text>']
    for n in NS:
        xx = x(n)
        parts += [f'<path d="M {xx:.3f} {top} v {height}" stroke="#eee"/>',
                  f'<text x="{xx-9:.3f}" y="398">{n}</text>']
    parts += ['<text x="240" y="431">n (log2 spacing)</text>',
              '<text transform="translate(15 285) rotate(-90)">abs_error (log10 scale)</text>']
    for j, (method, power, color) in enumerate((('trapezoid', 2, '#1764ac'),
                                               ('trapezoid', 4, '#bd5018'),
                                               ('simpson', 4, '#25813d'))):
        series = sorted((r for r in rows if r['method'] == method and r['power'] == power), key=lambda r:r['n'])
        require(all(r['abs_error'] > 0 for r in series), 'Logarithm requires positive errors')
        points = ' '.join(f"{x(r['n']):.3f},{y(r['abs_error']):.3f}" for r in series)
        label = escape(f'{method} x**{power}')
        parts += [f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"><title>{label}</title></polyline>',
                  f'<path d="M 635 {70+j*30} h 30" stroke="{color}" stroke-width="2"/>',
                  f'<text x="675" y="{74+j*30}">{label}</text>']
    return '\n'.join(parts+['</g></svg>\n'])


def report(rows, verification):
    ratios = verification['observed_ratios']
    lines = ['# Точность численного интегрирования', '',
             'Это малый детерминированный численный эксперимент: интегрируются x**2 и x**4 на [0,1] при n=10,20,40,80 равных подынтервалах. Используется только стандартная библиотека Python. Исходные результаты двух исполнителей сохранены без изменений; comparison.csv содержит все 16 полученных строк.', '',
             'Точные эталоны следуют из первообразной x**(p+1)/(p+1): интегралы равны 1/3 и 1/5. Составная формула трапеций имеет веса 1/2 на концах и 1 внутри, множитель h=1/n. Формула Симпсона имеет веса 1,4,2,...,4,1 и множитель h/3; число подынтервалов должно быть чётным.', '',
             'Независимая проверка использует Fraction: значения на рациональной сетке суммируются точно и сравниваются с точными формулами подписанной ошибки Q−I. Для трапеций на x**2 ошибка равна 1/(6*n**2), на x**4 — 1/(3*n**2)−1/(30*n**4). Для Симпсона на x**4 она равна 2/(15*n**4), а на x**2 равна нулю. Эти равенства также следуют из формулы Эйлера—Маклорена для полиномов и соотношения S_n=(4*T_n−T_(n/2))/3. Все ненулевые ошибки положительны.', '',
             'Обе полученные реализации изучены и вызваны для каждой строки; их результаты воспроизводятся точно. Все оценки и ошибки проверены против рациональных формул с допуском 16 ulp округлённого эталона. Проверены также записанные эталоны и равенство abs_error=abs(estimate−exact). Хеши SHA256 относятся к фактическим байтам входных result.json.', '',
             '| Ряд | Отношения ошибок E(n)/E(2n) | Наблюдаемые порядки log2 отношения |',
             '|---|---|---|']
    for key, values in ratios.items():
        lines.append('| '+key+' | '+', '.join(f'{v:.10f}' for v in values)+' | '+
                     ', '.join(f'{math.log2(v):.8f}' for v in values)+' |')
    lines += ['', 'При удвоении n ошибки трапеций уменьшаются примерно в 4 раза: наблюдается второй порядок. Ошибка Симпсона на x**4 уменьшается примерно в 16 раз: четвёртый порядок. Формулы дают эти порядки теоретически; четыре сетки лишь подтверждают их в данном эксперименте.', '',
              'Симпсон интегрирует x**2 точно в рациональной арифметике. Во входных данных abs_error равна 1.1102230246251565e-16 для всех четырёх сеток: это округление двоичных чисел, а не ошибка дискретизации. Сам эталон 1/3 тоже хранится приближённо: поэтому записанная abs_error отличается от расстояния оценки до точного рационального интеграла. Последнее расстояние отдельно приведено в verification.json как true_abs_error. Ряд simpson x**2 исключён из графика, поскольку находится на уровне округления и не показывает сходимость; нулевые ошибки не логарифмируются. На errors.svg показаны три остальных ряда с логарифмической осью ошибки.', '',
              'Воспроизведение: python3 -B build_report.py --inputs inputs --output .', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows, verification = verify(args.inputs.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output/'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['method', 'power', 'n', 'estimate', 'exact', 'abs_error'])
        writer.writeheader()
        writer.writerows(rows)
    (args.output/'verification.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
    (args.output/'errors.svg').write_text(svg_graph(rows), encoding='utf-8')
    (args.output/'report.md').write_text(report(rows, verification), encoding='utf-8')


if __name__ == '__main__':
    main()
