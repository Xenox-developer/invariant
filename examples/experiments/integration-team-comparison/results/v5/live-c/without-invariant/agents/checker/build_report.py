"""Verify received quadrature artifacts and generate a reproducible report (stdlib)."""
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

NS = (10, 20, 40, 80)
METHODS = ('trapezoid', 'simpson')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def theoretical_error(method, p, n):
    if method == 'trapezoid':
        return Fraction(1, 6*n*n) if p == 2 else Fraction(1, 3*n*n)-Fraction(1, 30*n**4)
    return Fraction(0) if p == 2 else Fraction(2, 15*n**4)


def rational_quadrature(method, p, n):
    # Independent exact arithmetic: no floating grid or imported implementation.
    samples = [Fraction(i, n)**p for i in range(n+1)]
    if method == 'trapezoid':
        return (samples[0]/2 + sum(samples[1:-1]) + samples[-1]/2)/n
    return sum(v*(1 if i in (0, n) else 4 if i % 2 else 2)
               for i, v in enumerate(samples))/ (3*n)


def verify(inputs):
    rows, hashes, details = [], {}, []
    for method in METHODS:
        directory = inputs / method
        raw = (directory/'result.json').read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        document = json.loads(raw)
        require(document['method'] == method and document['interval'] == [0, 1], 'Invalid method or interval')
        received = document['rows']
        require(sorted((r['power'], r['n']) for r in received) ==
                [(p, n) for p in (2, 4) for n in NS], 'Missing or duplicate rows')
        spec = importlib.util.spec_from_file_location('received_' + method, directory/'solution.py')
        implementation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(implementation)  # main() is not invoked; inputs stay unchanged.
        for r in received:
            p, n = r['power'], r['n']
            for key in ('estimate', 'exact', 'abs_error'):
                require(type(r[key]) in (int, float) and math.isfinite(r[key]), 'Invalid numeric field')
            exact = Fraction(1, p+1)
            error = theoretical_error(method, p, n)
            rational = rational_quadrature(method, p, n)
            require(rational == exact + error, 'Exact sum disagrees with error formula')
            computed = implementation.integrate(lambda x: x**p, 0, 1, n)
            require(computed == r['estimate'], 'Implementation differs from received estimate')
            require(r['exact'] == float(exact), 'Wrong exact reference')
            require(r['abs_error'] == abs(r['estimate']-r['exact']), 'Wrong reported error')
            tolerance = 8*math.ulp(float(exact))
            true_error = abs(Fraction(r['estimate'])-exact)
            require(abs(Fraction(r['estimate'])-rational) <= Fraction(tolerance), 'Estimate disagrees with exact quadrature')
            require(abs(true_error-error) <= Fraction(tolerance), 'True error disagrees with mathematical formula')
            require(abs(r['abs_error']-float(error)) <= tolerance, 'Reported error disagrees with formula')
            rows.append({'method': method, **r})
            details.append({'method': method, 'power': p, 'n': n,
                            'formula_error': str(error), 'exact_quadrature': str(rational),
                            'error_against_rational_integral': float(true_error),
                            'roundoff_tolerance': tolerance})
        require(hashlib.sha256((directory/'result.json').read_bytes()).hexdigest() == hashes[method], 'Input changed')
    ratios = {}
    for method, p in (('trapezoid', 2), ('trapezoid', 4), ('simpson', 4)):
        errors = {r['n']: r['abs_error'] for r in rows if r['method'] == method and r['power'] == p}
        ratios[f'{method}_x{p}'] = [errors[n]/errors[2*n] for n in NS[:-1]]
        target = 4 if method == 'trapezoid' else 16
        require(all(abs(v-target) < .015 for v in ratios[f'{method}_x{p}']), 'Unexpected convergence')
    roundoff = all(r['abs_error'] <= 8*math.ulp(1/3) for r in rows if r['method']=='simpson' and r['power']==2)
    require(roundoff, 'Simpson quadratic error exceeds roundoff')
    verification = {'passed': True, 'rows_checked': len(rows), 'input_sha256': hashes,
                    'observed_ratios': ratios,
                    'conclusions': {'trapezoid_order': 2, 'simpson_order': 4,
                                    'simpson_x2_exact_within_roundoff': roundoff},
                    'verification_details': details}
    return rows, verification


def make_svg(rows, output):
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    def element(parent, tag, attrs=None, text=None):
        item = ET.SubElement(parent, '{http://www.w3.org/2000/svg}'+tag, attrs or {})
        item.text = text
        return item
    root = ET.Element('{http://www.w3.org/2000/svg}svg', {'width':'820', 'height':'520', 'viewBox':'0 0 820 520'})
    element(root, 'title', text='Absolute integration errors on [0,1]')
    element(root, 'rect', {'width':'820','height':'520','fill':'white'})
    element(root, 'text', {'x':'85','y':'28','font-size':'20'}, 'Quadrature error: x^2 and x^4 on [0,1]')
    def x(n): return 90 + 510*math.log2(n/10)/3
    def y(error): return 420 - (math.log10(error)+9)*350/7
    for exponent in range(-9, -1):
        yy = y(10**exponent)
        element(root, 'line', {'x1':'90','x2':'600','y1':str(yy),'y2':str(yy),'stroke':'#dddddd'})
        element(root, 'text', {'x':'25','y':str(yy+5),'font-size':'13'}, f'1e{exponent}')
    for n in NS:
        element(root, 'text', {'x':str(x(n)-8),'y':'445'}, str(n))
    element(root, 'path', {'d':'M90 60 V420 H610','fill':'none','stroke':'black'})
    element(root, 'text', {'x':'260','y':'478'}, 'n (subintervals; log2 spacing)')
    element(root, 'text', {'x':'18','y':'50'}, 'abs_error (log10)')
    for index, (method, p, color) in enumerate((('trapezoid',2,'#1764ab'),('trapezoid',4,'#bf4d00'),('simpson',4,'#18803b'))):
        data = sorted((r for r in rows if r['method']==method and r['power']==p), key=lambda r:r['n'])
        require(all(r['abs_error']>0 for r in data), 'Cannot plot nonpositive error')
        points = ' '.join(f"{x(r['n']):.4f},{y(r['abs_error']):.4f}" for r in data)
        element(root,'polyline',{'points':points,'fill':'none','stroke':color,'stroke-width':'2'})
        for r in data:
            element(root,'circle',{'cx':str(x(r['n'])),'cy':str(y(r['abs_error'])),'r':'3','fill':color})
        yy = str(95+28*index)
        element(root,'line',{'x1':'620','x2':'645','y1':yy,'y2':yy,'stroke':color,'stroke-width':'2'})
        element(root,'text',{'x':'652','y':str(100+28*index),'font-size':'14'},f'{method} x^{p}')
    element(root,'text',{'x':'90','y':'506','font-size':'13'}, 'Simpson x^2: omitted (roundoff level; see report.md).')
    ET.ElementTree(root).write(output, encoding='utf-8', xml_declaration=True)


def make_report(rows, verification):
    lines = ['# Проверка точности численного интегрирования', '',
        'Это малый детерминированный численный эксперимент: интегрируются x² и x⁴ на [0,1] при n=10,20,40,80 равных подынтервалах, h=1/n. Точные интегралы равны 1/3 и 1/5. Случайные данные и внешние библиотеки не используются.', '',
        'Составной метод трапеций (trapezoid) использует веса 1/2 на концах и 1 внутри, затем умножает сумму на h. Составной метод Симпсона (simpson) при чётном n использует веса 1,4,2,…,4,1 и множитель h/3.', '',
        'Проверены все 16 полученных строк. Обе реализации изучены и их integrate выполнены без запуска main и без перезаписи входов. Дополнительно независимо вычислены суммы с рациональными узлами и весами через Fraction. Эти суммы точно совпали с аналитическими формулами. Для положительной ошибки Q−I формулы имеют вид:', '',
        '- trapezoid, x²: 1/(6n²);',
        '- trapezoid, x⁴: 1/(3n²) − 1/(30n⁴);',
        '- simpson, x²: 0;',
        '- simpson, x⁴: 2/(15n⁴).', '',
        'Числа в comparison.csv сохранены из полученных JSON без замены расчётов. Поля exact проверены по рациональным эталонам, abs_error — по разности сохранённых float. Истинная ошибка относительно рационального интеграла проверена отдельно. Допуск для сравнения с формулой равен восьми ulp эталона (не более 4.45·10⁻¹⁶); это учитывает округление узлов, степеней и сумм.', '',
        '| Серия | E(10)/E(20) | E(20)/E(40) | E(40)/E(80) | Наблюдаемые порядки log₂(E(n)/E(2n)) |',
        '|---|---:|---:|---:|---|']
    for key, ratios in verification['observed_ratios'].items():
        lines.append('| '+key+' | '+' | '.join(f'{v:.10f}' for v in ratios)+' | '+', '.join(f'{math.log2(v):.7f}' for v in ratios)+' |')
    lines += ['', 'При удвоении n ошибка трапеций уменьшается примерно в 4 раза (порядок 2), а ошибка Симпсона для x⁴ — в 16 раз (порядок 4). Небольшое отклонение отношений для трапеций на x⁴ объясняется членом n⁻⁴. Эти выводы относятся к данным многочленам и сеткам; эксперимент не является доказательством для произвольных функций.', '',
        'Для x² метод Симпсона математически точен, поскольку точно интегрирует многочлены до третьей степени. Во всех четырёх строках сохранённая ошибка равна 5.551115123125783·10⁻¹⁷: это округление двоичной арифметики, а не ошибка усечения. Число 1/3 также не представимо точно как float. Для этой серии оценка порядка по отношениям неинформативна.', '',
        'В errors.svg показаны три серии с логарифмической шкалой ошибки и логарифмическим расположением n. Серия simpson x² опущена как близкая к нулю и лежащая на уровне округления; log(0) нигде не вычисляется. Хеши исходных result.json и подробности независимых проверок находятся в verification.json.', '',
        'Воспроизведение: `python3 -B build_report.py --inputs inputs --output OUTPUT_DIR`. Используется только стандартная библиотека Python.']
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    rows, verification = verify(args.inputs.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output/'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=('method','power','n','estimate','exact','abs_error'))
        writer.writeheader()
        writer.writerows(rows)
    (args.output/'verification.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
    (args.output/'report.md').write_text(make_report(rows, verification), encoding='utf-8')
    make_svg(rows, args.output/'errors.svg')


if __name__ == '__main__':
    main()
