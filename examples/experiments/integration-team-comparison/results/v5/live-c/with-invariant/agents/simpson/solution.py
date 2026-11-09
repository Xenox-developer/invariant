"""Composite Simpson integration and a reproducible polynomial experiment."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Integrate f on [a, b] using n even, uniform subintervals."""
    if n <= 0 or n % 2:
        raise ValueError("n must be positive and even")
    h = (b - a) / n
    # Evaluate every grid point once, including both endpoints.
    values = [f(a if i == 0 else b if i == n else a + i * h)
              for i in range(n + 1)]
    weighted_sum = math.fsum(
        value * (1 if i == 0 or i == n else 4 if i % 2 else 2)
        for i, value in enumerate(values)
    )
    return h * weighted_sum / 3


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
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
