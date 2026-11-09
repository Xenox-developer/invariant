"""Composite Simpson integration and the requested polynomial experiment."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Sample n+1 uniform grid points and apply composite Simpson's rule.

    A positive, even integer n is required. The rule is exact for cubics
    in exact arithmetic and has fourth-order error for smooth functions.
    """
    if n <= 0 or n % 2:
        raise ValueError("n must be positive and even")
    h = (b - a) / n
    samples = [f(a + i * h if i < n else b) for i in range(n + 1)]
    weighted_sum = math.fsum(
        value * (1 if i in (0, n) else 4 if i % 2 else 2)
        for i, value in enumerate(samples)
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
