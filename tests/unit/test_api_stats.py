import math
from types import SimpleNamespace

import pytest

from api.stats import GAP, MIN_PERIODS, MINUTE_MS, SPIKE, annotate, returns, summarize


def series(closes, *, skip=()):
    """Bars one minute apart, leaving out the minutes in ``skip``."""
    minutes = [m for m in range(len(closes) + len(skip)) if m not in skip]
    return [
        SimpleNamespace(start=m * MINUTE_MS, close=c) for m, c in zip(minutes, closes, strict=True)
    ]


def wiggle(n, base=100.0, step=0.001):
    """Closes that alternate up and down by ``step``: small, varied returns."""
    return [base * (1 + step * (-1) ** i) for i in range(n)]


def test_returns_need_the_previous_minute():
    bars = series([100.0, 110.0, 99.0, 99.0], skip={2})  # minutes 0, 1, 3, 4

    rets = returns(bars)

    assert rets[0] is None
    assert rets[1] == pytest.approx(0.1)
    assert rets[2] is None  # minute 2 is missing
    assert rets[3] == 0.0


def test_no_zscore_before_min_periods():
    # Bar i has i - 1 earlier returns; MIN_PERIODS are needed.
    stats = annotate(series(wiggle(MIN_PERIODS + 2)), window=60, z_threshold=3)

    assert all(s.zscore is None for s in stats[: MIN_PERIODS + 1])
    assert stats[MIN_PERIODS + 1].zscore is not None


def test_flat_prices_have_no_zscore():
    stats = annotate(series([100.0] * 40), window=60, z_threshold=3)

    assert all(s.zscore is None and s.flags == () for s in stats)


def test_a_jump_is_flagged_as_a_spike():
    closes = wiggle(40)
    closes.append(closes[-1] * 1.05)

    stats = annotate(series(closes), window=60, z_threshold=3)

    assert stats[-1].flags == (SPIKE,)
    assert stats[-1].zscore > 3
    assert not any(s.flags for s in stats[:-1])


def test_zscore_uses_the_preceding_window_only():
    closes = wiggle(41)
    bars = series(closes)
    rets = returns(bars)

    stats = annotate(bars, window=25, z_threshold=3)

    prior = rets[40 - 25 : 40]
    mean = sum(prior) / len(prior)
    std = math.sqrt(sum((r - mean) ** 2 for r in prior) / (len(prior) - 1))
    assert stats[40].zscore == pytest.approx((rets[40] - mean) / std)


def test_the_threshold_is_inclusive_in_both_directions():
    closes = wiggle(40)
    closes.append(closes[-1] * 0.95)
    stats = annotate(series(closes), window=60, z_threshold=3)
    z = stats[-1].zscore

    assert z < -3
    assert SPIKE in stats[-1].flags
    assert SPIKE in annotate(series(closes), window=60, z_threshold=abs(z))[-1].flags


def test_a_missing_minute_is_a_gap_not_a_move():
    closes = wiggle(30)
    closes.append(closes[-1] * 1.5)  # after the gap: would be a huge return

    stats = annotate(series(closes, skip={30}), window=60, z_threshold=3)

    assert stats[-1].flags == (GAP,)
    assert stats[-1].ret is None
    assert stats[-1].zscore is None


def test_summarize_covers_the_last_window():
    closes = [float(c) for c in range(1, 11)]
    bars = series(closes)
    stats = annotate(bars, window=4, z_threshold=3)

    summary = summarize(bars, stats, window=4)

    assert summary.sma_close == pytest.approx(8.5)  # mean of 7, 8, 9, 10
    rets = [7 / 6 - 1, 8 / 7 - 1, 9 / 8 - 1, 10 / 9 - 1]  # each window bar's own return
    assert summary.mean_return == pytest.approx(sum(rets) / 4)
    assert summary.volatility > 0
    assert summary.last_zscore is None  # too few returns for a z-score


def test_summarize_with_no_bars():
    summary = summarize([], [], window=60)

    assert (summary.sma_close, summary.mean_return, summary.volatility) == (None, None, None)
    assert summary.last_zscore is None


def test_summarize_needs_two_returns_for_volatility():
    bars = series([100.0, 101.0])
    summary = summarize(bars, annotate(bars, window=60, z_threshold=3), window=60)

    assert summary.mean_return == pytest.approx(0.01)
    assert summary.volatility is None
