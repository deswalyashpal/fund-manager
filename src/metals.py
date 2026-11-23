"""Gold, silver and the gold/silver ratio (COMEX futures GC=F / SI=F, labelled as futures)."""
import numpy as np

from .ta_lib import indicators, rsi_label, score

SCENARIOS = [  # HANDOVER 3.1, made 22 Nov 2026 (research view; not recomputed)
    dict(name="Base: grinding bottom", odds="~50%", gold="$3,100–4,000", silver="$32–48", ratio="80–95"),
    dict(name="Fed-pivot rebound", odds="~30%", gold="$4,200–4,800", silver="$60–70", ratio="65–70"),
    dict(name="1980-style squeeze", odds="~20%", gold="$2,700–3,100", silver="$25–32", ratio="95–110"),
]


def one(store, key):
    d = store.ohlc(key)
    I = indicators(d)
    x = I.iloc[-1]
    c = I.close
    fin = store.daily(key, final_only=True)["close"]
    return dict(key=key, close=float(x.close), date=str(I.index[-1].date()), live=bool(store.daily(key)["provisional"].iloc[-1]),
                chg_since_last_final=round(float((x.close / fin.iloc[-1] - 1) * 100), 2) if fin.index[-1] != I.index[-1] else round(float((c.iloc[-1] / c.iloc[-2] - 1) * 100), 2),
                chg5=round(float((c.iloc[-1] / c.iloc[-6] - 1) * 100), 2), rsi=round(float(x.rsi14), 1), rsi_label=rsi_label(x.rsi14),
                sma50=float(x.sma50), sma200=float(x.sma200), st_line=float(x.st_line), st_up=bool(x.st_dir > 0),
                hi52=float(x.hi52), from_high=round(float((x.close / x.hi52 - 1) * 100), 1), score=score(x))


def ratio_stats(store):
    g, s = store.close("GOLD"), store.close("SILVER")
    r = (g / s.where(s > 0)).dropna()
    now = float(r.iloc[-1])
    yr = r[r.index >= r.index[-1] - np.timedelta64(365, "D")]
    gf, sf = g.shift(-120) / g - 1, s.shift(-120) / s - 1
    rf = r.shift(-120) < r
    cond = (r > 85) & gf.notna() & sf.notna()
    return dict(now=round(now, 1), mean=round(float(r.mean()), 1), pct_below=round(float((r < now).mean() * 100)),
                low_1y=round(float(yr.min()), 1), low_1y_date=str(yr.idxmin().date()), since=str(r.index[0].year),
                analog_days=int(cond.sum()), analog_silver=round(float(sf[cond].mean() * 100), 1),
                analog_gold=round(float(gf[cond].mean() * 100), 1), analog_ratio_lower=round(float(rf[cond].mean() * 100)),
                all_silver=round(float(sf.mean() * 100), 1), all_gold=round(float(gf.mean() * 100), 1),
                series=[(str(i.date()), round(float(v), 1)) for i, v in r.iloc[-750::5].items()])
