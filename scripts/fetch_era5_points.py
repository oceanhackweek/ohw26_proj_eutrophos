"""
Fetch ERA5 surface forcing at the five site-cluster cells.

One request per cell against the CDS `reanalysis-era5-single-levels-timeseries`
dataset (the same product behind the existing single-point files - the output
CSVs use the same short column names: u10, v10, t2m, sst, skt, sp, ssrd, strd,
tp, valid_time, latitude, longitude). Results are combined into one tidy file
with a `region` key:

    data/era5/era5_forcing_points.csv

Waves are intentionally omitted. Requires the `cdsapi` package and a CDS
account with credentials in ~/.cdsapirc (https://cds.climate.copernicus.eu).

NOTE: if this request errors on schema (CDS occasionally renames keys), keep
the request dict from the notebook that produced the original files and just
loop it over CELLS below - the cells and the region key are the point here.

Usage:
    python fetch_era5_points.py             # fetch all cells, skip cached ones
    python fetch_era5_points.py --combine   # just rebuild the combined CSV
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pandas as pd

# Cells sit on the native 0.25-degree ERA5 grid, one per site cluster.
# See site_forcing_assignment.csv for which site uses which cell.
CELLS: dict[str, tuple[float, float]] = {          # region -> (lat, lon)
    "wcvi":         (48.75, -125.25),  # existing Folger cell, kept for continuity
    "juan_de_fuca": (48.25, -124.00),
    "sog_central":  (49.00, -123.50),
    "burrard":      (49.25, -123.25),  # nearest marine cell to Burrard Inlet
    "sog_north":    (49.75, -124.75),
    "wa_coast":     (47.00, -124.50),  # OOI Endurance Washington line (training only)
    "qc_strait":    (50.75, -127.25),  # Queen Charlotte / Johnstone Strait cluster
    "nw_coast":     (50.25, -128.75),  # NW outer coast / Brooks Peninsula shelf
}

VARIABLES = [                       # CDS names -> short names in the CSVs
    "10m_u_component_of_wind",      # u10
    "10m_v_component_of_wind",      # v10
    "2m_temperature",               # t2m
    "sea_surface_temperature",      # sst
    "skin_temperature",             # skt
    "surface_pressure",             # sp
    "surface_solar_radiation_downwards",    # ssrd
    "surface_thermal_radiation_downwards",  # strd
    "total_precipitation",          # tp
]

DATE_FROM = "2006-01-01"   # full record: pre-2014 DFO casts need forcing too
DATE_TO = "2026-08-01"
OUTDIR = Path("data") / "era5"


def cell_path(region: str) -> Path:
    """Cache path includes the date range, so changing the span re-fetches
    cleanly instead of silently reusing a shorter cached record."""
    return OUTDIR / f"era5_{region}_{DATE_FROM}_{DATE_TO}.zip"


def fetch_cell(region: str, lat: float, lon: float, outdir: Path) -> Path:
    """Request one cell from CDS; returns the path of the downloaded archive."""
    import cdsapi

    target = cell_path(region)
    if target.exists():
        print(f"[cache] {target.name}")
        return target
    print(f"[fetch] {region} ({lat}, {lon}) {DATE_FROM}..{DATE_TO}")
    cdsapi.Client().retrieve(
        "reanalysis-era5-single-levels-timeseries",
        {
            "variable": VARIABLES,
            "location": {"longitude": lon, "latitude": lat},
            "date": [f"{DATE_FROM}/{DATE_TO}"],
            "data_format": "csv",
        },
        str(target),
    )
    return target


def read_archive(path: Path, region: str) -> pd.DataFrame:
    """Read the CSV(s) inside a CDS download (zip or bare csv), tag the region."""
    frames = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.lower().endswith(".csv"):
                    frames.append(pd.read_csv(io.BytesIO(z.read(name))))
    else:
        frames.append(pd.read_csv(path))
    if not frames:
        raise RuntimeError(f"no CSV found inside {path}")
    df = frames[0]
    for extra in frames[1:]:  # some downloads split variables across files
        df = df.merge(extra, on=[c for c in ("valid_time", "latitude", "longitude")
                                 if c in df and c in extra], how="outer")
    df["region"] = region
    return df


def main() -> int:
    combine_only = "--combine" in sys.argv
    OUTDIR.mkdir(parents=True, exist_ok=True)

    frames = []
    for region, (lat, lon) in CELLS.items():
        path = cell_path(region)
        if not combine_only:
            path = fetch_cell(region, lat, lon, OUTDIR)
        if not path.exists():
            print(f"! missing {path.name}; run without --combine first")
            continue
        df = read_archive(path, region)
        # Land-contamination check: a cell centred on land has no SST.
        if "sst" in df and df["sst"].isna().all():
            print(f"! {region}: sst is all-NaN - this cell is land-contaminated;"
                  " pick the next marine cell (e.g. shift 0.25 deg seaward)")
        frames.append(df)
        print(f"  {region}: {len(df):,} rows, "
              f"{df['valid_time'].min()} -> {df['valid_time'].max()}")

    if not frames:
        sys.exit("nothing to combine")
    out = pd.concat(frames, ignore_index=True)
    out_path = OUTDIR / "era5_forcing_points.csv"
    out.to_csv(out_path, index=False)
    print(f"\nwrote {out_path} ({len(out):,} rows, {out['region'].nunique()} cells)")
    print("join sites to cells via site_forcing_assignment.csv (forcing_region)")
    return 0


if __name__ == "__main__":
    sys.exit(main())