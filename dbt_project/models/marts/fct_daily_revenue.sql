-- NOTE: A naive `where is_active = true` join can silently inflate revenue if the
-- customer dimension ever has more than one active row for the same customer_id
-- (a common SCD data-quality violation) -- the join fans out and double-counts
-- completed orders even though no SQL error is raised. `active_customers` below
-- explicitly ranks rows per customer_id and keeps exactly one (most recent
-- valid_from), so the join can never fan out even if upstream data is dirty.
-- See dbt_project/models/marts/unit_tests.yml ->
-- `duplicate_active_customer_rows_do_not_inflate_revenue` for the regression test
-- that exposes this failure mode against the naive version of this model.

with completed_orders as (
    select *
    from {{ ref('stg_orders') }}
    where status = 'completed'
),
ranked_active_customers as (
    select
        *,
        row_number() over (partition by customer_id order by valid_from desc) as active_rank
    from {{ ref('stg_customers') }}
    where is_active = true
),
active_customers as (
    select *
    from ranked_active_customers
    where active_rank = 1
)
select
    o.order_date,
    count(*) as completed_order_rows,
    sum(o.amount_usd) as daily_revenue
from completed_orders o
left join active_customers c
    on o.customer_id = c.customer_id
group by 1
order by 1
