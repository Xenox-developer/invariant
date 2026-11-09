"""Composite trapezoidal integration using only the standard library."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Approximate the integral of f over [a, b] with n uniform panels."""
    if n <= 0:
        raise ValueError("n must be positive")
    h = (b - a) / n
    # Sample every grid point exactly once, including both endpoints.
    values = [f(a if i == 0 else b if i == n else a + i * h)
              for i in range(n + 1)]
    return h * math.fsum(
        value * (0.5 if i == 0 or i == n else 1.0)
        for i, value in enumerate(values)
    )


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
        json.dumps(result, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
