"""SQLite storage (HANDOVER 8.4). Append-only history; re-runs of the same slot are idempotent."""
import json
import sqlite3

import pandas as pd

from .config import DB_PATH, UTC

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices(
  ticker TEXT, interval TEXT, ts_utc TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL,
  provisional INTEGER DEFAULT 0, source TEXT, fetched_at TEXT,
  PRIMARY KEY(ticker, interval, ts_utc));
CREATE TABLE IF NOT EXISTS snapshots(
  id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT UNIQUE, made_at_ist TEXT, job TEXT, payload_json TEXT);
CREATE TABLE IF NOT EXISTS forecasts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT, made_at_ist TEXT, horizon TEXT, target_ts_ist TEXT,
  base_price REAL, point REAL, low REAL, high REAL, p_up REAL, method TEXT, trust TEXT, backfill INTEGER DEFAULT 0,
  UNIQUE(slot, horizon, method));
CREATE TABLE IF NOT EXISTS outcomes(
  forecast_id INTEGER PRIMARY KEY, actual REAL, miss_pts REAL, miss_pct REAL, dir_ok INTEGER, in_range INTEGER,
  scored_at_ist TEXT);
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, slot TEXT, made_at_ist TEXT, area TEXT, action TEXT, detail TEXT,
  reason TEXT, prev_action TEXT, UNIQUE(slot, area));
CREATE TABLE IF NOT EXISTS pool_weights(
  date TEXT, market TEXT, base_w REAL, overlay_w REAL, below_100d INTEGER, vs_ma_pct REAL, high_risk INTEGER,
  PRIMARY KEY(date, market));
CREATE TABLE IF NOT EXISTS quality(
  run_id TEXT, ticker TEXT, last_ts TEXT, stale INTEGER, mismatch_pct REAL, note TEXT,
  PRIMARY KEY(run_id, ticker));
CREATE TABLE IF NOT EXISTS ibja(
  date TEXT, session TEXT, gold999 REAL, silver999 REAL, fetched_at TEXT, PRIMARY KEY(date, session));
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT, updated TEXT);
CREATE INDEX IF NOT EXISTS ix_fc_target ON forecasts(target_ts_ist);
CREATE INDEX IF NOT EXISTS ix_snap_time ON snapshots(made_at_ist);
"""


def connect(path=None):
    con = sqlite3.connect(str(path or DB_PATH), timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def upsert_prices(con, ticker, interval, df, source, fetched_at, provisional=None):
    """df: index = date (1d) or UTC timestamps (1h); columns open, high, low, close, volume."""
    if df is None or df.empty:
        return 0
    rows = []
    for ts, r in df.iterrows():
        key = ts.strftime("%Y-%m-%d") if interval == "1d" else pd.Timestamp(ts).tz_convert(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        prov = int(provisional.get(ts, 0)) if provisional is not None else 0
        rows.append((ticker, interval, key, *(None if pd.isna(r.get(c)) else float(r.get(c)) for c in
                    ("open", "high", "low", "close", "volume")), prov, source, fetched_at))
    con.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return len(rows)


def load_prices(con, ticker, interval="1d", final_only=False):
    q = "SELECT ts_utc, open, high, low, close, volume, provisional FROM prices WHERE ticker=? AND interval=?"
    if final_only:
        q += " AND provisional=0"
    df = pd.read_sql(q + " ORDER BY ts_utc", con, params=(ticker, interval))
    if df.empty:
        return df
    if interval == "1d":
        df.index = pd.to_datetime(df.ts_utc)
    else:
        df.index = pd.to_datetime(df.ts_utc, utc=True)
    return df.drop(columns="ts_utc")


def save_snapshot(con, slot, made_at, job, payload):
    con.execute("INSERT OR REPLACE INTO snapshots(slot, made_at_ist, job, payload_json) VALUES (?,?,?,?)",
                (slot, made_at, job, json.dumps(payload, default=_json_default)))
    con.commit()


def _json_default(o):
    if hasattr(o, "item"):
        return o.item()
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    return str(o)


def save_forecasts(con, rows):
    con.executemany(
        """INSERT INTO forecasts(slot, made_at_ist, horizon, target_ts_ist, base_price, point, low, high, p_up, method, trust, backfill)
           VALUES (:slot,:made_at_ist,:horizon,:target_ts_ist,:base_price,:point,:low,:high,:p_up,:method,:trust,:backfill)
           ON CONFLICT(slot, horizon, method) DO UPDATE SET made_at_ist=excluded.made_at_ist, target_ts_ist=excluded.target_ts_ist,
             base_price=excluded.base_price, point=excluded.point, low=excluded.low, high=excluded.high, p_up=excluded.p_up,
             trust=excluded.trust""", [{"backfill": 0, **r} for r in rows])
    con.commit()


def save_decisions(con, rows):
    con.executemany("""INSERT OR REPLACE INTO decisions(slot, made_at_ist, area, action, detail, reason, prev_action)
                       VALUES (:slot,:made_at_ist,:area,:action,:detail,:reason,:prev_action)""", rows)
    con.commit()


def last_decision(con, area, before_slot):
    r = con.execute("SELECT action, detail, made_at_ist FROM decisions WHERE area=? AND slot<>? ORDER BY made_at_ist DESC LIMIT 1",
                    (area, before_slot)).fetchone()
    return r
