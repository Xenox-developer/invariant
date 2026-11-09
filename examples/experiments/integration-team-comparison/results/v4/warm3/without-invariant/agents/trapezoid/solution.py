"""Composite trapezoidal integration and the assigned accuracy experiment."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Integrate f on [a, b] using n uniform trapezoids."""
    if n <= 0:
        raise ValueError("n must be positive")
    h = (b - a) / n
    samples = [f(a + i * h) if i < n else f(b) for i in range(n + 1)]
    return h * math.fsum(
        value * (0.5 if i in (0, n) else 1.0)
        for i, value in enumerate(samples)
    )


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
    result = {"method": "trapezoid", "interval": [0, 1], "rows": rows}
    Path(__file__).with_name("result.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
