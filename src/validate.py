"""Walk-forward validation of every forecast method (run by the monthly job). Writes data/validation.json,
which drives the trust labels, and back-fills the last 40 days of pre-open forecasts so the
actual-vs-predicted pages have history from day one."""
import json

import numpy as np
import pandas as pd

from . import db, forecast as fc, pool
from .config import DATA, F, ist, now_ist
from .series import Store


def run(con=None, backfill_days=40):
    con = con or db.connect()
    st = Store(con)
    out = {"made_at": now_ist().isoformat()}
    R = fc.preopen_rows(st, "09:10")
    W = fc.preopen_walkforward(R)
    for y in W:
        out[f"pre_{y}"] = fc.hit_stats(W[y]) | {"method": "Pre-open ridge (ES, Hang Seng, KOSPI, previous day)", "period": _period(W[y])}
    Ri = fc.insession_rows(st)
    Wi = fc.insession_walkforward(Ri)
    for y in Wi:
        out[f"in_{y}"] = fc.hit_stats(Wi[y]) | {"method": "In-session ridge", "period": _period(Wi[y])}
    T = fc.daily_targets(st)
    raw = fc.daily_coverage(T, 1.0)
    wid = fc.daily_coverage(T, F["gap_widen"])
    out["next_open"] = dict(hit=None, base=None, n=wid["next_open"]["n"], coverage=wid["next_open"]["coverage"],
                            coverage_unwidened=raw["next_open"]["coverage"], method="Vol-scaled range ×1.25 (re-tested)", period="2009–")
    out["week_close"] = dict(hit=None, base=None, n=raw["week_close"]["n"], coverage=raw["week_close"]["coverage"], method="Vol-scaled range", period="2009–")
    out["next_week_open"] = dict(hit=None, base=None, n=raw["next_week_open"]["n"], coverage=raw["next_week_open"]["coverage"], method="Vol-scaled range", period="2009–")
    G = fc.gap_rows(st)
    Pg = fc.gap_walkforward(G)
    out["gap_es"] = fc.hit_stats(Pg) | {"method": "Gap ridge on US futures at 03:00 IST", "period": _period(Pg)}
    out["pool_backtest"] = pool.backtest(st)
    (DATA / "validation.json").write_text(json.dumps(out, indent=1, default=float))
    if backfill_days:
        _backfill(con, st, R, W, backfill_days)
    return out


def _period(P):
    return f"{P.index[0].strftime('%b %Y')} – {P.index[-1].strftime('%b %Y')}"


def _backfill(con, st, R, W, n):
    """Walk-forward pre-open forecasts for the last n days, stored as backfill=1 and scored."""
    days = W["yc"].index[-n:]
    rows = []
    for D in days:
        pc = float(R.loc[D, "pc"])
        for y, h, tgt in (("y1", "h1", "10:15"), ("y3", "h3", "12:15"), ("yc", "close", "15:30")):
            p = W[y].loc[D]
            rows.append(dict(slot=f"{D.date()} preopen 09:10 backfill", made_at_ist=ist(D, "09:10").isoformat(), horizon=h,
                             target_ts_ist=ist(D, tgt).isoformat(), base_price=pc, point=pc * (1 + p.pred), low=pc * (1 + p.lo),
                             high=pc * (1 + p.hi), p_up=float(p.pup), method="preopen_ridge", trust="green", backfill=1))
    con.executemany("""INSERT OR IGNORE INTO forecasts(slot, made_at_ist, horizon, target_ts_ist, base_price, point, low, high, p_up, method, trust, backfill)
                       VALUES (:slot,:made_at_ist,:horizon,:target_ts_ist,:base_price,:point,:low,:high,:p_up,:method,:trust,:backfill)""", rows)
    con.commit()
    from . import score
    score.score_due(st, now_ist())


if __name__ == "__main__":
    o = run()
    for k, v in o.items():
        print(k, v)
