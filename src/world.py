"""World board (10 indices), world trend score and the India-vs-world divergence analogue (HANDOVER 3.9)."""
import numpy as np
import pandas as pd

from .config import FOREIGN, MARKETS
from .series import close_times
from .ta_lib import indicators, rsi_label, score, score_series, state_label


def board(store):
    rows, allI = [], {}
    for key, m in MARKETS.items():
        d = store.ohlc(key)
        I = indicators(d)
        allI[key] = I
        x = I.iloc[-1]
        s = score(x)
        prov = bool(store.daily(key)["provisional"].iloc[-1])
        rows.append(dict(key=key, market=m["name"], date=str(I.index[-1].date()), live=prov,
                         close=round(float(x.close), 2), chg1=_pct(I.close, 1), chg5=_pct(I.close, 5),
                         score=s, state=state_label(s), rsi=round(float(x.rsi14), 1), rsi_label=rsi_label(x.rsi14),
                         from_high=round(float((x.close / x.hi52 - 1) * 100), 1), vol20=round(float(x.vol20 * 100), 1),
                         st_line=round(float(x.st_line), 2), st_up=bool(x.st_dir > 0)))
    return rows, allI


def _pct(c, n):
    return round(float((c.iloc[-1] / c.iloc[-1 - n] - 1) * 100), 2)


def divergence(store, allI):
    """Every past day where the Sensex was strong while the 8 foreign markets were weak."""
    sc = {k: score_series(I) for k, I in allI.items()}
    sx = store.close("BSESN", final_only=True)
    idx = sx.index
    # exact clock alignment: at the Indian close (15:30 IST) only bars that had already closed are known
    # (Asia same day; Europe and US previous session). The research version used same-date values.
    dec = (idx.normalize() + pd.Timedelta(hours=15, minutes=30)).tz_localize("Asia/Kolkata").tz_convert("UTC")
    cols = {}
    for k in FOREIGN:
        s = sc[k].dropna()
        pos = close_times(k, s.index).searchsorted(dec, side="right") - 1
        cols[k] = np.where(pos >= 0, s.values[np.clip(pos, 0, None)], np.nan)
    fav = pd.DataFrame(cols, index=idx).mean(axis=1)
    s_sx = sc["BSESN"].reindex(idx)
    f5, f20, f60 = sx.shift(-5) / sx - 1, sx.shift(-20) / sx - 1, sx.shift(-60) / sx - 1

    def rep(name, cond):
        cond = cond.fillna(False)
        ep = cond & ~cond.shift(1, fill_value=False)
        return dict(setup=name, days=int(cond.sum()), episodes=int(ep.sum()),
                    f5=round(float(f5[cond].mean() * 100), 2), f20=round(float(f20[cond].mean() * 100), 2),
                    f60=round(float(f60[cond].mean() * 100), 2), p_fall5=round(float((f20[cond] < -0.05).mean() * 100), 1))

    table = [rep("Normal day", pd.Series(True, index=idx)),
             rep("India strong, world weak (≤3)", (s_sx >= 8) & (fav <= 3)),
             rep("India strong, world very weak (≤2)", (s_sx >= 8) & (fav <= 2)),
             rep("India strong, world strong (≥7)", (s_sx >= 8) & (fav >= 7))]
    # current reading uses the latest available scores (foreign markets may include today's live Asia bars)
    now_sx = float(sc["BSESN"].dropna().iloc[-1])
    now_world = float(np.mean([sc[k].dropna().iloc[-1] for k in FOREIGN]))
    gap_series = (s_sx - fav).dropna()
    gap_now = now_sx - now_world
    wider = int((gap_series > gap_now).sum())
    last_wider = str(gap_series[gap_series > gap_now].index[-1].date()) if wider else None
    if now_sx >= 8 and now_world <= 2:
        bucket = table[2]
    elif now_sx >= 8 and now_world <= 3:
        bucket = table[1]
    elif now_sx >= 8 and now_world >= 7:
        bucket = table[3]
    else:
        bucket = table[0]
    return dict(table=table, sensex=now_sx, world=round(now_world, 2), gap=round(gap_now, 2),
                days_wider=wider, last_wider=last_wider, bucket=bucket["setup"], drift5=bucket["f5"])
