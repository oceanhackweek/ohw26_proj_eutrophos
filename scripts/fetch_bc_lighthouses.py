"""
Fetch BC lightstation daily sea-surface temperature and salinity (BCSOP)
from the CIOOS Pacific ERDDAP -> data/derived/bc_lighthouses_daily.csv.

These are century-scale daily bucket samples at ~12 shore stations (BCSOP,
running since 1914). No oxygen - use them as MODEL FEATURES: a cold, salty
anomaly at Amphitrite Point is upwelling arriving at the outer coast.

The dataset's exact column names are discovered at runtime from the ERDDAP
info service (they are less standardized than the IOS profile datasets), and
the chosen mapping is printed so you can verify it. Default box is all of
coastal BC - lighthouses outside the study box are still useful features.

Usage:
    python fetch_bc_lighthouses.py
    python fetch_bc_lighthouses.py --box 47.5 51.0 -129.0 -122.8
"""

from __future__ import annotations

import argparse
import io
import sys
import time as time_mod
from pathlib import Path

import pandas as pd
import requests

SERVER = "https://data.cioospacific.ca/erddap"
DATASET = "BCSOP_daily"
BOX = {"lat": (47.5, 55.0), "lon": (-134.0, -122.0)}   # all coastal BC
DECADES = range(1930, 2031, 10)

OUT_PATH = Path("data") / "derived" / "bc_lighthouses_daily.csv"
CACHE_DIR = Path("data") / "lh_cache"


def discover_columns() -> dict:
    """Read the dataset's variable list and pick the columns we need."""
    url = f"{SERVER}/info/{DATASET}/index.csv"
    info = pd.read_csv(io.StringIO(requests.get(url, timeout=60).text))
    variables = info.loc[info["Row Type"] == "variable", "Variable Name"].tolist()

    def pick(*needles, exclude=()):
        for v in variables:
            lv = v.lower()
            if any(n in lv for n in needles) and not any(x in lv for x in exclude):
                return v
        return None

    cols = {
        "time": "time" if "time" in variables else pick("time"),
        "lat": pick("latitude") or "latitude",
        "lon": pick("longitude") or "longitude",
        "station": (pick("station", "site", "lightstation",
                         exclude=("lat", "lon", "time"))
                    or pick("name", exclude=("lat", "lon", "time"))
                    or pick("profile")),   # IOS datasets: station lives in 'profile'
        "temp": pick("temp"),
        "sal": pick("sal"),
    }
    missing = [k for k, v in cols.items() if v is None]
    if missing:
        sys.exit(f"could not identify columns {missing} in {variables} - "
                 f"check {SERVER}/tabledap/{DATASET}.html and hardcode them")
    print("column mapping:", cols)
    return cols


def fetch_decade(cols: dict, y0: int, box: dict) -> pd.DataFrame:
    want = [cols["station"], cols["time"], cols["lat"], cols["lon"],
            cols["temp"], cols["sal"]]
    cons = (f"&{cols['time']}>={y0}-01-01T00:00:00Z"
            f"&{cols['time']}<{y0 + 10}-01-01T00:00:00Z"
            f"&{cols['lat']}>={box['lat'][0]}&{cols['lat']}<={box['lat'][1]}"
            f"&{cols['lon']}>={box['lon'][0]}&{cols['lon']}<={box['lon'][1]}")
    url = (f"{SERVER}/tabledap/{DATASET}.csv?" + ",".join(want)
           + cons.replace(">=", "%3E%3D").replace("<=", "%3C%3D")
                 .replace("<", "%3C").replace(">", "%3E"))
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=300)
            if r.status_code == 404 and "no matching" in r.text.lower():
                return pd.DataFrame(columns=want)
            r.raise_for_status()
            return pd.read_csv(io.StringIO(r.text), skiprows=[1])
        except Exception as exc:
            print(f"    retry {attempt + 1}/3: {str(exc)[:100]}")
            time_mod.sleep(5 * (attempt + 1))
    print(f"    ! giving up on {y0}s")
    return pd.DataFrame(columns=want)


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch BC lighthouse daily SST/S.")
    p.add_argument("--box", nargs=4, type=float,
                   metavar=("LAT0", "LAT1", "LON0", "LON1"))
    p.add_argument("--out", type=Path, default=OUT_PATH)
    args = p.parse_args()
    box = ({"lat": (args.box[0], args.box[1]),
            "lon": (args.box[2], args.box[3])} if args.box else BOX)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cols = discover_columns()

    frames = []
    for y0 in DECADES:
        cache = CACHE_DIR / f"bcsop_{y0}s.csv"
        if cache.exists():
            df = pd.read_csv(cache)
            tag = "cache"
        else:
            df = fetch_decade(cols, y0, box)
            df.to_csv(cache, index=False)
            tag = "fetch"
            time_mod.sleep(1)
        print(f"[{tag}] {y0}s: {len(df):>8,} station-days")
        if len(df):
            frames.append(df)
    if not frames:
        sys.exit("no lighthouse data returned")

    df = pd.concat(frames, ignore_index=True)
    df = df.rename(columns={cols["station"]: "station", cols["time"]: "date",
                            cols["lat"]: "lat", cols["lon"]: "lon",
                            cols["temp"]: "sst_c", cols["sal"]: "salinity_psu"})
    df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce").dt.floor("D")
    for c in ("sst_c", "salinity_psu", "lat", "lon"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # 99.9-style missing codes and physical nonsense -> NaN
    df["sst_c"] = df["sst_c"].where(df["sst_c"].between(-3, 35))
    df["salinity_psu"] = df["salinity_psu"].where(df["salinity_psu"].between(0, 40))
    df = df.dropna(subset=["date"]).sort_values(["station", "date"])
    df.to_csv(args.out, index=False)

    print(f"\nwrote {args.out}: {len(df):,} station-days, "
          f"{df['station'].nunique()} stations")
    s = (df.dropna(subset=["sst_c"]).groupby("station")
           .agg(first=("date", "min"), last=("date", "max"),
                days=("date", "nunique")))
    print(s.to_string())
    print("\nNOTE: check the 'last' dates above - if the ERDDAP copy lags, the "
          "up-to-date per-station CSVs are on open.canada.ca (BCSOP dataset).")
    return 0


if __name__ == "__main__":
    sys.exit(main())