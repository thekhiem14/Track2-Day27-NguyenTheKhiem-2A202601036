import pytest
from student_api import multiwindow_burn, slo_status


def test_burn_rate_math():
    result = slo_status(0.995, bad_events=2, total_events=100)
    assert result["allowed_bad_rate"] == pytest.approx(0.005)
    assert result["actual_bad_rate"] == pytest.approx(0.02)
    assert result["burn_rate"] == pytest.approx(4.0)
    assert result["breached"] is True


def test_zero_events_is_safe():
    result = slo_status(0.99, bad_events=0, total_events=0)
    assert result["burn_rate"] == 0
    assert result["breached"] is False


def test_sustained_fast_burn_pages():
    # Both short (5m) and long (1h) windows burning fast -> page now.
    result = multiwindow_burn(short_window_burn=20.0, long_window_burn=18.0)
    assert result["page"] is True
    assert result["severity"] == "critical"


def test_transient_spike_does_not_page():
    # Short window spikes, but the long window stays low because the spike is
    # diluted once averaged over the longer window -> must not page.
    result = multiwindow_burn(short_window_burn=50.0, long_window_burn=1.5)
    assert result["page"] is False


def test_sustained_slow_burn_is_a_non_paging_ticket():
    result = multiwindow_burn(short_window_burn=8.0, long_window_burn=7.0)
    assert result["page"] is False
    assert result["severity"] == "warning"
