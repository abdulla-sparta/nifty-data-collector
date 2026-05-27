"""
config.py — All config from Railway environment variables.
Never hardcode credentials here.
Set these in Railway → Variables panel.
"""

import os

# ── Upstox ────────────────────────────────────────────────────
UPSTOX_API_KEY      = os.environ["UPSTOX_API_KEY"]
UPSTOX_API_SECRET   = os.environ["UPSTOX_API_SECRET"]
UPSTOX_REDIRECT_URI = os.environ.get("UPSTOX_REDIRECT_URI", "https://your-railway-app.up.railway.app/callback")

# ── PostgreSQL (Railway injects DATABASE_URL automatically) ───
DATABASE_URL = os.environ["DATABASE_URL"]

# ── Telegram ──────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

# ── Market ────────────────────────────────────────────────────
MARKET_OPEN  = "09:15"
MARKET_CLOSE = "15:30"

# ── Watchlist ─────────────────────────────────────────────────
WATCHLIST = {
    "RELIANCE":   "NSE_EQ|INE002A01018",
    "HDFCBANK":   "NSE_EQ|INE040A01034",
    "BHARTIARTL": "NSE_EQ|INE397D01024",
    "ICICIBANK":  "NSE_EQ|INE090A01021",
    "SBIN":       "NSE_EQ|INE062A01020",
    "TCS":        "NSE_EQ|INE467B01029",
}

NIFTY_INDEX_KEY = "NSE_INDEX|Nifty 50"
INDIA_VIX_KEY   = "NSE_INDEX|India VIX"

STOCK_WEIGHTS = {
    "RELIANCE":   9.64,
    "HDFCBANK":   6.20,
    "BHARTIARTL": 5.99,
    "ICICIBANK":  4.77,
    "SBIN":       4.60,
    "TCS":        4.41,
}

# ── Option Chain ──────────────────────────────────────────────
OC_POLL_INTERVAL_SEC = 30
NIFTY_STRIKE_STEP    = 50

# ── Candle ────────────────────────────────────────────────────
CANDLE_INTERVAL_SEC  = 60

# ── Backup ────────────────────────────────────────────────────
BACKUP_TIME = "15:45"   # IST — daily CSV dump

# ── VIX Tiers ─────────────────────────────────────────────────
VIX_TIER_THRESHOLDS = {
    1: (5,  10),
    2: (10, 20),
    3: (20, 999),
}
