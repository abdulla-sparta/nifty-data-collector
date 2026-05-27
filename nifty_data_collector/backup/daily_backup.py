"""
backup/daily_backup.py — CSV dump to /data volume after market close
"""

import os, csv, logging, psycopg2
from datetime import date, datetime
import pytz, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DATABASE_URL

logger   = logging.getLogger(__name__)
IST      = pytz.timezone("Asia/Kolkata")
DATA_DIR = os.environ.get("BACKUP_PATH", "/data/csv")   # Railway volume mount

TABLES = [
    ("candles_60s",      "SELECT * FROM candles_60s WHERE ts::date=%s ORDER BY ts,symbol"),
    ("option_chain_agg", "SELECT * FROM option_chain_agg WHERE ts::date=%s ORDER BY ts"),
    ("vix_60s",          "SELECT * FROM vix_60s WHERE ts::date=%s ORDER BY ts"),
    ("feature_snapshot", "SELECT * FROM feature_snapshot WHERE ts::date=%s ORDER BY ts"),
    ("scenario_hits",    "SELECT * FROM scenario_hits WHERE ts::date=%s ORDER BY ts"),
]


def run_backup(trade_date: date = None):
    trade_date = trade_date or datetime.now(IST).date()
    date_str   = trade_date.strftime("%Y-%m-%d")
    out_dir    = os.path.join(DATA_DIR, date_str)
    os.makedirs(out_dir, exist_ok=True)

    conn  = psycopg2.connect(dsn=DATABASE_URL)
    total = 0

    for table, query in TABLES:
        try:
            cur  = conn.cursor()
            cur.execute(query, (trade_date,))
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            fp   = os.path.join(out_dir, f"{table}.csv")
            with open(fp, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(cols)
                writer.writerows(rows)
            logger.info(f"✅ {table}: {len(rows)} rows → {fp}")
            total += len(rows)
        except Exception as e:
            logger.error(f"❌ {table}: {e}")

    conn.close()
    logger.info(f"📦 Backup done {date_str} | {total} total rows")
    return out_dir, total
