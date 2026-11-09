"""Composite Simpson integration and a reproducible polynomial experiment."""

import json
import math
from pathlib import Path


def integrate(f, a, b, n):
    """Integrate f on [a, b] using n positive, even uniform subintervals.

    Evaluate each of the n+1 grid points once. Simpson's weights are
    1, 4, 2, 4, ..., 2, 4, 1; the weighted sum is scaled by h/3.
    """
    if not isinstance(n, int) or n <= 0 or n % 2:
        raise ValueError("n must be a positive even integer")
    h = (b - a) / n
    samples = [f(a + i * h if i < n else b) for i in range(n + 1)]
    return (h / 3) * math.fsum(
        value * (1 if i in (0, n) else 4 if i % 2 else 2)
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
    result = {"method": "simpson", "interval": [0, 1], "rows": rows}
    Path("result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
