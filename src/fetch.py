"""Live data from Yahoo Finance (yfinance) into SQLite, with quality flags. Fails soft: on any error the
last good values stay in the database and the page shows a 'stale since' banner."""
import logging
import warnings

import numpy as np
import pandas as pd

from . import db
from .config import ALL, CFG, UTC
from .quality import clean_bars
from .series import close_times

warnings.filterwarnings("ignore")
log = logging.getLogger("fetch")
NONPOS_OK = {"US13W", "US10Y", "WTI", "BRENT"}
SECOND_SOURCE_NOTE = ("No second source reachable: BSE/NSE block automated requests and Stooq requires a browser check. "
                      "Cross-check uses Yahoo hourly vs daily close (internal).")


def _download(tickers, period, interval):
    import yfinance as yf
    df = yf.download(tickers, period=period, interval=interval, auto_adjust=False, group_by="ticker",
                     progress=False, threads=True)
    return df


def _norm(raw, ticker):
    try:
        d = raw[ticker].copy() if isinstance(raw.columns, pd.MultiIndex) else raw.copy()
    except KeyError:
        return pd.DataFrame()
    d.columns = [c.lower() for c in d.columns]
    d = d[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
    return d.astype(float)


def update(con, now_utc=None, daily_period="10d", hourly_period="5d", keys=None, hourly=True):
    now_utc = pd.Timestamp(now_utc or pd.Timestamp.now(tz=UTC))
    fetched = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    keys = keys or list(ALL)
    report = {}
    tick = {k: ALL[k]["yahoo"] for k in keys}
    try:
        raw = _download(list(tick.values()), daily_period, "1d")
    except Exception as e:  # network / Yahoo outage
        raw = None
        log.warning("daily download failed: %s", e)
    for k, t in tick.items():
        try:
            d = _norm(raw, t) if raw is not None else pd.DataFrame()
            if d.empty:
                report[k] = dict(ok=False, note="no daily data returned; keeping last good values")
                continue
            d.index = pd.DatetimeIndex(d.index).tz_localize(None).normalize()
            notes = []
            if k not in NONPOS_OK:
                d, notes = clean_bars(d)
            prov = pd.Series((close_times(k, d.index) > now_utc).astype(int), index=d.index)
            db.upsert_prices(con, k, "1d", d, "yahoo", fetched, prov)
            report[k] = dict(ok=True, last=str(d.index[-1].date()), live=bool(prov.iloc[-1]), note="; ".join(notes))
        except Exception as e:
            report[k] = dict(ok=False, note=f"daily update error: {e}")
    if hourly:
        hk = [k for k in CFG["hourly"] if k in tick]
        try:
            rawh = _download([tick[k] for k in hk], hourly_period, "60m")
        except Exception as e:
            rawh = None
            log.warning("hourly download failed: %s", e)
        for k in hk:
            try:
                h = _norm(rawh, tick[k]) if rawh is not None else pd.DataFrame()
                if h.empty:
                    report.setdefault(k, {})["hourly"] = "no hourly data returned"
                    continue
                h.index = pd.DatetimeIndex(h.index).tz_convert(UTC)
                if k not in NONPOS_OK:
                    h, _ = clean_bars(h)
                prov = pd.Series(((h.index + pd.Timedelta(hours=1)) > now_utc).astype(int), index=h.index)
                db.upsert_prices(con, k, "1h", h, "yahoo", fetched, prov)
                report.setdefault(k, {})["hourly_last"] = str(h.index[-1])
            except Exception as e:
                report.setdefault(k, {})["hourly"] = f"hourly update error: {e}"
    return report


def quality(con, run_id, now_utc=None, report=None):
    """Stale and mismatch flags for every series. Returns rows (also stored in the quality table)."""
    now_utc = pd.Timestamp(now_utc or pd.Timestamp.now(tz=UTC))
    rows = []
    for k in ALL:
        r = con.execute("SELECT max(ts_utc), max(fetched_at) FROM prices WHERE ticker=? AND interval='1d' AND provisional=0", (k,)).fetchone()
        last = r[0]
        if not last:
            continue
        last_close = close_times(k, pd.DatetimeIndex([last]))[0]
        stale = int((now_utc - last_close) > pd.Timedelta(days=4))
        note = (report or {}).get(k, {}).get("note", "") if report else ""
        mismatch = None
        if k == "BSESN":
            mismatch, extra = _sensex_internal_check(con, last)
            note = "; ".join(x for x in (note, extra, SECOND_SOURCE_NOTE) if x)
        if report and not report.get(k, {}).get("ok", True):
            note = "; ".join(x for x in (note, "fetch failed this run") if x)
        rows.append(dict(run_id=run_id, ticker=k, last_ts=last, stale=stale, mismatch_pct=mismatch, note=note))
    con.executemany("INSERT OR REPLACE INTO quality VALUES (:run_id,:ticker,:last_ts,:stale,:mismatch_pct,:note)", rows)
    con.commit()
    return rows


def _sensex_internal_check(con, day):
    d = con.execute("SELECT close FROM prices WHERE ticker='BSESN' AND interval='1d' AND ts_utc=?", (day,)).fetchone()
    h = con.execute("""SELECT close FROM prices WHERE ticker='BSESN' AND interval='1h' AND ts_utc LIKE ? ORDER BY ts_utc DESC LIMIT 1""",
                    (day + "%",)).fetchone()
    if not d or not h:
        return None, "hourly bars missing for the internal close check"
    mm = abs(h[0] / d[0] - 1) * 100
    return round(mm, 3), ("close mismatch > 0.05% between Yahoo daily and hourly" if mm > 0.05 else "")
