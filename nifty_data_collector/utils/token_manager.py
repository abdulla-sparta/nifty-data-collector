"""
utils/token_manager.py
Token stored in PostgreSQL token_store table (persists across Railway restarts).
Daily flow:
  08:45 → Telegram sends login link
  You tap on phone → Upstox redirects to Railway /callback
  Flask route captures auth code → exchanges for token → saves to DB
  09:00 → Collector reads token from DB and starts
"""

import requests
import logging
from datetime import date, datetime
import pytz
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import UPSTOX_API_KEY, UPSTOX_API_SECRET, UPSTOX_REDIRECT_URI
from db.connection import DBConn

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


def save_token(access_token: str):
    """Save token to DB — overwrites today's entry if exists."""
    today = date.today()
    sql = """
        INSERT INTO token_store (access_token, saved_date)
        VALUES (%s, %s)
        ON CONFLICT (saved_date) DO UPDATE
            SET access_token = EXCLUDED.access_token,
                created_at   = NOW()
    """
    with DBConn() as conn:
        conn.cursor().execute(sql, (access_token, today))
    logger.info(f"✅ Token saved for {today}")


def get_access_token() -> str:
    """Returns today's access token from DB. Raises if missing."""
    today = date.today()
    with DBConn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT access_token FROM token_store WHERE saved_date = %s",
            (today,)
        )
        row = cur.fetchone()
    if row:
        return row[0]
    raise RuntimeError(f"No token for {today} — login required")


def has_token_today() -> bool:
    try:
        get_access_token()
        return True
    except RuntimeError:
        return False


def exchange_code_for_token(auth_code: str) -> str:
    """
    Exchange Upstox auth code for access token.
    Called from Flask /callback route.
    """
    resp = requests.post(
        "https://api.upstox.com/v2/login/authorization/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "code":          auth_code,
            "client_id":     UPSTOX_API_KEY,
            "client_secret": UPSTOX_API_SECRET,
            "redirect_uri":  UPSTOX_REDIRECT_URI,
            "grant_type":    "authorization_code",
        }
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed: {resp.text}")

    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {resp.text}")

    save_token(token)
    return token
