"""
collector/oc_poller.py
FIX: invalidate expiry cache on start so fresh expiry is fetched from Upstox
FIX: pass token to nearest_expiry() to avoid double token fetch
FIX: log full error details, fallback INSERT without volume columns
"""

import time, logging, threading, requests
from datetime import datetime
import pytz, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import OC_POLL_INTERVAL_SEC, NIFTY_STRIKE_STEP
from utils.token_manager import get_access_token
from utils.market import is_market_open, nearest_expiry, invalidate_expiry_cache
from db.connection import DBConn

logger = logging.getLogger(__name__)
IST    = pytz.timezone("Asia/Kolkata")


class OCPoller:
    def __init__(self):
        self._running     = False
        self._thread      = None
        self._pcr_history = []
        self._prev_ce_oi  = None
        self._prev_pe_oi  = None

    def start(self):
        # Invalidate expiry cache so first poll fetches fresh from Upstox
        invalidate_expiry_cache()
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("OCPoller started")

    def stop(self):
        self._running = False
        logger.info("OCPoller stopped")

    def _loop(self):
        while self._running:
            if is_market_open():
                try:
                    self._poll()
                except Exception as e:
                    logger.error(f"OC poll exception: {e}", exc_info=True)
            else:
                logger.debug("OC poll skipped — market closed")
            time.sleep(OC_POLL_INTERVAL_SEC)

    def _poll(self):
        token  = get_access_token()

        # Pass token so nearest_expiry reuses it (no extra API call)
        expiry = nearest_expiry(token=token)

        logger.info(f"OC polling expiry={expiry}")

        r = requests.get(
            "https://api.upstox.com/v2/option/chain",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={
                "instrument_key": "NSE_INDEX|Nifty 50",
                "expiry_date":    expiry.strftime("%Y-%m-%d"),
            },
            timeout=10,
        )

        if r.status_code != 200:
            logger.error(f"OC API error {r.status_code}: {r.text[:300]}")
            return

        data  = r.json()
        chain = data.get("data", [])

        if not chain:
            logger.warning(f"OC chain empty for expiry={expiry}. Response: {str(data)[:300]}")
            # If empty, invalidate expiry cache — expiry may have shifted
            invalidate_expiry_cache()
            return

        logger.info(f"OC chain received: {len(chain)} strikes for expiry={expiry}")

        ts  = datetime.now(IST).replace(second=0, microsecond=0)
        agg = self._aggregate(chain)
        if agg:
            agg["ts"]     = ts
            agg["expiry"] = expiry
            self._write(agg)
        else:
            logger.warning("OC aggregate returned None — no nifty_ltp found in chain")

    def _aggregate(self, chain: list) -> dict | None:
        nifty_ltp = None
        for row in chain:
            sp = row.get("underlying_spot_price")
            if sp:
                nifty_ltp = float(sp)
                break
        if not nifty_ltp:
            return None

        atm         = round(nifty_ltp / NIFTY_STRIKE_STEP) * NIFTY_STRIKE_STEP
        total_ce_oi = total_pe_oi = 0
        atm_ce_oi   = atm_pe_oi  = 0
        atm_ce_iv   = atm_pe_iv  = 0.0
        atm_ce_vol  = atm_pe_vol = 0
        pain        = {}

        for row in chain:
            strike = int(row.get("strike_price", 0))
            ce     = (row.get("call_options") or {}).get("market_data") or {}
            pe     = (row.get("put_options")  or {}).get("market_data") or {}

            ce_oi  = int(ce.get("oi", 0) or 0)
            pe_oi  = int(pe.get("oi", 0) or 0)
            total_ce_oi += ce_oi
            total_pe_oi += pe_oi

            if strike == atm:
                atm_ce_oi  = ce_oi
                atm_pe_oi  = pe_oi
                atm_ce_iv  = float(ce.get("iv", 0) or 0)
                atm_pe_iv  = float(pe.get("iv", 0) or 0)
                atm_ce_vol = int(ce.get("volume", 0) or 0)
                atm_pe_vol = int(pe.get("volume", 0) or 0)

            pain[strike] = pain.get(strike, 0) + ce_oi + pe_oi

        pcr           = round(total_pe_oi / max(total_ce_oi, 1), 4)
        atm_pcr       = round(atm_pe_oi   / max(atm_ce_oi,   1), 4)
        iv_skew       = round(atm_pe_iv - atm_ce_iv, 4)
        max_pain      = min(pain, key=pain.get) if pain else None
        ce_oi_chg     = (total_ce_oi - self._prev_ce_oi) if self._prev_ce_oi is not None else 0
        pe_oi_chg     = (total_pe_oi - self._prev_pe_oi) if self._prev_pe_oi is not None else 0
        self._prev_ce_oi = total_ce_oi
        self._prev_pe_oi = total_pe_oi
        self._pcr_history.append(pcr)
        if len(self._pcr_history) > 5:
            self._pcr_history.pop(0)
        pcr_5snap_chg = round(pcr - self._pcr_history[0], 4) if len(self._pcr_history) >= 2 else 0.0

        return {
            "nifty_ltp":       nifty_ltp,
            "atm_strike":      atm,
            "total_ce_oi":     total_ce_oi,
            "total_pe_oi":     total_pe_oi,
            "pcr":             pcr,
            "atm_ce_oi":       atm_ce_oi,
            "atm_pe_oi":       atm_pe_oi,
            "atm_pcr":         atm_pcr,
            "atm_ce_iv":       atm_ce_iv,
            "atm_pe_iv":       atm_pe_iv,
            "iv_skew":         iv_skew,
            "ce_oi_chg":       ce_oi_chg,
            "pe_oi_chg":       pe_oi_chg,
            "atm_ce_vol":      atm_ce_vol,
            "atm_pe_vol":      atm_pe_vol,
            "max_pain_strike": max_pain,
            "pcr_5snap_chg":   pcr_5snap_chg,
        }

    def _write(self, agg: dict):
        sql_full = """
            INSERT INTO option_chain_agg (
                ts, expiry, nifty_ltp, atm_strike,
                total_ce_oi, total_pe_oi, pcr,
                atm_ce_oi, atm_pe_oi, atm_pcr,
                atm_ce_iv, atm_pe_iv, iv_skew,
                ce_oi_chg, pe_oi_chg,
                atm_ce_vol, atm_pe_vol,
                max_pain_strike, pcr_5snap_chg
            ) VALUES (
                %(ts)s, %(expiry)s, %(nifty_ltp)s, %(atm_strike)s,
                %(total_ce_oi)s, %(total_pe_oi)s, %(pcr)s,
                %(atm_ce_oi)s, %(atm_pe_oi)s, %(atm_pcr)s,
                %(atm_ce_iv)s, %(atm_pe_iv)s, %(iv_skew)s,
                %(ce_oi_chg)s, %(pe_oi_chg)s,
                %(atm_ce_vol)s, %(atm_pe_vol)s,
                %(max_pain_strike)s, %(pcr_5snap_chg)s
            ) ON CONFLICT DO NOTHING
        """
        sql_fallback = """
            INSERT INTO option_chain_agg (
                ts, expiry, nifty_ltp, atm_strike,
                total_ce_oi, total_pe_oi, pcr,
                atm_ce_oi, atm_pe_oi, atm_pcr,
                atm_ce_iv, atm_pe_iv, iv_skew,
                ce_oi_chg, pe_oi_chg,
                max_pain_strike, pcr_5snap_chg
            ) VALUES (
                %(ts)s, %(expiry)s, %(nifty_ltp)s, %(atm_strike)s,
                %(total_ce_oi)s, %(total_pe_oi)s, %(pcr)s,
                %(atm_ce_oi)s, %(atm_pe_oi)s, %(atm_pcr)s,
                %(atm_ce_iv)s, %(atm_pe_iv)s, %(iv_skew)s,
                %(ce_oi_chg)s, %(pe_oi_chg)s,
                %(max_pain_strike)s, %(pcr_5snap_chg)s
            ) ON CONFLICT DO NOTHING
        """
        try:
            with DBConn() as conn:
                conn.cursor().execute(sql_full, agg)
            logger.info(
                f"OC ✅ {agg['ts'].strftime('%H:%M')} | "
                f"expiry={agg['expiry']} | PCR={agg['pcr']} | "
                f"ATM={agg['atm_strike']} | "
                f"CE_OI={agg['total_ce_oi']:,} PE_OI={agg['total_pe_oi']:,}"
            )
        except Exception as e:
            if "atm_ce_vol" in str(e) or "atm_pe_vol" in str(e) or "column" in str(e).lower():
                logger.warning("Volume columns missing — using fallback INSERT. Run: python -m db.schema")
                try:
                    with DBConn() as conn:
                        conn.cursor().execute(sql_fallback, agg)
                    logger.info(f"OC ✅ fallback {agg['ts'].strftime('%H:%M')} PCR={agg['pcr']}")
                except Exception as e2:
                    logger.error(f"OC fallback INSERT failed: {e2}", exc_info=True)
            else:
                logger.error(f"OC write failed: {e}", exc_info=True)
