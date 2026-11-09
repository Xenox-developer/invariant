"""Composite trapezoidal integration and the requested accuracy experiment."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Sample f once at each of n+1 uniform nodes, weighting endpoints by 1/2."""
    if n <= 0:
        raise ValueError("n must be positive")
    h = (b - a) / n
    samples = []
    for i in range(n + 1):
        x = b if i == n else a + i * h
        weight = 0.5 if i in (0, n) else 1.0
        samples.append(weight * f(x))
    return h * math.fsum(samples)


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
    result = {"method": "trapezoid", "interval": [0, 1], "rows": rows}
    Path(__file__).with_name("result.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
