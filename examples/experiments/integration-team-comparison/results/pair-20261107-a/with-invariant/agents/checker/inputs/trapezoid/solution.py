"""Composite trapezoidal integration and reproducible accuracy measurements."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Integrate f over [a, b] using n uniform trapezoids.

    Evaluate each of the n+1 grid points once, including both endpoints.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    h = (b - a) / n
    values = [f(a + i * h) for i in range(n)]
    values.append(f(b))
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
    Path("result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
