"""Top news and event expectations, using free sources and fixed rules (no AI, no paid API).

News: headlines from free RSS feeds. Each headline is matched to a topic (RBI, Fed, crude, rupee ...)
by keywords, its direction is read from words such as "rises"/"falls", and the impact line comes from a
fixed table of how that topic usually moves the Sensex, the global pool and gold/silver. Stock tips and
single-company stories are dropped. The top 10 are ranked by topic importance, how many outlets carry
the story, and how recent it is.

Events: what the market expects.
- Fed: the 3-month US Treasury bill yield (already fetched) vs the Fed's target range (FRED, free).
- RBI / India CPI: what recent headlines (Google News) say, counted by keyword.
The "if as expected / if a surprise" lines are typical past reactions, not forecasts.
"""
import logging
import re
from datetime import timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import pandas as pd

from .config import CFG, IST

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (fund-manager dashboard)"}
NCFG = CFG.get("news") or {}

S, P, M = "Sensex", "Global pool", "Gold & silver"

# Stories that are about one stock, tips or corporate actions: not useful for index-fund decisions.
NOISE = re.compile(
    r"should (investors|you) buy|buy, sell or hold|stocks? to (buy|watch)|target price|multibagger|penny stock|"
    r"\bIPO\b|GMP|record date|bonus issue|dividend|stock split|upper circuit|lower circuit|results? (preview|review)|"
    r"Q[1-4] (results|earnings|profit)|net profit|order win|bags? order|shares? (surge|jump|zoom|soar|rally|rise|fall|slump|crash|tank)s?\b.*\d+%|"
    r"block deal|stake sale|brokerage|mutual fund nav|SIP calculator|horoscope|quiz|podcast|live updates?:?$", re.I)

UP = re.compile(r"\b(rise[sn]?|rose|gain(s|ed)?|jump(s|ed)?|surge[sd]?|rall(y|ies|ied)|climb(s|ed)?|higher|record high|"
                r"soar(s|ed)?|rebound(s|ed)?|advance[sd]?|strengthen(s|ed)?|up \d|beat(s)?|hotter|accelerat\w*|spike[sd]?)\b", re.I)
DOWN = re.compile(r"\b(fall(s|en)?|fell|drop(s|ped)?|slide[s]?|slid|slump(s|ed)?|plunge[sd]?|tumble[sd]?|lower|sink(s)?|sank|"
                  r"crash(es|ed)?|sell-?off|down \d|weak(er|ens|ened)?|slip(s|ped)?|retreat(s|ed)?|record low|cool(s|ed|ing)|"
                  r"ease[sd]?|eases|miss(es|ed)?|contract(s|ed)?|slow(s|ed|down)?)\b", re.I)

# Topic rules: pattern, weight (importance), affected areas, and impact text by direction.
# Effects are (area, +1 up / -1 down) for the headline moving "up"; reversed for "down".
TOPICS = [
    dict(key="rbi", name="RBI / India rates", w=10, pat=r"\bRBI\b|repo rate|\bMPC\b|monetary policy committee",
         areas=[S], cut=re.compile(r"\bcut|ease|easing|dovish", re.I), hike=re.compile(r"\bhike|tighten|hawkish|rais(e|es|ing) (repo|rates|interest)", re.I),
         txt={"cut": ("A rate cut (or hint of one) usually helps Indian shares, especially banks and rate-sensitive sectors.", [(S, 1)]),
              "hike": ("Higher Indian rates usually weigh on the Sensex.", [(S, -1)]),
              "none": ("RBI news can move bank stocks and the rupee; watch the tone on future cuts.", [(S, 0)])}),
    dict(key="fed", name="US Fed / rates", w=9, pat=r"\bFed\b|Federal Reserve|\bFOMC\b|Powell|Treasury yield|bond yield",
         areas=[P, S, M], cut=re.compile(r"\brate cuts?\b|\bcuts? rates|dovish|yields? (fall|drop|slide|ease|slip|retreat)", re.I),
         hike=re.compile(r"\bhikes?\b|hawkish|higher for longer|rais(e|es|ing) rates|yields? (rise|jump|climb|surge|edge higher|rise)", re.I),
         txt={"cut": ("Lower US rates usually lift global shares and gold, and help foreign money flow into India.", [(P, 1), (M, 1), (S, 1)]),
              "hike": ("Higher US rates usually weigh on global shares and gold, and can pull foreign money out of India.", [(P, -1), (M, -1), (S, -1)]),
              "none": ("Fed news moves global shares, gold and the rupee; direction depends on what is said about cuts.", [(P, 0), (M, 0)])}),
    dict(key="infl", name="Inflation", w=8, pat=r"\bCPI\b|inflation|\bWPI\b|price index", areas=[S, M],
         txt={"up": ("Higher inflation lowers the chance of rate cuts: usually negative for shares.", [(S, -1), (P, -1)]),
              "down": ("Cooler inflation raises the chance of rate cuts: usually positive for shares.", [(S, 1), (P, 1)]),
              "none": ("Inflation data shapes rate-cut expectations in India and the US.", [(S, 0)])}),
    dict(key="crude", name="Crude oil", w=8, pat=r"\bcrude\b|oil price|\bBrent\b|\bOPEC", areas=[S],
         txt={"up": ("Costlier oil raises India's import bill and inflation: usually negative for the Sensex and the rupee.", [(S, -1)]),
              "down": ("Cheaper oil helps India's import bill and inflation: usually positive for the Sensex and the rupee.", [(S, 1)]),
              "none": ("India imports most of its oil, so oil prices matter for the Sensex and the rupee.", [(S, 0)])}),
    dict(key="geo", name="Trade & geopolitics", w=7,
         pat=r"tariff|trade (deal|war|talks)|sanction|\bwar\b|ceasefire|missile|attack|Ukraine|Russia|Israel|Iran|Middle East|Taiwan",
         areas=[S, P, M], risk_on=re.compile(r"deal|ceasefire|truce|talks progress|agreement|eases?|lift(s|ed)? sanction", re.I),
         txt={"on": ("Easing tension is usually good for shares; gold often gives back some safe-haven gains.", [(S, 1), (P, 1), (M, -1)]),
              "off": ("Rising tension usually hits shares and supports gold as a safe haven.", [(S, -1), (P, -1), (M, 1)]),
              "none": ("Trade and geopolitical news can swing global shares and gold.", [(P, 0), (M, 0)])}),
    dict(key="rupee", name="Rupee", w=7, pat=r"\brupee\b|\bINR\b|forex reserves", areas=[S, P, M],
         txt={"up": ("A stronger rupee lowers the rupee value of the global pool and gold, and tends to attract foreign money to India.", [(S, 1), (P, -1), (M, -1)]),
              "down": ("A weaker rupee raises the rupee value of the global pool and gold, but can push foreign money out of Indian shares.", [(S, -1), (P, 1), (M, 1)]),
              "none": ("The rupee changes what your foreign funds and gold are worth in rupees.", [(P, 0)])}),
    dict(key="flows", name="Foreign investors", w=7, pat=r"\bFIIs?\b|\bFPIs?\b|foreign (investors|funds|portfolio)|\bDIIs?\b", areas=[S],
         buy=re.compile(r"\bbuy|inflow|pour|net buyers?", re.I), sell=re.compile(r"\bsell|outflow|pull out|exit|dump|net sellers?", re.I),
         txt={"buy": ("Foreign buying usually supports the Sensex.", [(S, 1)]),
              "sell": ("Foreign selling usually weighs on the Sensex.", [(S, -1)]),
              "none": ("Foreign money flows are a big driver of the Sensex.", [(S, 0)])}),
    dict(key="metal", name="Gold & silver", w=7, pat=r"\bgold\b|\bsilver\b|bullion", areas=[M],
         txt={"up": ("Gold/silver moving up: positive for your metals holding.", [(M, 1)]),
              "down": ("Gold/silver moving down: negative for your metals holding.", [(M, -1)]),
              "none": ("News that can move gold and silver prices.", [(M, 0)])}),
    dict(key="growth", name="Economy", w=6, pat=r"\bGDP\b|\bPMI\b|\bIIP\b|recession|jobs report|payrolls|unemployment|economic growth",
         areas=[S, P],
         txt={"up": ("Stronger growth usually supports company profits and shares.", [(S, 1), (P, 1)]),
              "down": ("Weaker growth usually weighs on shares (though it can bring rate cuts sooner).", [(S, -1), (P, -1)]),
              "none": ("Growth data shapes the outlook for company profits.", [(S, 0)])}),
    dict(key="india_mkt", name="Indian market", w=6, pat=r"\bSensex\b|\bNifty\b|Dalal Street|Indian (stocks|shares|equities|markets?)",
         areas=[S],
         txt={"up": ("The Indian market is rising; the forecasts and exit line already use this price.", [(S, 1)]),
              "down": ("The Indian market is falling; check the Sensex exit line.", [(S, -1)]),
              "none": ("Indian market update.", [(S, 0)])}),
    dict(key="global_mkt", name="Global markets", w=6,
         pat=r"Wall Street|S&P 500|Nasdaq|\bDow\b|Nikkei|Hang Seng|Kospi|Shanghai|China stocks|European (stocks|shares)|global (stocks|markets)|Nvidia|tech (stocks|selloff|sell-off)",
         areas=[P],
         txt={"up": ("Global shares rising: positive for the global pool.", [(P, 1)]),
              "down": ("Global shares falling: negative for the global pool; the 100-day rule handles big falls.", [(P, -1)]),
              "none": ("Global market news relevant to the pool.", [(P, 0)])}),
]
for t in TOPICS:
    t["rx"] = re.compile(t["pat"], re.I)


def _get(url, timeout=15):
    return urlopen(Request(url, headers=UA), timeout=timeout).read().decode("utf-8", errors="ignore")


def _clean(s):
    s = re.sub(r"<!\[CDATA\[|\]\]>", "", s or "")
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def parse_rss(xml, source):
    out = []
    for it in re.findall(r"<item\b.*?</item>", xml, flags=re.S):
        t = re.search(r"<title[^>]*>(.*?)</title>", it, re.S)
        l = re.search(r"<link[^>]*>(.*?)</link>", it, re.S)
        d = re.search(r"<pubDate[^>]*>(.*?)</pubDate>", it, re.S)
        if not t:
            continue
        title, src = _clean(t.group(1)), source
        if source == "Google News" and " - " in title:  # "Headline - Publisher"
            title, src = title.rsplit(" - ", 1)
        try:
            ts = parsedate_to_datetime(_clean(d.group(1))).astimezone(timezone.utc) if d else None
        except Exception:
            ts = None
        link = _clean(l.group(1)) if l else ""
        if title and link.startswith("http"):
            out.append(dict(title=title, link=link, source=src, ts=ts))
    return out


def _direction(topic, title):
    if topic["key"] in ("rbi", "fed"):
        if topic["cut"].search(title):
            return "cut"
        if topic["hike"].search(title):
            return "hike"
        return "none"
    if topic["key"] == "geo":
        return "on" if topic["risk_on"].search(title) else "off"
    if topic["key"] == "flows":
        if topic["sell"].search(title):
            return "sell"
        if topic["buy"].search(title):
            return "buy"
        return "none"
    # Direction word must come soon after the subject (e.g. "gold prices fall", not "gold imports fall").
    m = topic["rx"].search(title)
    tail = title[m.end():m.end() + 45] if m else ""
    if topic["key"] in ("metal", "crude") and re.match(r"\s*(imports?|exports?|demand|ETF|loans?|jewellery)\b", tail, re.I):
        return "none"
    u, d = UP.search(tail), DOWN.search(tail)
    if u and d:
        return "up" if u.start() < d.start() else "down"
    return "up" if u else "down" if d else "none"


def _tokens(s):
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 3}


def classify(item):
    """Attach topic, direction, effects and impact text; None if the headline isn't useful."""
    title = item["title"]
    if NOISE.search(title):
        return None
    hits = [t for t in TOPICS if t["rx"].search(title)]
    if not hits:
        return None
    t = max(hits, key=lambda x: x["w"])
    d = _direction(t, title)
    text, eff = t["txt"].get(d, t["txt"]["none"])
    return dict(item, topic=t["key"], topic_name=t["name"], weight=t["w"], direction=d, impact=text,
                effects=[dict(area=a, dir=v) for a, v in eff])


def rank(items, now_utc, max_items=10, max_age_h=36):
    """Dedupe similar headlines, score, keep the top N with at most 3 per topic."""
    fresh = [x for x in items if x["ts"] is None or (now_utc - x["ts"]).total_seconds() <= max_age_h * 3600]
    clusters = []
    for x in sorted(fresh, key=lambda x: -(x["ts"].timestamp() if x["ts"] else 0)):
        tk = _tokens(x["title"])
        for c in clusters:
            j = len(tk & c["tk"]) / max(1, len(tk | c["tk"]))
            if j >= 0.45:
                c["n"] += 1
                c["sources"].add(x["source"])
                break
        else:
            clusters.append(dict(x, tk=tk, n=1, sources={x["source"]}))
    for c in clusters:
        age_h = (now_utc - c["ts"]).total_seconds() / 3600 if c["ts"] else max_age_h
        c["score"] = c["weight"] + 1.5 * (len(c["sources"]) - 1) - age_h / 6
    out, per = [], {}
    for c in sorted(clusters, key=lambda c: -c["score"]):
        if per.get(c["topic"], 0) >= 3:
            continue
        per[c["topic"]] = per.get(c["topic"], 0) + 1
        out.append(dict(title=c["title"], link=c["link"], source=c["source"], n_sources=len(c["sources"]),
                        when=c["ts"].astimezone(IST).isoformat() if c["ts"] else None, topic=c["topic"],
                        topic_name=c["topic_name"], direction=c["direction"], impact=c["impact"], effects=c["effects"]))
        if len(out) >= max_items:
            break
    return out


def top_news(now_utc):
    items, ok = [], []
    for f in NCFG.get("feeds", []):
        try:
            got = parse_rss(_get(f["url"]), f["name"])
            items += got
            ok.append(f["name"])
        except Exception as e:
            log.warning("news feed %s failed: %s", f["name"], e)
    tagged = [c for c in (classify(x) for x in items) if c]
    return dict(items=rank(tagged, now_utc, NCFG.get("max_items", 10), NCFG.get("max_age_hours", 36)),
                feeds_ok=ok, n_seen=len(items))


# ----------------------------------------------------------------------------- events
TYPICAL = {
    "rbi": dict(
        expected="Usually little lasting move; markets react to the tone about future cuts.",
        surprise=[("Surprise cut", [(S, 1)], "Banks and rate-sensitive shares usually rally; rupee may dip."),
                  ("Surprise hike or hawkish hold", [(S, -1)], "Indian shares usually fall, led by banks.")]),
    "fed": dict(
        expected="Usually little lasting move if the decision matches the 3-month bill.",
        surprise=[("More dovish (cut / signals cuts)", [(P, 1), (M, 1), (S, 1)], "Global shares and gold usually rise; rupee firms."),
                  ("More hawkish (no cut / fewer cuts)", [(P, -1), (M, -1), (S, -1)], "Global shares and gold usually fall; foreign money can leave India.")]),
    "cpi_in": dict(
        expected="Usually little effect if close to forecasts.",
        surprise=[("Lower than expected", [(S, 1)], "Raises hopes of RBI cuts: usually positive for Indian shares."),
                  ("Higher than expected", [(S, -1)], "Lowers hopes of RBI cuts: usually negative for Indian shares.")]),
}


def _fred(series):
    """Latest (date, value) of a FRED series. FRED stalls Python's HTTP client, so use curl (on Mac and GitHub)."""
    import subprocess
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    try:
        txt = subprocess.run(["curl", "-s", "--max-time", "25", url], capture_output=True, text=True, timeout=30).stdout
        if "," not in txt:
            raise ValueError("empty FRED reply")
    except Exception:
        txt = _get(url, timeout=15)
    rows = [r.split(",") for r in txt.strip().splitlines()[1:]]
    vals = [(d, float(v)) for d, v in rows if v not in ("", ".")]
    return vals[-1]


def _headline_lean(kind, now_utc):
    """Count recent headlines saying cut / hold / hike (RBI) or quoting a number (CPI)."""
    q = (NCFG.get("event_queries") or {}).get(kind)
    if not q:
        return None
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}+when:14d&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        items = parse_rss(_get(url), "Google News")
    except Exception as e:
        log.warning("event headlines %s failed: %s", kind, e)
        return None
    items = [x for x in items if x["ts"] and (now_utc - x["ts"]).days <= 14]
    if kind == "cpi_in":
        nums, ex = [], None
        for x in items:
            if re.search(r"CPI|inflation", x["title"], re.I) and re.search(r"seen|expected|likely|forecast|poll|estimate|project", x["title"], re.I):
                m = re.findall(r"(\d+(?:\.\d+)?)\s?(?:%|per ?cent)", x["title"])
                if m:
                    nums += [float(v) for v in m if float(v) < 15]
                    ex = ex or x
        if not nums:
            return dict(text="No forecast number in recent headlines.", n=len(items), example=None)
        med = sorted(nums)[len(nums) // 2]
        return dict(text=f"Recent headlines point to inflation of about {med:g}%.", n=len(nums),
                    example=dict(title=ex["title"], link=ex["link"], source=ex["source"]))
    pats = {"cut": r"\brate cuts?\b|\bcuts? (repo|rates|interest)|reduce rates|easing cycle",
            "hold": r"\bhold|pause|unchanged|status quo|keep rates|on hold|steady",
            "hike": r"\bhikes?\b|rais(e|es|ing) (repo|rates|interest)|tighten|rate increase|repo rate (rising|increase)"}
    cnt = {k: [x for x in items if re.search(p, x["title"], re.I)] for k, p in pats.items()}
    tot = sum(len(v) for v in cnt.values())
    if tot < 3:
        return dict(text="Too few recent headlines to read an expectation.", n=tot, example=None)
    top = max(cnt, key=lambda k: len(cnt[k]))
    share = len(cnt[top]) / tot
    word = {"cut": "a rate cut", "hold": "no change in rates", "hike": "a rate hike"}[top]
    # Best example: a headline that states an expectation, not just mentions the word.
    ex = next((x for x in cnt[top] if re.search(r"expect|likely|may |seen|poll|economists|forecast|odds|bets?|projects?|appears", x["title"], re.I)),
              cnt[top][0])
    if share < 0.6:
        txt = f"Headlines are split; slightly more mention {word} ({len(cnt[top])} of {tot})."
    else:
        txt = f"Most recent headlines expect {word} ({len(cnt[top])} of {tot})."
    # A quoted probability, e.g. "December hike odds jump to 97%".
    odds = None
    for x in items:
        m = re.search(r"(odds|probabilit\w*|chance|bets?)\D{0,30}?(\d{1,3})\s?%", x["title"], re.I)
        if m and 0 < int(m.group(2)) <= 100:
            odds = dict(pct=int(m.group(2)), title=x["title"], link=x["link"], source=x["source"])
            break
    return dict(text=txt, lean=top, n=tot, odds=odds,
                example=dict(title=ex["title"], link=ex["link"], source=ex["source"]))


def event_views(events, store, now_utc, con=None):
    out = []
    for e in events:
        kind = e.get("kind")
        v = dict(e)
        v["typical"] = TYPICAL.get(kind)
        exp = None
        if kind == "fed":
            try:
                try:
                    (d_hi, hi), (_, lo) = _fred("DFEDTARU"), _fred("DFEDTARL")
                    if con is not None:
                        con.execute("INSERT OR REPLACE INTO kv VALUES ('fed_range', ?, ?)", (f"{lo},{hi},{d_hi}", now_utc.isoformat()))
                        con.commit()
                except Exception:
                    row = con.execute("SELECT value FROM kv WHERE key='fed_range'").fetchone() if con is not None else None
                    if not row:
                        raise
                    lo, hi, d_hi = row[0].split(",")
                    lo, hi = float(lo), float(hi)
                bill = float(store.close("US13W").iloc[-1])
                mid = (lo + hi) / 2
                gap = bill - (mid - 0.10)  # bills usually trade ~0.1 below the middle of the range
                lean = "a cut" if gap < -0.15 else "a hike" if gap > 0.15 else "no change"
                exp = dict(text=f"Market pricing leans to {lean}: 3-month US T-bill {bill:.2f}% vs Fed range {lo:.2f}–{hi:.2f}%.",
                           basis="market", detail=f"Fed range from FRED as of {d_hi}.")
            except Exception as ex:
                log.warning("fed expectation failed: %s", ex)
            hl = _headline_lean("fed", now_utc)
            if hl:
                v["headlines"] = hl
                if exp is None:
                    exp = dict(text=hl["text"], basis="headlines", example=hl.get("example"))
        elif kind in ("rbi", "cpi_in"):
            hl = _headline_lean(kind, now_utc)
            if hl:
                exp = dict(text=hl["text"], basis="headlines", example=hl.get("example"))
                v["headlines"] = hl
        v["expect"] = exp or dict(text="No reliable expectation available right now.", basis="none")
        out.append(v)
    return out
