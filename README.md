# Global Index Fund Manager

A website that updates itself on a schedule and gives three plain-language calls: the Sensex index fund, a 9-market global pool in INR, and gold & silver. It also shows Sensex forecasts with honest ranges and checks every forecast against what actually happened.


## Open the site
```bash
cd global-index-manager/site && python3 -m http.server 8765
```
Then open http://127.0.0.1:8765. You can also double-click `site/index.html`.

## Run a job by hand
```bash
cd global-index-manager && .venv/bin/python -m jobs.run preopen
```
The available jobs are `preopen`, `hourly`, `decision`, `close`, `evening`, `us_close`, `weekly` and `monthly`. Monthly needs `--force` when it isn't the first Saturday of the month.

Each run does the following, in order:
1. Fetches data from Yahoo Finance.
2. Flags data quality.
3. Scores any forecasts that are now due.
4. Makes new forecasts and decisions.
5. Saves a snapshot and re-renders `site/`.

Re-running the same time slot replaces that slot's results instead of duplicating them.

## Where it runs: GitHub (cloud)
The jobs run on GitHub's servers through `.github/workflows/jobs.yml`, so the Mac can be off. Each run takes these steps:
1. Restores the database from the `state` release; the very first run uses `bootstrap/market.db.gz`.
2. Runs the job.
3. Saves the database back to the `state` release.
4. Publishes `site/` to GitHub Pages.

Every open page checks `version.json` once a minute and reloads itself when a newer update is published.

| Job | IST (Mon–Fri) | UTC cron |
|---|---|---|
| preopen | 08:45, 09:10 | 03:15, 03:40 |
| hourly | 10:20 … 15:20 | 04:50 … 09:50 |
| decision (3 pm cut-off) | 14:40 | 09:10 |
| close | 16:15 | 10:45 |
| evening | 23:30 | 18:00 |
| us_close | 03:00 (Tue–Sat) | 21:30 (Mon–Fri) |
| weekly | Sat 10:00 | Sat 04:30 |
| monthly | 1st Sat 11:00 | Sat 05:30 |

- **Late starts:** GitHub can start scheduled runs 5–30 minutes late. Each run still files under its scheduled slot.
- **Manual runs:** open the repository's **Actions** tab, choose "Fund manager jobs", click "Run workflow" and pick a job.
- **Keeping the schedule alive:** the weekly job commits a heartbeat file, because GitHub pauses schedules after 60 days without commits.

### Optional: running on a Mac instead
`python -m src.install_launchd --install` installs the same schedule as macOS launchd agents (`--remove` removes them). If the project is inside Documents, the framework Python needs Full Disk Access.

## Tests
```bash
cd global-index-manager && .venv/bin/python -m pytest -q tests
```
The tests check that:
- the indicators reproduce the handover values;
- no forecast uses data from after its decision time;
- the pre-open validation reproduces the handover (60.7 / 58.4 / 58.4%);
- the widened next-day-open range holds between 70% and 90% of the time;
- re-running a job slot is idempotent;
- live (unfinished) bars are excluded from official closes.

## Layout
- `config.yaml`: every ticker, threshold, event date, holiday and schedule time.
- `src/`:
  - `seed.py`: imports the starter-kit data.
  - `fetch.py`: live data and quality flags.
  - `series.py`: INR conversion and the IST clock rule.
  - `ta_lib.py`: indicators and the trend score.
  - `forecast.py`: the six horizons.
  - `score.py`: actual vs predicted.
  - `pool.py`, `metals.py`, `world.py` and `signals.py`: the pool, metals, world board and signals.
  - `decide.py`: the playbook and trust labels.
  - `validate.py`: the monthly walk-forward validation.
  - `build.py`: assembles each snapshot.
  - `render.py` and `templates/`: the website.
- `data/market.db`: SQLite. All history is kept; the site shows the last 7 days.
- `data/validation.json`: the latest validation results, which set the trust labels.

## Known limits
- **Trading holidays:** `holidays:` in `config.yaml` is empty. NSE and BSE block automated requests, so paste the official list there. In the meantime, the hourly and decision jobs detect a day with no session.
- **No second price source:** BSE and NSE refuse automated requests, and Stooq requires a browser check. The Sensex close is cross-checked only against Yahoo's own hourly data.
- **Python 3.9:** the Mac's built-in version is used; the handover suggested 3.11+. Everything works on 3.9.
