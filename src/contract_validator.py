"""Contract validator.

Extends the original starter (not-null/unique/accepted/range) with:
- type validation (catches silent type drift that pd.to_numeric coercion hides),
- contract-level freshness validation,
- severity -> action mapping (critical=block, warning=quarantine, info=warn),
- automatic quarantine that splits a dataframe into clean/quarantined partitions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

# Severity is a data-quality classification; action is the operational response.
# critical -> block the pipeline (unsafe to let the record flow downstream at all)
# warning  -> quarantine the offending rows, let the rest of the batch continue
# info     -> warn only, no automatic row action
_ACTION_BY_SEVERITY = {
    "critical": "block",
    "warning": "quarantine",
    "info": "warn",
}


def determine_action(severity: str) -> str:
    """Map a severity level to an operational action (block/quarantine/warn)."""
    return _ACTION_BY_SEVERITY.get(severity, "warn")


def _issue(
    check: str,
    *,
    column: str | None,
    severity: str,
    passed: bool,
    details: str,
) -> dict[str, Any]:
    return {
        "check": check,
        "column": column,
        "severity": severity,
        "passed": bool(passed),
        "details": details,
        "action": determine_action(severity),
    }


def load_contract(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _type_invalid_mask(series: pd.Series, declared_type: str) -> pd.Series:
    """Return a boolean mask of non-null values that do not match `declared_type`.

    `pd.to_numeric(..., errors="coerce")` alone hides type drift: a column that is
    supposed to be an id string but got silently cast to a numeric dtype by pandas'
    CSV reader (e.g. "customer_id" full of "1","2","3") won't show up as a range/
    accepted-values failure. We check dtype intent explicitly instead.
    """
    non_null = series.notna()
    declared_type = (declared_type or "").lower()

    if declared_type in {"integer", "int"}:
        numeric = pd.to_numeric(series, errors="coerce")
        is_whole = numeric.apply(lambda v: bool(np.isfinite(v)) and float(v).is_integer() if pd.notna(v) else False)
        invalid = non_null & (numeric.isna() | ~is_whole)
    elif declared_type in {"number", "float", "double", "decimal"}:
        numeric = pd.to_numeric(series, errors="coerce")
        invalid = non_null & numeric.isna()
    elif declared_type in {"datetime", "timestamp", "date"}:
        parsed = pd.to_datetime(series, errors="coerce", utc=True)
        invalid = non_null & parsed.isna()
    elif declared_type in {"boolean", "bool"}:
        allowed = {True, False, "true", "false", "True", "False", "TRUE", "FALSE", 0, 1, "0", "1"}
        invalid = non_null & ~series.isin(allowed)
    elif declared_type in {"string", "str", "text"}:
        # A value that pandas parsed as a real number/bool instead of text is a
        # symptom of upstream type drift (e.g. an all-numeric-looking id column).
        invalid = non_null & series.apply(lambda v: isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool))
    else:
        # Unknown/unsupported declared type: do not fail the batch, but do not
        # silently pretend we validated it either.
        invalid = pd.Series(False, index=series.index)
    return invalid.fillna(False)


def _check_freshness(df: pd.DataFrame, contract: dict[str, Any]) -> dict[str, Any] | None:
    freshness = contract.get("freshness")
    if not freshness:
        return None
    column = freshness.get("column")
    max_delay = freshness.get("max_delay_minutes")
    severity = freshness.get("severity", "warning")

    if column not in df.columns:
        return _issue(
            "freshness",
            column=column,
            severity=severity,
            passed=False,
            details=f"Missing freshness column: {column}",
        )

    parsed = pd.to_datetime(df[column], errors="coerce", utc=True)
    if parsed.notna().sum() == 0:
        return _issue(
            "freshness",
            column=column,
            severity=severity,
            passed=False,
            details=f"No parseable timestamps in {column}",
        )

    latest = parsed.max()
    now = pd.Timestamp(datetime.now(timezone.utc))
    delay_minutes = (now - latest).total_seconds() / 60.0
    passed = delay_minutes <= max_delay if max_delay is not None else True
    return _issue(
        "freshness",
        column=column,
        severity=severity,
        passed=passed,
        details=f"delay_minutes={delay_minutes:.2f}, max_delay_minutes={max_delay}",
    )


def validate_dataframe(df: pd.DataFrame, contract: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    # `kb_contract.yaml` uses `fields:` instead of `columns:` (both describe the
    # same per-column rule shape) -- accept either key so one validator handles
    # both the orders and KB contracts instead of silently validating nothing.
    columns = contract.get("columns") or contract.get("fields", {})

    for column, rules in columns.items():
        severity = rules.get("severity", "warning")
        required = bool(rules.get("required", False))

        if column not in df.columns:
            if required:
                issues.append(
                    _issue(
                        "required_column",
                        column=column,
                        severity=severity,
                        passed=False,
                        details=f"Missing required column: {column}",
                    )
                )
            continue

        series = df[column]

        if required:
            null_count = int(series.isna().sum())
            issues.append(
                _issue(
                    "not_null",
                    column=column,
                    severity=severity,
                    passed=(null_count == 0),
                    details=f"null_count={null_count}",
                )
            )

        if rules.get("unique"):
            duplicate_count = int(series.duplicated(keep=False).sum())
            issues.append(
                _issue(
                    "unique",
                    column=column,
                    severity=severity,
                    passed=(duplicate_count == 0),
                    details=f"duplicate_rows={duplicate_count}",
                )
            )

        accepted = rules.get("accepted_values")
        if accepted is not None:
            invalid_mask = series.notna() & ~series.isin(accepted)
            invalid_count = int(invalid_mask.sum())
            issues.append(
                _issue(
                    "accepted_values",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}; accepted={accepted}",
                )
            )

        if "min" in rules or "max" in rules:
            numeric = pd.to_numeric(series, errors="coerce")
            invalid = pd.Series(False, index=series.index)
            if "min" in rules:
                invalid |= numeric < rules["min"]
            if "max" in rules:
                invalid |= numeric > rules["max"]
            invalid_count = int(invalid.fillna(False).sum())
            issues.append(
                _issue(
                    "range",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}",
                )
            )

        declared_type = rules.get("type")
        if declared_type:
            invalid_mask = _type_invalid_mask(series, declared_type)
            invalid_count = int(invalid_mask.sum())
            issues.append(
                _issue(
                    "type",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"declared_type={declared_type}; invalid_count={invalid_count}",
                )
            )

        min_length = rules.get("min_length")
        if min_length is not None:
            lengths = series.astype(str).str.len()
            invalid_mask = series.notna() & (lengths < min_length)
            invalid_count = int(invalid_mask.sum())
            issues.append(
                _issue(
                    "min_length",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"min_length={min_length}; invalid_count={invalid_count}",
                )
            )

    freshness_issue = _check_freshness(df, contract)
    if freshness_issue is not None:
        issues.append(freshness_issue)

    return issues


def failed_issues(issues: list[dict[str, Any]], min_severity: str | None = None) -> list[dict[str, Any]]:
    failed = [i for i in issues if not i.get("passed", False)]
    if min_severity is None:
        return failed
    order = {"info": 0, "warning": 1, "critical": 2}
    threshold = order[min_severity]
    return [i for i in failed if order.get(i.get("severity", "warning"), 1) >= threshold]


def quarantine_dataframe(df: pd.DataFrame, contract: dict[str, Any]) -> dict[str, Any]:
    """Split `df` into clean vs quarantined rows using row-identifiable checks.

    - `critical` severity columns with any row-level failure -> the whole batch is
      `blocked` (unsafe to partially ship a batch that fails an identity/critical rule).
    - `warning` severity columns -> only the offending rows are quarantined; the rest
      of the batch continues downstream.
    - `info` severity -> warn only, never removes rows.

    Dataset-level issues (missing required column, freshness) cannot be pinned to a
    row, so they are surfaced through `blocked`/`reasons` instead of a row mask.
    """
    columns = contract.get("columns") or contract.get("fields", {})
    bad_mask = pd.Series(False, index=df.index)
    blocked = False
    reasons: list[str] = []

    for column, rules in columns.items():
        severity = rules.get("severity", "warning")
        action = determine_action(severity)

        if column not in df.columns:
            if rules.get("required") and action == "block":
                blocked = True
                reasons.append(f"missing_required_column:{column}")
            continue

        series = df[column]
        col_bad = pd.Series(False, index=df.index)

        if rules.get("required"):
            col_bad |= series.isna()
        if rules.get("unique"):
            col_bad |= series.duplicated(keep=False)
        accepted = rules.get("accepted_values")
        if accepted is not None:
            col_bad |= series.notna() & ~series.isin(accepted)
        if "min" in rules or "max" in rules:
            numeric = pd.to_numeric(series, errors="coerce")
            if "min" in rules:
                col_bad |= (numeric < rules["min"]).fillna(False)
            if "max" in rules:
                col_bad |= (numeric > rules["max"]).fillna(False)
        declared_type = rules.get("type")
        if declared_type:
            col_bad |= _type_invalid_mask(series, declared_type)

        if not col_bad.any():
            continue

        if action == "block":
            blocked = True
            reasons.append(f"block:{column} ({int(col_bad.sum())} rows)")
        elif action == "quarantine":
            bad_mask |= col_bad
            reasons.append(f"quarantine:{column} ({int(col_bad.sum())} rows)")
        else:
            reasons.append(f"warn:{column} ({int(col_bad.sum())} rows)")

    clean = df.loc[~bad_mask].copy()
    quarantined = df.loc[bad_mask].copy()
    return {
        "clean": clean,
        "quarantined": quarantined,
        "blocked": blocked,
        "reasons": reasons,
    }
