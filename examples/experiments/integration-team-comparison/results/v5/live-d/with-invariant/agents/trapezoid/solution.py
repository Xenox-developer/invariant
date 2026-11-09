"""Composite trapezoidal integration and the assigned accuracy experiment."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Approximate the integral using n equal-width trapezoids.

    Evaluate f once at each of the n+1 grid points, including both ends.
    Reversed intervals are supported through the signed step size.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    h = (b - a) / n
    samples = (f(a if i == 0 else b if i == n else a + i * h)
               * (0.5 if i == 0 or i == n else 1.0)
               for i in range(n + 1))
    return h * math.fsum(samples)


def main():
    rows = []
    for power in (2, 4):
        exact = 1 / (power + 1)
        for n in (10, 20, 40, 80):
            estimate = integrate(lambda x: x ** power, 0, 1, n)
            rows.append({"power": power, "n": n, "estimate": estimate,
                         "exact": exact, "abs_error": abs(estimate - exact)})
    result = {"method": "trapezoid", "interval": [0, 1], "rows": rows}
    Path("result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
