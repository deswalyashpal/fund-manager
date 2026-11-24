"""Run one scheduled job:  python -m jobs.run <job> [--at "2026-11-23 08:45"] [--no-fetch]

Jobs (IST): preopen, hourly, decision, close, evening, us_close, weekly, monthly, refresh.
refresh = prices, news, gold/silver, events and the page, without new forecasts (runs in the other hours).
Each run: fetch -> quality flags -> score due forecasts -> new forecasts/decisions -> snapshot -> render site.
Re-running the same slot replaces that slot's rows (idempotent); history is never deleted.
"""
import argparse
import logging
import os
import sys
import traceback
from datetime import timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import build, db, fetch, render, validate  # noqa: E402
from src.config import CFG, DATA, IST, is_trading_day, now_ist  # noqa: E402

JOBS = ["preopen", "hourly", "decision", "close", "evening", "us_close", "weekly", "monthly", "refresh"]
REFRESH_KEEP_DAYS = 30
MARKET_DAY_JOBS = {"preopen", "hourly", "decision", "close"}
LOG = DATA / "logs"


def slot_for(job, now):
    """Latest scheduled time of this job at or before now (so a re-run lands in the same slot)."""
    if job == "refresh":  # hourly at :20; a late start belongs to the :20 before it
        n = (now - timedelta(minutes=20)).replace(minute=20, second=0, microsecond=0)
        return f"{n.date()} refresh {n.strftime('%H:%M')}"
    times = []
    for t in CFG["schedule"][job]:
        hhmm = t.split()[-1]
        times.append(hhmm)
    past = [t for t in sorted(times) if t <= now.strftime("%H:%M")]
    hhmm = past[-1] if past else now.strftime("%H:%M")
    return f"{now.date()} {job} {hhmm}"


def no_session_today(con, now):
    """After 09:45 IST, no Sensex hourly bar today = market closed (holiday not in the config list)."""
    if now.strftime("%H:%M") < "09:45":
        return False
    r = con.execute("SELECT count(*) FROM prices WHERE ticker='BSESN' AND interval='1h' AND ts_utc>=?",
                    ((pd.Timestamp(now.date()).tz_localize(IST).tz_convert("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),)).fetchone()[0]
    return r == 0


def skipped(reason):
    """Tell the cloud workflow there is nothing new to save or publish (so it keeps the current site)."""
    print(reason)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write("skipped=true\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=JOBS)
    ap.add_argument("--at", help="pretend the time is this IST time (for re-runs/tests)")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--force", action="store_true", help="run monthly even if not the first Saturday")
    a = ap.parse_args(argv)
    LOG.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG / "jobs.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    now = pd.Timestamp(a.at).tz_localize(IST) if a.at else pd.Timestamp(now_ist())
    job = a.job
    slot = slot_for(job, now)
    con = db.connect()
    india_day = now.date()
    if job == "monthly" and not a.force and not a.at and now.day > 7:
        return skipped("monthly: runs on the first Saturday of the month only (use --force)")
    if job in MARKET_DAY_JOBS and not is_trading_day(india_day):
        if job == "preopen":
            return skipped(f"{job}: {india_day} is not a trading day; nothing to do")
        logging.info("%s: %s is not a trading day -> refresh instead", job, india_day)
        job, slot = "refresh", slot_for("refresh", now)
    # GitHub's own timer often fires 30-60 min late. When the on-time trigger (cron-job.org) has already
    # done this slot, the late duplicate does nothing.
    if os.environ.get("TRIGGER") == "schedule" and con.execute("SELECT 1 FROM snapshots WHERE slot=?", (slot,)).fetchone():
        return skipped(f"{job}: slot {slot} was already done on time; skipping the late GitHub run")
    try:
        if not a.no_fetch:
            rep = fetch.update(con, now.tz_convert("UTC"), hourly=True,
                               daily_period="60d" if job == "monthly" else "10d")
            fetch.quality(con, slot, now.tz_convert("UTC"), rep)
        if job in ("hourly", "decision", "close") and no_session_today(con, now):
            logging.info("%s: no Sensex bars today -> treating %s as a market holiday; refresh instead", job, india_day)
            print(f"{job}: no Sensex trading today (holiday?); doing a refresh instead")
            job, slot = "refresh", slot_for("refresh", now)
        if job == "monthly":
            validate.run(con, backfill_days=0)
        payload = build.run(job, now, slot, con, online=not a.no_fetch)
        # Hourly refreshes are kept 30 days; main jobs, forecasts and scores are kept forever.
        cutoff = (now - timedelta(days=REFRESH_KEEP_DAYS)).isoformat()
        con.execute("DELETE FROM snapshots WHERE job='refresh' AND made_at_ist < ?", (cutoff,))
        con.commit()
        render.render_all(con, payload)
        logging.info("%s ok slot=%s forecasts=%s scored=%s", job, slot, payload["n_forecasts"], payload["n_scored"])
        print(f"{job} ok · slot {slot} · {payload['n_forecasts']} forecasts · {payload['n_scored']} scored · "
              + " | ".join(f"{c['title']}: {c['action']}" for c in payload["calls"]))
        return 0
    except Exception:
        logging.error("%s failed:\n%s", job, traceback.format_exc())
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
