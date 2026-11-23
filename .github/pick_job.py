"""Map the cron line that fired (UTC) to the job name. Manual runs pass the job directly."""
import sys

CRON = {
    "15 3 * * 1-5": "preopen", "40 3 * * 1-5": "preopen",
    "50 4-9 * * 1-5": "hourly",
    "10 9 * * 1-5": "decision",
    "45 10 * * 1-5": "close",
    "0 18 * * 1-5": "evening",
    "30 21 * * 1-5": "us_close",
    "30 4 * * 6": "weekly",
    "30 5 * * 6": "monthly",
}


def auto(now):
    """Manual run with 'auto': the job that fits the current India time."""
    hm = now.strftime("%H:%M")
    if now.weekday() >= 5:
        return "weekly"
    if hm < "09:15":
        return "preopen"
    if hm < "15:30":
        return "hourly"
    if hm < "18:00":
        return "close"
    return "evening"


schedule, manual = (sys.argv + ["", ""])[1:3]
if manual == "auto":
    from datetime import datetime, timedelta, timezone
    manual = auto(datetime.now(timezone(timedelta(hours=5, minutes=30))))
print("job=" + (manual or CRON.get(schedule, "")))
