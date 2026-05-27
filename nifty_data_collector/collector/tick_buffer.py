"""
collector/tick_buffer.py
FIX: Removed reentrant lock acquisition inside flush_all() that caused deadlock.
     Python threading.Lock is NOT reentrant — acquiring it twice in the same thread hangs forever.
     This was silently blocking every candle write, which is why candles/OC/features never recorded.
     VIX still worked because it's fetched via REST in _fetch_vix(), bypassing this buffer entirely.
"""

import threading
from collections import defaultdict
from datetime import datetime
from typing import Optional


class TickBuffer:
    def __init__(self):
        self._lock   = threading.Lock()
        self._ticks: dict[str, list] = defaultdict(list)
        # Track last known cum_vol per symbol to compute candle delta
        self._last_cum_vol: dict[str, int] = {}

    def add_tick(
        self,
        symbol:  str,
        ltp:     float,
        ts:      datetime,
        is_buy:  Optional[bool] = None,
        ltq:     int = 0,        # last traded quantity this tick
        cum_vol: int = 0,        # cumulative volume today
    ):
        with self._lock:
            self._ticks[symbol].append((ts, ltp, is_buy, ltq, cum_vol))

    def flush_all(self, candle_ts: datetime) -> list[dict]:
        # Acquire lock once to snapshot + drain ticks and grab _last_cum_vol state
        with self._lock:
            symbols  = list(self._ticks.keys())
            snapshot = {s: self._ticks.pop(s, []) for s in symbols}
            # Snapshot cum_vol state here inside the lock — avoids reentrant acquire later
            prev_cum_vols = {s: self._last_cum_vol.get(s, 0) for s in symbols}

        # Process outside the lock — pure CPU work, no shared state writes yet
        candles = []
        new_cum_vols = {}

        for symbol, ticks in snapshot.items():
            if not ticks:
                continue

            ltps       = [t[1] for t in ticks]
            tick_count = len(ticks)

            # ── Side-based volume ───────────────────────────
            buy_ticks  = [t for t in ticks if t[2] is True]
            sell_ticks = [t for t in ticks if t[2] is False]
            unk_ticks  = [t for t in ticks if t[2] is None]

            buy_vol  = sum(t[3] for t in buy_ticks)
            sell_vol = sum(t[3] for t in sell_ticks)
            unk_vol  = sum(t[3] for t in unk_ticks)

            # Distribute unknown volume proportionally by side
            if unk_vol > 0:
                ratio     = buy_vol / max(buy_vol + sell_vol, 1)
                buy_vol  += int(unk_vol * ratio)
                sell_vol += unk_vol - int(unk_vol * ratio)

            # ── Real traded volume this candle ───────────────
            # Method 1: sum of ltq per tick (most accurate)
            traded_volume = sum(t[3] for t in ticks)

            # Method 2: cum_vol delta (use as cross-check / fallback)
            latest_cum_vol  = ticks[-1][4]
            prev_cum        = prev_cum_vols.get(symbol, 0)
            new_cum_vols[symbol] = latest_cum_vol
            cum_vol_delta   = latest_cum_vol - prev_cum if prev_cum > 0 else traded_volume

            # Use ltq sum as primary; cum_vol delta as fallback if ltq missing
            final_traded = traded_volume if traded_volume > 0 else cum_vol_delta

            # Avg trade size (high = institutional block, low = retail)
            avg_trade_size = round(final_traded / max(tick_count, 1), 2)

            candles.append({
                "ts":             candle_ts,
                "symbol":         symbol,
                "open":           ltps[0],
                "high":           max(ltps),
                "low":            min(ltps),
                "close":          ltps[-1],
                "tick_count":     tick_count,
                "buy_vol":        buy_vol,
                "sell_vol":       sell_vol,
                "total_vol":      buy_vol + sell_vol,
                "traded_volume":  final_traded,
                "cum_volume":     latest_cum_vol,
                "avg_trade_size": avg_trade_size,
            })

        # Write back updated cum_vol state with a single lock acquire
        if new_cum_vols:
            with self._lock:
                self._last_cum_vol.update(new_cum_vols)

        return candles

    def get_last_ltp(self, symbol: str) -> Optional[float]:
        with self._lock:
            ticks = self._ticks.get(symbol, [])
            return ticks[-1][1] if ticks else None
