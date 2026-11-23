"""Generate macOS launchd agents for the job schedule (config.yaml, IST) in this Mac's local time zone.

  python -m src.install_launchd            # write plists to launchd/ (review them)
  python -m src.install_launchd --install  # copy to ~/Library/LaunchAgents and load them
  python -m src.install_launchd --remove   # unload and delete them
"""
import argparse
import plistlib
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import CFG, IST, ROOT

LABEL = "com.globalindexmanager"
AGENTS = Path.home() / "Library/LaunchAgents"
IST_DAYS = {  # which IST weekdays (Mon=0) each job runs
    "preopen": range(0, 5), "hourly": range(0, 5), "decision": range(0, 5), "close": range(0, 5),
    "evening": range(0, 5), "us_close": range(1, 6), "weekly": [5], "monthly": [5],
}


def local_tz():
    import time
    name = Path("/etc/localtime").resolve().as_posix().split("zoneinfo/")[-1]
    return ZoneInfo(name)


def intervals(job, tz):
    out = []
    ref = datetime(2026, 11, 23)  # a Monday
    for t in CFG["schedule"][job]:
        hh, mm = map(int, t.split()[-1].split(":"))
        for wd in IST_DAYS[job]:
            d = (ref + timedelta(days=wd)).replace(hour=hh, minute=mm, tzinfo=IST).astimezone(tz)
            out.append({"Weekday": (d.weekday() + 1) % 7, "Hour": d.hour, "Minute": d.minute})
    return out


def plists():
    tz = local_tz()
    # Call the framework Python directly (the one granted Full Disk Access) instead of the venv's
    # /usr/bin/python3 shim, which macOS privacy treats as a different app. Packages come from the venv.
    py = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3.9"
    site = str(ROOT / ".venv/lib/python3.9/site-packages")
    res = {}
    for job in CFG["schedule"]:
        res[f"{LABEL}.{job}"] = {
            "Label": f"{LABEL}.{job}", "ProgramArguments": [py, str(ROOT / "jobs/run.py"), job], "WorkingDirectory": str(ROOT),
            "EnvironmentVariables": {"PYTHONPATH": f"{ROOT}:{site}", "PYTHONWARNINGS": "ignore"},
            "StartCalendarInterval": intervals(job, tz), "StandardOutPath": str(ROOT / "data/logs" / f"{job}.out"),
            "StandardErrorPath": str(ROOT / "data/logs" / f"{job}.err"), "RunAtLoad": False}
    return res, tz


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--remove", action="store_true")
    a = ap.parse_args()
    (ROOT / "data/logs").mkdir(parents=True, exist_ok=True)
    out = ROOT / "launchd"
    out.mkdir(exist_ok=True)
    ps, tz = plists()
    uid = subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()
    for label, p in ps.items():
        f = out / f"{label}.plist"
        f.write_bytes(plistlib.dumps(p))
        times = sorted({f"{['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][i['Weekday']]} {i['Hour']:02d}:{i['Minute']:02d}" for i in p["StartCalendarInterval"]})
        print(f"{label}: {len(times)} runs/week ({tz.key}); e.g. {', '.join(times[:3])}")
        dst = AGENTS / f.name
        if a.install or a.remove:
            subprocess.run(["launchctl", "bootout", f"gui/{uid}", str(dst)], capture_output=True)
            if dst.exists():
                dst.unlink()
        if a.install:
            AGENTS.mkdir(exist_ok=True)
            dst.write_bytes(f.read_bytes())
            subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(dst)], check=False)
    print("installed" if a.install else "removed" if a.remove else f"written to {out} (not installed)")


if __name__ == "__main__":
    main()
