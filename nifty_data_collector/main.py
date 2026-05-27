"""
main.py — NiftyCollector Railway Entry Point

Single process runs:
  Flask server  → /callback (Upstox OAuth), /health, /status
  Scheduler     → 08:45 login link, 15:45 backup, daily cycle
  Collectors    → WebSocket ticks, OC poller, Candle writer
"""

import os, sys, logging, time, threading
from datetime import datetime, date
import pytz, schedule
from flask import Flask, request, jsonify

from db.schema import run as create_tables
from utils.market import is_trading_day, seconds_to_market_open, today_ist, is_market_open
from utils.token_manager import exchange_code_for_token, has_token_today, get_access_token
from utils.telegram import (send, send_login_link, send_startup,
                             send_shutdown, send_error, send_no_token_alert)
from collector.tick_buffer import TickBuffer
from collector.tick_collector import TickCollector
from collector.oc_poller import OCPoller
from collector.candle_writer import CandleWriter
from backup.daily_backup import run_backup

# Near top with imports
from dashboard import dashboard_bp



# ── Logging ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")
IST    = pytz.timezone("Asia/Kolkata")

# ── Flask ─────────────────────────────────────────────────────
app = Flask(__name__)
# Right after app = Flask(__name__)
app.register_blueprint(dashboard_bp)

# Globals — collector handles
_buffer: TickBuffer | None         = None
_tick_col: TickCollector | None    = None
_oc_pol: OCPoller | None           = None
_cw: CandleWriter | None           = None
_collecting = False


@app.route("/health")
def health():
    return jsonify({"status": "ok", "ts": datetime.now(IST).isoformat()})


@app.route("/status")
def status():
    candles, oc = (_cw.get_counts() if _cw else (0, 0))
    return jsonify({
        "collecting":     _collecting,
        "token_today":    has_token_today(),
        "is_trading_day": is_trading_day(),
        "is_market_open": is_market_open(),
        "candles_today":  candles,
        "oc_snaps_today": oc,
        "ts":             datetime.now(IST).isoformat(),
    })


@app.route("/callback")
def upstox_callback():
    """
    Upstox OAuth2 redirect lands here after user logs in on phone.
    Exchanges code → token → saves to DB → starts collectors if market open.
    """
    code  = request.args.get("code")
    error = request.args.get("error")

    if error:
        logger.error(f"OAuth error: {error}")
        send(f"❌ Login failed: {error}")
        return f"<h2>Login failed: {error}</h2>", 400

    if not code:
        return "<h2>No auth code received.</h2>", 400

    try:
        token = exchange_code_for_token(code)
        logger.info("✅ Token received and saved")
        send("✅ <b>Login successful!</b>\nToken saved. Collector will start at 09:15.")

        # If already market time, start immediately
        if is_market_open():
            threading.Thread(target=_start_collectors, daemon=True).start()

        return "<h2>✅ Login successful! You can close this tab.</h2>"

    except Exception as e:
        logger.error(f"Token exchange error: {e}")
        send_error("OAuth callback", str(e))
        return f"<h2>Error: {e}</h2>", 500


# ── Collector lifecycle ───────────────────────────────────────

def _start_collectors():
    global _buffer, _tick_col, _oc_pol, _cw, _collecting
    if _collecting:
        return
    logger.info("▶️  Starting collectors...")
    _buffer   = TickBuffer()
    _tick_col = TickCollector(_buffer)
    _oc_pol   = OCPoller()
    _cw       = CandleWriter(_buffer)
    _tick_col.start()
    _oc_pol.start()
    _cw.start()
    _collecting = True
    send_startup(str(today_ist()))
    logger.info("🟢 All collectors running")


def _stop_collectors():
    global _collecting
    if not _collecting:
        return
    candles, oc = (_cw.get_counts() if _cw else (0, 0))
    if _tick_col: _tick_col.stop()
    if _oc_pol:   _oc_pol.stop()
    if _cw:       _cw.stop()
    _collecting = False
    logger.info("🔴 Collectors stopped")
    return candles, oc


# ── Daily scheduler ───────────────────────────────────────────

def job_send_login_link():
    if is_trading_day():
        logger.info("📲 Sending login link...")
        send_login_link()


def job_start_collecting():
    """09:15 — start collectors if token available."""
    if not is_trading_day():
        return
    if has_token_today():
        threading.Thread(target=_start_collectors, daemon=True).start()
    else:
        logger.warning("⚠️  No token at 09:15 — cannot start")
        send_no_token_alert()


def job_eod_backup():
    """15:45 — stop collectors, run backup, send summary."""
    if not is_trading_day():
        return
    candles, oc = _stop_collectors() or (0, 0)
    try:
        out_dir, total = run_backup()
        send_shutdown(str(today_ist()), candles, oc)
        send(f"📦 <b>Backup complete</b>\nRows: {total}\nPath: {out_dir}")
    except Exception as e:
        send_error("EOD backup", str(e))


def setup_schedule():
    schedule.every().day.at("08:45").do(job_send_login_link)
    schedule.every().day.at("09:15").do(job_start_collecting)
    schedule.every().day.at("15:45").do(job_eod_backup)
    logger.info("📅 Schedule set: login=08:45 | start=09:15 | backup=15:45")


def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(15)


# ── Bootstrap ─────────────────────────────────────────────────

def bootstrap():
    logger.info("=" * 55)
    logger.info("  NiftyCollector — Railway Boot")
    logger.info(f"  {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")
    logger.info("=" * 55)

    # Create tables (idempotent)
    try:
        create_tables()
    except Exception as e:
        logger.error(f"Schema init failed: {e}")
        send_error("Schema init", str(e))

    # Schedule jobs
    setup_schedule()

    # Start scheduler thread
    threading.Thread(target=run_scheduler, daemon=True).start()

    # If already in market hours with token → start immediately (e.g. after crash restart)
    if is_trading_day() and is_market_open() and has_token_today():
        logger.info("🔄 Resuming mid-session after restart")
        threading.Thread(target=_start_collectors, daemon=True).start()
    elif is_trading_day():
        logger.info("⏳ Waiting for market / token")
        send(
            f"🟡 <b>NiftyCollector Restarted</b>\n"
            f"Date: {today_ist()}\n"
            f"Token ready: {'✅' if has_token_today() else '❌ — login link coming at 08:45'}"
        )


if __name__ == "__main__":
    bootstrap()
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
