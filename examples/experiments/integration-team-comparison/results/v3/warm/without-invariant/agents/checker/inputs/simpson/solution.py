"""Composite Simpson integration and reproducible polynomial accuracy results."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Integrate f on [a, b] using n positive, even uniform subintervals.

    Each of the n + 1 grid points is sampled exactly once. Simpson's
    weights are 1 at the endpoints, 4 at odd indices, and 2 at even ones.
    """
    if n <= 0 or n % 2:
        raise ValueError("n must be positive and even")
    h = (b - a) / n
    terms = []
    for i in range(n + 1):
        x = b if i == n else a + i * h
        weight = 1 if i in (0, n) else (4 if i % 2 else 2)
        terms.append(weight * f(x))
    return (h / 3) * math.fsum(terms)


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
    Path(__file__).with_name("result.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
