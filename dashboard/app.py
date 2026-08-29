from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observability.slo import evaluate_multiwindow_burn

REPORT = ROOT / "reports" / "latest_metrics.json"
HISTORY = ROOT / "data" / "history" / "metrics_history.csv"

st.set_page_config(page_title="Data Reliability Lab", layout="wide")
st.title("Data Reliability Game Day")
st.caption("Pipeline SUCCESS != data correct. This board exists to make that visible before the CEO does.")

if not REPORT.exists():
    st.warning("Run `make baseline` first to generate reports/latest_metrics.json")
    st.stop()

report = json.loads(REPORT.read_text(encoding="utf-8"))

status_ok = (
    report["critical_contract_failures"] == 0
    and not report.get("quarantine", {}).get("blocked", False)
    and not report["row_count_anomaly"]["is_anomaly"]
    and not report.get("kb_slo", {}).get("breached", False)
)
st.subheader("Overall status: " + ("🟢 HEALTHY" if status_ok else "🔴 ATTENTION NEEDED"))

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Orders rows", report["orders_rows"])
c2.metric("Freshness (min)", f"{report['freshness_minutes']:.1f}")
c3.metric("Contract failures", report["failed_contract_checks"])
c4.metric("Critical failures", report["critical_contract_failures"])
quarantine = report.get("quarantine", {})
c5.metric("Quarantined rows", quarantine.get("quarantined_rows", 0), delta="BLOCKED" if quarantine.get("blocked") else None)

st.subheader("Orders contract & anomaly signals")
st.json({
    "quarantine": quarantine,
    "row_count_anomaly": report["row_count_anomaly"],
    "contract_slo": report["contract_slo"],
})

st.subheader("Knowledge-base (RAG) reliability")
kb_col1, kb_col2 = st.columns(2)
with kb_col1:
    st.json({
        "kb_freshness": report.get("kb_freshness"),
        "kb_text_length_signal": report.get("kb_text_length_signal"),
    })
with kb_col2:
    kb_slo = report.get("kb_slo", {})
    st.metric("KB freshness SLO burn rate", f"{kb_slo.get('burn_rate', 0):.2f}x")
    st.metric("KB error budget remaining", f"{kb_slo.get('remaining_error_budget_fraction', 1) * 100:.1f}%")
    st.metric("KB SLO breached", "YES" if kb_slo.get("breached") else "no")
    st.caption("Owner: support-ai (see contracts/kb_contract.yaml)")

st.subheader("Multi-window burn-rate policy (paging decision demo)")
st.caption(
    "This uses the current run's single-window burn rate as both the short and "
    "long window for illustration; wire real 5m/1h (or 30m/6h) rolling windows "
    "in production."
)
demo_burn = evaluate_multiwindow_burn(
    short_window_burn=report["contract_slo"]["burn_rate"],
    long_window_burn=report["contract_slo"]["burn_rate"],
)
st.json(demo_burn)

history = pd.read_csv(HISTORY)
st.subheader("Historical row count")
st.line_chart(history.set_index("date")[["row_count"]])

st.subheader("Blast radius")
st.write("stg_orders -> " + " -> ".join(report["sample_blast_radius_from_stg_orders"]))
if report.get("kb_blast_radius"):
    st.write("kb_documents -> " + " -> ".join(report["kb_blast_radius"]))

st.subheader("Incident runbook")
st.markdown(
    "- Contract `critical` failure -> **block** ingestion, page data on-call.\n"
    "- Contract `warning` failure -> **quarantine** offending rows, continue batch.\n"
    "- Row-count / KB anomaly -> investigate via `reports/incident_report.md` checklist.\n"
    "- Fill `reports/agent_log.md` for any AI-assisted decision made during triage."
)
