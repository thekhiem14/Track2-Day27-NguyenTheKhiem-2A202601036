"""Anomaly detection.

`zscore_detector` is the original simple baseline (kept as-is: `method="zscore"`
must stay a plain, easy-to-explain mean/std check). `mad_detector` is a robust
median/MAD alternative with its zero-MAD edge case fixed (see below). `auto_detector`
is the context-aware upgrade: same-weekday/same-segment baseline when the caller can
supply one, a rolling + median/MAD robust baseline otherwise, and an EWMA fallback
when MAD degenerates to zero.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def zscore_detector(current: float, history: Iterable[float], threshold: float = 3.0) -> dict[str, Any]:
    values = np.asarray(list(history), dtype=float)
    if values.size < 3:
        return {"is_anomaly": False, "score": 0.0, "method": "zscore", "reason": "insufficient_history"}
    mean = float(np.mean(values))
    std = float(np.std(values))
    if std == 0:
        score = float("inf") if float(current) != mean else 0.0
    else:
        score = abs(float(current) - mean) / std
    return {
        "is_anomaly": bool(score > threshold),
        "score": float(score),
        "method": "zscore",
        "reason": f"mean={mean:.3f}, std={std:.3f}, threshold={threshold}",
    }


def mad_detector(current: float, history: Iterable[float], threshold: float = 3.5) -> dict[str, Any]:
    """Robust median/MAD detector (modified z-score, Iglewicz & Hoaglin 1993).

    Less sensitive than z-score to a few extreme points already sitting in
    `history` (those points barely move the median, whereas they can inflate
    mean/std enough to mask a *new* anomaly under z-score).
    """
    values = np.asarray(list(history), dtype=float)
    if values.size < 5:
        return {"is_anomaly": False, "score": 0.0, "method": "mad", "reason": "insufficient_history"}
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad == 0:
        # Degenerate case: history is (near-)constant, so the modified z-score is
        # undefined/infinite for literally any deviation. The original starter
        # returned `is_anomaly=False` unconditionally here, which silently misses
        # a real anomaly against a flat history (e.g. history=[1000]*5,
        # current=50). Fall back to an exact-match check instead.
        is_anomaly = float(current) != median
        return {
            "is_anomaly": is_anomaly,
            "score": float("inf") if is_anomaly else 0.0,
            "method": "mad",
            "reason": f"median={median:.3f}, mad=0 (degenerate history), exact_match_fallback=true",
        }
    modified_z = 0.6745 * abs(float(current) - median) / mad
    return {
        "is_anomaly": bool(modified_z > threshold),
        "score": float(modified_z),
        "method": "mad",
        "reason": f"median={median:.3f}, mad={mad:.3f}, threshold={threshold}",
    }


def _ewma(values: np.ndarray, alpha: float = 0.3) -> float:
    if values.size == 0:
        return 0.0
    result = float(values[0])
    for v in values[1:]:
        result = alpha * float(v) + (1 - alpha) * result
    return result


def _robust_baseline(values: np.ndarray) -> tuple[float, float]:
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    return median, mad


def _score_against(current: float, median: float, mad: float, threshold: float) -> float:
    if mad == 0:
        return float("inf") if float(current) != median else 0.0
    return 0.6745 * abs(float(current) - median) / mad


def auto_detector(
    current: float,
    history: Iterable[float],
    *,
    threshold: float = 3.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Context-aware detector.

    Priority order:
    1. `context["same_segment_history"]` (e.g. the same weekday's past values) if
       the caller can supply it -- this removes weekday seasonality directly,
       instead of requiring the caller to pre-filter `history` before calling in.
    2. A rolling window (last 14 points) of `history` with a robust median/MAD
       baseline, which adapts to recent trend/growth better than an all-time mean
       and resists a handful of past anomalies skewing the baseline.
    3. If MAD degenerates to 0 (near-constant window), fall back to an
       EWMA-smoothed baseline with the window's std as spread, instead of the
       zero-MAD trap `mad_detector` has to guard against.

    `context["known_event"]` (promo, holiday, planned migration, ...) widens the
    threshold rather than suppressing detection outright: legitimate events raise
    variance but a true collapse (e.g. -70%) should still fire.
    """
    context = context or {}
    known_event = context.get("known_event")
    effective_threshold = threshold * (1.6 if known_event else 1.0)

    same_segment = context.get("same_segment_history")
    if same_segment is not None:
        seg = np.asarray(list(same_segment), dtype=float)
        if seg.size >= 3:
            median, mad = _robust_baseline(seg)
            if mad > 0:
                score = _score_against(current, median, mad, effective_threshold)
                reason = f"segment_median={median:.3f}, segment_mad={mad:.3f}, threshold={effective_threshold}"
                method = "auto:same_segment_mad"
            else:
                mean = float(np.mean(seg))
                std = float(np.std(seg))
                score = abs(float(current) - mean) / std if std > 0 else (float("inf") if float(current) != mean else 0.0)
                reason = f"segment_mean={mean:.3f}, segment_std={std:.3f}, threshold={effective_threshold}"
                method = "auto:same_segment_zscore"
            if known_event:
                reason += f"; known_event={known_event}"
            return {
                "is_anomaly": bool(score > effective_threshold),
                "score": float(score),
                "method": method,
                "reason": reason,
            }

    values = np.asarray(list(history), dtype=float)
    if values.size < 3:
        return {"is_anomaly": False, "score": 0.0, "method": "auto:insufficient_history", "reason": "insufficient_history"}

    window = values[-14:] if values.size > 14 else values
    median, mad = _robust_baseline(window)

    if mad > 0:
        score = _score_against(current, median, mad, effective_threshold)
        method = "auto:mad"
        reason = f"median={median:.3f}, mad={mad:.3f}, threshold={effective_threshold}, window={window.size}"
    else:
        baseline = _ewma(window)
        std = float(np.std(window))
        score = abs(float(current) - baseline) / std if std > 0 else (float("inf") if float(current) != baseline else 0.0)
        method = "auto:ewma_fallback"
        reason = f"ewma_baseline={baseline:.3f}, std={std:.3f}, threshold={effective_threshold}, window={window.size}"

    if known_event:
        reason += f"; known_event={known_event}"

    return {
        "is_anomaly": bool(score > effective_threshold),
        "score": float(score),
        "method": method,
        "reason": reason,
    }


def detect_anomaly(
    current: float,
    history: Iterable[float],
    *,
    method: str = "auto",
    threshold: float = 3.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stable lab API.

    - `zscore`: plain mean/std check. Good default, but two known failure modes:
      (1) it assumes a roughly-normal, stationary metric -- weekday/weekend
      seasonality or a growth trend baked into `history` inflates std enough to
      hide a real drop, or flags a normal Saturday dip as anomalous; (2) mean/std
      are not robust -- a handful of past anomalies already sitting in `history`
      drag the mean and inflate the std, raising the bar for detecting a *new*
      anomaly (masking effect).
    - `mad`: robust median/MAD modified z-score; degenerate mad=0 handled via an
      exact-match fallback instead of silently reporting "not anomalous".
    - `auto`: context-aware -- same-segment baseline when available, else a
      rolling robust baseline, else an EWMA fallback. See `auto_detector`.
    """
    if method == "mad":
        return mad_detector(current, history)
    if method == "zscore":
        return zscore_detector(current, history, threshold=threshold)
    if method == "auto":
        return auto_detector(current, history, threshold=threshold, context=context)
    raise ValueError(f"Unsupported method: {method}")
