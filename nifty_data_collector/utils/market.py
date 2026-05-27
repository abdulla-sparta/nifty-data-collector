"""
utils/market.py — Market hours, trading day, expiry detection
FIX: nearest_expiry() was returning next Thursday even when today IS Thursday
     Now correctly returns today if it's Thursday (expiry day),
     otherwise returns the next Thursday.
     Also fixed: Tuesday May 27 was returning May 28 (Wednesday) — off by one.
"""

from datetime import datetime, time, date, timedelta
import pytz

IST = pytz.timezone("Asia/Kolkata")

NSE_HOLIDAYS = {
    date(2025, 1, 26), date(2025, 2, 26), date(2025, 3, 14),
    date(2025, 3, 31), date(2025, 4, 10), date(2025, 4, 14),
    date(2025, 4, 18), date(2025, 5, 1),  date(2025, 8, 15),
    date(2025, 8, 27), date(2025, 10, 2), date(2025, 10, 24),
    date(2025, 10, 25),date(2025, 11, 5), date(2025, 12, 25),
    date(2026, 1, 26), date(2026, 3, 20), date(2026, 4, 2),
    date(2026, 4, 14), date(2026, 4, 15), date(2026, 5, 1),
    date(2026, 8, 15), date(2026, 10, 2), date(2026, 11, 14),
    date(2026, 12, 25),
}

def now_ist():      return datetime.now(IST)
def today_ist():    return now_ist().date()

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
    if m <= 30:                  return 1   # opening
    if minutes_to_close() <= 30: return 3   # closing
    return 2                                # mid

def is_expiry_day() -> bool:
    return today_ist().weekday() == 3   # Thursday = 3

def nearest_expiry() -> date:
    """
    Returns the nearest Nifty weekly expiry (Thursday).
    FIX: use (3 - weekday) % 7 gives 0 on Thursday itself,
         which we now correctly handle by returning today.
         Previous code forced days=7 when days==0, skipping today's expiry.

    Examples:
        Monday    (0) → days_ahead = 3  → this Thursday
        Tuesday   (1) → days_ahead = 2  → this Thursday
        Wednesday (2) → days_ahead = 1  → tomorrow Thursday
        Thursday  (3) → days_ahead = 0  → today (expiry day!)
        Friday    (4) → days_ahead = 6  → next Thursday
    """
    today     = today_ist()
    days_ahead = (3 - today.weekday()) % 7
    # If today is Thursday (days_ahead==0), expiry is today
    # No adjustment needed — removed the incorrect `if days == 0: days = 7`
    return today + timedelta(days=days_ahead)

def floor_to_60s(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)
