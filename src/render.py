"""Render snapshot payloads to static HTML (site/). Charts are inline SVG, no external chart library."""
import json
import re
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader

from . import db
from .config import CFG, IST, SITE
from .score import LABELS

NAV = [("index.html", "Today"), ("sensex.html", "Sensex"), ("pool.html", "Global pool"), ("metals.html", "Gold & silver"),
       ("history.html", "History & help")]
# Old page names keep working (bookmarks, links shared earlier).
REDIRECTS = {"forecasts.html": "sensex.html", "accuracy.html": "sensex.html#track", "world.html": "pool.html",
             "guide.html": "history.html#help"}
METHODS = {"preopen_ridge": "Pre-open model", "insession_ridge": "In-session model", "volscaled": "Volatility range",
           "gap_es": "After the US close (futures)"}
AREAS = {"sensex_fund": "Sensex fund", "pool": "Pool", "metals": "Metals"}


def _env():
    e = Environment(loader=FileSystemLoader(str(Path(__file__).parent / "templates")), autoescape=True)
    e.tests["bold"] = lambda s: "**" in str(s)
    e.filters.update(
        n0=lambda v: "—" if v is None else f"{v:,.0f}", n1=lambda v: f"{v:,.1f}", n2=lambda v: f"{v:,.2f}",
        sgn0=lambda v: f"{v:+,.0f}", sgn1=lambda v: f"{v:+.1f}", sgn2=lambda v: f"{v:+.2f}",
        when=_when, day=_day, md=_md, spark=_spark,
        nf=lambda v, dp=0: f"{v:,.{dp}f}", nfs=lambda v, dp=0: f"{v:+,.{dp}f}",
        dd=lambda s: pd.Timestamp(s).strftime("%-d"), mon=lambda s: pd.Timestamp(s).strftime("%b"))
    return e


def _spark(vals, w=120, h=30):
    """Tiny line chart for price tiles."""
    vals = [v for v in (vals or []) if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    pts = " ".join(f"{i * w / (len(vals) - 1):.1f},{h - 2 - (v - lo) / rng * (h - 4):.1f}" for i, v in enumerate(vals))
    col = "var(--gr)" if vals[-1] >= vals[0] else "var(--rd)"
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>')


def _fc_close(p):
    f = next((f for f in p.get("forecasts", []) if f["horizon"] == "close"), None)
    return dict(point=f["point"], low=f["low"], high=f["high"]) if f else None


def _when(s):
    if not s:
        return "—"
    t = pd.Timestamp(s)
    t = t.tz_convert(IST) if t.tzinfo else t
    return t.strftime("%a %-d %b, %H:%M")


def _day(s):
    return pd.Timestamp(s).strftime("%a %-d %b %Y") if s else "—"


def _md(s):
    from markupsafe import Markup, escape
    return Markup(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", str(escape(s))))


# ------------------------------------------------------------------ svg helpers
def _poly(points):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def line_chart(series, w=1000, h=260, bands=None, marks=None, ref=None, ylab=""):
    """series: list of (label, [(i, value)], css_color, dashed). bands: [(i, lo, hi, outside)]."""
    vals = [v for _, pts, _, _ in series for _, v in pts if v is not None]
    if bands:
        vals += [b[1] for b in bands] + [b[2] for b in bands]
    if ref is not None:
        vals.append(ref)
    if not vals:
        return ""
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    n = max(i for _, pts, _, _ in series for i, _ in pts) + 1 if series else 1
    L, R, T, B = 60, 10, 10, 26
    X = lambda i: L + (w - L - R) * (i + 0.5) / max(n, 1)
    Y = lambda v: T + (h - T - B) * (1 - (v - lo) / (hi - lo))
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{ylab}">']
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        out.append(f'<line x1="{L}" x2="{w-R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="var(--line)"/>'
                   f'<text x="{L-6}" y="{Y(v)+4:.1f}" text-anchor="end">{v:,.0f}</text>')
    if ref is not None:
        out.append(f'<line x1="{L}" x2="{w-R}" y1="{Y(ref):.1f}" y2="{Y(ref):.1f}" stroke="var(--mag)" stroke-dasharray="4 4"/>')
    bw = (w - L - R) / max(n, 1) * 0.5
    for i, a, b, outside in (bands or []):
        out.append(f'<rect x="{X(i)-bw/2:.1f}" y="{Y(b):.1f}" width="{bw:.1f}" height="{max(Y(a)-Y(b),1):.1f}" rx="2" fill="var(--rngc)"/>')
    for lab, pts, col, dash in series:
        pts = [(i, v) for i, v in pts if v is not None]
        if len(pts) > 1:
            da = 'stroke-dasharray="5 4"' if dash else ""
            out.append(f'<polyline fill="none" stroke="{col}" stroke-width="2" {da} points="{_poly([(X(i), Y(v)) for i, v in pts])}"/>')
        for i, v in pts:
            out.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3.5" fill="{col}"><title>{lab}: {v:,.0f}</title></circle>')
    for i, v, col, title in (marks or []):
        out.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="6" fill="none" stroke="{col}" stroke-width="2.5"><title>{title}</title></circle>')
    return "\n".join(out) + "</svg>"


def dev_bars(rows, w=1000, h=150):
    if not rows:
        return ""
    vals = [r["miss_pts"] for r in rows]
    m = max(abs(v) for v in vals) or 1
    L, R, T, B = 60, 10, 18, 26
    n = len(rows)
    X = lambda i: L + (w - L - R) * i / n
    Y0 = T + (h - T - B) / 2
    sc = (h - T - B) / 2 / m
    bw = (w - L - R) / n * 0.7
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Miss in points per day">',
           f'<text x="{L}" y="12">Miss in points (actual − predicted)</text>',
           f'<line x1="{L}" x2="{w-R}" y1="{Y0}" y2="{Y0}" stroke="var(--mute)"/>']
    for i, r in enumerate(rows):
        v = r["miss_pts"]
        y = Y0 - max(v, 0) * sc
        col = "var(--rd)" if not r["in_range"] else "var(--blue)"
        out.append(f'<rect x="{X(i)+bw*0.2:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{abs(v)*sc:.1f}" fill="{col}"><title>{_when(r["target_ts_ist"])}: {v:+,.0f}</title></rect>')
        if i % max(1, n // 10) == 0:
            out.append(f'<text x="{X(i)+bw*0.7:.1f}" y="{h-8}" text-anchor="middle">{pd.Timestamp(r["target_ts_ist"]).strftime("%-d %b")}</text>')
    return "\n".join(out) + "</svg>"


def gap_gauge(sensex, world, w=600, h=70):
    X = lambda v: 20 + (w - 40) * v / 10
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:600px" role="img" aria-label="Trend scores, Sensex vs world">'
            f'<rect x="20" y="28" width="{w-40}" height="10" rx="5" fill="var(--line)"/>'
            + "".join(f'<text x="{X(k):.0f}" y="62" text-anchor="middle">{k}</text>' for k in range(0, 11, 2))
            + f'<circle cx="{X(world):.0f}" cy="33" r="9" fill="var(--rd)"/><text x="{X(world):.0f}" y="16" text-anchor="middle">World {world}</text>'
            + f'<circle cx="{X(sensex):.0f}" cy="33" r="9" fill="var(--gr)"/><text x="{X(sensex):.0f}" y="16" text-anchor="middle">Sensex {sensex:.0f}</text></svg>')


# ------------------------------------------------------------------ page context
def headline(p):
    c = {x["area"]: x for x in p["calls"]}
    dv = p["divergence"]
    sx, pl, mt = c["sensex_fund"], c["pool"], c["metals"]
    weak = sum(1 for r in p["world"] if r["key"] not in ("BSESN", "NSEI") and r["score"] <= 3)
    cl = (p.get("sx_chart") or {}).get("closes") or []
    d1 = (cl[-1] / cl[-2] - 1) * 100 if len(cl) > 1 else 0
    if abs(d1) >= 1 and dv["sensex"] >= 7:
        h = f"Sensex {'fell' if d1 < 0 else 'rose'} {abs(d1):.1f}% today, but its uptrend holds"
    elif abs(d1) >= 1 and dv["sensex"] <= 3:
        h = f"Sensex {'fell' if d1 < 0 else 'rose'} {abs(d1):.1f}% today; the trend is down"
    elif dv["sensex"] >= 8 and weak >= 6:
        h = ("India is still rising while all 8 other big markets fall" if weak == 8
             else f"India is still rising while {weak} of the 8 other big markets fall")
    elif dv["sensex"] <= 3:
        h = "India has turned down"
    else:
        h = f"Sensex trend {int(dv['sensex'])}/10, world {dv['world']}/10"
    h += f" — {sx['action'].lower()} the Sensex fund, pool {pl['action'].lower()}, metals {mt['action'].lower()}"
    lede = (f"Sensex closed at {p['sensex']['close']:,.0f} on {pd.Timestamp(p['sensex']['date']).strftime('%a %-d %b')}; "
            f"the pool is {p['pool']['invested']}% invested.")
    return h, lede


def banners(p):
    """Only problems with the data itself get a banner; market news goes in the alert chips."""
    b = []
    if p["quality"]["stale"]:
        b.append(dict(kind="stale", text="<b>Stale data:</b> no new data for " + ", ".join(p["quality"]["stale"]) + ". Last good values are shown."))
    return b


def fc_scale(p):
    vals = [p["sensex"]["close"]] + [v for f in p["forecasts"] for v in (f["low"], f["high"])]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.05 or 100
    return lo - pad, hi + pad


def accuracy_sections(con):
    df = pd.read_sql("""SELECT f.*, o.actual, o.miss_pts, o.miss_pct, o.dir_ok, o.in_range FROM forecasts f
                        JOIN outcomes o ON o.forecast_id=f.id ORDER BY f.target_ts_ist""", con)
    out = []
    for (h, m), g in df.groupby(["horizon", "method"], sort=False):
        g = g.sort_values("made_at_ist").groupby("target_ts_ist").tail(1).sort_values("target_ts_ist").tail(30)
        rows = g.to_dict("records")
        idx = list(range(len(rows)))
        svg = line_chart([("Predicted", list(zip(idx, g.point)), "var(--blue)", True),
                          ("Actual", list(zip(idx, g.actual)), "var(--ink)", False)],
                         bands=[(i, r["low"], r["high"], not r["in_range"]) for i, r in enumerate(rows)],
                         marks=[(i, r["actual"], "var(--rd)", "outside range") for i, r in enumerate(rows) if not r["in_range"]],
                         ylab=f"{LABELS.get(h, h)} predicted vs actual")
        out.append(dict(title=f"{LABELS.get(h, h)} — {METHODS.get(m, m)}", svg=svg, dev_svg=dev_bars(rows), rows=rows, key=(h, m)))
    order = {"close": 0, "h1": 1, "h3": 2, "next_open": 3, "week_close": 4, "next_week_open": 5}
    return sorted(out, key=lambda s: (order.get(s["key"][0], 9), s["key"][1] != "preopen_ridge"))


def trust_table(val):
    from .decide import trust_for, TRUST
    spec = [("pre_y1", "First hour (from pre-open)"), ("pre_y3", "First 3 hours (from pre-open)"), ("pre_yc", "Day's close (from pre-open)"),
            ("in_y1", "Next 1 hour (during session)"), ("in_y3", "Next 3 hours (during session)"), ("in_ycl", "Day's close (during session)"),
            ("gap_es", "Next day's open (after the US close)"), ("next_open", "Next day's open (volatility range)"),
            ("week_close", "Week-end close"), ("next_week_open", "Next week's open")]
    out = []
    for k, name in spec:
        st = val.get(k)
        if not st:
            continue
        lvl = trust_for(st)
        cov = f"{st['coverage']}%" + (f" (was {st['coverage_unwidened']}% before widening ×1.25)" if st.get("coverage_unwidened") else "")
        out.append(dict(name=name, n=f"{st['n']:,}", period=st.get("period", ""), hit=f"{st['hit']}%" if st.get("hit") is not None else "—",
                        base=f"{st['base']}%" if st.get("base") is not None else "—", cov=cov, level=lvl, label=TRUST[lvl][0]))
    return out


def path_svg(con, p):
    today = pd.Timestamp(p["made_at"]).tz_convert(IST).date()
    h = db.load_prices(con, "BSESN", "1h")
    if h.empty:
        return ""
    h.index = h.index.tz_convert(IST)
    t = h[h.index.date == today]
    if t.empty:
        return '<p class="mute">No trading yet today.</p>'
    pts = [(i, v) for i, v in enumerate(t.close)]
    pre = [f for f in p["forecasts"] if f["method"] == "preopen_ridge"]
    marks = []
    slots = {"10:15": 0, "12:15": 2, "15:30": 6}
    for f in pre:
        k = pd.Timestamp(f["target"]).strftime("%H:%M")
        if k in slots:
            marks.append((slots[k], f["point"], "var(--blue)", f"{f['label']} forecast {f['point']:,.0f}"))
    return line_chart([("Sensex", pts, "var(--ink)", False)], marks=marks, ref=p["sensex"]["close"], ylab="Sensex today")


def ratio_svg(p):
    s = p["metals"]["ratio"]["series"]
    pts = [(i, v) for i, (_, v) in enumerate(s)]
    return line_chart([("Gold/silver ratio", pts, "var(--blue)", False)], ref=85, h=220, ylab="Gold/silver ratio, last 3 years") + \
        '<p class="small mute">Last 3 years, weekly points. Dashed line = 85.</p>'


def decision_log(con):
    df = pd.read_sql("SELECT * FROM decisions ORDER BY made_at_ist", con)
    out = []
    for a, g in df.groupby("area"):
        g = g[g.action != g.action.shift(1)]
        for r in g.to_dict("records"):
            r["area_name"] = AREAS.get(a, a)
            out.append(r)
    return sorted(out, key=lambda r: r["made_at_ist"], reverse=True)[:60]


# ------------------------------------------------------------------ investor-facing summaries
def alerts(p):
    """Short chips for moves that matter right now (kind: warn / good / info; area for colour)."""
    out = []
    c = {x["area"]: x for x in p["calls"]}
    for x in p["calls"]:
        if x.get("changed"):
            out.append(dict(kind="warn", area=_AREA_KEY[x["area"]], text=f"{x['title']} changed to {x['action']} (was {x['prev_action']})"))
    sx = p["sensex"]
    ex = c["sensex_fund"].get("exit_line")
    if ex:
        gap = (sx["close"] / ex - 1) * 100
        if gap < 0:
            out.append(dict(kind="warn", area="sensex", text=f"Sensex closed below its exit line ({ex:,.0f})"))
        elif gap < 3:
            out.append(dict(kind="warn", area="sensex", text=f"Sensex only {gap:.1f}% above its exit line ({ex:,.0f})"))
    for t in p.get("pulse") or []:
        lim = {"BSESN": 1.5, "NSEI": 99, "INDIAVIX": 10, "USDINR": 0.8, "BRENT": 3, "gold": 1.5, "silver": 2.5}.get(t["key"], 2)
        if abs(t["pct"]) >= lim:
            unit = "₹" if t["key"] in ("gold", "silver") else ""
            out.append(dict(kind="info", area=t["area"], text=f"{t['name']} {'▲' if t['chg'] > 0 else '▼'} {abs(t['pct']):.1f}% "
                            f"({unit}{t['chg']:+,.{t['dp']}f})"))
    vix = next((t for t in p.get("pulse") or [] if t["key"] == "INDIAVIX"), None)
    if vix and vix["value"] >= 20:
        out.append(dict(kind="warn", area="sensex", text=f"India VIX {vix['value']:.0f}: fear is high (dip-ladder level at 25)"))
    for e in p.get("event_views") or p.get("events") or []:
        if e["days"] <= 3:
            out.append(dict(kind="warn", area="sensex", text=f"{e['name'].split(' (')[0]} in {e['days']} day{'s' if e['days'] != 1 else ''}: no lump sums"))
    if p["regime"]["warning"]:
        s = p["regime"]["stats"]
        out.append(dict(kind="info", area="sensex", text=f"Forecast direction right only {s['hit']} of the last {s['n']} days: trust the ranges"))
    return out


_AREA_KEY = {"sensex_fund": "sensex", "pool": "pool", "metals": "metals"}


def visit_state(p):
    """What the 'since your last visit' script compares between visits on the same device."""
    return dict(t=p["made_at"], calls={_AREA_KEY[c["area"]]: c["action"] for c in p["calls"]},
                news=[n["link"] for n in (p.get("news") or {}).get("items", [])],
                px={t["key"]: [t["name"], t["value"], t["dp"]] for t in p.get("pulse") or []})


def chart_data(con):
    """Series for the interactive charts on the Sensex, pool and metals pages (last ~2 years, daily)."""
    from .india_metals import OZ_G
    from .series import Store
    from .ta_lib import indicators
    store = Store(con)
    out = {}
    I = indicators(store.ohlc("BSESN", final_only=True)).iloc[-520:]
    out["sensex"] = dict(dates=[str(d.date()) for d in I.index], close=_r(I.close), ma100=_r(I.sma100), exit=_r(I.st_line))
    from .pool import inr_daily
    P = inr_daily(store).ffill().iloc[-620:]
    ma = P.rolling(100).mean()
    P, ma = P.iloc[-520:], ma.iloc[-520:]
    from .config import MARKETS
    out["pool"] = dict(dates=[str(d.date()) for d in P.index],
                       markets={MARKETS[k]["name"]: dict(inr=_r(P[k]), ma=_r(ma[k])) for k in P.columns})
    g, sv, fx = store.close("GOLD"), store.close("SILVER"), store.close("USDINR")
    df = pd.DataFrame({"g": g, "s": sv}).dropna()
    fx = fx.reindex(df.index.union(fx.index)).ffill().reindex(df.index)
    df = df.iloc[-520:]
    fx = fx.iloc[-520:]
    gm, sm = indicators(store.ohlc("GOLD")), indicators(store.ohlc("SILVER"))
    out["metals"] = dict(dates=[str(d.date()) for d in df.index],
                         gold=_r(df.g * fx / OZ_G * 10), silver=_r(df.s * fx / OZ_G * 1000), ratio=_r(df.g / df.s, 2),
                         gold_usd=_r(df.g, 1), silver_usd=_r(df.s, 2),
                         gold_buy=_r(gm.st_line.reindex(df.index), 1), silver_buy=_r(sm.st_line.reindex(df.index), 2))
    return out


def _r(s, dp=0):
    return [None if pd.isna(v) else round(float(v), dp) for v in s]


def track_record(con):
    """Recent day's-close forecasts vs what happened, for the Sensex page chart."""
    secs = accuracy_sections(con)
    sec = next((x for x in secs if x["key"] == ("close", "preopen_ridge")), secs[0] if secs else None)
    if not sec:
        return None
    rows = sec["rows"][-30:]
    return dict(title=sec["title"], dates=[str(pd.Timestamp(r["target_ts_ist"]).date()) for r in rows],
                point=_r([r["point"] for r in rows]), low=_r([r["low"] for r in rows]), high=_r([r["high"] for r in rows]),
                actual=_r([r["actual"] for r in rows]), inside=sum(1 for r in rows if r["in_range"]),
                right=sum(1 for r in rows if r["dir_ok"]), n=len(rows))


# ------------------------------------------------------------------ render
def render_all(con, p, out_dir=None):
    out = Path(out_dir or SITE)
    (out / "snap").mkdir(parents=True, exist_ok=True)
    env = _env()
    stamp = f"Updated {_when(p['made_at'])} IST · Indian data to the {_day(p['data_through']['india'])} close"
    fc_made = max((f["made"] for f in p["forecasts"]), default=None)
    base = dict(p=p, nav=NAV, stamp=stamp, banners=banners(p), root="", methods=METHODS, live=True)
    h, lede = headline(p)
    sig = {r["key"]: r for r in p["signals"]}
    cd = chart_data(con)
    log = decision_log(con)
    al = alerts(p)
    pages = {
        "index.html": dict(title="Today", headline=h, lede=lede, scale=fc_scale(p), fc_made=fc_made, sig=sig, fc_close=_fc_close(p),
                           alerts=al, visit=visit_state(p)),
        "sensex.html": dict(title="Sensex", scale=fc_scale(p), fc_close=_fc_close(p), chart=cd["sensex"], track=track_record(con),
                            trust_table=trust_table(p["validation"]), alerts=[a for a in al if a["area"] == "sensex"]),
        "pool.html": dict(title="Global pool", maxw=max([r["base"] for r in p["pool"]["rows"]] + [20]), chart=cd["pool"],
                          bt=p["validation"].get("pool_backtest", {}), log=[r for r in log if r["area"] == "pool"],
                          world={r["market"]: r for r in p["world"]}, alerts=[a for a in al if a["area"] == "pool"]),
        "metals.html": dict(title="Gold & silver", chart=cd["metals"], log=[r for r in log if r["area"] == "metals"],
                            alerts=[a for a in al if a["area"] == "metals"]),
    }
    for name, ctx in pages.items():
        (out / name).write_text(env.get_template(name).render(**base, page=name, **ctx))
    for old, new in REDIRECTS.items():
        (out / old).write_text(f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={new}">'
                               f'<link rel="canonical" href="{new}"><title>Moved</title><a href="{new}">This page has moved.</a>')
    # snapshot copy of the summary exactly as it was
    snap_id = con.execute("SELECT id FROM snapshots WHERE slot=?", (p["slot"],)).fetchone()[0]
    render_snap(env, out, p, snap_id)
    render_history(con, env, out, base)
    (out / "version.json").write_text(json.dumps({"version": f"{p['slot']}|{p['made_at']}", "made_at": p["made_at"], "slot": p["slot"]}))
    (out / ".nojekyll").write_text("")


def render_snap(env, out, q, sid):
    (out / "snap").mkdir(parents=True, exist_ok=True)
    h, lede = headline(q)
    stamp = f"Snapshot of {_when(q['made_at'])} IST · Indian data to the {_day(q['data_through']['india'])} close"
    html = env.get_template("index.html").render(
        p=q, nav=NAV, stamp=stamp, banners=banners(q), root="../", methods=METHODS, live=False, page="",
        title=f"Snapshot {q['slot']}", headline=h, lede=lede, scale=fc_scale(q),
        fc_made=max((f["made"] for f in q["forecasts"]), default=None), sig={r["key"]: r for r in q["signals"]}, fc_close=_fc_close(q),
        alerts=alerts(q), visit=None)
    (out / "snap" / f"{sid}.html").write_text(html)


def render_history(con, env, out, base):
    days = CFG.get("history_days_shown", 7)
    cutoff = (pd.Timestamp.now(tz=IST) - pd.Timedelta(days=days)).isoformat()
    rows = con.execute("SELECT id, slot, made_at_ist, job, payload_json FROM snapshots WHERE made_at_ist>=? ORDER BY made_at_ist DESC", (cutoff,)).fetchall()
    snaps = []
    for sid, slot, made, job, pj in rows:
        q = json.loads(pj)
        close = next((f for f in q["forecasts"] if f["horizon"] == "close"), None)
        calls = q["calls"]
        snaps.append(dict(id=sid, when=_when(made), date=str(pd.Timestamp(made).date()), job=job, calls=calls,
                          changed=[str(i) for i, c in enumerate(calls) if c.get("changed")],
                          close_fc=close["point"] if close else None,
                          close_rng=f"{close['low']:,.0f}–{close['high']:,.0f}" if close else "—",
                          text=" ".join([_when(made), job] + [c["action"] for c in calls])))
        if not (out / "snap" / f"{sid}.html").exists():  # e.g. a fresh cloud checkout: rebuild from the stored payload
            render_snap(env, out, q, sid)
    html = env.get_template("history.html").render(**base, page="history.html", title="History & help", snaps=snaps, days=days,
                                                   dates=sorted({s["date"] for s in snaps}, reverse=True), jobs=sorted({s["job"] for s in snaps}))
    (out / "history.html").write_text(html)
