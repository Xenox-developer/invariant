"""Independently verify received quadrature results and build deterministic reports.

Usage: python3 -B build_report.py --inputs inputs --output .
Only the Python standard library is required. Input files are never written.
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
from xml.sax.saxutils import escape

sys.dont_write_bytecode = True
NS = (10, 20, 40, 80)
METHODS = ("trapezoid", "simpson")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def rational_sum(method, power, n):
    """Independent quadrature on an exact rational grid, with no float samples."""
    samples = [Fraction(i, n) ** power for i in range(n + 1)]
    if method == "trapezoid":
        return (sum(samples[1:-1]) + (samples[0] + samples[-1]) / 2) / n
    return (samples[0] + samples[-1]
            + 4 * sum(samples[1:-1:2]) + 2 * sum(samples[2:-1:2])) / (3 * n)


def error_formula(method, power, n):
    """Signed quadrature minus integral; positive for the nonzero cases here."""
    if method == "trapezoid":
        return (Fraction(1, 6 * n**2) if power == 2 else
                Fraction(1, 3 * n**2) - Fraction(1, 30 * n**4))
    return Fraction(0) if power == 2 else Fraction(2, 15 * n**4)


def verify(inputs):
    rows, hashes, evidence = [], {}, []
    for method in METHODS:
        raw = (inputs / method / "result.json").read_bytes()
        hashes[method] = hashlib.sha256(raw).hexdigest()
        doc = json.loads(raw)
        require(doc["method"] == method and doc["interval"] == [0, 1],
                f"Invalid metadata for {method}")
        require(sorted((r["power"], r["n"]) for r in doc["rows"]) ==
                [(p, n) for p in (2, 4) for n in NS], f"Invalid row set: {method}")
        spec = importlib.util.spec_from_file_location(
            "received_" + method, inputs / method / "solution.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # main() is not called: inputs stay untouched.
        for source in doc["rows"]:
            p, n = source["power"], source["n"]
            label = f"{method} x**{p} n={n}"
            for key in ("estimate", "exact", "abs_error"):
                require(type(source[key]) in (int, float) and math.isfinite(source[key]),
                        f"Nonfinite or nonnumeric {key}: {label}")
            exact = Fraction(1, p + 1)
            formula = error_formula(method, p, n)
            independent = rational_sum(method, p, n)
            require(independent - exact == formula, f"Exact formula failed: {label}")
            estimate = source["estimate"]
            tolerance = 8 * math.ulp(float(independent))
            residual = abs(Fraction(estimate) - independent)
            require(residual <= Fraction(tolerance), f"Rational verification failed: {label}")
            require(source["exact"] == float(exact), f"Wrong reference: {label}")
            require(source["abs_error"] == abs(estimate - source["exact"]),
                    f"Wrong reported error: {label}")
            true_error = abs(Fraction(estimate) - exact)
            require(abs(true_error - formula) <= Fraction(tolerance),
                    f"Error formula mismatch: {label}")
            executed = module.integrate(lambda x: x**p, 0, 1, n)
            require(executed == estimate, f"Implementation differs from artifact: {label}")
            rows.append({"method": method, **source})
            evidence.append({"method": method, "power": p, "n": n,
                             "exact_rational_quadrature": str(independent),
                             "exact_signed_error_formula": str(formula),
                             "estimate_minus_rational_quadrature": float(Fraction(estimate) - independent),
                             "error_against_rational_integral": float(true_error),
                             "roundoff_tolerance": tolerance,
                             "implementation_matches_received": True})
    ratios = {}
    for method, p in (("trapezoid", 2), ("trapezoid", 4), ("simpson", 4)):
        errors = {r["n"]: r["abs_error"] for r in rows
                  if r["method"] == method and r["power"] == p}
        values = [errors[n] / errors[2*n] for n in NS[:-1]]
        target = 4 if method == "trapezoid" else 16
        require(all(abs(v - target) < .015 for v in values), "Unexpected convergence")
        ratios[f"{method}_x{p}"] = values
    quadratic_roundoff = all(r["abs_error"] <= 8 * math.ulp(1/3)
                              for r in rows if r["method"] == "simpson" and r["power"] == 2)
    require(quadratic_roundoff, "Simpson quadratic exceeds roundoff")
    verification = {"passed": True, "rows_checked": len(rows), "input_sha256": hashes,
                    "observed_ratios": ratios,
                    "conclusions": {"trapezoid_order": 2, "simpson_order": 4,
                                    "simpson_x2_exact_within_roundoff": quadratic_roundoff},
                    "verification_method": "Exact Fraction grid sum, exact error identities, eight-ULP bound, and execution of both received integrators",
                    "row_verification": evidence}
    return rows, verification


def svg_graph(rows):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="850" height="540" viewBox="0 0 850 540">',
             '<title>Quadrature absolute errors on [0,1]</title>',
             '<desc>Logarithmic error axis; Simpson x**2 is omitted because its errors are rounding noise.</desc>',
             '<rect width="850" height="540" fill="white"/>',
             '<g font-family="sans-serif" font-size="14" fill="#222">',
             '<text x="90" y="28">Absolute integration error (log scale)</text>']
    x = lambda n: 90 + (n - 10) / 70 * 650
    y = lambda error: 440 - (math.log10(error) + 9) / 7 * 370
    for exponent in range(-9, -1):
        yy = y(10**exponent)
        parts.append(f'<path d="M90 {yy:.3f} H740" stroke="#ddd"/>')
        parts.append(f'<text x="24" y="{yy+5:.3f}">10^{exponent}</text>')
    parts.append('<path d="M90 65 V440 H750" fill="none" stroke="#222"/>')
    for n in NS:
        parts.append(f'<text x="{x(n)-8:.3f}" y="462">{n}</text>')
    parts.append('<text x="350" y="490">n (subintervals)</text>')
    for i, (method, p, color) in enumerate((("trapezoid", 2, "#1769aa"),
                                            ("trapezoid", 4, "#bc4512"),
                                            ("simpson", 4, "#21833b"))):
        data = sorted((r for r in rows if r["method"] == method and r["power"] == p),
                      key=lambda r: r["n"])
        require(all(r["abs_error"] > 0 for r in data), "Cannot plot nonpositive error")
        points = " ".join(f'{x(r["n"]):.3f},{y(r["abs_error"]):.3f}' for r in data)
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for r in data:
            parts.append(f'<circle cx="{x(r["n"]):.3f}" cy="{y(r["abs_error"]):.3f}" r="3" fill="{color}"/>')
        parts.append(f'<text x="{90+i*235}" y="522" fill="{color}">{escape(method)} x**{p}</text>')
    parts.append('</g></svg>\n')
    return "\n".join(parts)


def report(rows, verification):
    text = """# Проверка точности численного интегрирования

Это малый детерминированный численный эксперимент: на отрезке [0,1]
интегрируются x**2 и x**4 при n = 10, 20, 40, 80 равных подынтервалах.
Точные эталоны — рациональные числа 1/3 и 1/5. Составная формула трапеций
(trapezoid) использует веса 1/2 на концах и 1 внутри, с множителем h=1/n.
Составная формула Симпсона (simpson) при чётном n использует веса
1,4,2,...,4,1 и множитель h/3.

Для знаковой ошибки «квадратура минус интеграл» точные тождества таковы:
trapezoid x**2: 1/(6n²); trapezoid x**4: 1/(3n²) − 1/(30n⁴);
simpson x**2: 0; simpson x**4: 2/(15n⁴).
Они следуют из сумм степеней на равномерной сетке; для Симпсона также
можно использовать S_n = (4T_n − T_(n/2))/3. Обе ненулевые ошибки положительны.

Проверка независима от полученных вычислений: все 16 квадратур заново
суммированы на рациональной сетке через Fraction, без округления, и сверены
с этими тождествами. Полученные оценки сравниваются с рациональными
значениями с допуском 8 ULP; обе исходные функции integrate выполнены
для каждой строки и точно воспроизвели полученные оценки. Исходные файлы
не изменялись. SHA-256 исходных байтов JSON и построчные свидетельства
сохранены в verification.json. comparison.csv сохраняет все полученные
числовые значения; расчёты ими не заменяются.

Отношения соседних полученных абсолютных ошибок E(n)/E(2n):

| Метод и степень | 10→20 | 20→40 | 40→80 |
|---|---:|---:|---:|
"""
    for name, values in verification["observed_ratios"].items():
        text += f'| {name} | ' + ' | '.join(f'{v:.10f}' for v in values) + ' |\n'
    text += """
Отношения около 4 подтверждают второй порядок трапеций, а около 16 —
четвёртый порядок Симпсона для x**4. Небольшое отклонение трапеций для
x**4 от 4 объясняет член −1/(30n⁴). Это измеренные порядки на выбранных
сетках, согласующиеся с точными формулами; эксперимент не является
статистическим исследованием или доказательством для любых функций.

Формула Симпсона для x**2 точна в рациональной арифметике. В полученных
данных ошибка относительно float(1/3) равна 5.551115123125783e-17 на всех
четырёх сетках: это один ULP, то есть округление двоичной арифметики.
Сам эталон float(1/3) также округлён; ошибка относительно рационального
1/3 отдельно указана в verification.json. Порядок по этому шуму оценивать
нельзя. Поэтому почти нулевая серия simpson x**2 исключена из errors.svg.
На графике ось ошибки логарифмическая (основание 10), ось n линейная;
нулевые ошибки не логарифмируются.

Воспроизведение средствами стандартной библиотеки Python:
`python3 -B build_report.py --inputs inputs --output .`
Параметр --output допускает новый каталог; результаты не зависят от
времени запуска и абсолютных путей.
"""
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows, verification = verify(args.inputs.resolve())
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "comparison.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("method", "power", "n", "estimate", "exact", "abs_error"))
        writer.writeheader()
        writer.writerows(rows)
    (args.output / "verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (args.output / "errors.svg").write_text(svg_graph(rows), encoding="utf-8")
    (args.output / "report.md").write_text(report(rows, verification), encoding="utf-8")


if __name__ == "__main__":
    main()
