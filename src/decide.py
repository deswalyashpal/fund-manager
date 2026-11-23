"""The fund-manager playbook (HANDOVER section 4, with the decisions agreed on 23 Nov 2026)."""
import pandas as pd

from .config import CFG, R

TRUST = {  # agreed labels
    "green": ("Direction + range", "Up/down right about {hit} in 10. Price stayed in range {cov} in 10. Describes today; not a buy/sell signal."),
    "amber": ("Range only, not direction", "Up or down is a coin toss. Price stayed in range {cov} in 10."),
    "red": ("Don't rely on it", "Tested and no better than guessing. Shown for information only."),
}


def trust_for(stats):
    """Label from validation: green if direction beats the coin-flip rate by 5+ points and the range is calibrated."""
    if not stats:
        return "amber"
    lo, hi = [x * 100 for x in CFG["forecast"]["coverage_ok"]]
    cov_ok = stats.get("coverage") is not None and lo <= stats["coverage"] <= hi
    if stats.get("hit") is not None and stats["hit"] >= stats["base"] + 5 and stats["n"] >= 100 and cov_ok:
        return "green"
    if cov_ok:
        return "amber"
    return "red"


def trust_text(level, stats):
    name, tmpl = TRUST[level]
    stats = stats or {}
    hit = round((stats.get("hit") or 50) / 10)
    cov = round((stats.get("coverage") or 80) / 10)
    return name, tmpl.format(hit=hit, cov=cov)


def upcoming_events(today, days=45):
    t = pd.Timestamp(today)
    out = []
    for e in CFG.get("events") or []:
        d = pd.Timestamp(e["date"])
        if t <= d <= t + pd.Timedelta(days=days):
            out.append(dict(date=e["date"], name=e["name"], confirmed=e.get("confirmed", False), days=(d - t).days,
                            kind=e.get("kind")))
    return out


def sensex_fund(I, sig, regime_warning, events, prev):
    x = I.iloc[-1]
    close, st_line, st_up = float(x.close), float(x.st_line), bool(x.st_dir > 0)
    from .ta_lib import score
    sc = score(x)
    rsi = float(x.rsi14)
    sma100 = float(x.sma100)
    near_event = [e for e in events if e["days"] <= R["event_caution_days"]]
    bullets = []
    if not st_up:
        action = "SELL" if (prev or "").upper() not in ("SELL", "STAY OUT") else "STAY OUT"
        reason = f"Closed at {close:,.0f}, below the exit line {st_line:,.0f} (Supertrend turned down)."
        bullets.append(f"Exit rule triggered: close below {st_line:,.0f}.")
        bullets.append(f"Buy back on a close above {st_line:,.0f}.")
    else:
        action = "BUY BACK" if (prev or "").upper() in ("SELL", "STAY OUT") else "HOLD"
        reason = (f"Trend up (score {sc}/10, close {close:,.0f} above exit line {st_line:,.0f})."
                  + (" Stretched (RSI {:.0f}).".format(rsi) if rsi > R["rsi_stretched"] else ""))
        if rsi > R["rsi_stretched"]:
            bullets.append("Don't put in a lump sum today.")
            bullets.append(f"New money: {R['stagger_parts']} equal parts, one each week. Bring the next part forward on a dip to the 20-day average ({x.sma20:,.0f}).")
        elif sc >= R["invest_normally_min_score"]:
            bullets.append("New money: invest normally.")
        else:
            bullets.append(f"New money: trend is mixed (score {sc}/10); stagger in {R['stagger_parts']} weekly parts.")
        bullets.append(f"Sell only on a close below **{st_line:,.0f}**.")
    if near_event:
        bullets.append("Event in the next 2 days (" + ", ".join(e["name"] for e in near_event) + "): avoid lump sums.")
    dd = sig["drawdown"]
    ladder = [t for t in R["dip_ladder"] if dd <= -t]
    if ladder:
        bullets.append(f"Dip ladder: Sensex is {dd*100:.0f}% below its 1-yr high → put in an extra instalment ({'larger' if len(ladder) > 1 else 'normal size'}).")
    if sig["indiavix"] >= R["vix_fear"]:
        bullets.append(f"Fear spike (India VIX {sig['indiavix']:.0f}): the largest dip-ladder instalment.")
    if regime_warning:
        bullets.append("Model out of step with the market: forecasts are down-weighted today.")
    light = _light(action)
    if action == "HOLD" and (rsi > R["rsi_stretched"] or near_event):
        light = "amber"
    return dict(area="sensex_fund", title="Sensex index fund", action=action, light=light, bullets=bullets, reason=reason,
                exit_line=st_line, slow_exit=sma100, score=sc, rsi=rsi)


def pool_call(pool):
    cut = [r for r in pool["rows"] if r["below"]]
    foreign_cut = [r for r in cut if r["key"] != "BSESN"]
    n = len(cut)
    action = "FULLY INVESTED" if n == 0 else "DEFENSIVE" if n >= 5 else "PARTLY DEFENSIVE"
    bullets = []
    if n:
        bullets.append(f"{n} market{'s' if n > 1 else ''} below the 100-day average, so each is held at **half weight**.")
    bullets.append(f"Pool: **{pool['invested']}%** invested, **{pool['liquid']}%** in a liquid fund.")
    if n:
        bullets.append("Restore each market when it closes back above its average (checked at the Friday close).")
    hr = [r["market"] for r in pool["rows"] if r["high_risk"]]
    if hr:
        bullets.append("High risk (volatility 1.4× normal): " + ", ".join(hr) + " — new money in smaller instalments.")
    india = next(r for r in pool["rows"] if r["key"] == "BSESN")
    if not india["below"]:
        bullets.append(f"India is held at its full {india['base']}% and not increased.")
    reason = f"{len(foreign_cut)} of 8 foreign markets below their 100-day average in INR."
    return dict(area="pool", title="Global index pool", action=action, light=_light(action), bullets=bullets, reason=reason)


def metals_call(gold, silver, ratio, prev):
    bullets = []
    sig = {k: (m["st_up"], m["score"]) for k, m in (("gold", gold), ("silver", silver))}
    buy = [k for k, (up, sc) in sig.items() if up and sc >= R["metals_min_score"]]
    watch = [k for k, (up, sc) in sig.items() if up and sc < R["metals_min_score"]]
    if buy:
        action = "BUY"
        bullets.append("Buy signal confirmed for " + " and ".join(buy) + " (close above the Supertrend line and trend score ≥4).")
        bullets.append("Buy in stages, not all at once.")
    elif watch:
        action = "WATCH"
        bullets.append(" and ".join(watch).capitalize() + " closed above the buy-signal line, but the trend score is still ≤3: wait for confirmation.")
    else:
        action = "WAIT"
        bullets.append(f"Both still in downtrends ({gold['score']} and {silver['score']} of 10 trend signals).")
        bullets.append(f"Buy signal: gold closes above **${gold['st_line']:,.0f}**, silver above **${silver['st_line']:,.2f}**.")
    if ratio["now"] > R["ratio_tilt"]:
        bullets.append(f"When buying, favour silver (ratio {ratio['now']}, higher than {ratio['pct_below']}% of days since {ratio['since']}).")
    reason = f"Gold trend {gold['score']}/10, silver {silver['score']}/10; buy lines ${gold['st_line']:,.0f} / ${silver['st_line']:,.2f}."
    return dict(area="metals", title="Gold and silver", action=action, light=_light(action), bullets=bullets, reason=reason)


def _light(action):
    a = action.upper()
    if a in ("SELL", "STAY OUT", "DEFENSIVE", "WAIT"):
        return "red" if a in ("SELL", "STAY OUT") else "amber"
    if a in ("PARTLY DEFENSIVE", "WATCH"):
        return "amber"
    return "green"
