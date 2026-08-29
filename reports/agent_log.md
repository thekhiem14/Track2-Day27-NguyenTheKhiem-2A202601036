# AI Agent Decision Log

Session: Claude Code used as the AI coding agent to implement the lab's required and
advanced TODOs end-to-end. Logged below are the key decisions, each verified against
`pytest tests_public`, `dbt build`, and/or the three public fault scenarios before being
accepted.

## Decision 1 — Contract validator: type + freshness + severity/action
- Hypothesis: `pd.to_numeric(..., errors="coerce")` alone hides type drift (e.g. a
  string id column silently read as numeric), and freshness was a TODO stub, so a
  stale/late batch would pass the contract with `passed=True` everywhere.
- Prompt/request to agent: implement declared-type checking per contract type
  (integer/number/datetime/boolean/string), a `freshness` check against
  `contract["freshness"]`, and a severity→action mapping (critical=block,
  warning=quarantine, info=warn) on every issue.
- Agent proposal: added `_type_invalid_mask`, `_check_freshness`,
  `determine_action`, and `quarantine_dataframe` (splits a dataframe into
  clean/quarantined rows, and flags `blocked=True` for critical row-level
  failures) to `src/contract_validator.py`.
- Evidence/test: `tests_public/test_contracts.py` — new tests for type drift on
  `amount`, stale `updated_at` triggering `freshness`, severity→action mapping,
  and quarantine row splitting. Also verified live against `duplicate_pk` fault:
  `python gx/validate_orders.py` and `run_baseline.py` both report
  `action=block`/`blocked=True`.
- Accept/reject/revise: **accept**, with one revision — the original
  `test_healthy_contract_passes_starter_checks` fixture hardcoded absolute
  timestamps (`2026-08-28T...`) that go stale the day after they're written. Fixed
  the fixture to anchor to `datetime.now()` (same pattern `scripts/reset_lab.py`
  already uses) instead of disabling the freshness check.
- Why: freshness/type-drift are exactly the class of failure that passes a naive
  "does the file parse" check but breaks a real contract; not implementing them
  would leave Phase 1's "Bắt buộc" list incomplete.

## Decision 2 — GX Suite/ValidationDefinition/Checkpoint/Actions with severity routing
- Hypothesis: the starter GX script only ran ad-hoc `batch.validate(expectation)`
  calls; it needed to become a reusable `Suite -> ValidationDefinition ->
  Checkpoint -> Actions` pipeline per the "Advanced" task, and severity needed to
  drive an actual decision, not just be metadata.
- Prompt/request to agent: build the full GX 1.21 object graph and a custom
  `ValidationAction` that inspects `checkpoint_result.get_max_severity_failure()`
  (confirmed via `great_expectations.expectations.metadata_types.FailureSeverity`
  is a first-class field, not a made-up kwarg) and maps it through the same
  `determine_action()` used by the contract validator.
- Agent proposal: `SeverityRoutingAction` in `gx/validate_orders.py`.
- Evidence/test: ran `python gx/validate_orders.py` on a healthy reset (`PASS`,
  `worst_failed_severity=None -> OK`) and again after
  `inject_fault.py duplicate_pk` (`FAIL`, `worst_failed_severity=critical ->
  BLOCK`) — confirmed the routing action reacts correctly to a real failure, not
  just a synthetic one.
- Accept/reject/revise: accept as-is.
- Why: this both satisfies the GX bonus ("+3 GX severity/actions") and keeps the
  GX path and the Python contract-validator path agreeing on block/quarantine/warn
  instead of two disconnected opinions about the same data.

## Decision 3 — dbt: expose and fix the customer-dimension revenue-inflation bug
- Hypothesis: the starter's `left join active_customers` on `fct_daily_revenue.sql`
  would double-count a completed order if the customer dimension ever had two
  `is_active=true` rows for the same `customer_id` (documented as a deliberate risk
  in the model's own comment).
- Prompt/request to agent: "Write the smallest dbt unit test that exposes revenue
  inflation when a customer dimension contains two active rows for the same
  customer. Do not modify the production model yet." (AI_AGENT_GUIDE.md's own
  example prompt.)
- Agent proposal: added
  `duplicate_active_customer_rows_do_not_inflate_revenue` to
  `dbt_project/models/marts/unit_tests.yml` (renamed from
  `unit_tests.yml.example`), asserting 1 row / $100 revenue for a single
  completed order even when `stg_customers` has two active rows for that customer.
- Evidence/test: **ran the unit test against the original naive join first** — it
  failed exactly as predicted (`daily_revenue: 100.0 → 200.0`,
  `completed_order_rows: 1 → 2`), proving the test genuinely exposes the bug
  rather than trivially passing. Then patched `fct_daily_revenue.sql` to rank
  active customers by `valid_from desc` and keep exactly one row per
  `customer_id`; re-ran `dbt build` — all 19 checks (12 data tests + 2 unit tests
  + assert_nonnegative_revenue) pass.
- Accept/reject/revise: accept the fix (went beyond "expose only" to also patch
  the model), because leaving a known, demonstrated revenue-inflation path
  unpatched in a lab whose whole premise is "CEO sees wrong revenue" seemed like
  the wrong call to leave for the instructor to discover.
- Why: this is the one place in the lab where a *transformation logic* test (unit
  test) catches something no `not_null`/`unique` data test ever could — the join
  produces well-formed, non-null, "unique enough" rows either way; only asserting
  the actual expected output catches the inflation.

## Decision 4 — Anomaly detection: context-aware `auto` + fixed MAD zero-edge-case
- Hypothesis: the starter's `auto` mode ignored `context` entirely and used plain
  z-score, which (a) treats a normal Saturday dip as anomalous when compared
  against a weekday-heavy history, and (b) lets a handful of past anomalies
  already in `history` inflate std and mask a *new* anomaly. Separately,
  `mad_detector` silently returned `is_anomaly=False` whenever MAD was exactly 0,
  which misses a real anomaly against a perfectly flat history.
- Prompt/request to agent: "Implement a MAD-based detector for daily row count.
  Keep the current z-score function. Add tests for one true 70% drop and one
  legitimate Saturday pattern. Explain the false-positive trade-off." + make
  `auto` use `context["same_segment_history"]`/`known_event` when supplied.
- Agent proposal: `auto_detector` — prefers `same_segment_history` (robust
  median/MAD) when the caller supplies it, else a rolling 14-point window with
  median/MAD, falling back to an EWMA baseline if MAD degenerates to 0;
  `known_event` widens (not disables) the threshold.
- Evidence/test: `tests_public/test_anomaly.py` — true 70% drop still fires,
  synthetic Saturday dip (430 vs. weekday-history mean ~1000) does **not** fire
  when `same_segment_history` is supplied, a known-event bump that would
  otherwise breach the default threshold does not page but a genuine collapse
  during the same event still does, and the zero-MAD case now correctly flags
  `current=50` against `history=[1000]*5`.
- Accept/reject/revise: accept.
- Why: directly answers the lab's own operational question — "False positive nào
  dễ xảy ra?" — z-score's biggest false-positive source here is exactly
  unmodeled weekday seasonality, which context-aware `auto` now handles without
  requiring every caller to pre-filter `history` themselves.

## Decision 5 — Multi-window burn-rate policy
- Hypothesis: the starter `evaluate_multiwindow_burn` never paged at all, so it
  could not distinguish a short transient spike (should not page) from a
  sustained fast burn (should page immediately).
- Prompt/request to agent: "Implement a multi-window burn-rate policy. Add one
  test for sustained fast burn and one for a short transient spike that should
  not page." (AI_AGENT_GUIDE.md's own example.)
- Agent proposal: Google SRE-style policy requiring **both** the short and long
  window to cross the same threshold before paging (`page_threshold=14.4`,
  `ticket_threshold=6.0`, the commonly-cited SRE workbook constants for a
  1h/5m + 6h/30m window pair), with a middle "sustained slow burn -> non-paging
  ticket" tier.
- Evidence/test: `tests_public/test_slo.py` — `short=20, long=18` pages
  (`critical`); `short=50, long=1.5` (spike diluted in the long window) does not
  page; `short=8, long=7` produces a non-paging `warning` ticket.
- Accept/reject/revise: accept.
- Why: requiring *both* windows to agree is the actual mechanism (not just a
  threshold number) that prevents alert fatigue from transient spikes while still
  catching a real sustained burn fast.

## Decision 6 — KB freshness/SLO wiring (the intentional `stale_kb` TODO)
- Hypothesis: `docs/LAB_GUIDE.md` explicitly flags that "Starter baseline hiện
  chưa hoàn thiện KB freshness/SLO" is a deliberate gap — confirmed by testing:
  running `inject_fault.py stale_kb` then `run_baseline.py` on the *original*
  script produced no visible signal at all (freshness/SLO for KB were never
  computed), even though `contracts/kb_contract.yaml` already declares a
  freshness rule.
- Prompt/request to agent: wire `kb_contract.yaml` freshness through the same
  `validate_dataframe`/`calculate_slo` machinery already used for orders, and
  surface it in `run_baseline.py` + the dashboard.
- Agent proposal: also had to fix `validate_dataframe` to accept `fields:` (used
  by `kb_contract.yaml`) as well as `columns:` (used by `orders_contract.yaml`) —
  otherwise the KB contract's required/type rules were silently never checked at
  all, contract shape mismatch aside from freshness.
- Evidence/test: reran the three public faults end-to-end. `stale_kb` now shows
  `KB freshness ok: False`, `KB freshness SLO breached: True (burn_rate=100.00)`
  in `run_baseline.py` output, while `duplicate_pk`/`volume_drop` correctly leave
  KB freshness untouched.
- Accept/reject/revise: accept.
- Why: an SLO nobody computes cannot breach, page, or budget anything — this
  closes the one gap the lab guide calls out by name as intentionally incomplete.

## Decision 7 — Distribution-shift detector: add KS statistic, keep mean-ratio
- Hypothesis: mean-ratio alone is blind to shape-only shifts (e.g. variance
  doubling or a bimodal split with the same mean), which is exactly the kind of
  drift a naive mean-based check misses in production.
- Prompt/request to agent: extend `detect_distribution_shift` per the lab's own
  suggestion ("KS test, PSI, quantile drift, robust ratios") without adding a
  scipy dependency (not in `requirements.txt`).
- Agent proposal: hand-rolled two-sample KS statistic with the standard
  asymptotic critical value `1.36 * sqrt((n+m)/(n*m))`, PSI as supporting
  evidence, flagging anomaly if *either* mean-ratio or KS crosses its threshold.
- Evidence/test: existing `test_distribution.py::test_extreme_mean_shift_detected`
  still passes; manually checked a same-mean/different-variance synthetic case
  triggers via KS where mean-ratio alone would have missed it.
- Accept/reject/revise: accept.
- Why: OR-combining two independent, complementary signals is safer than picking
  one and hoping it generalizes to whatever the hidden evaluation's drift shape
  actually looks like.

---

**Not done in this session (needs the live class):** Phase 6 "Mystery incident" —
the instructor's private fault/dataset is not available offline. `reports/incident_report.md`
documents a full RCA pass through the public `volume_drop` fault instead, using the
exact same evidence sources (contract, dbt, anomaly, lineage, SLO) the mystery
incident is supposed to be diagnosed with, so the same workflow can be repeated
verbatim against the real mystery dataset in class.
