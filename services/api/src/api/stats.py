"""Rolling statistics and anomaly flags, computed when bars are read.

They are computed here rather than stored by the processor: a late tick can
still move a bar's close, which would leave stored statistics wrong, and the
window or threshold can change without a backfill.

For each bar, oldest first:

- ``ret``: the 1-minute simple return, ``close / previous close - 1``. None
  when the previous minute has no bar, so a gap never counts as one big move.
- ``zscore``: ``(ret - mean) / std`` over the returns of the ``window`` bars
  before it (the bar's own return excluded). None with fewer than
  ``MIN_PERIODS`` such returns or no variation in them.
- ``flags``: ``spike`` when ``|zscore| >= z_threshold``; ``gap`` when the
  previous minute has no bar.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

MINUTE_MS = 60_000
MIN_PERIODS = 20

SPIKE = "spike"
GAP = "gap"


class Priced(Protocol):
    @property
    def start(self) -> int: ...
    @property
    def close(self) -> float: ...


@dataclass(frozen=True)
class BarStats:
    ret: float | None
    zscore: float | None
    flags: tuple[str, ...]


@dataclass(frozen=True)
class Summary:
    """The rolling window ending at the last bar."""

    sma_close: float | None
    mean_return: float | None
    volatility: float | None  # sample std of the window's returns
    last_zscore: float | None


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _std(values: Sequence[float], mean: float) -> float:
    return math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1))


def returns(bars: Sequence[Priced]) -> list[float | None]:
    """Each bar's 1-minute return; None for the first bar and after a gap."""
    out: list[float | None] = [None] * len(bars)
    for i in range(1, len(bars)):
        prev, bar = bars[i - 1], bars[i]
        if bar.start - prev.start == MINUTE_MS:
            out[i] = bar.close / prev.close - 1
    return out


def annotate(bars: Sequence[Priced], *, window: int, z_threshold: float) -> list[BarStats]:
    """Return, z-score and flags for every bar of one symbol, oldest first.

    The first ``window`` bars have a shorter history, so callers that want a
    full window for every bar they show pass that many earlier bars too and
    drop their results. Each window is summed exactly (``math.fsum``) instead
    of with running totals, so a flat window has a variance of exactly zero
    rather than a rounding residue that would make the next tiny move a spike.
    """
    rets = returns(bars)
    out: list[BarStats] = []
    for i, bar in enumerate(bars):
        flags: list[str] = []
        if i and bar.start - bars[i - 1].start > MINUTE_MS:
            flags.append(GAP)
        zscore = None
        ret = rets[i]
        prior = [r for r in rets[max(0, i - window) : i] if r is not None]
        if ret is not None and len(prior) >= MIN_PERIODS:
            mean = _mean(prior)
            std = _std(prior, mean)
            if std > 0:
                zscore = (ret - mean) / std
                if abs(zscore) >= z_threshold:
                    flags.append(SPIKE)
        out.append(BarStats(ret=ret, zscore=zscore, flags=tuple(flags)))
    return out


def summarize(bars: Sequence[Priced], stats: Sequence[BarStats], *, window: int) -> Summary:
    """Moving average, mean return and volatility over the last ``window`` bars."""
    if not bars:
        return Summary(sma_close=None, mean_return=None, volatility=None, last_zscore=None)
    closes = [bar.close for bar in bars[-window:]]
    rets = [s.ret for s in stats[-window:] if s.ret is not None]
    mean_return = _mean(rets) if rets else None
    volatility = _std(rets, mean_return) if mean_return is not None and len(rets) > 1 else None
    return Summary(
        sma_close=_mean(closes),
        mean_return=mean_return,
        volatility=volatility,
        last_zscore=stats[-1].zscore,
    )
