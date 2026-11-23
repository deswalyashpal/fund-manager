"""Global index pool (9 markets, INR): risk-parity base weights + half-cut below the daily 100-day average.

Agreed 23 Nov 2026: the live rule uses the true daily 100-day SMA of INR prices, checked at the Friday
close; base weights reset monthly from 36 monthly INR returns (Ledoit-Wolf), capped at 30%.
"""
import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

from .config import MARKETS, POOL, R


def inr_daily(store):
    return pd.DataFrame({k: store.inr_close(k) for k in POOL}).sort_index()


def base_weights(monthly_ret):
    S = LedoitWolf().fit(monthly_ret.values).covariance_
    iv = 1 / np.sqrt(np.diag(S))
    w = iv / iv.sum()
    w = np.minimum(w, R["pool_cap"])
    return w / w.sum()


def monthly_prices(P):
    return P.resample("ME").last().loc["2003-12-31":].dropna()


def current(store, asof=None):
    """Weights as of the latest data (or `asof` date). Returns a list of dicts + totals."""
    P = inr_daily(store)
    if asof is not None:
        P = P[P.index <= pd.Timestamp(asof)]
    M = monthly_prices(P)
    # base weights use complete months only
    last_month_end = P.index[-1].to_period("M").start_time - pd.Timedelta(days=1)
    Mr = M[M.index <= last_month_end].pct_change().dropna()
    base = base_weights(Mr.iloc[-R["pool_lookback_months"]:])
    Pf = P.ffill()
    sma = Pf.rolling(R["pool_sma_days"], min_periods=R["pool_sma_days"]).mean()
    last = Pf.iloc[-1]
    vs = last / sma.iloc[-1] - 1
    # risk flag: EWMA vol vs its own 3-year average
    lr = np.log(Pf).diff()
    ew = np.sqrt(lr.ewm(alpha=0.06).var() * 252)
    rows = []
    for i, k in enumerate(POOL):
        below = bool(vs[k] < 0)
        cut = R["pool_cut_factor"] if below else 1.0
        hr = bool(ew[k].iloc[-1] > R["high_risk_vol_multiple"] * ew[k].iloc[-756:].mean())
        rows.append(dict(key=k, market=MARKETS[k]["name"], base=round(base[i] * 100, 1), now=round(base[i] * cut * 100, 1),
                         below=below, vs_ma=round(float(vs[k] * 100), 1), high_risk=hr, ewma_vol=round(float(ew[k].iloc[-1] * 100), 1),
                         last_date=str(store.daily(k, True).index[-1].date())))
    inv = round(sum(r["now"] for r in rows), 1)
    return dict(rows=rows, invested=inv, liquid=round(100 - inv, 1), base_month=str(Mr.index[-1].date()),
                prices_date=str(P.index[-1].date()))


def _stats(v, periods, cash):
    eq = (1 + v).cumprod()
    yrs = len(v) / periods
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    vol = v.std() * np.sqrt(periods)
    dd = (eq / eq.cummax() - 1).min()
    yr = lambda y: round(float(((1 + v[v.index.year == y]).prod() - 1) * 100), 1)
    return dict(cagr=round(float(cagr * 100), 2), vol=round(float(vol * 100), 1), ratio=round(float(cagr / vol), 2),
                mdd=round(float(dd * 100), 1), avg_cash=round(float(np.mean(cash) * 100)), y2008=yr(2008), y2022=yr(2022))


def backtest(store):
    """Re-test with the rule as it is now applied live.
    A) monthly check using the daily 100-day SMA at month end; B) weekly (Friday) check with weekly returns.
    Base weights reset monthly from the prior 36 monthly returns in both."""
    P = inr_daily(store).ffill().dropna()
    sma = P.rolling(R["pool_sma_days"]).mean()
    M = monthly_prices(P)
    Mr = M.pct_change().dropna()
    LB, cost, n = R["pool_lookback_months"], R["cost_per_switch"], len(POOL)
    out = {}
    # A) monthly
    for name, cut in (("Risk parity, no rule", None), ("Half-cut below 100-day avg (chosen)", 0.5), ("Full-cut below 100-day avg", 0.0)):
        prev, res, cash = np.zeros(n), [], []
        for i, d in enumerate(Mr.index[LB:]):
            base = base_weights(Mr.iloc[i:i + LB])
            t0 = Mr.index[i + LB - 1]
            px, sm = P[P.index <= t0].iloc[-1], sma[sma.index <= t0].iloc[-1]
            w = base.copy() if cut is None else np.where((px < sm).values, base * cut, base)
            cw = 1 - w.sum()
            r = Mr.loc[d].values
            cm = R["cash_yield"] / 12
            res.append(w @ r + cw * cm - cost * np.abs(w - prev).sum())
            cash.append(cw)
            g = w * (1 + r)
            prev = g / (g.sum() + cw * (1 + cm))
        out["monthly: " + name] = _stats(pd.Series(res, index=Mr.index[LB:]), 12, cash)
    # B) weekly check (the live rule)
    W = P.resample("W-FRI").last()
    Wr = W.pct_change().dropna()
    Wsma = sma.resample("W-FRI").last()
    for name, cut in (("Risk parity, no rule", None), ("Half-cut below 100-day avg (chosen)", 0.5), ("Full-cut below 100-day avg", 0.0)):
        prev, res, cash, idx = np.zeros(n), [], [], []
        start = Mr.index[LB]
        base, base_m = None, None
        for j in range(1, len(Wr)):
            d, t0 = Wr.index[j], Wr.index[j - 1]
            if t0 < start:
                continue
            m_end = t0.to_period("M").start_time - pd.Timedelta(days=1)
            if base_m != m_end:
                hist = Mr[Mr.index <= m_end].iloc[-LB:]
                base, base_m = base_weights(hist), m_end
            below = (W.loc[t0] < Wsma.loc[t0]).values
            w = base.copy() if cut is None else np.where(below, base * cut, base)
            cw = 1 - w.sum()
            r = Wr.loc[d].values
            cwk = R["cash_yield"] / 52
            res.append(w @ r + cw * cwk - cost * np.abs(w - prev).sum())
            cash.append(cw)
            idx.append(d)
            g = w * (1 + r)
            prev = g / (g.sum() + cw * (1 + cwk))
        out["weekly: " + name] = _stats(pd.Series(res, index=pd.DatetimeIndex(idx)), 52, cash)
    out["_period"] = f"{Mr.index[LB].strftime('%b %Y')} – {P.index[-1].strftime('%b %Y')}"
    return out
