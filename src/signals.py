"""Status today of the signals that were right 70%+ of the time (HANDOVER 3.13).
Accuracy figures are the research results (2002-2026), shown as such; the status is computed live."""
import numpy as np
import pandas as pd

from .config import R, ist
from .series import last_close_before

RESEARCH = {  # signal -> (predicts, right, normal, recheck, tested, use)
    "check_215": ("Whether today's close ends up or down vs yesterday", "89%; 99% if move >0.5%", "~51%", "2023–26 only", "721 days",
                  "Knowing before the 3 pm cut-off whether today's NAV will be above or below yesterday's"),
    "us_big": ("Sensex opens the same way", "88.6%", "51%", "81.7%", "420 days", "Expecting the open (already priced in by 9:15)"),
    "es_on": ("Sensex opens the same way", "81.2%", "~60%", "2023–26 only", "213 days", "Same as above"),
    "preopen_strong": ("First-hour direction", "78.2%; 87% if >0.5%", "~53%", "2025–26 test", "147 days", "Expecting the first hour"),
    "dip10": ("Higher 1 year later", "85.5%; avg +27%", "79.6%; avg +17%", "100%", "16 episodes", "Putting extra new money in"),
    "dip20": ("Higher 1 / 3 years later", "86% / 100%; avg +39%", "80% / 97%", "100%", "7 episodes", "Large one-time investment"),
    "vix25": ("Higher 1 year later", "92.7%; avg +30%", "79.6%", "100%", "16 episodes", "Buying when others panic"),
    "hold3": ("Higher 3 / 5 years later", "97% / 99%", "—", "99% / 100%", "265 / 240 months", "The single most reliable rule"),
    "calm": ("Next month calm or stormy", "76.2%", "62.9%", "77.5%", "286 months", "Sizing instalments; expecting swings"),
    "atr": ("Tomorrow moves >1.2% high-to-low", "69.0%", "52.7%", "68.2%", "6,154 days", "Not panicking at big intraday moves"),
    "ratio": ("Silver beats gold over 6 months", "~70%", "—", "mostly 2020", "869 days", "Choosing silver over gold once metals turn up"),
}
NAMES = {"check_215": "Sensex direction at 2:15 pm", "us_big": "Big US move overnight (NYSE ±2%)", "es_on": "US futures overnight (ES ±0.6%)",
         "preopen_strong": "Strong pre-open forecast (>0.3%)", "dip10": "Deep dip: ≥10% below 1-yr high", "dip20": "Very deep dip: ≥20% below 1-yr high",
         "vix25": "Fear spike: India VIX ≥25", "hold3": "Hold 3+ years (any month)", "calm": "Calm stays calm (volatility)",
         "atr": "Wide-range days cluster (ATR >1.2%)", "ratio": "Gold/silver ratio >85"}


def status(store, now, sx_I, preopen=None, es_on=None, ratio=None):
    out = {}
    c = store.close("BSESN", final_only=True)
    # 2:15 pm check
    h = store.hourly("BSESN")
    today = h[h.index.date == now.date()]
    t215 = ist(now.date(), "14:15")
    if now >= t215 and len(today):
        p, ts = last_close_before(today, t215)
        prev = c[c.index < pd.Timestamp(now.date())].iloc[-1]
        mv = p / prev - 1 if p == p else np.nan
        act = bool(mv == mv and mv < -R["check_215_threshold"])
        txt = f"{mv*100:+.2f}% at 2:15 pm" + (" → today's NAV very likely lower; place a due instalment before 3 pm" if act else "")
        out["check_215"] = (act, txt)
    else:
        out["check_215"] = (None, "Check at 2:15 pm")
    ny = store.close("NYA", final_only=True)
    nyr = ny.iloc[-1] / ny.iloc[-2] - 1
    out["us_big"] = (bool(abs(nyr) > 0.02), f"NYSE {nyr*100:+.2f}% ({ny.index[-1].strftime('%d %b')})" + (" → gap " + ("up" if nyr > 0 else "down") if abs(nyr) > 0.02 else ""))
    if es_on is not None and es_on == es_on:
        out["es_on"] = (bool(abs(es_on) > 0.006), f"ES {es_on*100:+.2f}%" + (" → gap " + ("up" if es_on > 0 else "down") if abs(es_on) > 0.006 else ""))
    else:
        out["es_on"] = (None, "Pre-open only")
    if preopen:
        p1 = preopen["y1"]["pred"]
        out["preopen_strong"] = (bool(abs(p1) > 0.003), f"{p1*100:+.2f}%" + (" → " + ("up" if p1 > 0 else "down") if abs(p1) > 0.003 else ""))
    else:
        out["preopen_strong"] = (None, "Pre-open only")
    dd = c.iloc[-1] / c.iloc[-250:].max() - 1
    out["dip10"] = (bool(dd <= -0.10), f"{dd*100:.1f}% from 1-yr high")
    out["dip20"] = (bool(dd <= -0.20), f"{dd*100:.1f}% from 1-yr high")
    iv = store.close("INDIAVIX", final_only=True).iloc[-1]
    out["vix25"] = (bool(iv >= R["vix_fear"]), f"India VIX {iv:.1f}")
    out["hold3"] = (True, "Always on")
    r = c.pct_change()
    v20 = r.rolling(20).std()
    med = v20.expanding(250).median()
    calm = bool(v20.iloc[-1] < med.iloc[-1])
    out["calm"] = (calm, f"{'Calm' if calm else 'Stormy'}: 20-day vol {v20.iloc[-1]*np.sqrt(252)*100:.1f}% vs median {med.iloc[-1]*np.sqrt(252)*100:.1f}%")
    atrp = float(sx_I["atrp"].iloc[-1])
    out["atr"] = (bool(atrp > 0.012), f"ATR {atrp*100:.2f}%" + (" → expect a wide day" if atrp > 0.012 else ""))
    if ratio:
        out["ratio"] = (bool(ratio > R["ratio_tilt"]), f"Ratio {ratio:.1f}")
    rows = []
    for k, (act, txt) in out.items():
        pr, right, normal, rc, tested, use = RESEARCH[k]
        rows.append(dict(key=k, name=NAMES[k], predicts=pr, right=right, normal=normal, recheck=rc, tested=tested, use=use,
                         active=act, status=txt))
    return rows, dict(drawdown=float(dd), indiavix=float(iv), atrp=atrp, calm=calm)
