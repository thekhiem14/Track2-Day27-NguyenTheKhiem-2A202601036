#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observability.anomaly import detect_anomaly
from observability.lineage import get_downstream_assets
from observability.rag_metrics import detect_text_length_shift
from observability.slo import calculate_slo
from src.contract_validator import failed_issues, load_contract, quarantine_dataframe, validate_dataframe
from src.io_utils import load_jsonl


def main() -> None:
    orders = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    history = pd.read_csv(ROOT / "data" / "history" / "metrics_history.csv")
    contract = load_contract(ROOT / "contracts" / "orders_contract.yaml")
    issues = validate_dataframe(orders, contract)
    failed = failed_issues(issues)
    critical_failed = failed_issues(issues, min_severity="critical")
    quarantine = quarantine_dataframe(orders, contract)

    # Public example: segment by weekday before applying the simple detector.
    # `detect_anomaly(..., context=...)` is itself context-aware now (see
    # observability/anomaly.py::auto_detector), so this pre-filtering is a
    # convenience, not a requirement for seasonality handling to work.
    current_dow = datetime.now().weekday()
    segment = history.loc[history["day_of_week"] == current_dow, "row_count"].tail(8).tolist()
    row_history = segment if len(segment) >= 3 else history["row_count"].tail(14).tolist()
    row_result = detect_anomaly(
        len(orders),
        row_history,
        method="auto",
        context={"metric_name": "row_count", "day_of_week": current_dow, "same_segment_history": segment or None},
    )

    updated = pd.to_datetime(orders["updated_at"], utc=True, errors="coerce")
    freshness_minutes = (
        pd.Timestamp(datetime.now(timezone.utc)) - updated.max()
    ).total_seconds() / 60.0

    docs = load_jsonl(ROOT / "data" / "incoming" / "kb_documents.jsonl")
    text_result = detect_text_length_shift(
        [d["content"] for d in docs], history["mean_text_length"].tail(14).tolist()
    )

    # --- KB freshness / SLO -------------------------------------------------
    # Intentional starter TODO in earlier drafts of this lab: the `stale_kb`
    # fault only becomes an *actionable* incident signal once KB freshness is
    # actually validated and turned into an SLO, not just eyeballed.
    kb_contract = load_contract(ROOT / "contracts" / "kb_contract.yaml")
    kb_df = pd.DataFrame(docs)
    kb_issues = validate_dataframe(kb_df, kb_contract)
    kb_failed = failed_issues(kb_issues)
    kb_freshness_issue = next((i for i in kb_issues if i["check"] == "freshness"), None)
    kb_slo = calculate_slo(
        target=0.99,  # matches lab_config.yaml -> slo.rag_index_freshness.target
        bad_events=1 if (kb_freshness_issue and not kb_freshness_issue["passed"]) else 0,
        total_events=1,
    )

    # Demo SLO: one check event for this run.
    bad = 1 if critical_failed else 0
    contract_slo = calculate_slo(0.999, bad_events=bad, total_events=1)

    with open(ROOT / "data" / "baseline" / "lineage_graph.json", "r", encoding="utf-8") as f:
        lineage = json.load(f)["dataset_lineage"]
    blast_radius = get_downstream_assets(lineage, "stg_orders")
    kb_blast_radius = get_downstream_assets(lineage, "kb_documents")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "orders_rows": int(len(orders)),
        "failed_contract_checks": len(failed),
        "critical_contract_failures": len(critical_failed),
        "quarantine": {
            "blocked": quarantine["blocked"],
            "quarantined_rows": int(len(quarantine["quarantined"])),
            "clean_rows": int(len(quarantine["clean"])),
            "reasons": quarantine["reasons"],
        },
        "row_count_anomaly": row_result,
        "freshness_minutes": freshness_minutes,
        "kb_text_length_signal": text_result,
        "kb_freshness": kb_freshness_issue,
        "kb_failed_checks": len(kb_failed),
        "kb_slo": kb_slo,
        "kb_blast_radius": kb_blast_radius,
        "contract_slo": contract_slo,
        "sample_blast_radius_from_stg_orders": blast_radius,
    }
    out = ROOT / "reports" / "latest_metrics.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("=== DATA RELIABILITY BASELINE ===")
    print(f"orders rows              : {len(orders)}")
    print(f"contract failed checks   : {len(failed)}")
    print(f"critical contract fails  : {len(critical_failed)}")
    print(f"quarantine               : blocked={quarantine['blocked']}, quarantined_rows={len(quarantine['quarantined'])}")
    print(f"row-count anomaly        : {row_result['is_anomaly']} ({row_result['method']}, score={row_result['score']:.2f})")
    print(f"freshness minutes        : {freshness_minutes:.1f}")
    print(f"KB length anomaly        : {text_result['is_anomaly']}")
    kb_fresh_ok = kb_freshness_issue["passed"] if kb_freshness_issue else True
    print(f"KB freshness ok          : {kb_fresh_ok}")
    print(f"KB freshness SLO breached: {kb_slo['breached']} (burn_rate={kb_slo['burn_rate']:.2f})")
    print(f"sample blast radius      : {', '.join(blast_radius)}")
    print(f"KB blast radius          : {', '.join(kb_blast_radius)}")
    print(f"report                    : {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
