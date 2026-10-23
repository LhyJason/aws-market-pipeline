"""1-minute OHLC bars.

The feed carries prices only, so the bar's volume is ``tick_count``, the number
of distinct ticks in the minute.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from processor.records import Tick

MINUTE_MS = 60_000
HOUR_MS = 3_600_000


def minute_start(ts: int) -> int:
    return ts - ts % MINUTE_MS


def hour_start(ts: int) -> int:
    return ts - ts % HOUR_MS


@dataclass(frozen=True)
class Bar:
    """One symbol's minute starting at ``start`` (epoch ms).

    ``tick_ts`` holds the timestamp of every tick merged in. Counting distinct
    timestamps instead of incrementing a counter is what keeps a replayed tick
    from being counted twice.
    """

    symbol: str
    start: int
    open: float
    high: float
    low: float
    close: float
    first_ts: int
    last_ts: int
    tick_ts: frozenset[int]

    @property
    def tick_count(self) -> int:
        return len(self.tick_ts)

    @classmethod
    def from_tick(cls, tick: Tick) -> Bar:
        return cls(
            symbol=tick.symbol,
            start=minute_start(tick.ts),
            open=tick.price,
            high=tick.price,
            low=tick.price,
            close=tick.price,
            first_ts=tick.ts,
            last_ts=tick.ts,
            tick_ts=frozenset({tick.ts}),
        )

    def merge(self, other: Bar) -> Bar:
        """Combine two partial bars of the same minute.

        Commutative and idempotent: merging in ticks that are already part of
        the bar changes nothing, so Kinesis retries and replays are harmless.
        Open and close follow tick time, not arrival order; ties on time break
        on price so the result does not depend on which side is ``self``.
        """
        if (self.symbol, self.start) != (other.symbol, other.start):
            raise ValueError(
                f"cannot merge bar {other.symbol}@{other.start} into {self.symbol}@{self.start}"
            )
        earliest = min(self, other, key=lambda b: (b.first_ts, b.open))
        latest = max(self, other, key=lambda b: (b.last_ts, b.close))
        return Bar(
            symbol=self.symbol,
            start=self.start,
            open=earliest.open,
            high=max(self.high, other.high),
            low=min(self.low, other.low),
            close=latest.close,
            first_ts=earliest.first_ts,
            last_ts=latest.last_ts,
            tick_ts=self.tick_ts | other.tick_ts,
        )


def build_bars(ticks: Iterable[Tick]) -> list[Bar]:
    """Bars for every (symbol, minute) in ``ticks``, sorted by symbol then start."""
    bars: dict[tuple[str, int], Bar] = {}
    for tick in ticks:
        bar = Bar.from_tick(tick)
        key = (bar.symbol, bar.start)
        bars[key] = bars[key].merge(bar) if key in bars else bar
    return sorted(bars.values(), key=lambda b: (b.symbol, b.start))
