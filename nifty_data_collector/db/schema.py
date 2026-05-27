"""
db/schema.py
CHANGED: Added volume columns to candles_60s, option_chain_agg, feature_snapshot
Run: python -m db.schema
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import DATABASE_URL
import psycopg2

DDL = """

CREATE TABLE IF NOT EXISTS candles_60s (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    open            NUMERIC(12,2),
    high            NUMERIC(12,2),
    low             NUMERIC(12,2),
    close           NUMERIC(12,2),
    tick_count      INTEGER,
    buy_vol         BIGINT,
    sell_vol        BIGINT,
    total_vol       BIGINT,
    traded_volume   BIGINT,        -- ADDED: actual shares traded this 60s candle
    cum_volume      BIGINT,        -- ADDED: cumulative day volume at candle close
    avg_trade_size  NUMERIC(12,2), -- ADDED: traded_volume / tick_count
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_candles_ts_sym ON candles_60s (ts, symbol);
CREATE INDEX IF NOT EXISTS idx_candles_ts ON candles_60s (ts DESC);


CREATE TABLE IF NOT EXISTS option_chain_agg (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL,
    expiry          DATE NOT NULL,
    nifty_ltp       NUMERIC(10,2),
    atm_strike      INTEGER,
    total_ce_oi     BIGINT,
    total_pe_oi     BIGINT,
    pcr             NUMERIC(8,4),
    atm_ce_oi       BIGINT,
    atm_pe_oi       BIGINT,
    atm_pcr         NUMERIC(8,4),
    atm_ce_iv       NUMERIC(8,4),
    atm_pe_iv       NUMERIC(8,4),
    iv_skew         NUMERIC(8,4),
    ce_oi_chg       BIGINT,
    pe_oi_chg       BIGINT,
    atm_ce_vol      BIGINT,        -- ADDED: CE contracts traded at ATM this snapshot
    atm_pe_vol      BIGINT,        -- ADDED: PE contracts traded at ATM this snapshot
    max_pain_strike INTEGER,
    pcr_5snap_chg   NUMERIC(8,4),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_oc_ts_expiry ON option_chain_agg (ts, expiry);
CREATE INDEX IF NOT EXISTS idx_oc_ts ON option_chain_agg (ts DESC);


CREATE TABLE IF NOT EXISTS vix_60s (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL UNIQUE,
    vix             NUMERIC(8,4),
    vix_open        NUMERIC(8,4),
    vix_pct_chg     NUMERIC(8,4),
    vix_1min_chg    NUMERIC(8,4),
    vix_5min_chg    NUMERIC(8,4),
    vix_tier        SMALLINT,
    vix_zscore_20   NUMERIC(8,4),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS feature_snapshot (
    id                   BIGSERIAL PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL UNIQUE,
    -- Nifty price
    nifty_open           NUMERIC(10,2),
    nifty_close          NUMERIC(10,2),
    nifty_1min_chg       NUMERIC(8,4),
    nifty_5min_chg       NUMERIC(8,4),
    nifty_vwap           NUMERIC(10,2),
    nifty_vs_vwap        NUMERIC(8,4),
    -- Nifty volume                              ← ADDED block
    nifty_traded_vol     BIGINT,                -- actual traded volume this 60s candle
    nifty_cum_volume     BIGINT,                -- cumulative day volume
    nifty_vol_ratio      NUMERIC(8,4),          -- this candle vol / 20-candle avg
    -- Stocks
    reliance_close       NUMERIC(10,2),
    reliance_1m_chg      NUMERIC(8,4),
    reliance_vol_ratio   NUMERIC(8,4),          -- ADDED: volume ratio vs 20-candle avg
    hdfcbank_close       NUMERIC(10,2),
    hdfcbank_1m_chg      NUMERIC(8,4),
    hdfcbank_vol_ratio   NUMERIC(8,4),          -- ADDED
    bhartiartl_close     NUMERIC(10,2),
    bhartiartl_1m_chg    NUMERIC(8,4),
    bhartiartl_vol_ratio NUMERIC(8,4),          -- ADDED
    icicibank_close      NUMERIC(10,2),
    icicibank_1m_chg     NUMERIC(8,4),
    icicibank_vol_ratio  NUMERIC(8,4),          -- ADDED
    sbin_close           NUMERIC(10,2),
    sbin_1m_chg          NUMERIC(8,4),
    sbin_vol_ratio       NUMERIC(8,4),          -- ADDED
    tcs_close            NUMERIC(10,2),
    tcs_1m_chg           NUMERIC(8,4),
    tcs_vol_ratio        NUMERIC(8,4),          -- ADDED
    -- Composite volume signal                  ← ADDED
    composite_vol_score  NUMERIC(8,4),          -- weighted avg vol ratio across 6 stocks
    -- Heavyweight divergence
    hw_divergence        NUMERIC(8,4),
    hw_direction         SMALLINT,
    -- Microstructure
    tick_velocity        NUMERIC(8,4),
    imbalance_ratio      NUMERIC(8,4),
    -- VIX
    vix                  NUMERIC(8,4),
    vix_pct_chg          NUMERIC(8,4),
    vix_tier             SMALLINT,
    vix_zscore_20        NUMERIC(8,4),
    -- Option chain
    pcr                  NUMERIC(8,4),
    atm_pcr              NUMERIC(8,4),
    iv_skew              NUMERIC(8,4),
    pcr_5snap_chg        NUMERIC(8,4),
    ce_oi_chg            BIGINT,
    pe_oi_chg            BIGINT,
    atm_ce_vol           BIGINT,                -- ADDED
    atm_pe_vol           BIGINT,                -- ADDED
    max_pain_strike      INTEGER,
    -- Session context
    minutes_since_open   SMALLINT,
    minutes_to_close     SMALLINT,
    day_of_week          SMALLINT,
    is_expiry_day        BOOLEAN DEFAULT FALSE,
    session_zone         SMALLINT,
    -- Scenarios
    active_scenarios     INTEGER[],
    scenario_count       SMALLINT,
    -- Labels (NULL during collection, filled at training time)
    nifty_15min_fwd_chg  NUMERIC(8,4),
    big_move_label       SMALLINT,
    move_direction       SMALLINT,
    created_at           TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_feat_ts ON feature_snapshot (ts DESC);
CREATE INDEX IF NOT EXISTS idx_feat_label ON feature_snapshot (big_move_label)
    WHERE big_move_label IS NOT NULL;


CREATE TABLE IF NOT EXISTS scenario_hits (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL,
    scenario_id     INTEGER NOT NULL,
    scenario_name   VARCHAR(100),
    stocks          TEXT[],
    combined_weight NUMERIC(6,2),
    direction       VARCHAR(10),
    vix_tier        SMALLINT,
    nifty_ltp       NUMERIC(10,2),
    alert_sent      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_scen_ts ON scenario_hits (ts DESC);


CREATE TABLE IF NOT EXISTS token_store (
    id           SERIAL PRIMARY KEY,
    access_token TEXT NOT NULL,
    saved_date   DATE NOT NULL UNIQUE,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS collection_log (
    id               BIGSERIAL PRIMARY KEY,
    trade_date       DATE NOT NULL UNIQUE,
    start_time       TIMESTAMPTZ,
    end_time         TIMESTAMPTZ,
    candles_written  INTEGER DEFAULT 0,
    oc_snaps_written INTEGER DEFAULT 0,
    vix_rows_written INTEGER DEFAULT 0,
    gaps_detected    INTEGER DEFAULT 0,
    gap_timestamps   TIMESTAMPTZ[],
    notes            TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

"""

# ── Migration for existing deployments ────────────────────────
# Run this if tables already exist — safely adds new columns only
MIGRATION = """
ALTER TABLE candles_60s
    ADD COLUMN IF NOT EXISTS traded_volume   BIGINT,
    ADD COLUMN IF NOT EXISTS cum_volume      BIGINT,
    ADD COLUMN IF NOT EXISTS avg_trade_size  NUMERIC(12,2);

ALTER TABLE option_chain_agg
    ADD COLUMN IF NOT EXISTS atm_ce_vol  BIGINT,
    ADD COLUMN IF NOT EXISTS atm_pe_vol  BIGINT;

ALTER TABLE feature_snapshot
    ADD COLUMN IF NOT EXISTS nifty_traded_vol     BIGINT,
    ADD COLUMN IF NOT EXISTS nifty_cum_volume     BIGINT,
    ADD COLUMN IF NOT EXISTS nifty_vol_ratio      NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS reliance_vol_ratio   NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS hdfcbank_vol_ratio   NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS bhartiartl_vol_ratio NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS icicibank_vol_ratio  NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS sbin_vol_ratio       NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS tcs_vol_ratio        NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS composite_vol_score  NUMERIC(8,4),
    ADD COLUMN IF NOT EXISTS atm_ce_vol           BIGINT,
    ADD COLUMN IF NOT EXISTS atm_pe_vol           BIGINT;
"""


def run():
    conn = psycopg2.connect(dsn=DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(DDL)
    cur.execute(MIGRATION)
    conn.close()
    print("✅ Schema ready (tables created + volume columns migrated)")


if __name__ == "__main__":
    run()
