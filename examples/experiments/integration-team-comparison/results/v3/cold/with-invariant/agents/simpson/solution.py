"""Composite Simpson integration and reproducible polynomial experiments."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Approximate the integral of f on [a, b] using n even subintervals.

    Sample each of the n+1 uniform grid points exactly once. Simpson's
    weights are 1 at the endpoints, 4 at odd indices, and 2 at even
    interior indices. The rule integrates cubics exactly up to roundoff;
    for smooth functions its global error is of order (b-a)/n to power 4.
    """
    if n <= 0 or n % 2:
        raise ValueError("n must be positive and even")
    h = (b - a) / n
    weighted_samples = []
    for i in range(n + 1):
        x = b if i == n else a + i * h
        weight = 1 if i == 0 or i == n else (4 if i % 2 else 2)
        weighted_samples.append(weight * f(x))
    return h * math.fsum(weighted_samples) / 3


def main():
    """Write all eight computed estimates and their absolute errors."""
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
    Path(__file__).with_name("result.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
