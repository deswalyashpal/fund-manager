"""Gold and silver prices in India, in rupees.

Source: IBJA (India Bullion and Jewellers Association) benchmark rates, ibjarates.com, published twice
each business day (AM ~12:00 IST, PM ~17:00 IST). Gold 999 (24 carat) is per 10 g, silver 999 per kg,
both excluding GST and making charges. If IBJA can't be read, we fall back to the international price
converted to rupees (COMEX futures x USD/INR), clearly labelled as such.
"""
import logging
import re
from datetime import timedelta
from urllib.request import Request, urlopen

import pandas as pd

from .config import IST

log = logging.getLogger(__name__)
URL = "https://ibjarates.com/"
OZ_G = 31.1035  # grams per troy ounce


def _cells(row_html):
    return [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.S)]


def _num(s):
    s = (s or "").replace(",", "").strip()
    try:
        v = float(s)
        return v if v > 0 else None
    except ValueError:
        return None


def parse(html, today):
    """-> list of dict(date, session, gold999, silver999). `today` is the IST date of the fetch."""
    out = []
    tabs = (("AM", html.find('id="tab-am"')), ("PM", html.find('id="tab-pm"')))
    for sess, i in tabs:
        if i < 0:
            continue
        nxt = [j for _, j in tabs if j > i]
        seg = html[i:min(nxt) if nxt else i + 20000]
        for r in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, flags=re.S):
            c = _cells(r)
            if len(c) >= 7 and re.match(r"\d{2}/\d{2}/\d{4}$", c[0]):
                d = pd.to_datetime(c[0], format="%d/%m/%Y").date()
                out.append(dict(date=d, session=sess, gold999=_num(c[1]), silver999=_num(c[6])))
    hist_max = max((r["date"] for r in out), default=None)
    # The headline AM/PM figures are for the next business day after the newest history row.
    if hist_max is not None:
        cur = hist_max + timedelta(days=1)
        while cur.weekday() >= 5:
            cur += timedelta(days=1)
        cur = min(cur, today)
        if cur > hist_max:
            for sess in ("AM", "PM"):
                g = re.search(rf'id="lblGold999_{sess}"[^>]*>([^<]*)<', html)
                s = re.search(rf'id="lblSilver999_{sess}"[^>]*>([^<]*)<', html)
                gv, sv = _num(g.group(1) if g else ""), _num(s.group(1) if s else "")
                if gv and sv:
                    out.append(dict(date=cur, session=sess, gold999=gv, silver999=sv))
    return [r for r in out if r["gold999"] and r["silver999"]]


def fetch(con, now):
    """Fetch IBJA and store the rows. Returns number of rows stored (0 if the site could not be read)."""
    try:
        req = Request(URL, headers={"User-Agent": "Mozilla/5.0 (fund-manager dashboard)"})
        html = urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
        rows = parse(html, now.tz_convert(IST).date())
    except Exception as e:
        log.warning("IBJA fetch failed: %s", e)
        return 0
    stamp = now.isoformat()
    for r in rows:
        con.execute("INSERT OR REPLACE INTO ibja VALUES (?,?,?,?,?)",
                    (str(r["date"]), r["session"], r["gold999"], r["silver999"], stamp))
    con.commit()
    return len(rows)


def _intl(store):
    """International price in rupees: gold per 10 g, silver per kg (COMEX futures x USD/INR), daily."""
    g, s, fx = store.close("GOLD"), store.close("SILVER"), store.close("USDINR")
    fx = fx.reindex(g.index.union(s.index)).ffill()
    gi = (g * fx.reindex(g.index) / OZ_G * 10).dropna()
    si = (s * fx.reindex(s.index) / OZ_G * 1000).dropna()
    return gi, si


def summary(con, store):
    """Prices for the dashboard: IBJA if we have it, else the international price in rupees."""
    df = pd.read_sql("SELECT * FROM ibja ORDER BY date, session", con)
    gi, si = _intl(store)
    intl = dict(gold=float(gi.iloc[-1]), silver=float(si.iloc[-1]), date=str(gi.index[-1].date()),
                gold_spark=[round(float(v)) for v in gi.iloc[-66:]], silver_spark=[round(float(v)) for v in si.iloc[-66:]])
    if df.empty:
        g0, s0 = float(gi.iloc[-2]), float(si.iloc[-2])
        return dict(source="intl", label="International price in ₹ (COMEX × USD/INR); excludes import duty and GST",
                    gold=intl["gold"], silver=intl["silver"], gold_chg=intl["gold"] - g0, silver_chg=intl["silver"] - s0,
                    gold_pct=(intl["gold"] / g0 - 1) * 100, silver_pct=(intl["silver"] / s0 - 1) * 100,
                    date=intl["date"], session=None, prev_date=str(gi.index[-2].date()), intl=intl, premium=None)
    last = df.iloc[-1]
    sess = last.session
    same = df[(df.session == sess) & (df.date < last.date)]
    prev = same.iloc[-1] if len(same) else (df.iloc[-2] if len(df) > 1 else last)
    hist = df[df.session == "PM"].tail(30)
    return dict(
        source="ibja", label="IBJA benchmark rate, 24 carat (999). Excludes 3% GST and making charges.",
        gold=float(last.gold999), silver=float(last.silver999),
        gold_chg=float(last.gold999 - prev.gold999), silver_chg=float(last.silver999 - prev.silver999),
        gold_pct=float((last.gold999 / prev.gold999 - 1) * 100), silver_pct=float((last.silver999 / prev.silver999 - 1) * 100),
        date=last.date, session=sess, prev_date=prev.date, prev_session=prev.session,
        gold_hist=[float(v) for v in hist.gold999], silver_hist=[float(v) for v in hist.silver999],
        intl=intl,
        # Indian price vs international price in rupees: duty, local premium/discount, timing.
        premium=dict(gold=round((float(last.gold999) / intl["gold"] - 1) * 100, 1),
                     silver=round((float(last.silver999) / intl["silver"] - 1) * 100, 1)),
    )
