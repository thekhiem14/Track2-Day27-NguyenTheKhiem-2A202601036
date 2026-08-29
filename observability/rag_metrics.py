from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from observability.anomaly import mad_detector, zscore_detector


def approximate_token_lengths(texts: Iterable[str]) -> list[int]:
    # Deliberately simple proxy; no tokenizer/model download needed.
    return [len(str(t).split()) for t in texts]


def detect_text_length_shift(
    current_texts: Iterable[str],
    baseline_batch_means: Iterable[float],
    *,
    threshold: float = 3.0,
) -> dict[str, Any]:
    lengths = approximate_token_lengths(current_texts)
    current_mean = float(np.mean(lengths)) if lengths else 0.0
    result = zscore_detector(current_mean, baseline_batch_means, threshold=threshold)
    result["metric"] = "mean_text_length"
    result["current_mean"] = current_mean
    return result


def detect_embedding_norm_shift(
    current_norms: Iterable[float],
    baseline_norms: Iterable[float],
    *,
    threshold: float = 3.5,
) -> dict[str, Any]:
    """Embedding-space drift signal via the mean L2 norm of embedding vectors.

    No embedding model/network call is required: callers pass precomputed norms
    (e.g. `np.linalg.norm(embedding)` per document). A shift in the mean norm is a
    cheap, model-agnostic proxy for embedding drift -- a re-indexed KB with a
    different embedding model version, truncated/garbled input text, or an
    encoding bug typically shows up as a norm-scale change even before anyone
    inspects cosine similarities.

    Uses the robust median/MAD detector (`observability.anomaly.mad_detector`)
    rather than mean/std: embedding norms across a healthy corpus are usually
    tightly clustered, so a couple of outlier documents already in
    `baseline_norms` should not be allowed to inflate the spread and mask a real
    shift in `current_norms`.
    """
    current = list(current_norms)
    baseline = list(baseline_norms)
    if not current or not baseline:
        return {"is_anomaly": False, "score": 0.0, "method": "embedding_norm_mad", "reason": "empty_input"}

    current_mean = float(np.mean(current))
    result = mad_detector(current_mean, baseline, threshold=threshold)
    result["method"] = "embedding_norm_mad"
    result["metric"] = "mean_embedding_norm"
    result["current_mean"] = current_mean
    return result
