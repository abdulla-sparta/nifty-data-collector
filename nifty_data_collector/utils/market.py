"""
utils/market.py
FIX: Added May 28 2026 (Bakri Id) to NSE holidays
FIX: nearest_expiry() now fetches live expiry list from Upstox API
     instead of calculating — handles all holiday shifts automatically
"""

from datetime import datetime, time, date, timedelta
from typing import Optional
import pytz
import requests
import logging

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

NSE_HOLIDAYS = {
    date(2025, 1, 26), date(2025, 2, 26), date(2025, 3, 14),
    date(2025, 3, 31), date(2025, 4, 10), date(2025, 4, 14),
    date(2025, 4, 18), date(2025, 5, 1),  date(2025, 8, 15),
    date(2025, 8, 27), date(2025, 10, 2), date(2025, 10, 24),
    date(2025, 10, 25),date(2025, 11, 5), date(2025, 12, 25),
    date(2026, 1, 26), date(2026, 3, 20), date(2026, 4, 2),
    date(2026, 4, 14), date(2026, 4, 15), date(2026, 5, 1),
    date(2026, 5, 28),  # ← ADDED: Bakri Id
    date(2026, 8, 15), date(2026, 10, 2), date(2026, 11, 14),
    date(2026, 12, 25),
}

def now_ist():   return datetime.now(IST)
def today_ist(): return now_ist().date()

def is_trading_day(d: date = None) -> bool:
    d = d or today_ist()
    return d.weekday() < 5 and d not in NSE_HOLIDAYS

def is_market_open() -> bool:
    n = now_ist()
    return is_trading_day(n.date()) and time(9, 15) <= n.time() < time(15, 30)

def is_pre_open() -> bool:
    n = now_ist()
    return is_trading_day(n.date()) and time(9, 0) <= n.time() < time(9, 15)

def seconds_to_market_open() -> float:
    n = now_ist()
    target = n.replace(hour=9, minute=15, second=0, microsecond=0)
    return (target - n).total_seconds()

def minutes_since_open() -> int:
    n = now_ist()
    o = n.replace(hour=9, minute=15, second=0, microsecond=0)
    return max(0, int((n - o).total_seconds() / 60))

def minutes_to_close() -> int:
    n = now_ist()
    c = n.replace(hour=15, minute=30, second=0, microsecond=0)
    return max(0, int((c - n).total_seconds() / 60))

def session_zone() -> int:
    m = minutes_since_open()
    if m <= 30:                  return 1
    if minutes_to_close() <= 30: return 3
    return 2

def is_expiry_day() -> bool:
    """True if today is a Nifty weekly expiry day (usually Thursday, or shifted date)."""
    today = today_ist()
    exp   = nearest_expiry()
    return exp == today

def floor_to_60s(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


# ── Expiry cache — refreshed once per day ─────────────────────
_expiry_cache: Optional[date] = None
_expiry_cache_date: Optional[date] = None


def nearest_expiry(token: str = None) -> date:
    """
    Returns the nearest Nifty weekly expiry date.

    Strategy:
      1. Try to fetch live expiry list from Upstox (most accurate)
         — handles all holiday shifts, special weekly expiries automatically
      2. Fall back to calculated Thursday logic if API unavailable

    The live fetch is cached for the trading day so it doesn't add API calls.
    Pass token=None to use the token from token_manager automatically.
    """
    global _expiry_cache, _expiry_cache_date

    today = today_ist()

    # Return cached value if still valid for today
    if _expiry_cache_date == today and _expiry_cache is not None:
        return _expiry_cache

    # Try live fetch first
    expiry = _fetch_nearest_expiry_from_upstox(token)
    if expiry:
        _expiry_cache      = expiry
        _expiry_cache_date = today
        logger.info(f"Expiry from Upstox API: {expiry}")
        return expiry

    # Fallback: calculate next Thursday, handle holidays by shifting forward
    expiry = _calculate_nearest_expiry(today)
    logger.warning(f"Expiry from calculation (API unavailable): {expiry}")
    _expiry_cache      = expiry
    _expiry_cache_date = today
    return expiry


def _fetch_nearest_expiry_from_upstox(token: str = None) -> Optional[date]:
    """
    Fetch available expiry dates from Upstox and return the nearest one.
    Uses /v2/option/contract endpoint.
    """
    try:
        if token is None:
            # Import here to avoid circular import
            from utils.token_manager import get_access_token
            token = get_access_token()

        r = requests.get(
            "https://api.upstox.com/v2/option/contract",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"instrument_key": "NSE_INDEX|Nifty 50"},
            timeout=8,
        )

        if r.status_code != 200:
            logger.warning(f"Expiry fetch HTTP {r.status_code}: {r.text[:200]}")
            return None

        data = r.json().get("data", [])
        if not data:
            return None

        today = today_ist()
        expiry_dates = []

        for item in data:
            # Each item has an 'expiry' field as string YYYY-MM-DD
            exp_str = item.get("expiry") or item.get("expiry_date") or item.get("expiryDate")
            if not exp_str:
                continue
            try:
                exp_date = date.fromisoformat(str(exp_str)[:10])
                if exp_date >= today:
                    expiry_dates.append(exp_date)
            except Exception:
                continue

        if not expiry_dates:
            return None

        # Return the nearest (smallest) future expiry
        return min(expiry_dates)

    except Exception as e:
        logger.warning(f"Expiry fetch failed: {e}")
        return None


def _calculate_nearest_expiry(today: date) -> date:
    """
    Fallback: calculate nearest Thursday expiry.
    If Thursday is a holiday, shift forward to next trading day.
    If shifted date is already past, move to next week.
    """
    days_ahead = (3 - today.weekday()) % 7
    thursday   = today + timedelta(days=days_ahead)

    # If Thursday is holiday, shift forward
    if thursday in NSE_HOLIDAYS:
        expiry = thursday + timedelta(days=1)
        while expiry in NSE_HOLIDAYS or expiry.weekday() >= 5:
            expiry += timedelta(days=1)
    else:
        expiry = thursday

    # If expiry already passed today, move to next week's Thursday
    if expiry < today:
        next_thursday = thursday + timedelta(weeks=1)
        if next_thursday in NSE_HOLIDAYS:
            expiry = next_thursday + timedelta(days=1)
            while expiry in NSE_HOLIDAYS or expiry.weekday() >= 5:
                expiry += timedelta(days=1)
        else:
            expiry = next_thursday

    return expiry


def invalidate_expiry_cache():
    """Call this at market open to force a fresh expiry fetch."""
    global _expiry_cache, _expiry_cache_date
    _expiry_cache      = None
    _expiry_cache_date = None
