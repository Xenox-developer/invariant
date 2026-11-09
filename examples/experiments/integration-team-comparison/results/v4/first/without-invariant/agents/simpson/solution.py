"""Composite Simpson quadrature and the assigned polynomial experiment.

Run with ``python3 -B solution.py`` to regenerate result.json.
"""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Integrate f on [a, b] using n equal, paired subintervals.

    Sample each of the n+1 grid points once. Simpson weights are
    1, 4, 2, 4, ..., 2, 4, 1, scaled by the grid spacing divided by 3.
    """
    if n <= 0 or n % 2:
        raise ValueError("Simpson integration requires a positive even n")
    h = (b - a) / n
    values = [f(a + i * h if i < n else b) for i in range(n + 1)]
    weighted_sum = math.fsum(
        value * (1 if i == 0 or i == n else 4 if i % 2 else 2)
        for i, value in enumerate(values)
    )
    return (h / 3) * weighted_sum


def main():
    rows = []
    for power in (2, 4):
        exact = 1 / (power + 1)
        for n in (10, 20, 40, 80):
            estimate = integrate(lambda x: x ** power, 0, 1, n)
            rows.append({
                "power": power,
                "n": n,
                "estimate": estimate,
                "exact": exact,
                "abs_error": abs(estimate - exact),
            })
    result = {"method": "simpson", "interval": [0, 1], "rows": rows}
    Path("result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
