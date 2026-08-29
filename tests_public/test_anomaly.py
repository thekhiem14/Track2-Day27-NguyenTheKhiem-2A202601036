from student_api import detect_metric


def test_large_volume_drop_is_anomaly():
    history = [1000, 1010, 995, 1008, 1004, 1012, 998]
    result = detect_metric(300, history, method="zscore")
    assert result["is_anomaly"] is True


def test_stable_value_is_not_anomaly():
    history = [1000, 1010, 995, 1008, 1004, 1012, 998]
    result = detect_metric(1002, history, method="zscore")
    assert result["is_anomaly"] is False


def test_true_70pct_drop_is_anomaly_under_auto():
    # Weekday baseline ~1000/day; today's actual is a genuine 70% collapse (~300).
    history = [990, 1005, 998, 1010, 1002, 995, 1008, 1001, 996, 1004]
    result = detect_metric(300, history, method="auto", context={"metric_name": "row_count"})
    assert result["is_anomaly"] is True


def test_legitimate_saturday_pattern_is_not_anomaly_under_auto():
    # Weekday history sits around 1000/day, so a naive z-score against the whole
    # mixed history would flag a normal Saturday dip (~430) as anomalous. `auto`
    # should use the same-weekday baseline supplied via context instead.
    weekday_history = [990, 1005, 998, 1010, 1002, 995, 1008, 1001, 996, 1004]
    saturday_history = [420, 435, 410, 445, 428]
    result = detect_metric(
        430,
        weekday_history,
        method="auto",
        context={"metric_name": "row_count", "day_of_week": 5, "same_segment_history": saturday_history},
    )
    assert result["is_anomaly"] is False


def test_known_event_widens_threshold_but_still_catches_collapse():
    history = [990, 1005, 998, 1010, 1002, 995, 1008, 1001, 996, 1004]
    # This bump alone (~1025) would breach the default auto threshold...
    without_event = detect_metric(1025, history, method="auto")
    assert without_event["is_anomaly"] is True
    # ...but during a known event (promo/holiday) the same bump is expected noise.
    with_event = detect_metric(1025, history, method="auto", context={"known_event": "flash_sale"})
    assert with_event["is_anomaly"] is False
    # A genuine collapse must still fire even during a known event.
    collapse = detect_metric(150, history, method="auto", context={"known_event": "flash_sale"})
    assert collapse["is_anomaly"] is True


def test_mad_handles_degenerate_zero_mad_history():
    from observability.anomaly import mad_detector

    result = mad_detector(50, [1000, 1000, 1000, 1000, 1000])
    assert result["is_anomaly"] is True
