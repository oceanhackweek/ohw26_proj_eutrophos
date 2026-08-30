"""
Fetch OOI Coastal Endurance (Washington line) dissolved-oxygen time series
from the OOI Data Explorer ERDDAP, reduced to daily means per depth bin.

Sites (same upwelling system as WCVI, just south of the border):
  CE06ISSM  inshore surface mooring (~29 m water)
  CE07SHSM  shelf surface mooring (~87 m)     <- the E01 analogue
  CE09OSSM  offshore surface mooring (~542 m)
  CE09OSPM  offshore wire-following profiler (35-510 m)

OOI dataset IDs carry instrument-instance suffixes that cannot be guessed,
so this script DISCOVERS them: it searches the server for each site +
"oxygen", keeps dissolved-oxygen instrument classes (dosta/dofst), reads
each dataset's variable names and units from the info service, and converts
oxygen to mL/L whatever the native unit. The chosen datasets and unit
conversions are printed - verify them on the first run.

Output: data/derived/ooi_oxygen_daily.csv with the same shape as
dfo_moorings_daily.csv (site_code, date, sensor_depth_m, oxygen_ml_l,
lat, lon, deepest_at_site, source). These sites are OUTSIDE the study box
and outside BC: they are model training signal, not map content.

Usage:  python fetch_ooi_oxygen.py
"""

from __future__ import annotations

import io
import re
import sys
import time as time_mod
from pathlib import Path

import numpy as np
import pandas as pd
import requests

SERVER = "https://erddap.dataexplorer.oceanobservatories.org/erddap"
SITES = ["CE06ISSM", "CE07SHSM", "CE09OSSM", "CE09OSPM"]
SITE_DEPTHS = {"CE06ISSM": 29, "CE07SHSM": 87, "CE09OSSM": 542}  # water depth, m
DO_CLASSES = ("dosta", "dofst")          # OOI dissolved-oxygen instruments
DATE_FROM = "2014-01-01"
DEPTH_BIN_M = 10

CACHE_DIR = Path("data") / "ooi_cache"
OUT_PATH = Path("data") / "derived" / "ooi_oxygen_daily.csv"

# conversion to mL/L from whatever spelling the dataset uses
def unit_factor(unit: str) -> float | None:
    """Factor converting a native oxygen unit to mL/L; None if unrecognized."""
    u = (str(unit).lower().replace("\u00b5", "u").replace("micro", "u")
         .replace("milli", "m").replace("moles", "mol").replace("mole", "mol")
         .replace("grams", "g").replace("gram", "g")
         .replace("liters", "l").replace("liter", "l")
         .replace("litres", "l").replace("litre", "l").replace(" ", ""))
    if "mol" in u and "kg" in u:
        return 1 / 43.57          # umol/kg (seawater density ~1025 kg/m3)
    if "mol" in u and "l" in u:
        return 1 / 44.66          # umol/L
    if "mg" in u:
        return 1 / 1.42903        # mg/L
    if "ml" in u:
        return 1.0                # already mL/L
    return None


def get_csv(url: str, retries: int = 3) -> pd.DataFrame | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=300)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return pd.read_csv(io.StringIO(r.text))
        except Exception as exc:
            print(f"    retry {attempt + 1}/{retries}: {str(exc)[:100]}")
            time_mod.sleep(5 * (attempt + 1))
    return None


def discover_datasets() -> list[tuple[str, str]]:
    """Search each site for oxygen datasets; keep DO instrument classes."""
    found: list[tuple[str, str]] = []
    for site in SITES:
        url = (f"{SERVER}/search/index.csv?page=1&itemsPerPage=1000"
               f"&searchFor={site.lower()}+oxygen")
        res = get_csv(url)
        if res is None or "Dataset ID" not in res.columns:
            print(f"  ! search failed for {site}")
            continue
        titles = res["Title"] if "Title" in res.columns else [""] * len(res)
        hits = [(str(d), str(t)) for d, t in zip(res["Dataset ID"], titles)
                if site.lower() in str(d).lower()
                and any(k in str(d).lower() for k in DO_CLASSES)]
        print(f"  {site}: {len(hits)} oxygen dataset(s) -> {[d for d, _ in hits]}")
        found += hits
    return found


def dataset_plan(dsid: str, title: str) -> dict | None:
    """Read the info table: pick oxygen/depth variables and the oxygen unit."""
    info = get_csv(f"{SERVER}/info/{dsid}/index.csv")
    if info is None:
        return None
    variables = info.loc[info["Row Type"] == "variable",
                         "Variable Name"].tolist()
    oxy = next((v for v in variables if "mole_concentration" in v.lower()
                and "oxygen" in v.lower()),
               next((v for v in variables if "oxygen" in v.lower()
                     and not v.lower().endswith("_qc_agg")), None))
    depth = next((v for v in variables if v in ("z", "depth")), None)
    if oxy is None or depth is None:
        print(f"  ! {dsid}: no usable oxygen/depth variable, skipping")
        return None
    unit_rows = info[(info["Variable Name"] == oxy)
                     & (info["Attribute Name"] == "units")]
    unit = str(unit_rows["Value"].iloc[0]).lower() if len(unit_rows) else ""
    factor = unit_factor(unit)
    if factor is None:
        print(f"  ! {dsid}: unrecognized oxygen unit '{unit}' - SKIPPING "
              f"(add it to unit_factor if this dataset matters)")
        return None
    m = re.search(r"(\d+)\s*met", str(title).lower())
    nominal = int(m.group(1)) if m else None
    node = dsid.split("-")[2] if dsid.count("-") >= 2 else ""
    print(f"  {dsid}: oxygen={oxy} [{unit}] x{factor:.4g}, depth={depth}, "
          f"nominal={nominal}, node={node}")
    return {"id": dsid, "oxy": oxy, "depth": depth, "factor": factor,
            "nominal_depth": nominal, "node": node}


def fetch_raw_reduced(plan: dict) -> pd.DataFrame:
    """Fallback: fetch raw samples year by year and reduce locally."""
    q = ["time", "latitude", "longitude", plan["depth"], plan["oxy"]]
    frames = []
    for year in range(int(DATE_FROM[:4]), pd.Timestamp.now(tz="UTC").year + 1):
        cons = (f"&time>={year}-01-01T00:00:00Z"
                f"&time<{year + 1}-01-01T00:00:00Z")
        url = (f"{SERVER}/tabledap/{plan['id']}.csv?" + ",".join(q)
               + cons.replace(">=", "%3E%3D").replace("<", "%3C")
                     .replace(">", "%3E"))
        df = get_csv(url)
        if df is None or len(df) < 2:
            continue
        df = df.iloc[1:].copy()                      # drop units row
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
        for c in q[1:]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df[plan["depth"]] = ((df[plan["depth"]] / DEPTH_BIN_M).round()
                             * DEPTH_BIN_M)
        df["time"] = df["time"].dt.floor("D")
        frames.append(df.groupby(["time", "latitude", "longitude",
                                  plan["depth"]], as_index=False)
                        [plan["oxy"]].mean())
        time_mod.sleep(0.5)
    return (pd.concat(frames, ignore_index=True) if frames
            else pd.DataFrame(columns=q))


def fetch_dataset(plan: dict) -> pd.DataFrame:
    cache = CACHE_DIR / f"{plan['id']}.csv"
    if cache.exists():
        print(f"[cache] {plan['id']}")
        return pd.read_csv(cache)
    q = ["time", "latitude", "longitude", plan["depth"], plan["oxy"]]
    mean = f'orderByMean("{plan["depth"]}/{DEPTH_BIN_M},time/1day")'
    cons = (f"&time>={DATE_FROM}T00:00:00Z&" + mean)
    url = (f"{SERVER}/tabledap/{plan['id']}.csv?" + ",".join(q)
           + cons.replace(">=", "%3E%3D").replace('"', "%22")
                 .replace("(", "%28").replace(")", "%29").replace("/", "%2F"))
    r = get_csv(url)
    if r is not None and len(r):
        r = r.iloc[1:]                               # drop units row
    else:
        print(f"  ! {plan['id']}: server-side reduction failed - "
              f"falling back to raw yearly fetches")
        r = fetch_raw_reduced(plan)
    r.to_csv(cache, index=False)
    print(f"[fetch] {plan['id']}: {len(r):,} daily bins")
    time_mod.sleep(1)
    return r


def tidy(frames: list[tuple[dict, pd.DataFrame]]) -> pd.DataFrame:
    rows = []
    for plan, df in frames:
        if df.empty:
            continue
        d = df.rename(columns={plan["depth"]: "sensor_depth_m",
                               plan["oxy"]: "o2_native"})
        d["date"] = pd.to_datetime(d["time"], utc=True,
                                   errors="coerce").dt.floor("D")
        for c in ("sensor_depth_m", "o2_native", "latitude", "longitude"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        if d["sensor_depth_m"].median() < 0:      # 'z' is negative-down
            d["sensor_depth_m"] = -d["sensor_depth_m"]
        # This ERDDAP can return orderByMean depth as BIN INDICES (value /
        # divisor): real rounded depths are multiples of DEPTH_BIN_M, indices
        # are not. Detect and rescale.
        nz = d.loc[d["sensor_depth_m"] > 0, "sensor_depth_m"]
        if len(nz) and (nz % DEPTH_BIN_M != 0).mean() > 0.5:
            d["sensor_depth_m"] *= DEPTH_BIN_M
        if d["sensor_depth_m"].abs().max() < 1:
            # fixed sensors report z=0: recover depth from the title, else
            # from the node type (mf* = seafloor package -> site water depth;
            # anything else is the near-surface instrument frame ~7 m)
            site = plan["id"].split("-")[1].upper()
            if plan.get("nominal_depth"):
                d["sensor_depth_m"] = float(plan["nominal_depth"])
            elif str(plan.get("node", "")).startswith("mf"):
                d["sensor_depth_m"] = float(SITE_DEPTHS.get(site, np.nan))
            else:
                d["sensor_depth_m"] = 7.0
        d["oxygen_ml_l"] = d["o2_native"] * plan["factor"]
        d["site_code"] = "OOI_" + plan["id"].split("-")[1].upper()
        rows.append(d[["site_code", "date", "sensor_depth_m", "oxygen_ml_l",
                       "latitude", "longitude"]])
    if not rows:
        sys.exit("no OOI data retrieved")
    out = pd.concat(rows, ignore_index=True).dropna(subset=["date",
                                                            "oxygen_ml_l"])
    out = out[out["oxygen_ml_l"].between(-0.5, 11)]   # physical ceiling
    out = (out.groupby(["site_code", "date", "sensor_depth_m"],
                       as_index=False)
              .agg(oxygen_ml_l=("oxygen_ml_l", "mean"),
                   lat=("latitude", "mean"), lon=("longitude", "mean")))
    deepest = out.groupby("site_code")["sensor_depth_m"].transform("max")
    out["deepest_at_site"] = out["sensor_depth_m"] >= deepest - DEPTH_BIN_M / 2
    out["source"] = "OOI_ENDURANCE_WA"
    return out.sort_values(["site_code", "sensor_depth_m", "date"])


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("discovering oxygen datasets:")
    plans = [p for d, t in discover_datasets() if (p := dataset_plan(d, t))]
    if not plans:
        sys.exit("no OOI oxygen datasets found - check the server/site list")

    frames = [(p, fetch_dataset(p)) for p in plans]
    out = tidy(frames)
    out.to_csv(OUT_PATH, index=False)

    print(f"\nwrote {OUT_PATH}: {len(out):,} rows, "
          f"{out['site_code'].nunique()} sites")
    s = (out[out.deepest_at_site].groupby("site_code")
         .agg(days=("date", "nunique"), depth=("sensor_depth_m", "max"),
              o2_med=("oxygen_ml_l", "median"),
              pct_hypox=("oxygen_ml_l", lambda x: 100 * (x < 1.4).mean()))
         .round(2))
    print("\ndeepest-bin oxygen by site:")
    print(s.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
