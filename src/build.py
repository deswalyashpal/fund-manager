"""Assemble the whole dashboard state for one job run: forecasts, decisions, boards -> snapshot payload."""
import json
from datetime import timedelta

import numpy as np
import pandas as pd

from . import db, decide, forecast as fc, metals, pool, score, signals, world
from .config import CFG, DATA, IST, R, ist, is_trading_day, next_trading_day
from .series import Store, ewma_sigma
from .ta_lib import indicators, pivots, score as tscore

VALIDATION = DATA / "validation.json"
METHOD_STATS = {  # forecast (horizon, method) -> key in validation.json
    ("h1", "preopen_ridge"): "pre_y1", ("h3", "preopen_ridge"): "pre_y3", ("close", "preopen_ridge"): "pre_yc",
    ("h1", "insession_ridge"): "in_y1", ("h3", "insession_ridge"): "in_y3", ("close", "insession_ridge"): "in_ycl",
    ("next_open", "volscaled"): "next_open", ("week_close", "volscaled"): "week_close",
    ("next_week_open", "volscaled"): "next_week_open", ("next_open", "gap_es"): "gap_es",
}


def load_validation():
    try:
        return json.loads(VALIDATION.read_text())
    except Exception:
        return {}


def last_weekly_check(now):
    """Friday whose weekly check (Saturday 10:00 IST) has already happened."""
    d = now.date()
    while True:
        if d.weekday() == 4 and ist(d + timedelta(days=1), "10:00") <= now:
            return d
        d -= timedelta(days=1)


def _trust(val, horizon, method):
    st = val.get(METHOD_STATS.get((horizon, method), ""), None)
    lvl = decide.trust_for(st)
    name, text = decide.trust_text(lvl, st)
    return lvl, name, text, st


def make_forecasts(store, job, now, slot, val):
    """Forecast rows for this job (saved to the forecasts table)."""
    today = now.date()
    dfinal = store.daily("BSESN", final_only=True)
    last_day = dfinal.index[-1].date()
    C_last = float(dfinal.close.iloc[-1])
    sigma = float(ewma_sigma(dfinal.close, CFG["forecast"]["ewma_alpha"]).iloc[-1])
    T = fc.daily_targets(store)
    zq = fc.z_quantiles(T, today.year)
    rows, extra = [], {}
    made = now.isoformat()

    def add(r, horizon, method, target, p_up=None):
        lvl = _trust(val, horizon, method)[0]
        rows.append(dict(slot=slot, made_at_ist=made, horizon=horizon, target_ts_ist=pd.Timestamp(target).isoformat(),
                         base_price=r["base_price"], point=float(r["point"]), low=float(r["low"]), high=float(r["high"]),
                         p_up=p_up, method=method, trust=lvl))

    drift = (store._drift if hasattr(store, "_drift") else 0.0) / 100 / 5
    if job == "preopen" and is_trading_day(today) and last_day < today:
        hhmm = now.strftime("%H:%M")
        dec = "08:45" if hhmm < "09:00" else "09:10"
        Rr = fc.preopen_rows(store, dec)
        # Clock the inputs at the scheduled decision time, never later: a late (cloud-delayed or manual)
        # run must not see futures/Asia prices from after 08:45/09:10 IST.
        t_dec = min(now, ist(today, dec))
        pf, feats = fc.preopen_forecast(store, today, last_day, t_dec, dec, Rr)
        extra["preopen_features"] = feats
        extra["preopen"] = pf
        if pf:
            for y, h, tgt in (("y1", "h1", "10:15"), ("y3", "h3", "12:15"), ("yc", "close", "15:30")):
                f = pf[y]
                add(dict(base_price=C_last, point=C_last * (1 + f["pred"]), low=C_last * (1 + f["lo"]), high=C_last * (1 + f["hi"])),
                    h, "preopen_ridge", ist(today, tgt), p_up=f["pup"])
            close_fc = (pf["yc"]["pred"], pf["yc"]["lo"], pf["yc"]["hi"])
            for r in fc.daily_horizons(today, C_last, close_fc, sigma, zq, drift, after_close=False):
                add(r, r["horizon"], "volscaled", r["target"])
    elif job in ("hourly", "decision") and is_trading_day(today) and last_day < today:
        ins = fc.insession_forecast(store, now)
        extra["insession"] = ins
        if ins and job == "hourly":
            P = ins["price"]
            for y, h in (("y1", "h1"), ("y3", "h3"), ("ycl", "close")):
                f = ins[y]
                add(dict(base_price=ins["prev_close"] if h == "close" else P, point=P * (1 + f["pred"]), low=P * (1 + f["lo"]), high=P * (1 + f["hi"])),
                    h, "insession_ridge", f["target"])
            pc = P * (1 + ins["ycl"]["pred"]) / C_last - 1
            lc = P * (1 + ins["ycl"]["lo"]) / C_last - 1
            hc = P * (1 + ins["ycl"]["hi"]) / C_last - 1
            for r in fc.daily_horizons(today, C_last, (pc, lc, hc), sigma, zq, drift, after_close=False):
                add(r, r["horizon"], "volscaled", r["target"])
    elif job == "close" and last_day == today:
        for r in fc.daily_horizons(today, C_last, (0, 0, 0), sigma, zq, drift, after_close=True):
            add(r, r["horizon"], "volscaled", r["target"])
    elif job == "us_close":
        india_day = today if is_trading_day(today) else next_trading_day(today)
        if last_day < india_day:
            g = fc.gap_forecast(store, india_day, C_last, last_day, now)
            extra["gap_es"] = dict(es_ovn=g["es_ovn"], es_ts=g["es_ts"])
            add(g, "next_open", "gap_es", g["target"])
    extra["sigma"] = sigma
    extra["zq"] = zq
    return rows, extra


def current_forecasts(con, now, val):
    """Latest forecast per horizon whose target has not passed yet (plus today's already-scored ones)."""
    df = pd.read_sql("""SELECT f.*, o.actual, o.in_range, o.dir_ok FROM forecasts f LEFT JOIN outcomes o ON o.forecast_id=f.id
                        WHERE f.backfill=0 ORDER BY f.made_at_ist""", con)
    if df.empty:
        return []
    df["t"] = pd.to_datetime(df.target_ts_ist, utc=True).dt.tz_convert(IST)
    live = df[df.t >= now - pd.Timedelta(hours=1)]
    out = []
    for h in ("h1", "h3", "close", "next_open", "week_close", "next_week_open"):
        g = live[live.horizon == h]
        if g.empty:
            continue
        r = g.iloc[-1]
        lvl, name, text, st = _trust(val, h, r.method)
        out.append(dict(horizon=h, label=score.LABELS[h], made=r.made_at_ist, target=r.target_ts_ist, base=r.base_price,
                        point=r.point, low=r.low, high=r.high, p_up=None if pd.isna(r.p_up) else float(r.p_up),
                        chg=(r.point / r.base_price - 1) * 100, method=r.method, trust=lvl, trust_name=name, trust_text=text,
                        actual=None if pd.isna(r.actual) else float(r.actual)))
    return out


def run(job, now, slot, con=None):
    con = con or db.connect()
    store = Store(con)
    val = load_validation()
    # ---- boards
    rows_w, allI = world.board(store)
    div = world.divergence(store, allI)
    store._drift = div["drift5"]
    sxI = indicators(store.ohlc("BSESN", final_only=True))
    x = sxI.iloc[-1]
    d = store.daily("BSESN", final_only=True)
    piv = pivots(d.high.iloc[-1], d.low.iloc[-1], d.close.iloc[-1])
    gold, silver = metals.one(store, "GOLD"), metals.one(store, "SILVER")
    ratio = metals.ratio_stats(store)
    wk = last_weekly_check(now)
    pl = pool.current(store, asof=wk)
    pl["checked"] = str(wk)
    for r in pl["rows"]:
        con.execute("INSERT OR REPLACE INTO pool_weights VALUES (?,?,?,?,?,?,?)",
                    (str(wk), r["market"], r["base"], r["now"], int(r["below"]), r["vs_ma"], int(r["high_risk"])))
    con.commit()
    # ---- scoring and forecasts
    n_scored = score.score_due(store, now)
    fc_rows, extra = make_forecasts(store, job, now, slot, val)
    if fc_rows:
        db.save_forecasts(con, fc_rows)
    regime, regime_stats = score.regime_warning(con)
    es_on = (extra.get("preopen_features") or {}).get("es_on")
    sig_rows, sig = signals.status(store, now, sxI, extra.get("preopen"), es_on, ratio["now"])
    events = decide.upcoming_events(now.date())
    # ---- decisions
    prev = {a: db.last_decision(con, a, slot) for a in ("sensex_fund", "pool", "metals")}
    calls = [decide.sensex_fund(sxI, sig, regime, events, prev["sensex_fund"][0] if prev["sensex_fund"] else None),
             decide.pool_call(pl), decide.metals_call(gold, silver, ratio, prev["metals"][0] if prev["metals"] else None)]
    for c in calls:
        p = prev[c["area"]]
        c["prev_action"] = p[0] if p else None
        c["prev_at"] = p[2] if p else None
        c["changed"] = bool(p and p[0] != c["action"])
    db.save_decisions(con, [dict(slot=slot, made_at_ist=now.isoformat(), area=c["area"], action=c["action"],
                                 detail=json.dumps(c["bullets"]), reason=c["reason"], prev_action=c["prev_action"]) for c in calls])
    # ---- data quality
    q = pd.read_sql("SELECT * FROM quality WHERE run_id=(SELECT run_id FROM quality WHERE run_id<>'seed' ORDER BY rowid DESC LIMIT 1)", con)
    stale = q[q.stale == 1].ticker.tolist() if len(q) else []
    payload = dict(
        made_at=now.isoformat(), job=job, slot=slot,
        data_through=dict(india=str(d.index[-1].date()), live_asia=[r["market"] for r in rows_w if r["live"]]),
        calls=calls, regime=dict(warning=regime, stats=regime_stats),
        forecasts=current_forecasts(con, now, val), events=events,
        sensex=dict(close=float(x.close), date=str(sxI.index[-1].date()), sma20=float(x.sma20), sma50=float(x.sma50), sma100=float(x.sma100),
                    sma200=float(x.sma200), macd=float(x.macd), macds=float(x.macds), macdh=float(x.macdh), rsi=float(x.rsi14),
                    adx=float(x.adx), pdi=float(x.pdi), ndi=float(x.ndi), st_line=float(x.st_line), st_up=bool(x.st_dir > 0),
                    stk=float(x.stk), pctb=float(x.pctb), atrp=float(x.atrp), hi52=float(x.hi52), lo52=float(x.lo52),
                    score=tscore(x), pivots=piv, sigma=extra.get("sigma")),
        world=rows_w, divergence=div, metals=dict(gold=gold, silver=silver, ratio=ratio, scenarios=metals.SCENARIOS),
        pool=pl, signals=sig_rows, signal_ctx=sig, preopen_inputs=extra.get("preopen_features"), gap_es=extra.get("gap_es"),
        insession=_jsonable(extra.get("insession")), validation=val,
        accuracy=score.rolling(con), quality=dict(stale=stale, rows=q.to_dict("records") if len(q) else []),
        n_scored=n_scored, n_forecasts=len(fc_rows),
    )
    db.save_snapshot(con, slot, now.isoformat(), job, payload)
    return payload


def _jsonable(o):
    if o is None:
        return None
    return json.loads(json.dumps(o, default=str))
