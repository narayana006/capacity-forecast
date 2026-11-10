from datetime import datetime, timedelta, timezone

import pytest

from capacity_forecast import forecast as fc
from capacity_forecast.loader import Sample

T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)


def series(name, values, limit=100.0, step_hours=24):
    return [
        Sample(name, T0 + timedelta(hours=i * step_hours), value, limit)
        for i, value in enumerate(values)
    ]


def one(samples, horizon=60, **kwargs):
    return fc.forecast_all(samples, horizon, **kwargs)[0]


def test_fit_line_recovers_exact_slope():
    fit = fc.fit_line([0, 1, 2, 3], [5, 7, 9, 11])
    assert fit.slope == pytest.approx(2.0)
    assert fit.slope_stderr == pytest.approx(0.0, abs=1e-9)
    assert fit.r_squared == pytest.approx(1.0)


def test_fit_line_needs_three_points_and_time_spread():
    with pytest.raises(ValueError):
        fc.fit_line([0, 1], [1, 2])
    with pytest.raises(ValueError):
        fc.fit_line([1, 1, 1], [1, 2, 3])


def test_perfect_linear_growth_gives_exact_days_left():
    # 50, 52, ... 68 over 10 days: 2/day, 32 left -> 16 days.
    f = one(series("disk", [50 + 2 * i for i in range(10)]))
    assert f.status == fc.AT_RISK
    assert f.growth_per_day == pytest.approx(2.0)
    assert f.days_left == pytest.approx(16.0)
    assert f.days_earliest == pytest.approx(16.0)
    assert f.days_latest == pytest.approx(16.0)
    assert f.exhaustion_date == T0 + timedelta(days=9 + 16)
    assert f.utilisation == pytest.approx(0.68)


def test_noisy_growth_has_a_range_around_the_estimate():
    values = [50 + 2 * i + (1.5 if i % 2 else -1.5) for i in range(20)]
    f = one(series("disk", values))
    assert f.days_earliest < f.days_left < f.days_latest
    assert f.growth_low < f.growth_per_day < f.growth_high


def test_higher_confidence_widens_the_range():
    values = [50 + 2 * i + (1.5 if i % 2 else -1.5) for i in range(20)]
    narrow = one(series("disk", values), confidence=0.5)
    wide = one(series("disk", values), confidence=0.99)
    assert wide.days_earliest < narrow.days_earliest
    assert wide.days_latest > narrow.days_latest


def test_beyond_horizon_is_ok_and_within_is_at_risk():
    samples = series("disk", [10 + i for i in range(10)])  # 81 left at 1/day
    assert one(samples, horizon=60).status == fc.OK
    assert one(samples, horizon=90).status == fc.AT_RISK


def test_flat_and_shrinking_usage_never_run_out():
    flat = one(series("cpu", [40.0] * 10))
    assert flat.status == fc.STABLE and flat.days_left is None and flat.days_earliest is None
    shrinking = one(series("disk", [80 - i for i in range(10)]))
    assert shrinking.status == fc.STABLE and shrinking.days_left is None


def test_uncertain_trend_that_might_hit_the_limit_is_watch():
    # Slight downward fit, but so noisy that the fast end of the range reaches the limit soon.
    values = [91, 97, 89, 97, 89, 95, 90, 94, 90, 92]
    f = one(series("mem", values), horizon=365)
    assert f.days_left is None
    assert f.days_earliest is not None
    assert f.days_latest is None
    assert f.status == fc.WATCH


def test_already_over_limit_is_exhausted():
    f = one(series("quota", [95, 97, 99, 101]))
    assert f.status == fc.EXHAUSTED
    assert f.days_left == 0.0


def test_too_few_samples_is_reported_not_guessed():
    f = one(series("new", [10, 20]))
    assert f.status == fc.NO_DATA
    assert f.days_left is None and f.growth_per_day is None


def test_latest_limit_is_used_when_the_limit_changes():
    samples = series("disk", [50 + 2 * i for i in range(10)], limit=100.0)
    last = samples[-1]
    samples[-1] = Sample(last.resource, last.time, last.used, 200.0)
    f = one(samples, horizon=365)
    assert f.limit == 200.0
    assert f.days_left == pytest.approx((200 - 68) / 2)


def test_window_fits_only_recent_samples():
    values = [50.0] * 20 + [50 + 3 * i for i in range(1, 8)]  # flat, then 3/day
    whole = one(series("disk", values))
    recent = one(series("disk", values), window_days=6)
    assert recent.samples == 7
    assert recent.growth_per_day == pytest.approx(3.0)
    assert recent.days_left < whole.days_left


def test_sample_order_and_sub_daily_spacing_do_not_matter():
    samples = series("disk", [50 + 0.5 * i for i in range(40)], step_hours=6)  # 2/day
    f = one(list(reversed(samples)))
    assert f.growth_per_day == pytest.approx(2.0)


def test_ranking_puts_soonest_exhaustion_first():
    samples = (
        series("slow", [10 + i for i in range(10)])
        + series("fast", [50 + 5 * i for i in range(10)])
        + series("full", [99, 100, 101, 102])
        + series("flat-high", [90.0] * 10)
        + series("flat-low", [20.0] * 10)
        + series("tiny", [1.0])
    )
    ranked = [f.resource for f in fc.forecast_all(samples, 30)]
    assert ranked == ["full", "fast", "slow", "flat-high", "flat-low", "tiny"]


@pytest.mark.parametrize("text, days", [("60d", 60), ("12h", 0.5), ("2w", 14), ("7", 7), (" 1.5D ", 1.5)])
def test_parse_duration(text, days):
    assert fc.parse_duration(text) == pytest.approx(days)


@pytest.mark.parametrize("text", ["", "d", "soon", "-3d", "0d", "5m"])
def test_parse_duration_rejects_nonsense(text):
    with pytest.raises(ValueError):
        fc.parse_duration(text)


@pytest.mark.parametrize("kwargs", [{"confidence": 0}, {"confidence": 1}, {"window_days": 0}])
def test_invalid_settings(kwargs):
    with pytest.raises(ValueError):
        fc.forecast_all(series("x", [1, 2, 3]), 30, **kwargs)
    with pytest.raises(ValueError):
        fc.forecast_all(series("x", [1, 2, 3]), 0)
