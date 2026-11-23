"""Sensex forecasts for six horizons (HANDOVER section 5).

Every model is trained only on data available before its decision time:
pre-open and in-session ridges are retrained monthly (training rows before the 1st of the current
month), daily z-quantiles use history up to the end of the prior year.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from .config import F, IST, ist, is_trading_day, next_trading_day, trading_days_between
from .series import ewma_sigma, last_close_before

PRE_FEATS = ["es_on", "hs_am", "ks_am", "prev_ret"]
IN_FEATS = ["k", "r_bar", "r_open", "r_prev", "es_1h"]
SESSION_START, SESSION_END = (9, 15), (15, 15)   # hourly bar starts


def session_bars(store):
    sx = store.hourly("BSESN")
    m = sx.index.hour * 60 + sx.index.minute
    return sx[(m >= 555) & (m <= 915)]


def bar_end(ts):
    """Sensex hourly bars end one hour after they start; the 15:15 bar ends at the 15:30 close."""
    end = ts + pd.Timedelta(hours=1)
    return min(end, ts.normalize() + pd.Timedelta(hours=15, minutes=30))


# ---------------------------------------------------------------- pre-open model
def preopen_features(store, day, prev_day, t_dec):
    """Features known at IST time t_dec on India day `day` (previous session = prev_day)."""
    t_prev = ist(prev_day, "15:30")
    out = {}
    for name, key in (("es_on", "ES"), ("hs_am", "HSI"), ("ks_am", "KS11")):
        h = store.hourly(key)
        a, _ = last_close_before(h, t_dec)
        b, _ = last_close_before(h, t_prev)
        out[name] = a / b - 1 if b and not np.isnan(b) else np.nan
    c = store.close("BSESN", final_only=True)
    c = c[c.index <= pd.Timestamp(prev_day)]
    out["prev_ret"] = c.iloc[-1] / c.iloc[-2] - 1 if len(c) > 1 else np.nan
    return out


def preopen_rows(store, dec_hhmm="09:10"):
    """One row per past India day: pre-open features + realised first hour / 3 hours / close."""
    sx = session_bars(store)
    dc = store.close("BSESN", final_only=True)
    days = sorted(set(sx.index.date))
    es, hs, ks = store.hourly("ES"), store.hourly("HSI"), store.hourly("KS11")
    rows = []
    for i in range(1, len(days)):
        D, P = days[i], days[i - 1]
        b = sx[sx.index.date == D]
        if len(b) < 6:
            continue
        pc, dcl = dc.get(pd.Timestamp(P), np.nan), dc.get(pd.Timestamp(D), np.nan)
        prv = dc[dc.index < pd.Timestamp(P)]
        if np.isnan(pc) or np.isnan(dcl) or not len(prv):
            continue
        t_dec, t_prev = ist(D, dec_hhmm), ist(P, "15:30")
        f = {}
        for name, h in (("es_on", es), ("hs_am", hs), ("ks_am", ks)):
            a, _ = last_close_before(h, t_dec)
            z, _ = last_close_before(h, t_prev)
            f[name] = a / z - 1
        rows.append(dict(date=pd.Timestamp(D), **f, prev_ret=pc / prv.iloc[-1] - 1, pc=pc,
                         y1=b.close.iloc[0] / pc - 1, y3=b.close.iloc[2] / pc - 1, yc=dcl / pc - 1))
    R = pd.DataFrame(rows).set_index("date").replace([np.inf, -np.inf], np.nan).dropna()
    return R


def fit_ridge(X, y):
    m = Ridge(alpha=F["ridge_alpha"]).fit(X, y)
    return m, y - m.predict(X)


def preopen_walkforward(R, warmup_months=6, q=None):
    """Walk-forward predictions (each month trained on earlier months). Returns {target: DataFrame}."""
    q = q or F["preopen_q"]
    out = {}
    mon = R.index.to_period("M")
    for y in ("y1", "y3", "yc"):
        P = pd.DataFrame(index=R.index, columns=["pred", "lo", "hi", "pup"], dtype=float)
        for m in sorted(set(mon))[warmup_months:]:
            tr, te = R[mon < m], R[mon == m]
            mdl, res = fit_ridge(tr[PRE_FEATS], tr[y])
            p = mdl.predict(te[PRE_FEATS])
            P.loc[te.index, "pred"] = p
            P.loc[te.index, "lo"] = p + np.quantile(res, q[0])
            P.loc[te.index, "hi"] = p + np.quantile(res, q[1])
            P.loc[te.index, "pup"] = [((pp + res) > 0).mean() for pp in p]
        P["actual"] = R[y]
        out[y] = P.dropna()
    return out


def preopen_forecast(store, day, prev_day, t_dec, dec_hhmm, R=None):
    R = R if R is not None else preopen_rows(store, dec_hhmm)
    cutoff = pd.Timestamp(day).to_period("M").start_time
    tr = R[R.index < cutoff]
    x = preopen_features(store, day, prev_day, t_dec)
    X = pd.DataFrame([x])[PRE_FEATS]
    if X.isna().any(axis=None) or len(tr) < 60:
        return None, x
    fc = {}
    q = F["preopen_q"]
    for y in ("y1", "y3", "yc"):
        mdl, res = fit_ridge(tr[PRE_FEATS], tr[y])
        p = float(mdl.predict(X)[0])
        fc[y] = dict(pred=p, lo=p + np.quantile(res, q[0]), hi=p + np.quantile(res, q[1]), pup=float(((p + res) > 0).mean()),
                     coef=dict(zip(PRE_FEATS, np.round(mdl.coef_, 3))), n_train=len(tr))
    return fc, x


# ---------------------------------------------------------------- in-session model
def insession_rows(store):
    sx = session_bars(store)
    dc = store.close("BSESN", final_only=True)
    es = store.hourly("ES")
    rows = []
    for D, b in sx.groupby(sx.index.date):
        if len(b) < 7:
            continue
        prv = dc[dc.index < pd.Timestamp(D)]
        if not len(prv) or pd.Timestamp(D) not in dc.index:
            continue
        pc, close_d, o = prv.iloc[-1], dc[pd.Timestamp(D)], b.open.iloc[0]
        for j in range(len(b) - 1):
            t = bar_end(b.index[j])
            c = b.close.iloc[j]
            e1, _ = last_close_before(es, t)
            e0, _ = last_close_before(es, t - pd.Timedelta(hours=1))
            rows.append(dict(ts=t, k=j, r_bar=c / b.open.iloc[j] - 1, r_open=c / o - 1, r_prev=c / pc - 1,
                             es_1h=(e1 / e0 - 1) if e0 == e0 and e1 == e1 else 0.0,
                             y1=b.close.iloc[j + 1] / c - 1, y3=b.close.iloc[min(j + 3, len(b) - 1)] / c - 1,
                             ycl=close_d / c - 1))
    return pd.DataFrame(rows).set_index("ts")


def insession_walkforward(R, warmup_months=6):
    q = F["insession_q"]
    out = {}
    mon = R.index.to_period("M")
    for y in ("y1", "y3", "ycl"):
        P = pd.DataFrame(index=R.index, columns=["pred", "lo", "hi"], dtype=float)
        for m in sorted(set(mon))[warmup_months:]:
            tr, te = R[mon < m], R[mon == m]
            mdl, res = fit_ridge(tr[IN_FEATS], tr[y])
            p = mdl.predict(te[IN_FEATS])
            P.loc[te.index, "pred"], P.loc[te.index, "lo"], P.loc[te.index, "hi"] = p, p + np.quantile(res, q[0]), p + np.quantile(res, q[1])
        P["actual"] = R[y]
        out[y] = P.dropna()
    return out


def insession_forecast(store, now, R=None):
    """Forecast from the latest completed Sensex bar today. Returns dict or None."""
    sx = session_bars(store)
    today = sx[sx.index.date == now.date()]
    today = today[[bar_end(t) <= now for t in today.index]]
    if not len(today) or len(today) >= 7:
        return None
    dc = store.close("BSESN", final_only=True)
    prv = dc[dc.index < pd.Timestamp(now.date())]
    j = len(today) - 1
    t = bar_end(today.index[j])
    c = today.close.iloc[j]
    es = store.hourly("ES")
    e1, _ = last_close_before(es, t)
    e0, _ = last_close_before(es, t - pd.Timedelta(hours=1))
    x = dict(k=j, r_bar=c / today.open.iloc[j] - 1, r_open=c / today.open.iloc[0] - 1, r_prev=c / prv.iloc[-1] - 1,
             es_1h=(e1 / e0 - 1) if e0 == e0 and e1 == e1 else 0.0)
    R = R if R is not None else insession_rows(store)
    tr = R[R.index < pd.Timestamp(now.date()).to_period("M").start_time.tz_localize(IST)]
    q = F["insession_q"]
    out = dict(bar_end=t, price=c, prev_close=prv.iloc[-1], k=j)
    ends = [ist(now.date(), s) for s in ("10:15", "11:15", "12:15", "13:15", "14:15", "15:15", "15:30")]
    for y, tgt in (("y1", ends[min(j + 1, 6)]), ("y3", ends[min(j + 3, 6)]), ("ycl", ends[6])):
        mdl, res = fit_ridge(tr[IN_FEATS], tr[y])
        p = float(mdl.predict(pd.DataFrame([x])[IN_FEATS])[0])
        out[y] = dict(pred=p, lo=p + np.quantile(res, q[0]), hi=p + np.quantile(res, q[1]), target=tgt)
    return out


# ---------------------------------------------------------------- daily horizons (vol-scaled ranges)
def daily_targets(store):
    d = store.daily("BSESN", final_only=True)
    c, o = d.close, d.open
    flat = (d.open == d.close) & (d.high == d.low)
    sig = ewma_sigma(c, F["ewma_alpha"])
    gapnext = (o.shift(-1) / c - 1).mask(flat.shift(-1, fill_value=False))
    r5 = c.shift(-5) / c - 1
    wk = c.shift(-5) * (1 + gapnext.shift(-5)) / c - 1
    return pd.DataFrame(dict(sig=sig, gapnext=gapnext, r5=r5, wk=wk, close=c))


def z_quantiles(T, upto_year):
    """Empirical quantiles of target / (sigma * sqrt(h)) using history before `upto_year`."""
    T = T[(T.index >= "2004-01-01") & (T.index.year < upto_year)]
    res = {}
    for name, col, h in (("gap", "gapnext", 1), ("week", "r5", 5), ("nwo", "wk", 5.5)):
        z = (T[col] / (T.sig * np.sqrt(h if name != "gap" else 1))).dropna()
        res[name] = dict(q10=float(np.quantile(z, 0.1)), med=float(np.quantile(z, 0.5)), q90=float(np.quantile(z, 0.9)), mean=float(z.mean()))
    return res


def daily_coverage(T, widen_gap=1.0):
    """Walk-forward (yearly) 80% range coverage for the three daily horizons."""
    out = {}
    for name, col, h, w in (("next_open", "gapnext", 1, widen_gap), ("week_close", "r5", 5, 1.0), ("next_week_open", "wk", 5.5, 1.0)):
        z = (T[col] / (T.sig * np.sqrt(h))).dropna()
        z = z[z.index >= "2004-01-01"]
        ok = []
        for y in range(2009, int(z.index.year.max()) + 1):
            trn, tst = z[z.index.year < y], z[z.index.year == y]
            if not len(tst):
                continue
            ql, qh = np.quantile(trn, 0.1) * w, np.quantile(trn, 0.9) * w
            ok += list((tst >= ql) & (tst <= qh))
        out[name] = dict(coverage=round(float(np.mean(ok)) * 100, 1), n=len(ok))
    return out


def week_end(day):
    """Last trading day of the week containing `day` (or of the next week if none remain)."""
    d = pd.Timestamp(day).date()
    fri = d + pd.Timedelta(days=4 - d.weekday())
    while not is_trading_day(fri) and fri > d:
        fri -= pd.Timedelta(days=1)
    return fri


def daily_horizons(today, C0, close_fc, sigma, zq, drift_per_day, after_close):
    """close_fc = (pc, lc, hc) as returns vs C0 for today's close (zeros once today's close is known)."""
    pc, lc, hc = close_fc
    g = zq["gap"]
    w = F["gap_widen"]
    rows = []
    nd = next_trading_day(today)
    rows.append(dict(horizon="next_open", target=ist(nd, "09:15"),
                     point=C0 * (1 + pc) * (1 + g["mean"] * sigma),
                     low=C0 * (1 + lc) * (1 + g["q10"] * sigma * w), high=C0 * (1 + hc) * (1 + g["q90"] * sigma * w)))
    we = week_end(today)
    if after_close and pd.Timestamp(we).date() <= pd.Timestamp(today).date():
        we = week_end(next_trading_day(today))
    dleft = trading_days_between(today, we)
    zw, zn = zq["week"], zq["nwo"]
    sw = sigma * np.sqrt(max(dleft, 0))
    mid_w = (1 + pc) * (1 + drift_per_day * dleft) - 1
    if dleft == 0:
        lo_w, hi_w = lc, hc
    else:
        lo_w, hi_w = pc + zw["q10"] * sw, pc + zw["q90"] * sw
    rows.append(dict(horizon="week_close", target=ist(we, "15:30"), point=C0 * (1 + mid_w), low=C0 * (1 + lo_w), high=C0 * (1 + hi_w)))
    nwo = next_trading_day(we)
    sn = sigma * np.sqrt(dleft + 0.5)
    mid_n = (1 + mid_w) * (1 + g["mean"] * sigma) - 1
    rows.append(dict(horizon="next_week_open", target=ist(nwo, "09:15"), point=C0 * (1 + mid_n),
                     low=C0 * (1 + pc + zn["q10"] * sn), high=C0 * (1 + pc + zn["q90"] * sn)))
    for r in rows:
        r.update(base_price=C0, p_up=None, method="volscaled")
    return rows


# ---------------------------------------------------------------- next-day open refit after the US close
def gap_rows(store, hhmm="03:00"):
    """Rows for India day D: ES change from previous India close (15:30) to D 03:00 IST, and the opening gap."""
    d = store.daily("BSESN", final_only=True)
    es = store.hourly("ES")
    flat = (d.open == d.close) & (d.high == d.low)
    rows = []
    idx = list(d.index)
    for i in range(1, len(idx)):
        D, P = idx[i], idx[i - 1]
        if flat.iloc[i] or D < es.index[0].tz_localize(None):
            continue
        a, _ = last_close_before(es, ist(D, hhmm))
        b, _ = last_close_before(es, ist(P, "15:30"))
        if a != a or b != b:
            continue
        rows.append(dict(date=D, es_ovn=a / b - 1, gap=d.open.iloc[i] / d.close.iloc[i - 1] - 1))
    return pd.DataFrame(rows).set_index("date")


def gap_walkforward(G, warmup_months=6):
    mon = G.index.to_period("M")
    P = pd.DataFrame(index=G.index, columns=["pred", "lo", "hi"], dtype=float)
    for m in sorted(set(mon))[warmup_months:]:
        tr, te = G[mon < m], G[mon == m]
        mdl, res = fit_ridge(tr[["es_ovn"]], tr["gap"])
        p = mdl.predict(te[["es_ovn"]])
        P.loc[te.index, "pred"], P.loc[te.index, "lo"], P.loc[te.index, "hi"] = p, p + np.quantile(res, 0.1), p + np.quantile(res, 0.9)
    P["actual"] = G["gap"]
    return P.dropna()


def gap_forecast(store, india_day, prev_close, prev_day, now):
    G = gap_rows(store)
    tr = G[G.index < pd.Timestamp(india_day).to_period("M").start_time]
    es = store.hourly("ES")
    a, ts_a = last_close_before(es, now)
    b, _ = last_close_before(es, ist(prev_day, "15:30"))
    x = a / b - 1
    mdl, res = fit_ridge(tr[["es_ovn"]], tr["gap"])
    p = float(mdl.predict(pd.DataFrame({"es_ovn": [x]}))[0])
    return dict(horizon="next_open", target=ist(india_day, "09:15"), base_price=prev_close, point=prev_close * (1 + p),
                low=prev_close * (1 + p + np.quantile(res, 0.1)), high=prev_close * (1 + p + np.quantile(res, 0.9)),
                p_up=None, method="gap_es", es_ovn=x, es_ts=str(ts_a))


def hit_stats(P, pred="pred", actual="actual"):
    t, a = P[pred], P[actual]
    base = max((a > 0).mean(), 1 - (a > 0).mean())
    return dict(n=int(len(P)), hit=round(float(((t > 0) == (a > 0)).mean()) * 100, 1), base=round(float(base) * 100, 1),
                corr=round(float(t.corr(a)), 2), coverage=round(float(((a >= P.lo) & (a <= P.hi)).mean()) * 100, 1))
