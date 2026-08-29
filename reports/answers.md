# Câu trả lời ngắn / Giải thích & Defend Solution

Tài liệu này gom các câu hỏi ngắn yêu cầu trong `docs/LAB_GUIDE.md` mỗi phase, để
phục vụ mục "Baseline & system understanding" (5đ) và "Giải thích/defend solution"
(5đ) trong `docs/SCORING.md`. Toàn bộ số liệu bên dưới lấy từ chạy thật lệnh trong
repo (`make reset && make baseline && pytest tests_public -q`, `make dbt`, 3 fault
scenario), không phải suy đoán.

## Phase 0 — Healthy baseline

**Dataset nào critical?**
`orders` (qua `stg_orders` -> `fct_daily_revenue` -> `ceo_revenue_dashboard`) là
critical nhất về mặt business impact tài chính trực tiếp (CEO nhìn số revenue sai).
`kb_documents` (qua `kb_active_docs -> rag_index -> support_agent`) critical theo
hướng khác: agent trả lời sai chính sách (refund/shipping) ảnh hưởng trực tiếp
khách hàng, rủi ro compliance/trust cao dù không lộ ngay trên dashboard.

**Downstream consumer nào?**
- `ceo_revenue_dashboard` — người xem: leadership, ra quyết định business dựa trên
  con số này.
- `support_agent` — người xem: khách hàng cuối (qua RAG), dùng nội dung KB đã
  index để trả lời.
Cả hai được xác nhận bằng code qua `observability.lineage.get_downstream_assets`
trên `data/baseline/lineage_graph.json`.

**Metric nào cho biết data không đáng tin (dù pipeline SUCCESS)?**
- `row_count_anomaly` (`observability.anomaly.detect_anomaly`) — bắt được volume
  drop mà không có lỗi schema nào.
- `kb_freshness` / `kb_slo` (mới wire trong `scripts/run_baseline.py`) — bắt được
  KB publish timestamp cũ dù nội dung JSON vẫn hợp lệ.
- `critical_contract_failures` / `quarantine.blocked` — bắt được vi phạm identity
  (duplicate PK, null critical field) dù file vẫn parse được.
Bằng chứng: chạy `make dbt` trên tình huống `volume_drop` cho `PASS=19 ERROR=0`
(dbt không thấy gì sai) trong khi `row_count_anomaly.is_anomaly=True` — chứng minh
"pipeline SUCCESS không có nghĩa data đúng" đúng theo README.

## Phase 1 — Vì sao severity → action lại quan trọng

`src/contract_validator.determine_action()`: `critical -> block`,
`warning -> quarantine`, `info -> warn`. Lý do tách 3 mức thay vì chỉ pass/fail nhị
phân: một `order_id` null (critical, không thể xử lý downstream — không biết đây
là order nào) *phải* chặn toàn batch, trong khi một `status` lạ (`warning`) chỉ nên
cách ly đúng những dòng đó để phần còn lại của batch vẫn chạy tiếp — chặn cả batch
vì một vài dòng status lỗi sẽ làm chậm toàn bộ revenue reporting một cách không cần
thiết. `quarantine_dataframe()` hiện thực hoá đúng phân biệt này (xem
`tests_public/test_contracts.py::test_quarantine_splits_bad_rows_from_clean_rows`).

## Phase 2 — Vì sao `not_null`/`unique` KHÔNG phải là dbt unit test

`not_null`/`unique`/`accepted_values`/`relationships` là **data tests**: chúng
assert một fact về dữ liệu *đã build ra*, dùng dữ liệu thật trong warehouse. Chúng
không biết gì về logic SQL bên trong model — miễn kết quả hiện tại thoả điều kiện
(không null, không trùng), test pass, kể cả khi con số đó sai về mặt toán học.

**dbt unit test** chạy trên **mock rows literal** đưa thẳng vào `given:`, hoàn toàn
độc lập với dữ liệu thật trong warehouse, và assert **output chính xác** cho một
input đã biết trước — tức là kiểm tra *logic transform*, không phải *tính chất của
dữ liệu hiện có*.

Bằng chứng cụ thể trong repo này: `fct_daily_revenue` join `stg_orders` với
`active_customers`. Nếu customer dimension có 2 dòng `is_active=true` cho cùng
`customer_id`, join sẽ nhân đôi order — kết quả `daily_revenue` vẫn **not null**,
`order_date` vẫn **unique** (mỗi ngày 1 dòng), nên **toàn bộ data tests hiện có vẫn
PASS trong khi con số bị sai gấp đôi**. Đã verify: chạy unit test
`duplicate_active_customer_rows_do_not_inflate_revenue` trong
`dbt_project/models/marts/unit_tests.yml` trên bản model gốc (naive join) →
**FAIL** đúng như dự đoán (100.0 → 200.0), rồi sửa model (rank + dedupe active
customer theo `valid_from`) → unit test **PASS**, toàn bộ 19 check trong
`dbt build` vẫn PASS. Đây là bằng chứng trực tiếp cho câu hỏi của guide.

## Phase 3 — Khi nào Z-score sai

1. **Seasonality không được model hoá**: `history` gồm cả ngày thường lẫn cuối
   tuần (weekend traffic ≈43% weekday trong `scripts/generate_data.py`) → mean/std
   bị kéo lệch, một ngày cuối tuần bình thường có thể bị flag anomaly
   (false positive), hoặc một ngày thường bất thường bị pha loãng vào baseline
   (false negative). Giải pháp: `context["same_segment_history"]` trong
   `auto_detector`.
2. **Không robust với outlier đã có sẵn trong history**: z-score dùng mean/std —
   một vài anomaly cũ còn nằm trong `history` sẽ kéo std lên, làm ngưỡng phát hiện
   lỏng hơn, che mất anomaly mới (masking effect). MAD (median/median-abs-deviation)
   ít bị ảnh hưởng hơn vì dùng median.
3. **Giả định phân phối gần chuẩn, dừng (stationary)**: nếu metric có trend tăng
   trưởng dài hạn, mean toàn lịch sử luôn "trễ" so với baseline thực tế "bây giờ".
   `auto_detector` dùng rolling window (14 điểm gần nhất) thay vì toàn bộ lịch sử.
4. **MAD=0 (history gần như hằng số)**: modified z-score chia cho MAD sẽ ra vô cực
   với bất kỳ độ lệch nào → cần fallback riêng (đã fix trong `mad_detector`, xem
   `reports/agent_log.md` Decision 4).

**Bằng chứng thực nghiệm gây bất ngờ khi chạy repo này (2026-08-30 là Chủ Nhật):**
`make baseline` trên baseline "khoẻ mạnh" (không inject fault gì) vẫn báo
`row_count_anomaly.is_anomaly=True`. Đây **không phải bug của detector** — nguyên
nhân là `scripts/generate_data.py` luôn sinh batch "hôm nay" cố định ~600 dòng bất
kể ngày trong tuần thực tế, trong khi `metrics_history.csv` mô phỏng traffic cuối
tuần thấp hơn hẳn (~43%). Khi chạy lab vào cuối tuần, baseline "khoẻ" tự nhiên lệch
khỏi baseline cùng-thứ trong lịch sử → false positive thật, tái hiện được. Đây
chính là câu hỏi vận hành "False positive nào dễ xảy ra?" mà lab yêu cầu tự hỏi —
minh hoạ rằng ngay cả detector đã context-aware vẫn phụ thuộc vào tính nhất quán
của dữ liệu sinh ra cho training baseline.

## Phase 5 — SLO tính tay (SLO=99.5%, 2 bad/100 checks)

- `allowed_bad_rate = 1 - 0.995 = 0.005` (0.5%)
- `actual_bad_rate = 2/100 = 0.02` (2%)
- `burn_rate = actual/allowed = 0.02/0.005 = 4.0` — đang tiêu error budget nhanh
  gấp 4 lần tốc độ bền vững.
- `breached = actual_bad_rate (0.02) > allowed_bad_rate (0.005)` → **True**.

Khớp chính xác với `tests_public/test_slo.py::test_burn_rate_math` (đã pass).

## Operational questions (cuối `docs/LAB_GUIDE.md`)

- **Failure này impact user nào?** Xem "Downstream consumer" ở Phase 0 — luôn trace
  bằng `downstream_assets`/`column_downstream`, không đoán.
- **Block pipeline hay warning?** Quyết định bởi `severity` trong contract, không
  phải bởi loại check — xem Phase 1.
- **Alert này có actionable không?** Mỗi anomaly/SLO result trong repo trả về
  `reason` giải thích baseline/threshold cụ thể (không chỉ `True`/`False`), và
  `multiwindow_burn()` trả `severity`+`reason` phân biệt page/ticket/info — đủ để
  người trực biết nên làm gì tiếp theo.
- **False positive nào dễ xảy ra?** Xem 2 ví dụ cụ thể ở Phase 3 (seasonality +
  chủ nhật baseline artifact) — cả hai đều tái hiện được bằng lệnh thật trong repo
  này, không phải giả định lý thuyết.
- **Nếu detector này không có, layer nào còn lại bắt được không?** Với
  `volume_drop`: **không** — đã verify `dbt build` PASS toàn bộ và contract PASS
  toàn bộ khi volume giảm 75%, chỉ anomaly layer bắt được (xem
  `reports/incident_report.md`). Đây là lý do Phase 3 (anomaly detection) đáng
  15 điểm trong rubric — nó là lớp phòng thủ duy nhất cho loại lỗi này.
