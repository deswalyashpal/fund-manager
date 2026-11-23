"""One-off import of the starter-kit CSVs (data to 23 Nov 2026 07:36 IST) into SQLite.

Rules agreed 23 Nov 2026:
- fx_KRWINR.csv (x100 unit flips) and fx_CNYINR.csv (1 row) are never used; cross rates = USDINR / USDxxx.
- Bars that were still in session when the kit was made (Asia rows dated 23 Nov) are stored as provisional.
"""
import pandas as pd

from . import db
from .config import ALL, SEED, close_ts_utc
from .quality import clean_bars

KIT_MADE_UTC = pd.Timestamp("2026-11-23 07:36", tz="Asia/Kolkata").tz_convert("UTC")
FETCHED = KIT_MADE_UTC.strftime("%Y-%m-%dT%H:%M:%SZ")

DAILY_FILES = {  # key -> file
    "IXIC": "IXIC.csv", "NYA": "NYA.csv", "SSE": "000001.SS.csv", "N225": "N225.csv", "N100": "N100.csv",
    "SZSE": "399001.SZ.csv", "HSI": "HSI.csv", "BSESN": "BSESN.csv", "NSEI": "NSEI.csv", "KS11": "KS11.csv",
    "VIX": "mac_VIX.csv", "INDIAVIX": "mac_INDIAVIX.csv", "US10Y": "mac_US10Y.csv", "US13W": "mac_US13W.csv",
    "WTI": "mac_WTI.csv", "BRENT": "mac_BRENT.csv", "GOLD": "mac_GOLD.csv", "SILVER": "mac_SILVER.csv",
    "COPPER": "mac_COPPER.csv", "DXY": "mac_DXY.csv", "ES": "mac_ES.csv", "NQ": "mac_NQ.csv",
    "USDINR": "mac_USDINR.csv", "USDJPY": "fx_JPY.csv", "USDCNY": "fx_CNY.csv", "USDEUR": "fx_EUR.csv",
    "USDHKD": "fx_HKD.csv", "USDKRW": "fx_KRW.csv",
}
HOURLY_FILES = {
    "IXIC": "IXIC.csv", "NYA": "NYA.csv", "SSE": "000001.SS.csv", "N225": "N225.csv", "N100": "N100.csv",
    "SZSE": "399001.SZ.csv", "HSI": "HSI.csv", "BSESN": "BSESN.csv", "NSEI": "NSEI.csv", "KS11": "KS11.csv",
    "VIX": "VIX.csv", "INDIAVIX": "INDIAVIX.csv", "US10Y": "US10Y.csv", "US13W": "US13W.csv", "WTI": "WTI.csv",
    "BRENT": "BRENT.csv", "GOLD": "GOLD.csv", "SILVER": "SILVER.csv", "COPPER": "COPPER.csv", "DXY": "DXY.csv",
    "ES": "ES.csv", "NQ": "NQ.csv", "USDINR": "USDINR.csv",
}
NONPOS_OK = {"US13W", "US10Y", "WTI", "BRENT"}


def _norm(d):
    d = d.rename(columns=str.lower)
    if "open" not in d:  # close-only FX files
        for c in ("open", "high", "low"):
            d[c] = d["close"]
    if "volume" not in d:
        d["volume"] = None
    return d[["open", "high", "low", "close", "volume"]].astype(float)


def run(con=None):
    con = con or db.connect()
    report = []
    for key, fn in DAILY_FILES.items():
        d = pd.read_csv(SEED / fn, index_col=0, parse_dates=True)
        d = _norm(d)
        if key in NONPOS_OK:
            notes = ["non-positive prints kept (real); masked in ratio maths"] if (d.close <= 0).any() else []
        else:
            d, notes = clean_bars(d)
        prov = pd.Series({ts: int(close_ts_utc(key, ts) > KIT_MADE_UTC) for ts in d.index[-3:]})
        n = db.upsert_prices(con, key, "1d", d, "yahoo (starter kit)", FETCHED, prov)
        pv = [ts.strftime("%d %b") for ts, v in prov.items() if v]
        report.append((key, "1d", n, str(d.index[0].date()), str(d.index[-1].date()),
                       "; ".join(notes + ([f"provisional: {', '.join(pv)}"] if pv else []))))
        for nt in notes + ([f"provisional bar {', '.join(pv)} (market was open when kit was made)"] if pv else []):
            con.execute("INSERT OR REPLACE INTO quality VALUES (?,?,?,?,?,?)",
                        ("seed", f"{key}:1d", str(d.index[-1].date()), 0, None, nt))
    for key, fn in HOURLY_FILES.items():
        d = pd.read_csv(SEED / "hourly" / fn, index_col=0)
        d.index = pd.to_datetime(d.index, utc=True)
        d = _norm(d)
        if key not in NONPOS_OK:
            d, _ = clean_bars(d)
        # the last bar of a session that was still running is provisional
        prov = pd.Series({ts: int(ts + pd.Timedelta(hours=1) > KIT_MADE_UTC) for ts in d.index[-2:]})
        n = db.upsert_prices(con, key, "1h", d, "yahoo (starter kit)", FETCHED, prov)
        report.append((key, "1h", n, str(d.index[0]), str(d.index[-1]), ""))
    con.commit()
    return report


if __name__ == "__main__":
    for r in run():
        print(*r, sep=" | ")
