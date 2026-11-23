"""Paths, config loading and time helpers."""
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SEED = DATA / "seed"
SITE = ROOT / "site"
DB_PATH = DATA / "market.db"
IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def load():
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


CFG = load()
MARKETS = {m["key"]: m for m in CFG["markets"]}
SERIES = {s["key"]: s for s in CFG["series"]}
ALL = {**MARKETS, **SERIES}
POOL = [k for k, m in MARKETS.items() if m["pool"]]
FOREIGN = [k for k in MARKETS if k not in ("BSESN", "NSEI")]
R = CFG["rules"]
F = CFG["forecast"]


def now_ist():
    return datetime.now(IST)


def close_ts_utc(key, day):
    """UTC timestamp at which the daily bar of `key` for local date `day` becomes final."""
    m = ALL[key]
    hh, mm = map(int, m["close_time"].split(":"))
    d = pd.Timestamp(day).date()
    while d.weekday() >= 5:  # futures/FX rows dated Sunday are the live Sunday-night session -> final Monday
        d += timedelta(days=1)
    return pd.Timestamp(datetime.combine(d, time(hh, mm), ZoneInfo(m["close_tz"]))).tz_convert("UTC")


def holidays():
    return {pd.Timestamp(h).date() for h in (CFG.get("holidays") or [])}


def is_trading_day(d):
    d = pd.Timestamp(d).date()
    return d.weekday() < 5 and d not in holidays()


def next_trading_day(d, n=1):
    d = pd.Timestamp(d).date()
    while n:
        d += timedelta(days=1)
        if is_trading_day(d):
            n -= 1
    return d


def trading_days_between(a, b):
    """Trading days strictly after a, up to and including b."""
    a, b = pd.Timestamp(a).date(), pd.Timestamp(b).date()
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += is_trading_day(d)
    return n


def ist(d, hhmm):
    hh, mm = map(int, hhmm.split(":"))
    return pd.Timestamp(datetime.combine(pd.Timestamp(d).date(), time(hh, mm), IST))
