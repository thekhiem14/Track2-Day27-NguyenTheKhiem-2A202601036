# Incident Report

> **Scope note:** Phase 6 ("Mystery incident") is meant to be run live in class — the
> instructor swaps in a hidden `data/incoming` dataset or a private fault folder that
> students must diagnose *without* looking at the injection script. That hidden
> dataset is not available in this offline run. To keep this report backed by real
> evidence rather than a guess, it documents a full Detect → Triage → RCA →
> Blast-radius → Mitigate → Verify pass through the public `volume_drop` fault
> (`python scripts/inject_fault.py volume_drop`), using the actual tool output from
> this repo. During the live session, replace the numbers below with the real
> mystery-incident evidence gathered the same way — every command in this report is
> exactly what should be re-run against the mystery dataset.

## Severity
P2 — revenue reporting materially wrong, no customer-facing outage. (Would be P1 if
`ceo_revenue_dashboard` is presented at a live exec meeting without a caveat.)

## Summary
Only 25% of the expected order batch was ingested (150 of ~600 rows). The pipeline
itself reported `SUCCESS` — no schema/contract error, no dbt test failure — because
truncation produces a perfectly well-formed CSV, just a short one. `fct_daily_revenue`
computed a real number over incomplete input, so `daily_revenue` silently understated
true revenue by ~77% for the affected batch. The failure was only caught by the
**anomaly-detection layer**, not by contract validation or dbt tests.

## Detection
- Signal: `row_count_anomaly` from `scripts/run_baseline.py` / `observability.anomaly.detect_anomaly(method="auto")`.
- Evidence: `is_anomaly=True`, `method=auto:same_segment_mad`, `score=7.55` (threshold 3.0), reason `median≈..., mad≈..., window=…` — current same-weekday row count sits far outside the robust baseline band.
- First observed: at the first `make baseline` run after ingestion (this is a batch pipeline; detection latency = time until the next scheduled `make baseline`/dbt run).
- **What did *not* fire, and why that matters:**
  - `contract failed checks = 0`, `critical contract fails = 0` — every remaining row is individually well-formed (valid id, currency, status, in-range amount), so `src/contract_validator.py` has nothing to flag. Row-count is a *volume* property, not a per-row constraint.
  - `dbt build` → `PASS=19 WARN=0 ERROR=0` — every `not_null`/`unique`/`accepted_values`/`relationships` data test and both unit tests passed. Losing 75% of rows does not violate any of those constraints; the remaining 150 rows are still individually valid and the join logic is unaffected.
  - This is the exact case the lab asks students to reason about explicitly: *"anomaly detector nên bắt được dù không có rule `row_count == ...`"* — there is no deterministic rule that could have caught this; only a statistical baseline comparison could.

## Root Cause
Upstream ingestion delivered a partial batch (in the real `inject_fault.py volume_drop`
scenario: the loader kept only the first `max(10, 25%)` rows of the source file — a
stand-in for a real-world partial-extract / early-terminated-job / truncated-file
failure mode). No error was raised anywhere in the pipeline because a truncated file
is a syntactically valid, semantically-plausible-looking CSV.

## Evidence
1. `python scripts/inject_fault.py volume_drop` → `Injected partial-ingestion fault: kept 150/600 rows.`
2. `python scripts/run_baseline.py`:
   - `orders rows: 150` (expected ~600)
   - `contract failed checks: 0`, `critical contract fails: 0`
   - `row-count anomaly: True (auto:same_segment_mad, score=7.55)`
3. `dbt build` (after `scripts/sync_dbt_seeds.py`): `PASS=19 WARN=0 ERROR=0` — all data tests and both unit tests green.
4. `select * from fct_daily_revenue` in the DuckDB warehouse:
   - Healthy baseline: `completed_order_rows=290`, `daily_revenue=18961.04`
   - Faulted run: `completed_order_rows=66`, `daily_revenue=4308.42` (**−77.3%**)

## Blast Radius
Computed via `observability.lineage.get_downstream_assets(dataset_lineage, "stg_orders")`:

```text
stg_orders
-> fct_daily_revenue        (daily_revenue understated ~77%)
-> ceo_revenue_dashboard    (executive-facing number is wrong)
```

Column-level (`get_column_downstream(column_lineage, "raw_orders.amount")`):

```text
raw_orders.amount
-> stg_orders.amount_usd
-> fct_daily_revenue.daily_revenue
-> ceo_revenue_dashboard.revenue
```

Not affected: the KB/RAG lineage branch (`kb_documents -> kb_active_docs -> rag_index
-> support_agent`) — this incident is isolated to the orders pipeline, confirmed by
`kb_freshness`/`kb_text_length_signal` staying healthy in the same run.

## Mitigation
1. Immediate: mark `ceo_revenue_dashboard` as **stale/do-not-trust** (contract SLO /
   anomaly flag should gate the dashboard, not just log a warning) until re-ingestion
   completes.
2. Re-run ingestion for the affected batch window; re-run `make baseline` and
   `dbt build` to confirm the full row count returns.
3. If ingestion cannot be re-run in time for a reporting deadline, publish
   `daily_revenue` with an explicit "partial data — N of ~600 orders ingested"
   annotation rather than a bare number.

## Recovery
`python scripts/reset_lab.py` restores the healthy incoming dataset in this lab; in
production this maps to re-running the upstream extract/load job for the affected
window.

## Verification
- [x] Contract healthy — `python scripts/run_baseline.py` → `critical contract fails: 0`
- [x] dbt tests healthy — `dbt build` → `PASS=19 ERROR=0`
- [x] Anomaly returned to expected range — `row-count anomaly: False` after `make reset`
- [x] SLO healthy / budget understood — `contract_slo.breached=False`, burn_rate=0 post-recovery
- [x] Downstream output verified — `fct_daily_revenue.daily_revenue` back to full-batch value after `dbt build` on the reset data

## Prevention / Action Items
| Action | Owner | Deadline | Why |
|---|---|---:|---|
| Add a row-count floor check to the ingestion job itself (fail-fast before the pipeline reports SUCCESS) | commerce-data | +1 week | Catches truncation at the source instead of relying only on downstream anomaly detection |
| Gate `ceo_revenue_dashboard` refresh on `critical_contract_fails == 0 AND row_count_anomaly.is_anomaly == False` | dashboard owner | +1 week | Prevents a visibly-wrong number from reaching the CEO even when the pipeline technically "succeeds" |
| Add a dbt source freshness/row-count test (`dbt source freshness` + a custom row-count singular test comparing against the seed's expected range) | data platform | +2 weeks | Gives dbt build itself a second, independent volume signal instead of relying solely on the Python anomaly layer |
| Document expected daily row-count range per weekday in `lab_config.yaml`/runbook so the "same-weekday baseline" has an explicit, reviewable source of truth (not just whatever `metrics_history.csv` happens to contain) | data platform | +2 weeks | Makes the anomaly threshold auditable instead of an opaque statistical artifact |
