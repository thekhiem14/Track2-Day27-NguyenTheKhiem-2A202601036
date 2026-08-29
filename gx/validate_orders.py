#!/usr/bin/env python3
"""Great Expectations Core 1.21 flow for the `orders` dataset.

Builds the full modern GX pipeline requested by the lab:

    ExpectationSuite -> ValidationDefinition -> Checkpoint -> Actions

Each expectation carries a native GX `severity` (critical/warning/info). A custom
`SeverityRoutingAction` inspects the checkpoint result and turns the worst failed
severity into the same block/quarantine/warn decision used by
`src/contract_validator.py`, so both validation paths agree on the operational
response instead of just reporting pass/fail.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import great_expectations as gx
    from great_expectations.checkpoint.actions import (
        UpdateDataDocsAction,
        ValidationAction,
    )
except ImportError as exc:  # friendlier classroom failure
    raise SystemExit("great_expectations is not installed. Run: pip install -r requirements.txt") from exc

from src.contract_validator import determine_action  # reuse the same severity->action policy


# Severity -> action mirrors src/contract_validator.determine_action so a GX failure
# and a contract-validator failure page/quarantine the same way.
class SeverityRoutingAction(ValidationAction):
    """Turn the worst failed-expectation severity into a block/quarantine/warn action."""

    type: str = "severity_routing"
    name: str = "severity_routing"

    def run(self, checkpoint_result, action_context=None) -> dict[str, Any]:  # noqa: ANN001
        from great_expectations.expectations import metadata_types

        rank = {
            metadata_types.FailureSeverity.INFO: 0,
            metadata_types.FailureSeverity.WARNING: 1,
            metadata_types.FailureSeverity.CRITICAL: 2,
        }
        worst_severity = None
        for result in checkpoint_result.run_results.values():
            severity = result.get_max_severity_failure()
            if severity is None:
                continue
            if worst_severity is None or rank[severity] > rank[worst_severity]:
                worst_severity = severity

        severity_str = worst_severity.value if worst_severity is not None else None
        action = determine_action(severity_str) if severity_str else "none"
        summary = {
            "worst_failed_severity": severity_str,
            "action": action,
            "success": bool(checkpoint_result.success),
        }
        label = "BLOCK" if action == "block" else "QUARANTINE" if action == "quarantine" else "WARN" if action == "warn" else "OK"
        print(f"[SeverityRoutingAction] worst_failed_severity={severity_str} -> {label}")
        return summary


def build_and_run(df: pd.DataFrame) -> dict[str, Any]:
    context = gx.get_context()

    # Unique names so re-running inside an ephemeral context each `make gx` is simple.
    data_source = context.data_sources.add_pandas("orders_pandas")
    asset = data_source.add_dataframe_asset(name="orders_dataframe")
    batch_definition = asset.add_batch_definition_whole_dataframe("whole_orders")

    suite = context.suites.add(gx.ExpectationSuite(name="orders_suite"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="order_id", severity="critical"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeUnique(column="order_id", severity="critical"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="customer_id", severity="critical"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToBeBetween(column="amount", min_value=0, severity="critical"))
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeInSet(column="currency", value_set=["USD", "VND"], severity="critical")
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="status",
            value_set=["pending", "completed", "refunded", "cancelled"],
            severity="warning",
        )
    )
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="created_at", severity="critical"))
    suite.add_expectation(gx.expectations.ExpectColumnValuesToNotBeNull(column="updated_at", severity="critical"))

    validation_definition = context.validation_definitions.add(
        gx.ValidationDefinition(name="orders_validation", data=batch_definition, suite=suite)
    )

    checkpoint = context.checkpoints.add(
        gx.Checkpoint(
            name="orders_checkpoint",
            validation_definitions=[validation_definition],
            actions=[UpdateDataDocsAction(name="update_data_docs"), SeverityRoutingAction()],
        )
    )

    checkpoint_result = checkpoint.run(batch_parameters={"dataframe": df})
    action_results = checkpoint_result.checkpoint_config if hasattr(checkpoint_result, "checkpoint_config") else None

    per_expectation = []
    for result in checkpoint_result.run_results.values():
        for expectation_result in result.results:
            config = expectation_result.expectation_config
            severity = config.get("severity")
            per_expectation.append(
                {
                    "expectation": config.type,
                    "column": config.kwargs.get("column"),
                    "severity": severity.value if severity is not None else "unset",
                    "success": bool(expectation_result.success),
                }
            )

    return {
        "success": bool(checkpoint_result.success),
        "per_expectation": per_expectation,
        "checkpoint_result": checkpoint_result,
    }


def main() -> None:
    df = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    outcome = build_and_run(df)

    print("\n=== GX Suite / ValidationDefinition / Checkpoint / Actions ===")
    for row in outcome["per_expectation"]:
        status = "PASS" if row["success"] else "FAIL"
        print(f"{row['expectation']:<40} column={str(row['column']):<12} severity={row['severity']:<8} {status}")

    print("\nCheckpoint result:", "PASS" if outcome["success"] else "FAIL")


if __name__ == "__main__":
    main()
