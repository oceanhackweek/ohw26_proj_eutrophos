"""
Assemble the modeling table: one row per oxygen observation, with features.

Inputs (searched in data/derived/, data/era5/, and the working directory):
  site_daily.csv            ONC continuous sites (daily)      -> weekly thinned
  dfo_moorings_daily.csv    DFO moorings, deepest sensor      -> weekly thinned
  ooi_oxygen_daily.csv      OOI WA line, deepest sensor       -> weekly thinned
  cf_casts.csv              Community Fishers casts           -> all casts
  dfo_casts.csv             DFO IOS rosette casts             -> all casts
  site_classification.csv   coordinates + station depths for ONC sites
  site_forcing_assignment.csv  site -> forcing region for roster sites
  era5_forcing_points.csv   hourly forcing per region cell
  fraser_discharge_daily.csv   Fraser at Hope daily discharge

Outputs:
  data/derived/training_table.csv          one row per observation
  data/derived/forcing_features_daily.csv  region x day feature table
                                           (reused by the model for gap-fill)

Feature set (documented once, computed once):
  tau_along_{3,7,14,30}d  alongshore wind stress, trailing mean - the
                          upwelling index (+ = upwelling-favourable, coast
                          azimuth 315 deg, projection onto 135 deg)
  wind3_{7,30}d           wind speed cubed - mixing power
  sstC_{7,30}d, dT_14d    surface temperature and air-sea difference
  heat_{14,30}d           downward solar+thermal radiation, W/m2
  tp_mm_{7,30}d           precipitation
  fraser_log_{7,30}d      log10 Fraser discharge (Salish stratification)
  doy_sin, doy_cos        season
  depth_log, lat, lon     site geometry (log10 station/cast depth)
  is_cast, is_omz_ref     data kind and natural-OMZ reference flag

Rules applied: oxygen kept in [-0.3, 11] mL/L; CF226 casts above 9 dropped
(QC-suspect station); continuous records thinned to a global weekly grid so
16 long records don't drown 900 cast stations; observations with no forcing
coverage (before the ERA5 span) are dropped with a printed count.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

RHO_AIR, CD = 1.22, 1.2e-3
ALONG = (np.sin(np.deg2rad(135)), np.cos(np.deg2rad(135)))  # toward 135 deg
SPEC = {"tau_along": [3, 7, 14, 30], "wind3": [7, 30], "sstC": [7, 30],
        "dT": [14], "heat": [14, 30], "tp_mm": [7, 30]}
FRASER_WINDOWS = [7, 30]
WEEK_EPOCH = pd.Timestamp("2006-01-05", tz="UTC")
OMZ_REF = {"BACAX", "NCBC"}

SEARCH = [Path("data/derived"), Path("data/era5"), Path("."),
          Path(__file__).parent / "data" / "derived",
          Path(__file__).parent / "data" / "era5"]
OUT_DIR = Path("data/derived")


def find(name: str, required: bool = True) -> Path | None:
    for d in SEARCH:
        if (d / name).is_file():
            return d / name
    if required:
        sys.exit(f"error: {name} not found in {[str(s) for s in SEARCH]}")
    return None


def load_forcing() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hourly ERA5 -> per-region daily trailing-window features (+ Fraser)."""
    e5 = pd.read_csv(find("era5_forcing_points.csv"))
    e5["valid_time"] = pd.to_datetime(e5["valid_time"], format="mixed", utc=True)
    e5["date"] = e5["valid_time"].dt.floor("D")
    day = (e5.groupby(["region", "date"], as_index=False)
             .agg(u10=("u10", "mean"), v10=("v10", "mean"),
                  t2m=("t2m", "mean"), sst=("sst", "mean"),
                  ssrd=("ssrd", "sum"), strd=("strd", "sum"),
                  tp=("tp", "sum"),
                  lat=("latitude", "first"), lon=("longitude", "first")))
    ws = np.hypot(day["u10"], day["v10"])
    day["wind3"] = ws ** 3
    tau_u = RHO_AIR * CD * ws * day["u10"]
    tau_v = RHO_AIR * CD * ws * day["v10"]
    day["tau_along"] = ALONG[0] * tau_u + ALONG[1] * tau_v
    day["sstC"] = day["sst"] - 273.15
    day["dT"] = (day["t2m"] - 273.15) - day["sstC"]
    day["heat"] = (day["ssrd"] + day["strd"]) / 86400.0
    day["tp_mm"] = day["tp"] * 1000.0

    frames = []
    for region, g in day.groupby("region"):
        g = g.sort_values("date").set_index("date")
        f = pd.DataFrame(index=g.index)
        for var, windows in SPEC.items():
            for w in windows:
                f[f"{var}_{w}d"] = g[var].rolling(f"{w}D", min_periods=max(2, w // 2)).mean()
        f["region"] = region
        frames.append(f.reset_index())
    feat = pd.concat(frames, ignore_index=True)

    fr = pd.read_csv(find("fraser_discharge_daily.csv"))
    fr["date"] = pd.to_datetime(fr["date"], format="mixed", utc=True).dt.floor("D")
    fr = fr.sort_values("date").set_index("date")
    flog = np.log10(fr["discharge_m3s"].clip(lower=1))
    fx = pd.DataFrame({f"fraser_log_{w}d": flog.rolling(f"{w}D", min_periods=3).mean()
                       for w in FRASER_WINDOWS}).reset_index()
    feat = feat.merge(fx, on="date", how="left")

    cells = day.groupby("region", as_index=False)[["lat", "lon"]].first()
    return feat, cells


def nearest_region(lat: pd.Series, lon: pd.Series, cells: pd.DataFrame) -> pd.Series:
    p = np.pi / 180
    d = {}
    for _, c in cells.iterrows():
        a = (np.sin((c.lat - lat) * p / 2) ** 2
             + np.cos(lat * p) * np.cos(c.lat * p)
             * np.sin((c.lon - lon) * p / 2) ** 2)
        d[c.region] = 2 * 6371 * np.arcsin(np.sqrt(a))
    return pd.DataFrame(d).idxmin(axis=1)


def weekly(df: pd.DataFrame) -> pd.DataFrame:
    keep = ((df["date"] - WEEK_EPOCH).dt.days % 7) == 0
    return df[keep]


def load_observations(cells: pd.DataFrame) -> pd.DataFrame:
    cls = pd.read_csv(find("site_classification.csv"))
    meta = cls.set_index("site_code")[["lat", "lon", "station_depth_m"]]
    assign = pd.read_csv(find("site_forcing_assignment.csv"))
    region_of = assign.set_index("site_code")["forcing_region"]

    rows = []

    onc = pd.read_csv(find("site_daily.csv"))
    onc["date"] = pd.to_datetime(onc["date"], format="mixed", utc=True).dt.floor("D")
    onc = onc.join(meta[[c for c in meta.columns if c not in onc.columns]],
                   on="site_code")
    rows.append(weekly(onc).assign(
        source="ONC", data_kind="continuous",
        depth=onc["station_depth_m"], o2=onc["oxygen_ml_l"]))

    mo = pd.read_csv(find("dfo_moorings_daily.csv"))
    mo["date"] = pd.to_datetime(mo["date"], format="mixed", utc=True).dt.floor("D")
    mo = mo[mo["deepest_at_site"] & mo["oxygen_ml_l"].notna()]
    rows.append(weekly(mo).assign(
        source="DFO_MOOR", data_kind="continuous",
        depth=mo["sensor_depth_m"], o2=mo["oxygen_ml_l"]))

    oo = pd.read_csv(find("ooi_oxygen_daily.csv"))
    oo["date"] = pd.to_datetime(oo["date"], format="mixed", utc=True).dt.floor("D")
    oo = oo[oo["deepest_at_site"] & oo["oxygen_ml_l"].notna()]
    rows.append(weekly(oo).assign(
        source="OOI", data_kind="continuous",
        depth=oo["sensor_depth_m"], o2=oo["oxygen_ml_l"]))

    cf = pd.read_csv(find("cf_casts.csv"))
    cf["date"] = pd.to_datetime(cf["time"], format="mixed", utc=True).dt.floor("D")
    if "lat" not in cf.columns or cf["lat"].isna().all():
        cf = cf.drop(columns=[c for c in ("lat", "lon") if c in cf], errors="ignore"
                     ).join(meta[["lat", "lon"]], on="site_code")
    depth = cf["cast_depth_m"].fillna(cf["site_code"].map(meta["station_depth_m"]))
    rows.append(cf.assign(source="ONC_CF", data_kind="cast",
                          depth=depth, o2=cf["near_bottom_o2_ml_l"]))

    dc = pd.read_csv(find("dfo_casts.csv"))
    dc["date"] = pd.to_datetime(dc["time"], format="mixed", utc=True).dt.floor("D")
    rows.append(dc.assign(source="DFO_CAST", data_kind="cast",
                          depth=dc["cast_depth_m"], o2=dc["near_bottom_o2_ml_l"]))

    cols = ["site_code", "source", "data_kind", "lat", "lon", "date", "depth", "o2"]
    obs = pd.concat([r[cols] for r in rows], ignore_index=True)

    obs = obs[obs["o2"].between(-0.3, 11)]
    cf226 = (obs["site_code"] == "CF226") & (obs["o2"] > 9)
    if cf226.any():
        print(f"QC: dropping {cf226.sum()} CF226 casts above 9 mL/L")
        obs = obs[~cf226]
    obs = obs.dropna(subset=["lat", "lon", "date", "o2"])

    obs["region"] = obs["site_code"].map(region_of)
    need = obs["region"].isna() | (~obs["region"].isin(cells["region"]))
    obs.loc[need, "region"] = nearest_region(obs.loc[need, "lat"],
                                             obs.loc[need, "lon"], cells)
    # OOI belongs to the Washington cell; if that cell is not fetched yet,
    # excluding OOI beats silently borrowing Juan de Fuca weather.
    if "wa_coast" not in set(cells["region"]):
        n = (obs["source"] == "OOI").sum()
        if n:
            print(f"NOTE: no wa_coast forcing cell yet - excluding {n:,} OOI "
                  f"rows (re-run after the six-cell ERA5 fetch)")
            obs = obs[obs["source"] != "OOI"]
    return obs


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    feat, cells = load_forcing()
    feat.to_csv(OUT_DIR / "forcing_features_daily.csv", index=False)
    print(f"forcing features: {len(feat):,} region-days, "
          f"{feat['region'].nunique()} regions, "
          f"{feat['date'].min().date()} -> {feat['date'].max().date()}")

    obs = load_observations(cells)
    n0 = len(obs)
    table = obs.merge(feat, on=["region", "date"], how="left")
    core = [c for c in table.columns if c.startswith("tau_along")]
    no_forcing = table[core].isna().all(axis=1)
    if no_forcing.any():
        print(f"dropping {no_forcing.sum():,} of {n0:,} observations outside "
              f"the forcing span ({feat['date'].min().date()} onward)")
        table = table[~no_forcing]

    doy = table["date"].dt.dayofyear
    table["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    table["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    table["depth_log"] = np.log10(table["depth"].clip(lower=1))
    table["is_cast"] = (table["data_kind"] == "cast").astype(int)
    table["is_omz_ref"] = table["site_code"].isin(OMZ_REF).astype(int)

    table = table.sort_values(["source", "site_code", "date"]).reset_index(drop=True)
    table.to_csv(OUT_DIR / "training_table.csv", index=False)

    print(f"\nwrote {OUT_DIR / 'training_table.csv'}: {len(table):,} rows, "
          f"{table['site_code'].nunique()} sites")
    print(table.groupby(["source", "data_kind"])
          .agg(rows=("o2", "size"), sites=("site_code", "nunique"),
               first=("date", lambda x: x.min().date()),
               last=("date", lambda x: x.max().date())).to_string())
    print("\nby region:", dict(table["region"].value_counts()))
    miss = table.filter(regex="_(3|7|14|30)d$|fraser").isna().mean()
    print("feature missingness (worst 4):")
    print(miss.sort_values(ascending=False).head(4).round(3).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
