"""Composite Simpson integration and the assigned polynomial experiment."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Sample n+1 uniform grid points and apply composite Simpson's rule."""
    if n <= 0 or n % 2:
        raise ValueError("n must be a positive even integer")
    h = (b - a) / n
    samples = [f(a + i * h if i < n else b) for i in range(n + 1)]
    weighted_sum = math.fsum(
        value * (1 if i in (0, n) else 4 if i % 2 else 2)
        for i, value in enumerate(samples)
    )
    return h * weighted_sum / 3


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
    Path(__file__).with_name("result.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
