"""Verify received quadrature data and generate a deterministic report (stdlib only)."""
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

NS = (10, 20, 40, 80)
METHODS = ('trapezoid', 'simpson')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rational_quadrature(method, power, n):
    """Independent exact-arithmetic weighted sum, with no input code reuse."""
    values = [Fraction(i, n) ** power for i in range(n + 1)]
    if method == 'trapezoid':
        return (values[0] / 2 + sum(values[1:-1]) + values[-1] / 2) / n
    return (values[0] + values[-1] +
            sum((4 if i % 2 else 2) * values[i] for i in range(1, n))) / (3 * n)


def formula_error(method, power, n):
    if method == 'trapezoid':
        return Fraction(1, 6*n*n) if power == 2 else Fraction(1, 3*n*n) - Fraction(1, 30*n**4)
    return Fraction(0) if power == 2 else Fraction(2, 15*n**4)


def verify(inputs):
    rows, hashes, evidence = [], {}, []
    for method in METHODS:
        folder = inputs / method
        raw = (folder / 'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        require(data['method'] == method and data['interval'] == [0, 1], 'Wrong method or interval')
        require(sorted((r['power'], r['n']) for r in data['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], 'Wrong or duplicate rows')
        # Import executes definitions only; main() would overwrite received artifacts.
        spec = importlib.util.spec_from_file_location('received_' + method, folder / 'solution.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for row in data['rows']:
            p, n = row['power'], row['n']
            label = f'{method} x{p} n={n}'
            for field in ('estimate', 'exact', 'abs_error'):
                require(type(row[field]) in (int, float) and math.isfinite(row[field]), label + ': invalid number')
            exact = Fraction(1, p + 1)
            theoretical_error = formula_error(method, p, n)
            rational = rational_quadrature(method, p, n)
            require(rational - exact == theoretical_error, label + ': exact formula mismatch')
            tolerance = 16 * math.ulp(float(exact))
            estimate = row['estimate']
            measured_exact_error = abs(Fraction.from_float(float(estimate)) - exact)
            require(row['exact'] == float(exact), label + ': wrong reference')
            require(row['abs_error'] == abs(estimate - row['exact']), label + ': wrong reported error')
            require(abs(estimate - float(rational)) <= tolerance, label + ': wrong estimate')
            require(abs(float(measured_exact_error) - float(theoretical_error)) <= tolerance,
                    label + ': mathematical error mismatch')
            computed = module.integrate(lambda x: x ** p, 0, 1, n)
            require(computed == estimate, label + ': implementation differs from artifact')
            evidence.append({'method': method, 'power': p, 'n': n,
                             'exact_quadrature': str(rational),
                             'theoretical_error': str(theoretical_error),
                             'error_against_rational_integral': float(measured_exact_error),
                             'tolerance': tolerance, 'implementation_reproduced': True})
            rows.append({'method': method, **row})
    ratios = {}
    for method, power, target in (('trapezoid', 2, 4), ('trapezoid', 4, 4), ('simpson', 4, 16)):
        errors = {r['n']: r['abs_error'] for r in rows if r['method'] == method and r['power'] == power}
        ratios[f'{method}_x{power}'] = [errors[n] / errors[2*n] for n in NS[:-1]]
        require(all(abs(r - target) < .015 for r in ratios[f'{method}_x{power}']), 'Unexpected convergence')
    roundoff = all(r['abs_error'] <= 16 * math.ulp(1/3) for r in rows
                   if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic exceeds roundoff')
    return rows, {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                  'observed_ratios': ratios,
                  'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                  'simpson_x2_exact_within_roundoff': roundoff},
                  'verification_details': evidence}


def svg_graph(rows):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="540" viewBox="0 0 900 540">',
             '<title>Quadrature absolute errors on [0,1]</title>',
             '<desc>Logarithmic error axis; Simpson x^2 is omitted because it is at roundoff.</desc>',
             '<rect width="900" height="540" fill="white"/>',
             '<g font-family="sans-serif" font-size="14" fill="#222">',
             '<text x="85" y="28">Absolute error versus subinterval count</text>']
    def x(n): return 90 + (math.log2(n/10) / 3) * 540
    def y(error): return 440 - (math.log10(error) + 9) / 7 * 380
    for exponent in range(-9, -1):
        yy = y(10**exponent)
        parts.append(f'<path d="M90 {yy:.3f} H630" stroke="#ddd"/>')
        parts.append(f'<text x="22" y="{yy+5:.3f}">1e{exponent}</text>')
    parts.append('<path d="M90 60 V440 H630" stroke="#222" fill="none"/>')
    for n in NS:
        parts.append(f'<text x="{x(n)-10:.3f}" y="465">{n}</text>')
    parts.extend(['<text x="310" y="500">n (log2 spacing)</text>',
                  '<text transform="translate(16 350) rotate(-90)">absolute error (log10 scale)</text>'])
    for index, (method, power, color) in enumerate((('trapezoid', 2, '#1769aa'), ('trapezoid', 4, '#c04412'), ('simpson', 4, '#208035'))):
        series = sorted((r for r in rows if r['method'] == method and r['power'] == power), key=lambda r:r['n'])
        require(all(r['abs_error'] > 0 for r in series), 'Cannot plot nonpositive log error')
        points = ' '.join(f"{x(r['n']):.3f},{y(r['abs_error']):.3f}" for r in series)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for r in series:
            parts.append(f'<circle cx="{x(r["n"]):.3f}" cy="{y(r["abs_error"]):.3f}" r="4" fill="{color}"/>')
        yy = 90 + index * 32
        parts.append(f'<path d="M660 {yy} H690" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="700" y="{yy+5}">{escape(method)} x^{power}</text>')
    return '\n'.join(parts + ['</g>', '</svg>', ''])


def report(rows, verification):
    lines = ['# Точность численного интегрирования', '',
        'Это малый детерминированный численный эксперимент: интегрируются x² и x⁴ на [0,1] при n = 10, 20, 40, 80 равных подынтервалах. Точные эталоны: ∫x² dx = 1/3 и ∫x⁴ dx = 1/5.', '',
        'Составной метод трапеций (trapezoid) использует половинные веса на концах сетки. Метод Симпсона (simpson) использует веса 1, 4, 2, …, 4, 1 и множитель h/3; все выбранные n чётные. При h = 1/n точные знаковые ошибки Q−I для трапеций равны h²/6 на x² и h²/3−h⁴/30 на x⁴. Для Симпсона они равны нулю на x² и 2h⁴/15 на x⁴. Эти формулы проверены независимыми суммами с рациональной арифметикой Fraction.', '',
        'Обе полученные реализации прочитаны и их функции integrate выполнены для всех 16 строк без запуска main и изменения входных файлов. Проверены численные оценки, точные эталоны, абсолютные ошибки и соответствие формулам с допуском 16 ulp эталона. SHA256 в verification.json относится к исходным байтам result.json. comparison.csv сохраняет все полученные числовые значения без замены независимыми расчётами.', '',
        '| Метод и степень | E(10)/E(20) | E(20)/E(40) | E(40)/E(80) | Наблюдаемые порядки log₂ отношения |',
        '|---|---:|---:|---:|---|']
    for key, ratios in verification['observed_ratios'].items():
        lines.append('| ' + key + ' | ' + ' | '.join(f'{r:.10f}' for r in ratios) + ' | ' + ', '.join(f'{math.log2(r):.7f}' for r in ratios) + ' |')
    lines.extend(['',
        'При удвоении n ошибки трапеций уменьшаются примерно в 4 раза (порядок 2), а Симпсона на x⁴ — в 16 раз (порядок 4). Небольшое отклонение отношения для трапеций на x⁴ объясняет член −h⁴/30. Эксперимент подтверждает порядок именно на этих полиномах и сетках.', '',
        'На x² Симпсон математически точен. Во всех четырёх полученных строках ошибка относительно float(1/3) составляет 5.551115123125783e−17: это округление двоичной арифметики, а не погрешность квадратуры. Сам эталон float(1/3) также округлён; независимая проверка сравнивает оценку с рациональным 1/3. Отношения этих почти нулевых ошибок не характеризуют сходимость.', '',
        'График errors.svg показывает три ненулевые серии с логарифмической шкалой ошибки и логарифмическим размещением n. Серия simpson x² исключена как находящаяся на уровне округления; логарифм нуля не вычисляется. Таблица использует полученное определение abs_error = abs(estimate − exact), где exact хранится как float.', '',
        'Воспроизведение (только стандартная библиотека Python):', '',
        '```sh', 'python3 -B build_report.py --inputs inputs --output .', '```', ''])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    rows, verification = verify(args.inputs.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['method', 'power', 'n', 'estimate', 'exact', 'abs_error'])
        writer.writeheader()
        writer.writerows(rows)
    (args.output / 'verification.json').write_text(json.dumps(verification, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    (args.output / 'report.md').write_text(report(rows, verification), encoding='utf-8')
    (args.output / 'errors.svg').write_text(svg_graph(rows), encoding='utf-8')


if __name__ == '__main__':
    main()
