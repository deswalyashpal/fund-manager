"""Loading price series from the database, INR conversion and IST clock alignment (HANDOVER 2.4).

Alignment rule (exact, replaces the research's "+1 calendar day" shortcut):
a daily bar of market M dated D can be used at IST time T only if close_ts(M, D) <= T.
"""
import numpy as np
import pandas as pd

from . import db
from .config import ALL, IST, MARKETS

CCY_FX = {"USD": None, "JPY": "USDJPY", "CNY": "USDCNY", "EUR": "USDEUR", "HKD": "USDHKD", "KRW": "USDKRW", "INR": "INR"}


class Store:
    """Caches series loaded from SQLite for one job run."""

    def __init__(self, con=None):
        self.con = con or db.connect()
        self._d, self._h = {}, {}

    def daily(self, key, final_only=False):
        k = (key, final_only)
        if k not in self._d:
            df = db.load_prices(self.con, key, "1d", final_only)
            self._d[k] = df
        return self._d[k]

    def ohlc(self, key, final_only=False):
        """Daily bars with capitalised columns for ta_lib."""
        d = self.daily(key, final_only)
        return d.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})

    def hourly(self, key):
        if key not in self._h:
            df = db.load_prices(self.con, key, "1h")
            if len(df):
                df.index = df.index.tz_convert(IST)
            self._h[key] = df
        return self._h[key]

    def close(self, key, final_only=False):
        return self.daily(key, final_only)["close"]

    def inr_close(self, key, final_only=True):
        """Index level converted to INR with forward-filled FX (FX known on or before the index date)."""
        c = self.close(key, final_only)
        ccy = MARKETS[key]["ccy"]
        if ccy == "INR":
            return c
        usdinr = self.close("USDINR").sort_index()
        if ccy == "USD":
            rate = usdinr
        else:
            rate = usdinr / self.close(CCY_FX[ccy]).sort_index()
        rate = rate.replace([np.inf, -np.inf], np.nan).dropna()
        return (c * rate.reindex(c.index.union(rate.index)).ffill().reindex(c.index)).dropna()


def close_times(key, index):
    """Vectorised UTC time at which each daily bar (dated by `index`) became final."""
    m = ALL[key]
    hh, mm = map(int, m["close_time"].split(":"))
    d = pd.DatetimeIndex(index).normalize()
    wd = d.weekday
    d = d + pd.to_timedelta(np.where(wd == 5, 2, np.where(wd == 6, 1, 0)), unit="D")
    local = (d + pd.Timedelta(hours=hh, minutes=mm)).tz_localize(m["close_tz"], nonexistent="shift_forward", ambiguous=False)
    return local.tz_convert("UTC")


def available_asof(store, key, t_ist, final_only=False):
    """Daily bars of `key` that had closed by IST time t_ist (no look-ahead)."""
    d = store.daily(key, final_only)
    if d.empty:
        return d
    return d[close_times(key, d.index) <= pd.Timestamp(t_ist).tz_convert("UTC")]


def align_daily(store, key, india_dates, decision_hhmm="09:10"):
    """For each Indian date, the latest bar of `key` that had closed by that date's decision time.
    Returns the source bar date (checked by the no-look-ahead test) and its close."""
    d = store.daily(key)
    idx = pd.DatetimeIndex(india_dates)
    hh, mm = map(int, decision_hhmm.split(":"))
    dec = (idx.normalize() + pd.Timedelta(hours=hh, minutes=mm)).tz_localize(IST).tz_convert("UTC")
    ct = close_times(key, d.index)
    pos = ct.searchsorted(dec, side="right") - 1
    src = pd.DatetimeIndex([d.index[i] if i >= 0 else pd.NaT for i in pos])
    cl = np.array([d["close"].iloc[i] if i >= 0 else np.nan for i in pos])
    return pd.DataFrame({"src_date": src, "close": cl}, index=idx)


def last_close_before(h, ts):
    """Close of the last hourly bar that had ENDED by ts (bar start + 1h <= ts)."""
    s = h[h.index + pd.Timedelta(hours=1) <= ts]
    return (s["close"].iloc[-1], s.index[-1]) if len(s) else (np.nan, None)


def ewma_sigma(close, alpha):
    return close.pct_change().ewm(alpha=alpha).std()
