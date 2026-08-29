from student_api import column_downstream, downstream_assets


def test_transitive_downstream_assets():
    graph = {
        "raw_orders": ["stg_orders"],
        "stg_orders": ["revenue"],
        "revenue": ["dashboard"],
    }
    assert downstream_assets(graph, "raw_orders") == ["stg_orders", "revenue", "dashboard"]


def test_transitive_column_downstream():
    column_graph = {
        "raw_orders.amount": ["stg_orders.amount_usd"],
        "stg_orders.amount_usd": ["fct_daily_revenue.daily_revenue"],
        "fct_daily_revenue.daily_revenue": ["ceo_revenue_dashboard.revenue"],
    }
    assert column_downstream(column_graph, "raw_orders.amount") == [
        "stg_orders.amount_usd",
        "fct_daily_revenue.daily_revenue",
        "ceo_revenue_dashboard.revenue",
    ]


def test_column_downstream_handles_fan_out_without_duplicates():
    column_graph = {
        "a.col": ["b.col", "c.col"],
        "b.col": ["d.col"],
        "c.col": ["d.col"],
    }
    result = column_downstream(column_graph, "a.col")
    assert set(result) == {"b.col", "c.col", "d.col"}
    assert len(result) == 3  # d.col reached via two paths must not be duplicated
