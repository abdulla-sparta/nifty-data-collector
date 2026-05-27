"""
dashboard.py — Flask blueprint for NiftyCollector data dashboard
Mount in main.py with: app.register_blueprint(dashboard_bp)

Routes:
  GET /dashboard               — HTML dashboard
  GET /api/stats               — live stats strip
  GET /api/candles             — candles_60s table
  GET /api/oc                  — option_chain_agg table
  GET /api/vix                 — vix_60s table
  GET /api/features            — feature_snapshot table
  GET /api/scenarios           — scenario_hits table
  GET /api/log                 — collection_log table
  GET /api/export_all          — ZIP of all tables as CSV
"""

import io
import csv
import zipfile
from datetime import date, datetime, timedelta

from flask import Blueprint, render_template, request, jsonify, send_file
import pytz

from db.connection import DBConn

dashboard_bp = Blueprint("dashboard", __name__, template_folder="templates")
IST = pytz.timezone("Asia/Kolkata")


# ── Helpers ───────────────────────────────────────────────────

def today_ist() -> date:
    return datetime.now(IST).date()


def parse_date_range(req):
    """Parse from/to query params. Default to today."""
    today = today_ist()
    from_s = req.args.get("from")
    to_s   = req.args.get("to")
    try:
        from_d = date.fromisoformat(from_s) if from_s else today
    except ValueError:
        from_d = today
    try:
        to_d = date.fromisoformat(to_s) if to_s else today
    except ValueError:
        to_d = today
    return from_d, to_d


def rows_to_json(cur):
    cols = [d[0] for d in cur.description]
    rows = []
    for row in cur.fetchall():
        d = {}
        for c, v in zip(cols, row):
            if isinstance(v, (datetime, date)):
                d[c] = v.isoformat()
            elif isinstance(v, (list,)):
                d[c] = v
            else:
                d[c] = v
        rows.append(d)
    return cols, rows


def query_table(sql, params=()):
    with DBConn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return rows_to_json(cur)


def make_csv_bytes(cols, rows) -> bytes:
    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(cols)
    for row in rows:
        w.writerow([row.get(c, '') for c in cols])
    return buf.getvalue().encode("utf-8")


# ── Dashboard HTML ────────────────────────────────────────────

@dashboard_bp.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


# ── Stats strip ───────────────────────────────────────────────

@dashboard_bp.route("/api/stats")
def api_stats():
    today = today_ist()
    with DBConn() as conn:
        cur = conn.cursor()

        # Candles today
        cur.execute("SELECT COUNT(*) FROM candles_60s WHERE ts::date = %s", (today,))
        candles_today = cur.fetchone()[0]

        # OC snapshots today
        cur.execute("SELECT COUNT(*) FROM option_chain_agg WHERE ts::date = %s", (today,))
        oc_today = cur.fetchone()[0]

        # Latest VIX
        cur.execute("SELECT vix, vix_pct_chg FROM vix_60s ORDER BY ts DESC LIMIT 1")
        vix_row = cur.fetchone()

        # Total rows across all tables
        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM candles_60s) +
                (SELECT COUNT(*) FROM option_chain_agg) +
                (SELECT COUNT(*) FROM vix_60s) +
                (SELECT COUNT(*) FROM feature_snapshot) AS total
        """)
        total_rows = cur.fetchone()[0]

        # Trading days collected
        cur.execute("SELECT COUNT(DISTINCT ts::date) FROM feature_snapshot")
        trading_days = cur.fetchone()[0]

        # Latest Nifty LTP + time
        cur.execute("""
            SELECT close, ts FROM candles_60s
            WHERE symbol = 'NIFTY' ORDER BY ts DESC LIMIT 1
        """)
        nifty_row = cur.fetchone()

    return jsonify({
        "candles_today":  candles_today,
        "oc_today":       oc_today,
        "vix_latest":     float(vix_row[0]) if vix_row else None,
        "vix_pct_chg":    float(vix_row[1]) if vix_row else None,
        "total_rows":     total_rows,
        "trading_days":   trading_days,
        "nifty_ltp":      float(nifty_row[0]) if nifty_row else None,
        "last_ts":        nifty_row[1].isoformat() if nifty_row else None,
    })


# ── Candles ───────────────────────────────────────────────────

@dashboard_bp.route("/api/candles")
def api_candles():
    from_d, to_d = parse_date_range(request)
    symbol       = request.args.get("symbol", "")

    sql = """
        SELECT ts, symbol, open, high, low, close,
               tick_count, buy_vol, sell_vol, total_vol,
               traded_volume, cum_volume, avg_trade_size
        FROM candles_60s
        WHERE ts::date BETWEEN %s AND %s
        {}
        ORDER BY ts DESC, symbol
        LIMIT 10000
    """.format("AND symbol = %s" if symbol else "")

    params = (from_d, to_d, symbol) if symbol else (from_d, to_d)
    cols, rows = query_table(sql, params)
    return jsonify({"columns": cols, "rows": rows})


# ── Option Chain ──────────────────────────────────────────────

@dashboard_bp.route("/api/oc")
def api_oc():
    from_d, to_d = parse_date_range(request)
    cols, rows   = query_table("""
        SELECT ts, expiry, nifty_ltp, atm_strike,
               total_ce_oi, total_pe_oi, pcr,
               atm_ce_oi, atm_pe_oi, atm_pcr,
               atm_ce_iv, atm_pe_iv, iv_skew,
               ce_oi_chg, pe_oi_chg,
               atm_ce_vol, atm_pe_vol,
               max_pain_strike, pcr_5snap_chg
        FROM option_chain_agg
        WHERE ts::date BETWEEN %s AND %s
        ORDER BY ts DESC
        LIMIT 10000
    """, (from_d, to_d))
    return jsonify({"columns": cols, "rows": rows})


# ── VIX ───────────────────────────────────────────────────────

@dashboard_bp.route("/api/vix")
def api_vix():
    from_d, to_d = parse_date_range(request)
    cols, rows   = query_table("""
        SELECT ts, vix, vix_open, vix_pct_chg,
               vix_1min_chg, vix_5min_chg,
               vix_tier, vix_zscore_20
        FROM vix_60s
        WHERE ts::date BETWEEN %s AND %s
        ORDER BY ts DESC
        LIMIT 10000
    """, (from_d, to_d))
    return jsonify({"columns": cols, "rows": rows})


# ── Feature Snapshot ──────────────────────────────────────────

@dashboard_bp.route("/api/features")
def api_features():
    from_d, to_d = parse_date_range(request)
    cols, rows   = query_table("""
        SELECT ts,
               nifty_close, nifty_1min_chg, nifty_5min_chg,
               nifty_vs_vwap, nifty_traded_vol, nifty_vol_ratio,
               reliance_close, reliance_1m_chg, reliance_vol_ratio,
               hdfcbank_close, hdfcbank_1m_chg, hdfcbank_vol_ratio,
               bhartiartl_close, bhartiartl_1m_chg,
               icicibank_close, icicibank_1m_chg,
               sbin_close, sbin_1m_chg,
               tcs_close, tcs_1m_chg,
               composite_vol_score,
               hw_divergence, hw_direction,
               tick_velocity, imbalance_ratio,
               vix, vix_tier, vix_zscore_20,
               pcr, atm_pcr, iv_skew, pcr_5snap_chg,
               atm_ce_vol, atm_pe_vol,
               minutes_since_open, session_zone, is_expiry_day,
               scenario_count, big_move_label
        FROM feature_snapshot
        WHERE ts::date BETWEEN %s AND %s
        ORDER BY ts DESC
        LIMIT 10000
    """, (from_d, to_d))
    return jsonify({"columns": cols, "rows": rows})


# ── Scenario Hits ─────────────────────────────────────────────

@dashboard_bp.route("/api/scenarios")
def api_scenarios():
    from_d, to_d = parse_date_range(request)
    cols, rows   = query_table("""
        SELECT ts, scenario_id, scenario_name,
               stocks, combined_weight, direction,
               vix_tier, nifty_ltp, alert_sent
        FROM scenario_hits
        WHERE ts::date BETWEEN %s AND %s
        ORDER BY ts DESC
        LIMIT 5000
    """, (from_d, to_d))
    return jsonify({"columns": cols, "rows": rows})


# ── Collection Log ────────────────────────────────────────────

@dashboard_bp.route("/api/log")
def api_log():
    cols, rows = query_table("""
        SELECT trade_date, start_time, end_time,
               candles_written, oc_snaps_written, vix_rows_written,
               gaps_detected, notes
        FROM collection_log
        ORDER BY trade_date DESC
        LIMIT 90
    """)
    return jsonify({"columns": cols, "rows": rows})


# ── Export ALL tables as ZIP of CSVs ─────────────────────────

@dashboard_bp.route("/api/export_all")
def api_export_all():
    from_d, to_d = parse_date_range(request)

    queries = {
        "candles_60s": ("""
            SELECT * FROM candles_60s
            WHERE ts::date BETWEEN %s AND %s ORDER BY ts, symbol
        """, (from_d, to_d)),

        "option_chain_agg": ("""
            SELECT * FROM option_chain_agg
            WHERE ts::date BETWEEN %s AND %s ORDER BY ts
        """, (from_d, to_d)),

        "vix_60s": ("""
            SELECT * FROM vix_60s
            WHERE ts::date BETWEEN %s AND %s ORDER BY ts
        """, (from_d, to_d)),

        "feature_snapshot": ("""
            SELECT * FROM feature_snapshot
            WHERE ts::date BETWEEN %s AND %s ORDER BY ts
        """, (from_d, to_d)),

        "scenario_hits": ("""
            SELECT * FROM scenario_hits
            WHERE ts::date BETWEEN %s AND %s ORDER BY ts
        """, (from_d, to_d)),

        "collection_log": ("""
            SELECT * FROM collection_log ORDER BY trade_date DESC
        """, ()),
    }

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for table_name, (sql, params) in queries.items():
            try:
                cols, rows = query_table(sql, params)
                csv_bytes  = make_csv_bytes(cols, rows)
                zf.writestr(f"{table_name}.csv", csv_bytes)
            except Exception as e:
                zf.writestr(f"{table_name}_error.txt", str(e))

    zip_buf.seek(0)
    filename = f"nifty_data_{from_d}_to_{to_d}.zip"
    return send_file(
        zip_buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )
