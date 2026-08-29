from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

from student_api import validate_orders

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "orders_contract.yaml"


def _iso(minutes_ago: float) -> str:
    # Contract freshness is relative to wall-clock "now", so a healthy fixture must
    # anchor to the current time instead of a frozen date (that would go stale and
    # start failing the freshness check on any day other than when it was written).
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def healthy_df():
    return pd.DataFrame([
        {
            "order_id": 1,
            "customer_id": "C1",
            "amount": 10.0,
            "currency": "USD",
            "status": "completed",
            "created_at": _iso(10),
            "updated_at": _iso(5),
        },
        {
            "order_id": 2,
            "customer_id": "C2",
            "amount": 20.0,
            "currency": "USD",
            "status": "pending",
            "created_at": _iso(9),
            "updated_at": _iso(4),
        },
    ])


def failed(issues):
    return [i for i in issues if not i["passed"]]


def test_healthy_contract_passes_starter_checks():
    assert not failed(validate_orders(healthy_df(), CONTRACT))


def test_duplicate_order_id_is_detected():
    df = healthy_df()
    df.loc[1, "order_id"] = 1
    issues = failed(validate_orders(df, CONTRACT))
    assert any(i["check"] == "unique" and i["column"] == "order_id" for i in issues)


def test_invalid_currency_is_detected():
    df = healthy_df()
    df.loc[0, "currency"] = "BTC"
    issues = failed(validate_orders(df, CONTRACT))
    assert any(i["check"] == "accepted_values" and i["column"] == "currency" for i in issues)


def test_type_drift_on_amount_is_detected():
    df = healthy_df()
    df["amount"] = df["amount"].astype(object)
    df.loc[0, "amount"] = "not-a-number"
    issues = failed(validate_orders(df, CONTRACT))
    assert any(i["check"] == "type" and i["column"] == "amount" for i in issues)


def test_stale_updated_at_triggers_freshness_failure():
    df = healthy_df()
    df["updated_at"] = _iso(120)  # 120 min > 30 min max_delay_minutes in the contract
    issues = failed(validate_orders(df, CONTRACT))
    freshness_issues = [i for i in issues if i["check"] == "freshness"]
    assert freshness_issues and freshness_issues[0]["severity"] == "warning"


def test_severity_maps_to_action():
    df = healthy_df()
    df.loc[1, "order_id"] = 1  # duplicate -> critical severity in the contract
    issues = failed(validate_orders(df, CONTRACT))
    unique_issue = next(i for i in issues if i["check"] == "unique")
    assert unique_issue["severity"] == "critical"
    assert unique_issue["action"] == "block"


def test_quarantine_splits_bad_rows_from_clean_rows():
    from src.contract_validator import load_contract, quarantine_dataframe

    df = healthy_df()
    df.loc[0, "status"] = "unknown_status"  # accepted_values, severity=warning -> quarantine
    result = quarantine_dataframe(df, load_contract(CONTRACT))
    assert len(result["quarantined"]) == 1
    assert len(result["clean"]) == 1
    assert result["blocked"] is False
