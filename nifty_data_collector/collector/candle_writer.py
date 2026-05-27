"""
collector/candle_writer.py
CHANGED: added volume ratio per symbol, nifty volume, composite_vol_score
         updated _write_candles and _build_feature for new volume columns
"""

import time, logging, threading, requests
from datetime import datetime, timedelta
from collections import deque
import pytz, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import WATCHLIST, STOCK_WEIGHTS, INDIA_VIX_KEY
from collector.tick_buffer import TickBuffer
from utils.token_manager import get_access_token
from utils.market import (is_market_open, minutes_since_open, minutes_to_close,
                           session_zone, is_expiry_day, floor_to_60s)
from db.connection import DBConn

logger = logging.getLogger(__name__)
IST    = pytz.timezone("Asia/Kolkata")

_vix_w    = deque(maxlen=20)
_nifty_w  = deque(maxlen=20)
_tick_w   = deque(maxlen=20)
_stock_w  = {s: deque(maxlen=5)  for s in WATCHLIST}
_vol_w    = {s: deque(maxlen=20) for s in list(WATCHLIST.keys()) + ["NIFTY"]}  # ADDED
_candles_written  = 0
_oc_snaps_written = 0


class CandleWriter:
    def __init__(self, buffer: TickBuffer):
        self.buffer   = buffer
        self._running = False
        self._thread  = None

    def start(self):
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def get_counts(self):
        return _candles_written, _oc_snaps_written

    def _loop(self):
        while self._running:
            now  = datetime.now(IST)
            nxt  = (now + timedelta(seconds=60)).replace(second=2, microsecond=0)
            secs = (nxt - now).total_seconds()
            time.sleep(max(secs, 0))
            if is_market_open():
                ts = floor_to_60s(datetime.now(IST))
                try:
                    self._on_candle(ts)
                except Exception as e:
                    logger.error(f"CandleWriter: {e}")

    def _on_candle(self, ts: datetime):
        global _candles_written
        candles    = self.buffer.flush_all(ts)
        candle_map = {c["symbol"]: c for c in candles}
        self._write_candles(candles)
        _candles_written += len(candles)

        vix_row = self._fetch_vix(ts)
        self._build_feature(ts, candle_map, vix_row)

        logger.info(
            f"Candle {ts.strftime('%H:%M')} | "
            f"syms={len(candles)} | "
            f"vix={vix_row.get('vix') if vix_row else '-'}"
        )

    # ── Candle write ─────────────────────────────────────────

    def _write_candles(self, candles: list[dict]):
        if not candles:
            return
        sql = """
            INSERT INTO candles_60s (
                ts, symbol, open, high, low, close,
                tick_count, buy_vol, sell_vol, total_vol,
                traded_volume, cum_volume, avg_trade_size
            ) VALUES (
                %(ts)s, %(symbol)s, %(open)s, %(high)s, %(low)s, %(close)s,
                %(tick_count)s, %(buy_vol)s, %(sell_vol)s, %(total_vol)s,
                %(traded_volume)s, %(cum_volume)s, %(avg_trade_size)s
            ) ON CONFLICT (ts, symbol) DO NOTHING
        """
        with DBConn() as conn:
            conn.cursor().executemany(sql, candles)

    # ── VIX ──────────────────────────────────────────────────

    def _fetch_vix(self, ts: datetime) -> dict | None:
        try:
            token = get_access_token()
            r     = requests.get(
                "https://api.upstox.com/v2/market-quote/ltp",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                params={"instrument_key": INDIA_VIX_KEY},
                timeout=5,
            )
            if r.status_code != 200:
                return None
            data = r.json().get("data", {})
            vix  = float(list(data.values())[0].get("last_price", 0)) if data else 0
            if vix == 0:
                return None

            _vix_w.append(vix)
            vix_open  = _vix_w[0]
            pct_chg   = round((vix - vix_open) / vix_open * 100, 4)
            min1_chg  = round(vix - _vix_w[-2], 4) if len(_vix_w) >= 2 else 0.0
            min5_chg  = round(vix - _vix_w[-6], 4) if len(_vix_w) >= 6 else 0.0
            if len(_vix_w) >= 5:
                mean   = sum(_vix_w) / len(_vix_w)
                std    = (sum((x-mean)**2 for x in _vix_w) / len(_vix_w)) ** 0.5
                zscore = round((vix - mean) / std, 4) if std > 0 else 0.0
            else:
                zscore = 0.0
            tier = 3 if abs(pct_chg)>=20 else 2 if abs(pct_chg)>=10 else 1 if abs(pct_chg)>=5 else 0

            row = {
                "ts": ts, "vix": vix, "vix_open": vix_open,
                "vix_pct_chg": pct_chg, "vix_1min_chg": min1_chg,
                "vix_5min_chg": min5_chg, "vix_tier": tier,
                "vix_zscore_20": zscore,
            }
            with DBConn() as conn:
                conn.cursor().execute("""
                    INSERT INTO vix_60s
                        (ts,vix,vix_open,vix_pct_chg,vix_1min_chg,vix_5min_chg,vix_tier,vix_zscore_20)
                    VALUES
                        (%(ts)s,%(vix)s,%(vix_open)s,%(vix_pct_chg)s,%(vix_1min_chg)s,
                         %(vix_5min_chg)s,%(vix_tier)s,%(vix_zscore_20)s)
                    ON CONFLICT (ts) DO NOTHING
                """, row)
            return row
        except Exception as e:
            logger.error(f"VIX: {e}")
            return None

    # ── Feature snapshot ─────────────────────────────────────

    def _vol_ratio(self, symbol: str, vol: int) -> float:
        """ADDED: Volume ratio = this candle vol / 20-candle rolling avg."""
        _vol_w[symbol].append(vol)
        avg = sum(_vol_w[symbol]) / len(_vol_w[symbol])
        return round(vol / max(avg, 1), 4)

    def _build_feature(self, ts: datetime, candle_map: dict, vix_row: dict | None):
        nifty = candle_map.get("NIFTY")
        if not nifty:
            return

        nc = nifty["close"]
        _nifty_w.append(nc)
        n1m    = round((nc - _nifty_w[-2]) / _nifty_w[-2] * 100, 4) if len(_nifty_w) >= 2 else 0.0
        n5m    = round((nc - _nifty_w[-6]) / _nifty_w[-6] * 100, 4) if len(_nifty_w) >= 6 else 0.0
        vwap   = round(sum(_nifty_w) / len(_nifty_w), 2)
        vsvwap = round((nc - vwap) / vwap * 100, 4)

        _tick_w.append(nifty.get("tick_count", 0))
        tick_vel = round(_tick_w[-1] / max(sum(_tick_w)/len(_tick_w), 1), 4)
        imbal    = round(nifty.get("buy_vol", 0) / max(nifty.get("total_vol", 1), 1), 4)

        # NIFTY volume                                       ← ADDED
        nifty_traded_vol = nifty.get("traded_volume", 0) or 0
        nifty_cum_vol    = nifty.get("cum_volume", 0) or 0
        nifty_vol_ratio  = self._vol_ratio("NIFTY", nifty_traded_vol)

        # Per-stock features
        stock_rows = {}
        hw_w = hw_wsum = 0.0
        vol_score_w = vol_score_wsum = 0.0                   # ADDED for composite

        for sym, weight in STOCK_WEIGHTS.items():
            c = candle_map.get(sym)
            if c:
                _stock_w[sym].append(c["close"])
                chg = round((c["close"] - _stock_w[sym][-2]) / _stock_w[sym][-2] * 100, 4) \
                      if len(_stock_w[sym]) >= 2 else 0.0

                # Volume ratio per stock                    ← ADDED
                sv = c.get("traded_volume", 0) or 0
                vol_ratio = self._vol_ratio(sym, sv)

                stock_rows[sym] = {
                    "close":     c["close"],
                    "chg":       chg,
                    "vol_ratio": vol_ratio,               # ADDED
                }
                hw_w       += chg * weight
                hw_wsum    += weight
                vol_score_w     += vol_ratio * weight     # ADDED
                vol_score_wsum  += weight                 # ADDED

        hw_div = round(hw_w / max(hw_wsum, 1) - n1m, 4)
        hw_dir = 1 if hw_div > 0.05 else (-1 if hw_div < -0.05 else 0)

        # Composite volume score — weighted avg across 6 stocks  ← ADDED
        composite_vol_score = round(vol_score_w / max(vol_score_wsum, 1), 4)

        oc = self._latest_oc()

        def sr(sym, key):
            return stock_rows.get(sym, {}).get(key)

        row = {
            "ts":                  ts,
            "nifty_open":          nifty["open"],
            "nifty_close":         nc,
            "nifty_1min_chg":      n1m,
            "nifty_5min_chg":      n5m,
            "nifty_vwap":          vwap,
            "nifty_vs_vwap":       vsvwap,
            "nifty_traded_vol":    nifty_traded_vol,      # ADDED
            "nifty_cum_volume":    nifty_cum_vol,         # ADDED
            "nifty_vol_ratio":     nifty_vol_ratio,       # ADDED
            "reliance_close":      sr("RELIANCE",   "close"),
            "reliance_1m_chg":     sr("RELIANCE",   "chg"),
            "reliance_vol_ratio":  sr("RELIANCE",   "vol_ratio"),   # ADDED
            "hdfcbank_close":      sr("HDFCBANK",   "close"),
            "hdfcbank_1m_chg":     sr("HDFCBANK",   "chg"),
            "hdfcbank_vol_ratio":  sr("HDFCBANK",   "vol_ratio"),   # ADDED
            "bhartiartl_close":    sr("BHARTIARTL", "close"),
            "bhartiartl_1m_chg":   sr("BHARTIARTL", "chg"),
            "bhartiartl_vol_ratio":sr("BHARTIARTL", "vol_ratio"),   # ADDED
            "icicibank_close":     sr("ICICIBANK",  "close"),
            "icicibank_1m_chg":    sr("ICICIBANK",  "chg"),
            "icicibank_vol_ratio": sr("ICICIBANK",  "vol_ratio"),   # ADDED
            "sbin_close":          sr("SBIN",        "close"),
            "sbin_1m_chg":         sr("SBIN",        "chg"),
            "sbin_vol_ratio":      sr("SBIN",        "vol_ratio"),  # ADDED
            "tcs_close":           sr("TCS",         "close"),
            "tcs_1m_chg":          sr("TCS",         "chg"),
            "tcs_vol_ratio":       sr("TCS",         "vol_ratio"),  # ADDED
            "composite_vol_score": composite_vol_score,             # ADDED
            "hw_divergence":       hw_div,
            "hw_direction":        hw_dir,
            "tick_velocity":       tick_vel,
            "imbalance_ratio":     imbal,
            "vix":                 vix_row.get("vix")           if vix_row else None,
            "vix_pct_chg":         vix_row.get("vix_pct_chg")  if vix_row else None,
            "vix_tier":            vix_row.get("vix_tier")      if vix_row else None,
            "vix_zscore_20":       vix_row.get("vix_zscore_20") if vix_row else None,
            "pcr":                 oc.get("pcr")            if oc else None,
            "atm_pcr":             oc.get("atm_pcr")        if oc else None,
            "iv_skew":             oc.get("iv_skew")        if oc else None,
            "pcr_5snap_chg":       oc.get("pcr_5snap_chg")  if oc else None,
            "ce_oi_chg":           oc.get("ce_oi_chg")      if oc else None,
            "pe_oi_chg":           oc.get("pe_oi_chg")      if oc else None,
            "atm_ce_vol":          oc.get("atm_ce_vol")     if oc else None,  # ADDED
            "atm_pe_vol":          oc.get("atm_pe_vol")     if oc else None,  # ADDED
            "max_pain_strike":     oc.get("max_pain_strike") if oc else None,
            "minutes_since_open":  minutes_since_open(),
            "minutes_to_close":    minutes_to_close(),
            "day_of_week":         ts.weekday(),
            "is_expiry_day":       is_expiry_day(),
            "session_zone":        session_zone(),
            "active_scenarios":    [],
            "scenario_count":      0,
        }

        sql = """
            INSERT INTO feature_snapshot (
                ts, nifty_open, nifty_close, nifty_1min_chg, nifty_5min_chg,
                nifty_vwap, nifty_vs_vwap,
                nifty_traded_vol, nifty_cum_volume, nifty_vol_ratio,
                reliance_close, reliance_1m_chg, reliance_vol_ratio,
                hdfcbank_close, hdfcbank_1m_chg, hdfcbank_vol_ratio,
                bhartiartl_close, bhartiartl_1m_chg, bhartiartl_vol_ratio,
                icicibank_close, icicibank_1m_chg, icicibank_vol_ratio,
                sbin_close, sbin_1m_chg, sbin_vol_ratio,
                tcs_close, tcs_1m_chg, tcs_vol_ratio,
                composite_vol_score,
                hw_divergence, hw_direction, tick_velocity, imbalance_ratio,
                vix, vix_pct_chg, vix_tier, vix_zscore_20,
                pcr, atm_pcr, iv_skew, pcr_5snap_chg,
                ce_oi_chg, pe_oi_chg, atm_ce_vol, atm_pe_vol, max_pain_strike,
                minutes_since_open, minutes_to_close,
                day_of_week, is_expiry_day, session_zone,
                active_scenarios, scenario_count
            ) VALUES (
                %(ts)s, %(nifty_open)s, %(nifty_close)s, %(nifty_1min_chg)s, %(nifty_5min_chg)s,
                %(nifty_vwap)s, %(nifty_vs_vwap)s,
                %(nifty_traded_vol)s, %(nifty_cum_volume)s, %(nifty_vol_ratio)s,
                %(reliance_close)s, %(reliance_1m_chg)s, %(reliance_vol_ratio)s,
                %(hdfcbank_close)s, %(hdfcbank_1m_chg)s, %(hdfcbank_vol_ratio)s,
                %(bhartiartl_close)s, %(bhartiartl_1m_chg)s, %(bhartiartl_vol_ratio)s,
                %(icicibank_close)s, %(icicibank_1m_chg)s, %(icicibank_vol_ratio)s,
                %(sbin_close)s, %(sbin_1m_chg)s, %(sbin_vol_ratio)s,
                %(tcs_close)s, %(tcs_1m_chg)s, %(tcs_vol_ratio)s,
                %(composite_vol_score)s,
                %(hw_divergence)s, %(hw_direction)s, %(tick_velocity)s, %(imbalance_ratio)s,
                %(vix)s, %(vix_pct_chg)s, %(vix_tier)s, %(vix_zscore_20)s,
                %(pcr)s, %(atm_pcr)s, %(iv_skew)s, %(pcr_5snap_chg)s,
                %(ce_oi_chg)s, %(pe_oi_chg)s, %(atm_ce_vol)s, %(atm_pe_vol)s, %(max_pain_strike)s,
                %(minutes_since_open)s, %(minutes_to_close)s,
                %(day_of_week)s, %(is_expiry_day)s, %(session_zone)s,
                %(active_scenarios)s, %(scenario_count)s
            ) ON CONFLICT (ts) DO NOTHING
        """
        with DBConn() as conn:
            conn.cursor().execute(sql, row)

    def _latest_oc(self) -> dict | None:
        try:
            with DBConn() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT pcr, atm_pcr, iv_skew, pcr_5snap_chg,
                           ce_oi_chg, pe_oi_chg, atm_ce_vol, atm_pe_vol, max_pain_strike
                    FROM option_chain_agg ORDER BY ts DESC LIMIT 1
                """)
                row = cur.fetchone()
                if row:
                    return dict(zip([
                        "pcr","atm_pcr","iv_skew","pcr_5snap_chg",
                        "ce_oi_chg","pe_oi_chg","atm_ce_vol","atm_pe_vol","max_pain_strike"
                    ], row))
        except Exception:
            pass
        return None
