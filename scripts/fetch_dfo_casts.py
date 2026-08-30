"""
Fetch DFO (Institute of Ocean Sciences) CTD casts with oxygen from the CIOOS
Pacific ERDDAP and reduce them to the project's cast schema.

Source: https://data.cioospacific.ca/erddap/tabledap/IOS_CTD_Profiles
  - one row per (profile, depth bin); `profile` is the cast ID
  - DOXYZZ01 = oxygen in mL/L (project units!), DOXMZZ01 = umol/kg fallback
  - depth in metres on every sample -> near-bottom extraction is direct
  - sentinel values (-9, -99, ...) mark bad data and are filtered out

Output: data/derived/dfo_casts.csv, one row per cast, same columns as
cf_casts.csv (site_code, site_name, time, cast_depth_m, n_samples,
near_bottom_o2_ml_l, method, lat, lon) plus `source` and `o2_source`.
Casts are clustered into stations by rounding coordinates to CLUSTER_DEG
(~5 km), so repeat stations classify like CF stations do.

Chunked by year and cached under data/dfo_cache/ - rerun to resume.

Usage:
    python fetch_dfo_casts.py
    python fetch_dfo_casts.py --box 48.0 51.0 -129.0 -122.8   # expanded border
    python fetch_dfo_casts.py --date-from 1990-01-01
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

BASE = "https://data.cioospacific.ca/erddap/tabledap/IOS_CTD_Profiles.csv"
COLUMNS = ["profile", "time", "latitude", "longitude", "depth",
           "DOXYZZ01", "DOXMZZ01", "geographic_area"]

# Study border - matches the project's GEBCO grid (47.5-51.5 N, 130-122 W).
BOX = {"lat": (47.5, 51.5), "lon": (-130.0, -122.0)}

DATE_FROM = "2006-01-01"
NEAR_BOTTOM_M = 5.0      # match collect_oxygen_sites.py
CLUSTER_DEG = 0.05       # ~5 km station clustering for site codes
UMOL_KG_TO_ML_L = 1 / 43.57   # approximate, seawater density ~1025 kg/m3

CACHE_DIR = Path("data") / "dfo_cache"
OUT_PATH = Path("data") / "derived" / "dfo_casts.csv"


def year_url(year: int, box: dict) -> str:
    """Build the tabledap query for one calendar year inside the box."""
    cons = (f"&time>={year}-01-01T00:00:00Z&time<{year + 1}-01-01T00:00:00Z"
            f"&latitude>={box['lat'][0]}&latitude<={box['lat'][1]}"
            f"&longitude>={box['lon'][0]}&longitude<={box['lon'][1]}")
    return (BASE + "?" + ",".join(COLUMNS)
            + cons.replace(">=", "%3E%3D").replace("<=", "%3C%3D")
                  .replace("<", "%3C").replace(">", "%3E"))


def fetch_year(year: int, box: dict, retries: int = 3) -> pd.DataFrame:
    """One year of samples; empty frame when ERDDAP has no matching rows."""
    for attempt in range(retries):
        try:
            r = requests.get(year_url(year, box), timeout=180)
            if r.status_code == 404 and "no matching results" in r.text.lower():
                return pd.DataFrame(columns=COLUMNS)
            r.raise_for_status()
            # ERDDAP CSV: row 0 = names, row 1 = units
            return pd.read_csv(io.StringIO(r.text), skiprows=[1])
        except Exception as exc:
            wait = 5 * (attempt + 1)
            print(f"    retry {attempt + 1}/{retries} after {wait}s: "
                  f"{str(exc)[:110]}")
            time_mod.sleep(wait)
    print(f"    ! giving up on {year}")
    return pd.DataFrame(columns=COLUMNS)


def reduce_casts(raw: pd.DataFrame) -> pd.DataFrame:
    """Collapse depth-bin samples into one near-bottom row per cast."""
    df = raw.copy()
    df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    for c in ("depth", "DOXYZZ01", "DOXMZZ01", "latitude", "longitude"):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # oxygen: native mL/L preferred, umol/kg converted as fallback;
    # negatives are IOS sentinel codes, not measurements
    ml = df["DOXYZZ01"].where(df["DOXYZZ01"] >= 0)
    um = (df["DOXMZZ01"].where(df["DOXMZZ01"] >= 0)) * UMOL_KG_TO_ML_L
    df["o2_ml_l"] = ml.fillna(um)
    df["o2_source"] = np.where(ml.notna(), "mL/L",
                               np.where(um.notna(), "umol/kg_converted", ""))
    df = df.dropna(subset=["o2_ml_l", "depth", "time"])
    if df.empty:
        return pd.DataFrame()

    rows = []
    for pid, g in df.groupby("profile"):
        bottom = g["depth"].max()
        nb = g.loc[g["depth"] >= bottom - NEAR_BOTTOM_M, "o2_ml_l"]
        if nb.empty:
            continue
        lat, lon = g["latitude"].iloc[0], g["longitude"].iloc[0]
        area = str(g["geographic_area"].iloc[0] or "").strip()
        clat = round(lat / CLUSTER_DEG) * CLUSTER_DEG
        clon = round(lon / CLUSTER_DEG) * CLUSTER_DEG
        rows.append({
            "site_code": f"DFO_{clat:.2f}_{abs(clon):.2f}W",
            "site_name": (f"{area} ({clat:.2f}, {clon:.2f})" if area
                          else f"DFO station ({clat:.2f}, {clon:.2f})"),
            "time": g["time"].iloc[0],
            "cast_depth_m": bottom,
            "n_samples": int(g["o2_ml_l"].notna().sum()),
            "near_bottom_o2_ml_l": nb.mean(),
            "method": f"deepest_{NEAR_BOTTOM_M:g}m",
            "lat": lat, "lon": lon,
            "source": "DFO_IOS_CTD",
            "o2_source": g["o2_source"].mode().iloc[0],
        })
    return pd.DataFrame(rows)


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch DFO IOS CTD casts.")
    p.add_argument("--box", nargs=4, type=float, metavar=("LAT0", "LAT1", "LON0", "LON1"))
    p.add_argument("--date-from", default=DATE_FROM)
    p.add_argument("--out", type=Path, default=OUT_PATH)
    args = p.parse_args()
    box = ({"lat": (args.box[0], args.box[1]), "lon": (args.box[2], args.box[3])}
           if args.box else BOX)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    y0 = pd.Timestamp(args.date_from).year
    y1 = pd.Timestamp.now(tz="UTC").year
    frames = []
    for year in range(y0, y1 + 1):
        cache = CACHE_DIR / (f"ios_ctd_{year}_{box['lat'][0]}_{box['lat'][1]}"
                             f"_{box['lon'][0]}_{box['lon'][1]}.csv")
        if cache.exists():
            raw = pd.read_csv(cache)
            tag = "cache"
        else:
            raw = fetch_year(year, box)
            raw.to_csv(cache, index=False)   # cache empties too
            tag = "fetch"
            time_mod.sleep(1)                # be polite to the server
        print(f"[{tag}] {year}: {len(raw):>7,} samples")
        if len(raw):
            frames.append(raw)

    if not frames:
        sys.exit("no samples returned - check the box and date range")
    casts = reduce_casts(pd.concat(frames, ignore_index=True))
    casts = casts.sort_values(["site_code", "time"]).reset_index(drop=True)
    casts.to_csv(args.out, index=False)

    n_st = casts["site_code"].nunique()
    print(f"\nwrote {args.out}: {len(casts):,} casts at {n_st} stations, "
          f"{casts['time'].min().date()} -> {casts['time'].max().date()}")
    nb = casts["near_bottom_o2_ml_l"]
    print(f"near-bottom O2: median {nb.median():.2f} mL/L, "
          f"{100 * (nb < 1.4).mean():.1f}% hypoxic, "
          f"{100 * (nb < 2.8).mean():.1f}% below at-risk")
    per = casts.groupby("site_code").size()
    print(f"casts per station: median {int(per.median())}, max {per.max()} - "
          f"stations with >=3 casts: {(per >= 3).sum()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())