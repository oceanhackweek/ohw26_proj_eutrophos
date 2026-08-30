"""
Fetch DFO moored CTD/oxygen time series (E01, A1, and every other mooring in
the box) from the CIOOS Pacific ERDDAP, reduced to DAILY means.

Source: https://data.cioospacific.ca/erddap/tabledap/IOS_CTD_Moorings
  - fixed-depth sensors reporting minutes-scale samples for years
  - DOXYZZ01 = oxygen mL/L (project units), DOXMZZ01 = umol/kg fallback
  - negative values are IOS sentinel codes, filtered out

The heavy lifting happens server-side: each year is requested with
orderByMean("latitude/0.01,longitude/0.01,depth/5,time/1day"), so ERDDAP
returns daily means per position/sensor-depth bin instead of raw samples.
If the server rejects that (older ERDDAP), the year is fetched raw and
reduced locally.

Output: data/derived/dfo_moorings_daily.csv - one row per (mooring cluster,
sensor depth bin, day) with oxygen (default: oxygen rows only; --keep-ts to
also keep temperature/salinity-only rows), plus a deepest_at_site flag so
the training table can grab the near-bottom sensor with one filter.

Usage:
    python fetch_dfo_moorings.py
    python fetch_dfo_moorings.py --box 48.0 51.0 -129.0 -122.8
"""

from __future__ import annotations

import argparse
import io
import sys
import time as time_mod
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE = "https://data.cioospacific.ca/erddap/tabledap/IOS_CTD_Moorings.csv"
COLS = ["time", "latitude", "longitude", "depth",
        "DOXYZZ01", "DOXMZZ01", "TEMPS901", "PSALST01"]
MEAN = 'orderByMean("latitude/0.01,longitude/0.01,depth/5,time/1day")'

# Study border - matches the project's GEBCO grid (47.5-51.5 N, 130-122 W).
# E01 and any Scott Islands moorings are naturally inside now.
BOX = {"lat": (47.5, 51.5), "lon": (-130.0, -122.0)}
DATE_FROM = "2006-01-01"
CLUSTER_DEG = 0.05
UMOL_KG_TO_ML_L = 1 / 43.57

CACHE_DIR = Path("data") / "mooring_cache"
OUT_PATH = Path("data") / "derived" / "dfo_moorings_daily.csv"


def _quote(s: str) -> str:
    return (s.replace('"', "%22").replace(">=", "%3E%3D")
             .replace("<=", "%3C%3D").replace("<", "%3C")
             .replace(">", "%3E").replace("(", "%28").replace(")", "%29")
             .replace("/", "%2F"))


def year_url(year: int, box: dict, server_mean: bool) -> str:
    cons = (f"&time>={year}-01-01T00:00:00Z&time<{year + 1}-01-01T00:00:00Z"
            f"&latitude>={box['lat'][0]}&latitude<={box['lat'][1]}"
            f"&longitude>={box['lon'][0]}&longitude<={box['lon'][1]}")
    if server_mean:
        cons += "&" + MEAN
    return BASE + "?" + ",".join(COLS) + _quote(cons)


def fetch_year(year: int, box: dict) -> pd.DataFrame:
    """Daily means for one year; tries server-side reduction first."""
    for server_mean in (True, False):
        for attempt in range(3):
            try:
                r = requests.get(year_url(year, box, server_mean), timeout=300)
                if r.status_code == 404 and "no matching" in r.text.lower():
                    return pd.DataFrame(columns=COLS)
                r.raise_for_status()
                df = pd.read_csv(io.StringIO(r.text), skiprows=[1])
                return df if server_mean else reduce_raw(df)
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else 0
                if server_mean and code in (400, 500):
                    print(f"    orderByMean rejected for {year}; "
                          f"falling back to raw fetch")
                    break  # try raw path
                time_mod.sleep(5 * (attempt + 1))
            except Exception as exc:
                print(f"    retry {attempt + 1}/3: {str(exc)[:100]}")
                time_mod.sleep(5 * (attempt + 1))
    print(f"    ! giving up on {year}")
    return pd.DataFrame(columns=COLS)


def reduce_raw(df: pd.DataFrame) -> pd.DataFrame:
    """Client-side fallback: daily means per rounded position/depth bin."""
    if df.empty:
        return df
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    for c in COLS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["latitude"] = (df["latitude"] / 0.01).round() * 0.01
    df["longitude"] = (df["longitude"] / 0.01).round() * 0.01
    df["depth"] = (df["depth"] / 5).round() * 5
    df["time"] = df["time"].dt.floor("D")
    return (df.groupby(["latitude", "longitude", "depth", "time"],
                       as_index=False)[["DOXYZZ01", "DOXMZZ01",
                                        "TEMPS901", "PSALST01"]].mean())


def tidy(df: pd.DataFrame, keep_ts: bool) -> pd.DataFrame:
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    for c in ("latitude", "longitude", "depth",
              "DOXYZZ01", "DOXMZZ01", "TEMPS901", "PSALST01"):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Some ERDDAP builds return orderByMean grouping variables as BIN INDICES
    # (value / divisor) instead of rounded values: latitude 4931 = 49.31/0.01.
    # Detect and rescale so cached files from such servers heal on re-derive.
    if df["latitude"].abs().median() > 90:
        df["latitude"] *= 0.01
        df["longitude"] *= 0.01
        df["depth"] *= 5
        print("  note: server returned bin indices for lat/lon/depth - rescaled")

    ml = df["DOXYZZ01"].where(df["DOXYZZ01"] >= 0)
    um = df["DOXMZZ01"].where(df["DOXMZZ01"] >= 0) * UMOL_KG_TO_ML_L
    df["oxygen_ml_l"] = ml.fillna(um)
    df["temperature_c"] = df["TEMPS901"].where(df["TEMPS901"] > -5)
    df["salinity_psu"] = df["PSALST01"].where(df["PSALST01"] >= 0)
    if not keep_ts:
        df = df.dropna(subset=["oxygen_ml_l"])

    clat = (df["latitude"] / CLUSTER_DEG).round() * CLUSTER_DEG
    clon = (df["longitude"] / CLUSTER_DEG).round() * CLUSTER_DEG
    df["site_code"] = [f"DFOM_{a:.2f}_{abs(b):.2f}W" for a, b in zip(clat, clon)]
    df["date"] = df["time"].dt.floor("D")

    out = (df.groupby(["site_code", "date", "depth"], as_index=False)
             .agg(oxygen_ml_l=("oxygen_ml_l", "mean"),
                  temperature_c=("temperature_c", "mean"),
                  salinity_psu=("salinity_psu", "mean"),
                  lat=("latitude", "mean"), lon=("longitude", "mean")))
    out = out.rename(columns={"depth": "sensor_depth_m"})
    deepest = out.groupby("site_code")["sensor_depth_m"].transform("max")
    out["deepest_at_site"] = out["sensor_depth_m"] == deepest
    out["source"] = "DFO_IOS_MOORING"
    return out.sort_values(["site_code", "sensor_depth_m", "date"])


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch DFO moored oxygen series.")
    p.add_argument("--box", nargs=4, type=float,
                   metavar=("LAT0", "LAT1", "LON0", "LON1"))
    p.add_argument("--date-from", default=DATE_FROM)
    p.add_argument("--keep-ts", action="store_true",
                   help="also keep rows that have only temperature/salinity")
    p.add_argument("--out", type=Path, default=OUT_PATH)
    args = p.parse_args()
    box = ({"lat": (args.box[0], args.box[1]),
            "lon": (args.box[2], args.box[3])} if args.box else BOX)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    frames = []
    y0 = pd.Timestamp(args.date_from).year
    y1 = pd.Timestamp.now(tz="UTC").year
    for year in range(y0, y1 + 1):
        cache = CACHE_DIR / (f"moorings_{year}_{box['lat'][0]}_{box['lat'][1]}"
                             f"_{box['lon'][0]}_{box['lon'][1]}.csv")
        if cache.exists():
            df = pd.read_csv(cache)
            tag = "cache"
        else:
            df = fetch_year(year, box)
            df.to_csv(cache, index=False)
            tag = "fetch"
            time_mod.sleep(1)
        print(f"[{tag}] {year}: {len(df):>8,} daily bins")
        if len(df):
            frames.append(df)

    if not frames:
        sys.exit("no mooring data returned - check the box and dates")
    out = tidy(pd.concat(frames, ignore_index=True), args.keep_ts)
    out.to_csv(args.out, index=False)

    print(f"\nwrote {args.out}: {len(out):,} rows, "
          f"{out['site_code'].nunique()} mooring clusters")
    nb = out[out["deepest_at_site"] & out["oxygen_ml_l"].notna()]
    if len(nb):
        s = (nb.groupby("site_code")
               .agg(days=("date", "nunique"), depth=("sensor_depth_m", "max"),
                    o2_med=("oxygen_ml_l", "median"),
                    pct_hypox=("oxygen_ml_l", lambda x: 100 * (x < 1.4).mean()))
               .sort_values("days", ascending=False).round(2))
        print("\ndeepest-sensor oxygen by mooring (top 12 by coverage):")
        print(s.head(12).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())