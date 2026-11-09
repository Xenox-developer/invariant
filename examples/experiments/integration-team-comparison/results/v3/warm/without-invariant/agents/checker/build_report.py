"""Verify received integration results and build a deterministic stdlib report.

Usage: python3 -B build_report.py --inputs inputs --output .
Input implementations are imported and called, never run through their file-writing main.
Independent quadrature uses exact Fraction arithmetic, not the received implementation.
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

METHODS = ('trapezoid', 'simpson')
NS = (10, 20, 40, 80)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def formula(method, power, n):
    if method == 'trapezoid':
        return Fraction(1, 6*n*n) if power == 2 else Fraction(1, 3*n*n) - Fraction(1, 30*n**4)
    return Fraction(0) if power == 2 else Fraction(2, 15*n**4)


def rational_quadrature(method, power, n):
    values = [Fraction(i, n)**power for i in range(n+1)]
    if method == 'trapezoid':
        return (values[0]/2 + sum(values[1:-1]) + values[-1]/2) / n
    return sum(value * (1 if i in (0, n) else 4 if i % 2 else 2)
               for i, value in enumerate(values)) / (3*n)


def verify(inputs):
    rows, hashes, diagnostics = [], {}, []
    for method in METHODS:
        raw = (inputs / method / 'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        doc = json.loads(raw)
        require(doc['method'] == method and doc['interval'] == [0, 1], 'Wrong method or interval')
        require(sorted((r['power'], r['n']) for r in doc['rows']) ==
                [(p, n) for p in (2, 4) for n in NS], 'Missing or duplicate rows')
        spec = importlib.util.spec_from_file_location('received_' + method, inputs / method / 'solution.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for source in doc['rows']:
            p, n = source['power'], source['n']
            label = f'{method} x**{p} n={n}'
            for key in ('estimate', 'exact', 'abs_error'):
                require(type(source[key]) in (float, int) and math.isfinite(source[key]), label + ': invalid number')
            exact = Fraction(1, p+1)
            quadrature = rational_quadrature(method, p, n)
            error = formula(method, p, n)
            require(quadrature - exact == error, label + ': rational sum disagrees with error formula')
            require(source['exact'] == float(exact), label + ': wrong exact reference')
            require(source['abs_error'] == abs(source['estimate'] - source['exact']), label + ': wrong reported error')
            # Eight ulps at the integral scale allow ordinary floating point grid/sum rounding.
            tolerance = 8 * math.ulp(float(exact))
            require(abs(Fraction(source['estimate']) - quadrature) <= Fraction(tolerance), label + ': estimate disagrees with exact quadrature')
            require(abs(source['abs_error'] - float(error)) <= tolerance, label + ': error disagrees with formula')
            executed = module.integrate(lambda x: x**p, 0, 1, n)
            require(executed == source['estimate'], label + ': received implementation does not reproduce estimate')
            diagnostics.append({'method': method, 'power': p, 'n': n,
                                'rational_quadrature': str(quadrature),
                                'theoretical_signed_error': str(error),
                                'true_abs_error_of_received_float': float(abs(Fraction(source['estimate']) - exact)),
                                'tolerance': tolerance, 'implementation_reproduced': True})
            rows.append({'method': method, **{k: source[k] for k in ('power', 'n', 'estimate', 'exact', 'abs_error')}})
    ratios = {}
    for method, p in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        data = {r['n']: r['abs_error'] for r in rows if r['method'] == method and r['power'] == p}
        ratios[f'{method}_x{p}'] = [data[n]/data[2*n] for n in NS[:-1]]
        target = 4 if method == 'trapezoid' else 16
        require(all(abs(r-target) < .015 for r in ratios[f'{method}_x{p}']), 'Unexpected convergence')
    roundoff = all(r['abs_error'] <= 8*math.ulp(1/3) for r in rows if r['method'] == 'simpson' and r['power'] == 2)
    require(roundoff, 'Simpson quadratic exceeds roundoff')
    return rows, {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                  'observed_ratios': ratios,
                  'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                  'simpson_x2_exact_within_roundoff': roundoff},
                  'verification_details': diagnostics}


def svg_graph(rows):
    # Fixed common log10 error axis: every plotted value is strictly positive.
    left, right, top, bottom = 95, 590, 65, 385
    def x(n):
        return left + math.log2(n/10)/3*(right-left)
    def y(error):
        require(error > 0, 'Cannot plot a nonpositive error on logarithmic axis')
        return bottom - (math.log10(error)+9)/7*(bottom-top)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="860" height="465" viewBox="0 0 860 465">',
             '<title>Integration absolute errors versus subinterval count</title>',
             '<desc>Logarithmic error axis; Simpson x squared omitted because its error is rounding only.</desc>',
             '<rect width="860" height="465" fill="white"/>',
             '<g font-family="sans-serif" font-size="14" fill="#222">',
             '<text x="95" y="28">Numerical integration on [0,1]</text>']
    for exponent in range(-9, -1):
        yy = y(10**exponent)
        parts.append(f'<path d="M {left} {yy} H {right}" stroke="#ddd"/><text x="38" y="{yy+5}">1e{exponent}</text>')
    for n in NS:
        xx = x(n)
        parts.append(f'<text x="{xx-9}" y="410">{n}</text>')
    parts.extend([f'<path d="M {left} {top} V {bottom} H {right}" fill="none" stroke="black"/>',
                  '<text x="275" y="445">Subintervals n (log2)</text>',
                  '<text transform="translate(19 310) rotate(-90)">Absolute error (log10)</text>'])
    for i, (method, p, color) in enumerate((('trapezoid', 2, '#1768ac'), ('trapezoid', 4, '#b14800'), ('simpson', 4, '#217a38'))):
        data = sorted((r for r in rows if r['method'] == method and r['power'] == p), key=lambda r: r['n'])
        points = ' '.join(f"{x(r['n']):.4f},{y(r['abs_error']):.4f}" for r in data)
        parts.append(f'<polyline points="{points}" stroke="{color}" stroke-width="2" fill="none"/>')
        for r in data:
            parts.append(f'<circle cx="{x(r["n"]):.4f}" cy="{y(r["abs_error"]):.4f}" r="3" fill="{color}"/>')
        yy = 100 + 32*i
        parts.append(f'<path d="M 615 {yy} h 25" stroke="{color}" stroke-width="2"/><text x="650" y="{yy+5}">{escape(method)} x^{p}</text>')
    return '\n'.join(parts + ['</g>', '</svg>', ''])


def report(rows, verification):
    ratios = verification['observed_ratios']
    lines = [
        '# Точность численного интегрирования',
        '',
        'Это малый детерминированный численный эксперимент: интегрируются x² и x⁴ на [0,1] при n = 10, 20, 40, 80 равных подынтервалах. Точные эталоны получены аналитически: ∫x² dx = 1/3 и ∫x⁴ dx = 1/5.',
        '',
        'Составная формула трапеций использует шаг h = 1/n и веса 1/2 на концах, 1 внутри. Составная формула Симпсона использует чётное n и веса 1,4,2,…,4,1 с множителем h/3. При точной арифметике знаковые ошибки Q−I равны: для trapezoid x² — 1/(6n²); для trapezoid x⁴ — 1/(3n²)−1/(30n⁴); для simpson x² — 0; для simpson x⁴ — 2/(15n⁴). Все ненулевые теоретические ошибки положительны.',
        '',
        'Независимая проверка всех 16 строк использует Fraction: рациональные узлы и взвешенные суммы сравниваются с этими формулами точно. Затем полученные значения сравниваются с принятыми числами с допуском восемь ulp на масштабе интеграла. Обе исходные реализации импортированы и исполнены через integrate; каждый результат воспроизведён. Их main не запускается, входные файлы не изменяются. SHA256 вычислен по фактическим байтам result.json.',
        '',
        'Измеренные отношения ошибок E(n)/E(2n):',
        '',
    ]
    for key, values in ratios.items():
        orders = ', '.join(f'{math.log2(v):.9f}' for v in values)
        lines.append(f'- {key}: ' + ', '.join(f'{v:.12g}' for v in values) + f'; порядки log₂ отношения: {orders}.')
    lines += ['',
        'Отношения около 4 подтверждают второй порядок трапеций, около 16 — четвёртый порядок Симпсона для x⁴. Небольшое отклонение отношения трапеций для x⁴ от 4 объясняется членом n⁻⁴. Это проверка данных конкретного эксперимента, а не эмпирическое доказательство порядка для всех функций.',
        '',
        'Для simpson x² теоретическая ошибка равна нулю: формула точна для многочленов степени до трёх. Во всех четырёх принятых строках ошибка относительно машинного эталона равна 5.551115123125783e-17; это округление двоичной арифметики, а не ошибка дискретизации. Само число 1/3 тоже не представимо точно в float. Поэтому сравнение float с float отличается от ошибки относительно рационального 1/3; последняя отдельно записана в verification.json. Ряд simpson x² исключён из errors.svg: он отражает только округление и не позволяет измерить порядок сходимости. Для остальных трёх рядов показана логарифмическая шкала ошибок; log(0) нигде не вычисляется.',
        '',
        'comparison.csv сохраняет все принятые числовые значения без замены рациональными расчётами. verification.json содержит проверку происхождения, формул и отношений. Повторный запуск: python3 -B build_report.py --inputs inputs --output NEW_DIR.',
        '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows, verification = verify(args.inputs.resolve())
    output = args.output.resolve()
    # Guard against accidentally overwriting received artifacts.
    require(not output.is_relative_to(args.inputs.resolve()), 'Output must be outside the input tree')
    output.mkdir(parents=True, exist_ok=True)
    with (output / 'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=('method', 'power', 'n', 'estimate', 'exact', 'abs_error'))
        writer.writeheader()
        writer.writerows(rows)
    (output / 'report.md').write_text(report(rows, verification), encoding='utf-8')
    (output / 'errors.svg').write_text(svg_graph(rows), encoding='utf-8')
    (output / 'verification.json').write_text(json.dumps(verification, indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
