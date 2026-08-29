from __future__ import annotations

from typing import Any


def calculate_slo(target: float, bad_events: int, total_events: int) -> dict[str, Any]:
    if not 0 < target < 1:
        raise ValueError("target must be between 0 and 1 (exclusive)")
    if bad_events < 0 or total_events < 0 or bad_events > total_events:
        raise ValueError("invalid event counts")
    allowed_bad_rate = 1.0 - target
    if total_events == 0:
        return {
            "target": target,
            "actual_bad_rate": 0.0,
            "allowed_bad_rate": allowed_bad_rate,
            "burn_rate": 0.0,
            "remaining_error_budget_fraction": 1.0,
            "breached": False,
        }
    actual_bad_rate = bad_events / total_events
    burn_rate = actual_bad_rate / allowed_bad_rate
    consumed_fraction = min(1.0, actual_bad_rate / allowed_bad_rate)
    return {
        "target": target,
        "actual_bad_rate": actual_bad_rate,
        "allowed_bad_rate": allowed_bad_rate,
        "burn_rate": burn_rate,
        "remaining_error_budget_fraction": max(0.0, 1.0 - consumed_fraction),
        "breached": bool(actual_bad_rate > allowed_bad_rate),
    }


def evaluate_multiwindow_burn(
    *,
    short_window_burn: float,
    long_window_burn: float,
    policy: str = "multi_window",
    page_threshold: float = 14.4,
    ticket_threshold: float = 6.0,
) -> dict[str, Any]:
    """Google SRE-style multi-window burn-rate policy (see sre.google/workbook/alerting-on-slos).

    Requiring the SAME threshold to be crossed on BOTH a short and a long window
    is exactly what distinguishes a short transient spike from a sustained fast
    burn:
    - transient spike: short-window burn is high but the long window stays low
      because the spike gets diluted across it once averaged -> must NOT page
      (a page here would just be alert fatigue for something already over).
    - sustained fast burn: both windows elevated together -> the budget is
      genuinely being consumed fast right now -> page immediately.
    - sustained slow burn: both windows moderately elevated (above
      `ticket_threshold` but below `page_threshold`) -> non-paging ticket; it
      still meaningfully eats the error budget over time even though it is not
      an emergency.

    `page_threshold=14.4` / `ticket_threshold=6.0` are the commonly-cited example
    constants from the SRE workbook for a 1-hour/5-minute and 6-hour/30-minute
    window pair on a 2%-budget-in-28-days policy; callers with a different SLO
    window/budget should pass their own thresholds.
    """
    if policy != "multi_window":
        return {
            "page": False,
            "severity": "info",
            "reason": f"unsupported_policy:{policy}",
            "short_window_burn": short_window_burn,
            "long_window_burn": long_window_burn,
        }

    both_at_or_above = lambda threshold: short_window_burn >= threshold and long_window_burn >= threshold  # noqa: E731

    if both_at_or_above(page_threshold):
        return {
            "page": True,
            "severity": "critical",
            "reason": (
                f"sustained fast burn: short={short_window_burn:.2f} and long={long_window_burn:.2f} "
                f"both >= page_threshold={page_threshold}"
            ),
            "short_window_burn": short_window_burn,
            "long_window_burn": long_window_burn,
        }

    if both_at_or_above(ticket_threshold):
        return {
            "page": False,
            "severity": "warning",
            "reason": (
                f"sustained slow burn: short={short_window_burn:.2f} and long={long_window_burn:.2f} "
                f"both >= ticket_threshold={ticket_threshold} but below page_threshold={page_threshold}"
            ),
            "short_window_burn": short_window_burn,
            "long_window_burn": long_window_burn,
        }

    return {
        "page": False,
        "severity": "info",
        "reason": (
            f"transient or within budget: short={short_window_burn:.2f}, long={long_window_burn:.2f} "
            f"do not both cross ticket_threshold={ticket_threshold}"
        ),
        "short_window_burn": short_window_burn,
        "long_window_burn": long_window_burn,
    }
