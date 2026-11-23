"""Acceptance checks (HANDOVER 8.6) that can run offline on the seeded database."""
import shutil
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import build, db, forecast as fc  # noqa: E402
from src.config import IST, ist  # noqa: E402
from src.series import Store, align_daily, close_times, last_close_before  # noqa: E402
from src.ta_lib import indicators, score  # noqa: E402


FROZEN = ROOT / "bootstrap" / "market.db.gz"


@pytest.fixture(scope="module")
def frozen_db(tmp_path_factory):
    """Tests run on the frozen starting database (data to 23 Nov 2026 07:36 IST), never on live data."""
    import gzip
    p = tmp_path_factory.mktemp("db") / "market.db"
    with gzip.open(FROZEN, "rb") as src, open(p, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return p


@pytest.fixture(scope="module")
def store(frozen_db):
    return Store(db.connect(frozen_db))


def test_seed_indicators_match_handover(store):
    """Sensex technicals on the 20 Nov 2026 close (HANDOVER 9)."""
    I = indicators(store.ohlc("BSESN", final_only=True))
    x = I.loc["2026-11-20"]
    assert round(x.close, 2) == 83919.71
    assert round(x.st_line) == 79934
    assert round(x.rsi14, 1) == 80.8
    assert round(x.sma100) == 76522
    assert score(x) == 9


def test_no_lookahead_daily_alignment(store):
    """Every daily bar used at a decision time had closed by then."""
    days = store.close("BSESN", final_only=True).index[-400:]
    for key in ("NYA", "IXIC", "N100", "HSI", "KS11", "N225", "SSE", "GOLD", "ES"):
        a = align_daily(store, key, days, "09:10").dropna()
        dec = (a.index.normalize() + pd.Timedelta(hours=9, minutes=10)).tz_localize(IST).tz_convert("UTC")
        ct = close_times(key, pd.DatetimeIndex(a.src_date))
        assert (ct <= dec).all(), key
    # US session of the same calendar date is never available to the Indian pre-open
    a = align_daily(store, "NYA", days, "09:10")
    assert (a.src_date < a.index).all()


def test_no_lookahead_preopen_features(store):
    """Pre-open features only use hourly bars that ENDED before the decision time."""
    es = store.hourly("ES")
    t = ist("2026-11-20", "09:10")
    _, ts = last_close_before(es, t)
    assert ts + pd.Timedelta(hours=1) <= t
    R = fc.preopen_rows(store, "09:10")
    # targets are realised after the features: y1 is the first session bar (ends 10:15)
    assert R.index.max() <= pd.Timestamp("2026-11-20")


def test_preopen_validation_reproduces_research(store):
    W = fc.preopen_walkforward(fc.preopen_rows(store, "09:10"))
    s = {y: fc.hit_stats(W[y]) for y in W}
    assert s["y1"]["hit"] == 60.7 and s["y3"]["hit"] == 58.4 and s["yc"]["hit"] == 58.4
    for y in s:  # range coverage within 70-90%
        assert 70 <= s[y]["coverage"] <= 90


def test_widened_gap_range_passes(store):
    T = fc.daily_targets(store)
    assert 70 <= fc.daily_coverage(T, 1.25)["next_open"]["coverage"] <= 90


def test_job_rerun_is_idempotent(tmp_path, frozen_db):
    """Running the same slot twice leaves one set of rows."""
    p = tmp_path / "m.db"
    shutil.copy(frozen_db, p)
    con = db.connect(p)
    now = pd.Timestamp("2026-11-23 07:36").tz_localize(IST)
    for _ in range(2):
        build.run("preopen", now, "test preopen 07:36", con)
    n_f = con.execute("SELECT count(*) FROM forecasts WHERE slot='test preopen 07:36'").fetchone()[0]
    n_d = con.execute("SELECT count(*) FROM decisions WHERE slot='test preopen 07:36'").fetchone()[0]
    n_s = con.execute("SELECT count(*) FROM snapshots WHERE slot='test preopen 07:36'").fetchone()[0]
    assert (n_f, n_d, n_s) == (6, 3, 1)


def test_provisional_bars_not_used_as_closes(store):
    """Bars from sessions still open are flagged and excluded from official-close series."""
    assert store.daily("BSESN", final_only=True)["provisional"].sum() == 0
    allbars = store.daily("GOLD")
    assert allbars["provisional"].iloc[-1] in (0, 1)
    assert len(store.daily("GOLD", final_only=True)) == len(allbars) - int(allbars["provisional"].sum())


def test_full_site_renders_from_scratch(tmp_path, frozen_db):
    """A fresh build (as in the cloud) renders every page, including History pages of older snapshots."""
    from src import render
    p = tmp_path / "m.db"
    shutil.copy(frozen_db, p)
    con = db.connect(p)
    payload = build.run("evening", pd.Timestamp("2026-11-23 23:30").tz_localize(IST), "test evening 23:30", con)
    out = tmp_path / "site"
    render.render_all(con, payload, out)
    assert (out / "index.html").exists() and (out / "metals.html").exists()
    assert len(list((out / "snap").glob("*.html"))) >= 1
