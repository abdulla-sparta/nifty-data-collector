"""
main.py — NiftyCollector Railway Entry Point

Single process runs:
  Flask server  → /callback (Upstox OAuth), /health, /status, /login
  Scheduler     → 08:45 login link, 15:45 backup, daily cycle
  Collectors    → WebSocket ticks, OC poller, Candle writer
"""

import os, sys, logging, time, threading
from datetime import datetime, date
import pytz, schedule
from flask import Flask, request, jsonify, redirect

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
app.register_blueprint(dashboard_bp)

# Globals — collector handles
_buffer: TickBuffer | None         = None
_tick_col: TickCollector | None    = None
_oc_pol: OCPoller | None           = None
_cw: CandleWriter | None           = None
_collecting = False


@app.route("/")
def home():
    """Redirect root to dashboard."""
    return redirect("/dashboard")


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


@app.route("/login")
def login_page():
    """
    Auto-generate Upstox login URL from environment variables.
    No manual API key entry needed — just visit /login and click.
    """
    from config import UPSTOX_API_KEY, UPSTOX_REDIRECT_URI
    auth_url = (
        f"https://api.upstox.com/v2/login/authorization/dialog"
        f"?response_type=code"
        f"&client_id={UPSTOX_API_KEY}"
        f"&redirect_uri={UPSTOX_REDIRECT_URI}"
    )
    token_status = "✅ Token already saved for today" if has_token_today() else "❌ No token yet — login required"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NiftyCollector — Login</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Syne:wght@700;800&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #0a0e1a; --surface: #111827; --border: #1e293b;
    --green: #10b981; --text: #e2e8f0; --muted: #64748b;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:'JetBrains Mono',monospace;
          display:flex; align-items:center; justify-content:center; min-height:100vh; }}
  .card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px;
           padding:48px; width:420px; text-align:center; }}
  .logo {{ font-family:'Syne',sans-serif; font-weight:800; font-size:22px;
           color:var(--green); letter-spacing:0.08em; margin-bottom:8px; }}
  .sub {{ color:var(--muted); font-size:12px; margin-bottom:32px; }}
  .status {{ font-size:13px; color:var(--muted); margin-bottom:28px;
             background:#0a0e1a; border:1px solid var(--border);
             border-radius:6px; padding:12px 16px; }}
  .btn {{ display:inline-block; background:var(--green); color:#fff;
          font-family:'JetBrains Mono',monospace; font-size:14px; font-weight:700;
          padding:14px 32px; border-radius:8px; text-decoration:none;
          letter-spacing:0.05em; transition:opacity 0.15s; }}
  .btn:hover {{ opacity:0.85; }}
  .note {{ color:var(--muted); font-size:11px; margin-top:20px; line-height:1.6; }}
  a.back {{ color:var(--green); font-size:11px; text-decoration:none; display:block; margin-top:24px; }}
</style>
</head>
<body>
<div class="card">
  <div class="logo">NIFTY COLLECTOR</div>
  <div class="sub">Daily Upstox Authorization</div>
  <div class="status">{token_status}</div>
  <a href="{auth_url}" class="btn">🔐 Authorize Upstox</a>
  <p class="note">Tap the button above on your phone.<br>
  You'll be redirected back here automatically.<br>
  Token is saved to the database for the day.</p>
  <a href="/dashboard" class="back">← Back to Dashboard</a>
</div>
</body>
</html>"""


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

        return """<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<meta http-equiv="refresh" content="3;url=/dashboard">
<style>body{{background:#0a0e1a;color:#10b981;font-family:monospace;
display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:16px;}}
a{{color:#10b981;}}</style></head>
<body><h2>✅ Login successful!</h2><p>Token saved. Redirecting to dashboard…</p>
<a href="/dashboard">Go now →</a></body></html>"""

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
