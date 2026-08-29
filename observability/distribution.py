from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def _ks_statistic(a: np.ndarray, b: np.ndarray) -> float:
    """Two-sample Kolmogorov-Smirnov statistic (max gap between empirical CDFs).

    Implemented without scipy (not a project dependency): sort both samples, then
    at every observed value compare what fraction of each sample is <= that value.
    """
    a = np.sort(a)
    b = np.sort(b)
    all_vals = np.concatenate([a, b])
    cdf_a = np.searchsorted(a, all_vals, side="right") / a.size
    cdf_b = np.searchsorted(b, all_vals, side="right") / b.size
    return float(np.max(np.abs(cdf_a - cdf_b)))


def _ks_critical_value(n: int, m: int, alpha: float = 0.05) -> float:
    # Asymptotic two-sample KS critical value: c(alpha) * sqrt((n+m)/(n*m)).
    c_alpha = 1.36  # alpha = 0.05
    return c_alpha * float(np.sqrt((n + m) / (n * m)))


def _population_stability_index(current: np.ndarray, baseline: np.ndarray, bins: int = 10) -> float:
    """PSI: how much a distribution shifted relative to baseline-derived bins.

    Rule of thumb: PSI < 0.1 no meaningful shift, 0.1-0.25 moderate, > 0.25 major.
    """
    quantiles = np.unique(np.quantile(baseline, np.linspace(0, 1, bins + 1)))
    if quantiles.size < 3:
        return 0.0
    edges = quantiles.copy()
    edges[0] = -np.inf
    edges[-1] = np.inf

    base_counts, _ = np.histogram(baseline, bins=edges)
    cur_counts, _ = np.histogram(current, bins=edges)

    base_pct = np.clip(base_counts / max(baseline.size, 1), 1e-6, None)
    cur_pct = np.clip(cur_counts / max(current.size, 1), 1e-6, None)

    return float(np.sum((cur_pct - base_pct) * np.log(cur_pct / base_pct)))


def detect_distribution_shift(
    current_values: Iterable[float],
    baseline_values: Iterable[float],
    *,
    ratio_threshold: float = 3.0,
    ks_alpha: float = 0.05,
    psi_threshold: float = 0.25,
) -> dict[str, Any]:
    """Distribution-shift detector combining three complementary signals.

    - `mean_ratio`: cheap, catches large scale shifts, blind to shape-only shifts.
    - `ks`: shape-aware, catches variance/skew changes even when the mean barely
      moves (e.g. a bimodal split), using an asymptotic critical value so the
      alarm threshold adapts to sample size instead of a fixed magic number.
    - `psi`: reports shift magnitude in a widely-used, human-interpretable scale
      (kept as supporting evidence, not a second independent trigger).

    Flags anomaly if EITHER the mean ratio or the KS statistic crosses its
    threshold, since they cover different failure shapes.
    """
    cur = np.asarray(list(current_values), dtype=float)
    base = np.asarray(list(baseline_values), dtype=float)
    if cur.size == 0 or base.size == 0:
        return {"is_anomaly": False, "score": 0.0, "method": "mean_ratio", "reason": "empty_input"}

    cur_mean = float(np.mean(cur))
    base_mean = float(np.mean(base))
    if base_mean == 0:
        ratio_score = float("inf") if cur_mean != 0 else 1.0
    else:
        ratio_score = max(abs(cur_mean / base_mean), abs(base_mean / cur_mean)) if cur_mean != 0 else float("inf")
    ratio_anomaly = ratio_score >= ratio_threshold

    ks_anomaly = False
    ks_score = 0.0
    ks_critical = 0.0
    if cur.size >= 2 and base.size >= 2:
        ks_score = _ks_statistic(cur, base)
        ks_critical = _ks_critical_value(cur.size, base.size, alpha=ks_alpha)
        ks_anomaly = ks_score > ks_critical

    psi = _population_stability_index(cur, base) if base.size >= 5 else 0.0

    is_anomaly = bool(ratio_anomaly or ks_anomaly)
    method = "mean_ratio+ks"
    reason = (
        f"baseline_mean={base_mean:.3f}, current_mean={cur_mean:.3f}, mean_ratio={ratio_score:.3f} "
        f"(threshold={ratio_threshold}); ks={ks_score:.3f} (critical={ks_critical:.3f}); psi={psi:.3f}"
    )
    return {
        "is_anomaly": is_anomaly,
        "score": float(max(ratio_score if np.isfinite(ratio_score) else 1e9, ks_score)),
        "method": method,
        "reason": reason,
        "mean_ratio": ratio_score,
        "ks_statistic": ks_score,
        "ks_critical_value": ks_critical,
        "psi": psi,
    }
