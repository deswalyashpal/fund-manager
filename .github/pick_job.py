"""Decide which job a run should do. Manual runs may name a job; "auto" (the default for manual runs)
and "timer" (the hourly cron-job.org trigger and GitHub's hourly backup) pick it from the India time.

Hourly triggers fire at :20 past each hour IST. A late start is mapped back to the :20 it belongs to,
so a GitHub run that starts at 11:05 is treated as the 10:20 run (and then skips if that is done).
"""
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
CRON = {
    "50 * * * *": "timer",      # every hour at :20 IST
    "40 3 * * 1-5": "preopen",  # 09:10 IST, Mon-Fri
}


def nominal(now):
    """The :20 trigger time this run belongs to."""
    t = now - timedelta(minutes=20)
    return t.replace(minute=20, second=0, microsecond=0)


def auto(now, manual=False):
    # A person pressing "Run workflow" before the open wants the morning forecast.
    if manual and now.weekday() < 5 and "07:00" <= now.strftime("%H:%M") < "09:15":
        return "preopen"
    n = nominal(now)
    wd, h = n.weekday(), n.hour  # Mon=0 ... Sun=6
    if wd < 5 and 10 <= h <= 15:
        return "hourly"
    if wd < 5 and h == 16:
        return "close"
    if 1 <= wd <= 5 and h == 3:
        return "us_close"            # after the US close (Mon-Fri US sessions)
    if wd == 5 and h == 10:
        return "weekly"
    if wd == 5 and h == 11 and n.day <= 7:
        return "monthly"
    return "refresh"                 # prices, news, gold/silver, events and the page; no new forecasts


if __name__ == "__main__":
    schedule, manual = (sys.argv + ["", ""])[1:3]
    job = manual or CRON.get(schedule, "timer")
    if job in ("auto", "timer"):  # "timer" = the hourly cron-job.org trigger; "auto" = a person pressing Run
        job = auto(datetime.now(IST), manual=(job == "auto"))
    print("job=" + job)
