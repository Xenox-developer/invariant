"""Composite Simpson quadrature and the assigned accuracy experiment."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Integrate f over [a, b] using n even, positive uniform subintervals.

    Evaluate each of the n+1 grid points exactly once. The Simpson weights
    are 1 at the endpoints, 4 at odd interior indices, and 2 at even ones.
    """
    if n <= 0 or n % 2:
        raise ValueError("Simpson integration requires positive, even n")
    h = (b - a) / n

    def weighted_samples():
        for i in range(n + 1):
            x = b if i == n else a + i * h
            weight = 1 if i in (0, n) else (4 if i % 2 else 2)
            yield weight * f(x)

    return (h / 3) * math.fsum(weighted_samples())


def main():
    rows = []
    for power in (2, 4):
        for n in (10, 20, 40, 80):
            estimate = integrate(lambda x: x ** power, 0, 1, n)
            exact = 1 / (power + 1)
            rows.append({
                "power": power,
                "n": n,
                "estimate": estimate,
                "exact": exact,
                "abs_error": abs(estimate - exact),
            })
    result = {"method": "simpson", "interval": [0, 1], "rows": rows}
    Path("result.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
