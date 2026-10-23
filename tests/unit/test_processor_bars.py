import pytest

from processor.bars import HOUR_MS, MINUTE_MS, Bar, build_bars, hour_start, minute_start
from processor.records import Tick

MINUTE = 1_729_699_980_000  # 2024-10-23T16:13:00Z


def tick(price: float, offset_ms: int, symbol: str = "BTCUSDT") -> Tick:
    return Tick(symbol, price, MINUTE + offset_ms, "test")


def test_window_starts():
    assert minute_start(MINUTE + 59_999) == MINUTE
    assert minute_start(MINUTE + MINUTE_MS) == MINUTE + MINUTE_MS
    assert hour_start(MINUTE) == 1_729_699_200_000  # 16:00:00Z
    assert hour_start(MINUTE) % HOUR_MS == 0


def test_build_bars_computes_ohlc_per_symbol_and_minute():
    ticks = [
        tick(101.0, 1_000),
        tick(99.0, 20_000),
        tick(105.0, 40_000),
        tick(103.0, 59_000),
        tick(200.0, MINUTE_MS + 5_000),
        tick(10.0, 30_000, symbol="ETHUSDT"),
    ]

    bars = build_bars(ticks)

    assert [(b.symbol, b.start) for b in bars] == [
        ("BTCUSDT", MINUTE),
        ("BTCUSDT", MINUTE + MINUTE_MS),
        ("ETHUSDT", MINUTE),
    ]
    btc = bars[0]
    assert (btc.open, btc.high, btc.low, btc.close) == (101.0, 105.0, 99.0, 103.0)
    assert (btc.first_ts, btc.last_ts, btc.tick_count) == (MINUTE + 1_000, MINUTE + 59_000, 4)
    assert bars[1].tick_count == 1


def test_open_and_close_follow_tick_time_not_arrival_order():
    (bar,) = build_bars([tick(3.0, 50_000), tick(1.0, 10_000), tick(2.0, 30_000)])

    assert (bar.open, bar.close) == (1.0, 3.0)


def test_merge_is_idempotent():
    (bar,) = build_bars([tick(1.0, 0), tick(2.0, 30_000)])

    assert bar.merge(bar) == bar
    assert bar.merge(Bar.from_tick(tick(2.0, 30_000))) == bar


def test_merge_is_commutative_even_on_timestamp_ties():
    a = Bar.from_tick(tick(1.0, 10_000))
    b = Bar.from_tick(tick(2.0, 10_000))
    c = Bar.from_tick(tick(3.0, 40_000))

    assert a.merge(b) == b.merge(a)
    assert a.merge(b).merge(c) == c.merge(b).merge(a)


def test_merging_partial_bars_matches_building_from_all_ticks():
    ticks = [tick(5.0, 1_000), tick(7.0, 15_000), tick(4.0, 30_000), tick(6.0, 45_000)]
    (whole,) = build_bars(ticks)
    (first,) = build_bars(ticks[2:])
    (second,) = build_bars(ticks[:2])

    assert first.merge(second) == whole


def test_merge_refuses_different_minutes():
    with pytest.raises(ValueError, match="cannot merge"):
        Bar.from_tick(tick(1.0, 0)).merge(Bar.from_tick(tick(1.0, MINUTE_MS)))
