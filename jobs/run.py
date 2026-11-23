"""Run one scheduled job:  python -m jobs.run <job> [--at "2026-11-23 08:45"] [--no-fetch]

Jobs (IST): preopen, hourly, decision, close, evening, us_close, weekly, monthly.
Each run: fetch -> quality flags -> score due forecasts -> new forecasts/decisions -> snapshot -> render site.
Re-running the same slot replaces that slot's rows (idempotent); history is never deleted.
"""
import argparse
import logging
import sys
import traceback
from datetime import timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import build, db, fetch, render, validate  # noqa: E402
from src.config import CFG, DATA, IST, is_trading_day, now_ist  # noqa: E402

JOBS = ["preopen", "hourly", "decision", "close", "evening", "us_close", "weekly", "monthly"]
MARKET_DAY_JOBS = {"preopen", "hourly", "decision", "close"}
LOG = DATA / "logs"


def slot_for(job, now):
    """Latest scheduled time of this job at or before now (so a re-run lands in the same slot)."""
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
        print("monthly: runs on the first Saturday of the month only (use --force)")
        return 0
    if job in MARKET_DAY_JOBS and not is_trading_day(india_day):
        logging.info("%s skipped: %s is not a trading day", job, india_day)
        print(f"{job}: {india_day} is not a trading day; nothing to do")
        return 0
    try:
        if not a.no_fetch:
            rep = fetch.update(con, now.tz_convert("UTC"), hourly=True,
                               daily_period="60d" if job == "monthly" else "10d")
            fetch.quality(con, slot, now.tz_convert("UTC"), rep)
        if job in ("hourly", "decision") and no_session_today(con, now):
            logging.info("%s: no Sensex bars today -> treating %s as a market holiday", job, india_day)
            print(f"{job}: no Sensex trading detected today; add {india_day} to holidays in config.yaml if this is a holiday")
            return 0
        if job == "monthly":
            validate.run(con, backfill_days=0)
        payload = build.run(job, now, slot, con, online=not a.no_fetch)
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
