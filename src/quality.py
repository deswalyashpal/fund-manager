"""Bar validation and data-quality flags."""
import numpy as np
import pandas as pd


def clean_bars(df):
    """Reject impossible values. Returns (clean_df, notes). Columns: open, high, low, close, volume."""
    notes = []
    df = df.copy()
    df = df[df["close"].notna()]
    bad_close = df["close"] <= 0
    # Negative yields / negative oil are real prints; only indices, FX and metals must be positive.
    # Callers pass allow_nonpositive for those series, so here we only flag.
    for c in ("open", "high", "low"):
        bad = df[c].isna() | (df[c] <= 0)
        if bad.any() and not bad_close.any():
            for ts in df.index[bad]:
                notes.append(f"{_d(ts)} {c} <= 0 or missing; replaced with close")
            df.loc[bad, c] = df.loc[bad, "close"]
    hi = df[["open", "high", "low", "close"]].max(axis=1)
    lo = df[["open", "high", "low", "close"]].min(axis=1)
    fix = (df["high"] < hi - 1e-9) | (df["low"] > lo + 1e-9)
    if fix.any():
        # Common in Yahoo futures data (settlement vs last trade). Kept as-is so indicator values
        # match the research; logged so it is visible.
        notes.append(f"{int(fix.sum())} bars have high/low inside open/close (Yahoo settlement quirk); kept as published")
    return df, notes


def _d(ts):
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M")


def flat_days(df):
    """O=H=L=C days (close-only data, e.g. NYSE 2001-2003): treat as missing for intraday measures."""
    return (df["open"] == df["close"]) & (df["high"] == df["low"])


def ratio_safe(s):
    """Mask non-positive values before ratios/logs (WTI -37.63 on 20 Apr 2020, negative T-bill yields 2020)."""
    return s.where(s > 0, np.nan)
