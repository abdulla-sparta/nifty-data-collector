"""
utils/telegram.py
All Telegram interactions:
  - Send alert messages
  - Send daily login link at 08:45 IST
  - Receive token via /token command
  - Health status pings
"""

import requests
import logging
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, UPSTOX_API_KEY, UPSTOX_REDIRECT_URI

logger = logging.getLogger(__name__)

BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def send(text: str, parse_mode: str = "HTML") -> bool:
    """Send a message to your Telegram chat."""
    try:
        resp = requests.post(
            f"{BASE}/sendMessage",
            json={
                "chat_id":    TELEGRAM_CHAT_ID,
                "text":       text,
                "parse_mode": parse_mode,
            },
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def send_login_link():
    """
    Send the Upstox login URL to Telegram at 08:45 IST.
    User taps the link on phone → completes auth →
    Railway receives the callback → saves token.
    """
    auth_url = (
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code"
        f"&client_id={UPSTOX_API_KEY}"
        f"&redirect_uri={UPSTOX_REDIRECT_URI}"
    )
    msg = (
        "🔐 <b>NiftyCollector — Daily Login</b>\n\n"
        "Tap the link below to authorize for today's session.\n"
        "Takes 30 seconds.\n\n"
        f'<a href="{auth_url}">👉 Login to Upstox</a>\n\n'
        "Market opens in <b>30 minutes</b>."
    )
    ok = send(msg)
    if ok:
        logger.info("✅ Login link sent via Telegram")
    else:
        logger.error("❌ Failed to send login link")


def send_startup(trade_date: str):
    send(
        f"🟢 <b>NiftyCollector Started</b>\n"
        f"Date: {trade_date}\n"
        f"All collectors running ✅"
    )


def send_shutdown(trade_date: str, candles: int, oc_snaps: int):
    send(
        f"🔴 <b>NiftyCollector Stopped</b>\n"
        f"Date: {trade_date}\n"
        f"Candles written : {candles}\n"
        f"OC snapshots    : {oc_snaps}\n"
        f"CSV backup      : ✅"
    )


def send_error(context: str, error: str):
    send(
        f"⚠️ <b>Collector Error</b>\n"
        f"Context : {context}\n"
        f"Error   : <code>{error[:200]}</code>"
    )


def send_gap_alert(ts: str, symbol: str):
    send(
        f"⚠️ <b>Data Gap Detected</b>\n"
        f"Timestamp : {ts}\n"
        f"Symbol    : {symbol}\n"
        f"Possible: machine sleep or network drop."
    )


def send_no_token_alert():
    send(
        "🔴 <b>No Token Available</b>\n\n"
        "Collector cannot start — no valid Upstox token for today.\n"
        "Please run the login flow immediately.\n\n"
        "Use the morning login link or visit the callback URL manually."
    )


def get_updates(offset: int = 0) -> list:
    """Poll for new Telegram messages (for token command handling)."""
    try:
        resp = requests.get(
            f"{BASE}/getUpdates",
            params={"offset": offset, "timeout": 5},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("result", [])
    except Exception:
        pass
    return []
