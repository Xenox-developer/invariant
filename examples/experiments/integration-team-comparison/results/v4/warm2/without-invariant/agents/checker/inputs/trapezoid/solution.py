"""Composite trapezoid integration and the requested accuracy experiment."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Approximate the integral of f using n uniform trapezoids.

    Evaluate each of the n+1 grid points once, with half weight at each
    endpoint. Reversed limits are supported through the signed step size.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    h = (b - a) / n
    values = (f(a if i == 0 else b if i == n else a + i * h)
              * (0.5 if i in (0, n) else 1.0)
              for i in range(n + 1))
    return h * math.fsum(values)


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
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
