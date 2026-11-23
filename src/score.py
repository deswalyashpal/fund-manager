"""Actual vs predicted bookkeeping (HANDOVER 5): score every forecast once its target time has passed."""
import numpy as np
import pandas as pd

from . import db
from .config import IST, R

LABELS = {"h1": "Next 1 hour", "h3": "Next 3 hours", "close": "Day's close", "next_open": "Next day's open",
          "week_close": "Week-end close", "next_week_open": "Next week's open"}
OPENS = {"next_open", "next_week_open"}


def actual_for(store, horizon, target, made_at):
    """Realised value for a forecast, or None if not yet known (only final bars count)."""
    d = store.daily("BSESN", final_only=True)
    t = pd.Timestamp(target)
    if horizon in OPENS:
        rows = d[d.index >= pd.Timestamp(t.date())]
        if not len(rows):
            return None
        return float(rows.open.iloc[0]), str(rows.index[0].date())
    td = pd.Timestamp(t.date())
    if horizon in ("close", "week_close") or (t.hour == 15 and t.minute == 30):
        if td in d.index:
            return float(d.loc[td, "close"]), str(td.date())
        if horizon == "week_close" and (d.index > td).any():  # last day of the week was a holiday
            prior = d[d.index <= td]
            return float(prior.close.iloc[-1]), str(prior.index[-1].date())
        return None
    h = store.hourly("BSESN")
    start = t - pd.Timedelta(hours=1)
    m = h[(h.index == start) & (h["provisional"] == 0)]
    if len(m):
        return float(m.close.iloc[0]), str(start)
    return None


def score_due(store, now):
    con = store.con
    due = pd.read_sql("""SELECT f.* FROM forecasts f LEFT JOIN outcomes o ON o.forecast_id=f.id
                         WHERE o.forecast_id IS NULL AND f.target_ts_ist <= ?""", con, params=(now.isoformat(),))
    n = 0
    for _, f in due.iterrows():
        a = actual_for(store, f.horizon, f.target_ts_ist, f.made_at_ist)
        if a is None:
            continue
        actual = a[0]
        miss = actual - f.point
        dir_ok = int(np.sign(f.point - f.base_price) == np.sign(actual - f.base_price))
        con.execute("INSERT OR REPLACE INTO outcomes VALUES (?,?,?,?,?,?,?)",
                    (int(f.id), actual, miss, miss / f.point * 100, dir_ok, int(f.low <= actual <= f.high), now.isoformat()))
        n += 1
    con.commit()
    return n


def scored(con, days=None):
    q = """SELECT f.*, o.actual, o.miss_pts, o.miss_pct, o.dir_ok, o.in_range, o.scored_at_ist
           FROM forecasts f JOIN outcomes o ON o.forecast_id=f.id"""
    df = pd.read_sql(q + " ORDER BY f.target_ts_ist", con)
    return df


def rolling(con, n=20):
    """Per horizon and method: last-n hit rate, mean absolute miss, range coverage."""
    df = scored(con)
    out = []
    for (h, m), g in df.groupby(["horizon", "method"]):
        # one forecast per target (the latest made before the target) so a horizon is not counted many times
        g = g.sort_values("made_at_ist").groupby("target_ts_ist").tail(1).tail(n)
        out.append(dict(horizon=h, label=LABELS.get(h, h), method=m, n=len(g), hit=round(g.dir_ok.mean() * 100),
                        mae=round(g.miss_pts.abs().mean()), mae_pct=round(g.miss_pct.abs().mean(), 2),
                        coverage=round(g.in_range.mean() * 100), above=int((g.miss_pts > 0).sum())))
    return out


def regime_warning(con):
    df = scored(con)
    g = df[(df.horizon == "close") & (df.method.str.startswith("preopen"))]
    g = g.sort_values("made_at_ist").groupby("target_ts_ist").tail(1).tail(20)
    if len(g) < 10:
        return False, None
    hit = g.dir_ok.mean()
    return bool(hit < R["regime_warning_hit_rate"]), dict(hit=int(g.dir_ok.sum()), n=len(g), above=int((g.miss_pts > 0).sum()),
                                                           mae=round(g.miss_pts.abs().mean()))
