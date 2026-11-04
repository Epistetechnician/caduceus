"""Seeded paired bootstrap and Wilson intervals. Deterministic given the seed.

Results are returned as integer micro-units (x 1_000_000) so they can enter the
ledger, which rejects floats.
"""

from __future__ import annotations

import math
import random
from typing import Dict, Sequence

MICRO = 1_000_000


def paired_bootstrap(diffs: Sequence[float], seed: int, resamples: int = 2000, alpha: float = 0.05) -> Dict[str, int]:
    n = len(diffs)
    if n == 0:
        raise ValueError("no paired differences")
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        s = 0.0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int((alpha / 2) * resamples)]
    hi = means[min(resamples - 1, int((1 - alpha / 2) * resamples))]
    mean = sum(diffs) / n
    return {
        "n": n,
        "mean_micro": int(round(mean * MICRO)),
        "lower_micro": int(round(lo * MICRO)),
        "upper_micro": int(round(hi * MICRO)),
    }


def wilson(k: int, n: int, z: float = 1.959963984540054) -> Dict[str, int]:
    if n <= 0 or not 0 <= k <= n:
        raise ValueError("invalid counts")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {
        "k": k,
        "n": n,
        "lower_micro": int(round((centre - half) * MICRO)),
        "upper_micro": int(round((centre + half) * MICRO)),
    }


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Nearest-rank quantile of an already sorted sample (q in (0, 1])."""
    if not sorted_values:
        raise ValueError("empty sample")
    rank = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[rank - 1]


def quantile_bootstrap(
    values: Sequence[int], q: float, seed: int, resamples: int = 2000, alpha: float = 0.05
) -> Dict[str, int]:
    """Point estimate and seeded percentile-bootstrap CI for one quantile.

    Values are integers (e.g. microseconds) and so are the results. Note the
    nearest-rank p99 of a sample of n < 100 is its maximum.
    """
    n = len(values)
    if n == 0:
        raise ValueError("empty sample")
    rng = random.Random(seed)
    stats = []
    for _ in range(resamples):
        stats.append(quantile(sorted(values[rng.randrange(n)] for _ in range(n)), q))
    stats.sort()
    return {
        "n": n,
        "point": int(quantile(sorted(values), q)),
        "lower": int(stats[int((alpha / 2) * resamples)]),
        "upper": int(stats[min(resamples - 1, int((1 - alpha / 2) * resamples))]),
    }
