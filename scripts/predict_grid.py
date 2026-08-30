"""
Predict near-bottom oxygen on the GEBCO grid and render dashboard-ready
surface frames.

Reads data/derived/training_table.csv (+ forcing_features_daily.csv) and the
project GEBCO netCDF, fits the three quantile models (same settings and seed
as train_oxygen_model.py), predicts every water cell, and writes:

  model_grid/<frame>.png    RGBA overlays for Leaflet imageOverlay:
                            color  = predicted mL/L on a continuous ramp that
                                     passes through the class colors, with
                                     thin contour lines at 1.4 and 2.8
                            alpha  = confidence: quantile-band width x
                                     distance-to-training-data fade
                                     (Meyer & Pebesma-style applicability),
                                     0 over land and outside the domain
  model_grid/manifest.json  bounds + frame list per the dashboard contract

Frames: --frames seasonal (DJF/MAM/JJA/SON climatological forcing),
        monthly (Jan..Dec), latest (most recent forcing = nowcast), or
        combinations: --frames seasonal,latest   (the default)

Re-run daily (see daily_update.sh): the 'latest' frame tracks the newest
forcing; climatological frames change only when the training table does.
Requires: scikit-learn, netCDF4, matplotlib, scipy.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

DERIVED = Path("data/derived")
OUT_DIR = Path("docs/model_grid")
MODEL_VERSION = "hgb_quantile_v1.1"
BAND_SCALE = 1.55        # held-out calibration; keep in sync with the trainer
GRID_STRIDE = 1          # full GEBCO 15" (~450 m) - crisp coastlines
MIN_WATER_M = 5.0        # cells shallower than this are masked
FADE_FULL_KM, FADE_ZERO_KM = 15.0, 40.0
VMAX = 7.0               # colormap ceiling, mL/L

SEASONS = {"djf": ((12, 1, 2), 15, "Winter (DJF)"),
           "mam": ((3, 4, 5), 105, "Spring (MAM)"),
           "jja": ((6, 7, 8), 196, "Summer (JJA)"),
           "son": ((9, 10, 11), 288, "Fall (SON)")}
MONTH_DOY = [15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349]

# value (mL/L) -> color anchors; class centers get the pure class color so
# the 1.4 and 2.8 thresholds land exactly on the color transitions
CMAP_ANCHORS = [(0.0, "#5f3dc4"), (0.05, "#5f3dc4"), (0.75, "#e03131"),
                (2.1, "#f59f00"), (3.5, "#2f9e44"), (VMAX, "#2f9e44")]


def find_gebco() -> Path:
    for d in (Path("."), DERIVED, Path("data"), Path(__file__).parent):
        hits = sorted(d.glob("gebco_*.nc"))
        if hits:
            return hits[0]
    sys.exit("error: no gebco_*.nc found - put the GEBCO grid beside this "
             "script or in data/")


def quantile_models(table: pd.DataFrame, x_cols: list[str]) -> dict:
    from sklearn.ensemble import HistGradientBoostingRegressor
    y = table["o2"].to_numpy()
    out = {}
    for q, tag in [(0.1, "lo"), (0.5, "med"), (0.9, "hi")]:
        m = HistGradientBoostingRegressor(
            loss="quantile", quantile=q, max_iter=400, learning_rate=0.06,
            max_leaf_nodes=31, l2_regularization=1.0, random_state=0)
        m.fit(table[x_cols], y)
        out[tag] = m
    return out


def frame_forcing(feat: pd.DataFrame, frame: str):
    """Per-region feature rows + representative day-of-year for one frame."""
    fcols = [c for c in feat.columns if c not in ("date", "region")]
    if frame in SEASONS:
        months, doy, label = SEASONS[frame]
        rows = (feat[feat["date"].dt.month.isin(months)]
                .groupby("region")[fcols].mean())
        return rows, doy, label, None
    if frame.startswith("m") and frame[1:].isdigit():
        m = int(frame[1:])
        rows = (feat[feat["date"].dt.month == m]
                .groupby("region")[fcols].mean())
        return rows, MONTH_DOY[m - 1], datetime(2000, m, 1).strftime("%B"), None
    if frame == "latest":
        per_date = feat.groupby("date")["region"].nunique()
        full = per_date[per_date == feat["region"].nunique()]
        date = full.index.max() if len(full) else feat["date"].max()
        rows = feat[feat["date"] == date].set_index("region")[fcols]
        return rows, int(pd.Timestamp(date).dayofyear), \
            f"Latest ({pd.Timestamp(date).date()})", str(pd.Timestamp(date).date())
    sys.exit(f"unknown frame '{frame}'")


def main() -> int:
    p = argparse.ArgumentParser(description="Render modeled oxygen surfaces.")
    p.add_argument("--frames", default="seasonal,latest",
                   help="seasonal | monthly | latest | comma list")
    p.add_argument("--out", type=Path, default=OUT_DIR)
    args = p.parse_args()
    frames: list[str] = []
    for tok in args.frames.split(","):
        tok = tok.strip()
        if tok == "seasonal":
            frames += list(SEASONS)
        elif tok == "monthly":
            frames += [f"m{i}" for i in range(1, 13)]
        else:
            frames.append(tok)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    from netCDF4 import Dataset
    from scipy.spatial import cKDTree

    table = pd.read_csv(DERIVED / "training_table.csv")
    table["date"] = pd.to_datetime(table["date"], format="mixed", utc=True)
    table = pd.concat([table, pd.get_dummies(table["region"], prefix="reg",
                                             dtype=int)], axis=1)
    reg_cols = sorted(c for c in table.columns if c.startswith("reg_"))
    forcing_cols = [c for c in table.columns
                    if c.endswith(("3d", "7d", "14d", "30d"))]
    x_cols = forcing_cols + ["doy_sin", "doy_cos", "depth_log", "lat", "lon",
                             "is_cast", "is_omz_ref"] + reg_cols
    print(f"training table: {len(table):,} rows -> fitting quantile models")
    models = quantile_models(table, x_cols)

    feat = pd.read_csv(DERIVED / "forcing_features_daily.csv")
    feat["date"] = pd.to_datetime(feat["date"], format="mixed", utc=True)
    cells = (pd.read_csv(DERIVED / "forcing_features_daily.csv",
                         usecols=["region"]).drop_duplicates())
    cell_pos = (table.groupby("region")[["lat", "lon"]].median()
                if not {"lat", "lon"}.issubset(cells.columns) else None)

    g = Dataset(find_gebco())
    lat = g["lat"][::GRID_STRIDE].filled(np.nan)
    lon = g["lon"][::GRID_STRIDE].filled(np.nan)
    elev = g["elevation"][::GRID_STRIDE, ::GRID_STRIDE].astype(float)
    elev = np.ma.filled(elev, np.nan)
    depth = -elev
    water = depth >= MIN_WATER_M
    LON, LAT = np.meshgrid(lon, lat)
    print(f"grid {elev.shape[0]} x {elev.shape[1]} "
          f"({100 * water.mean():.0f}% water)")

    # nearest forcing region per water cell
    regions = sorted(table["region"].unique())
    rc = np.array([[table.loc[table.region == r, "lat"].median(),
                    table.loc[table.region == r, "lon"].median()]
                   for r in regions])
    # use actual forcing-cell coordinates when available in the era5 file
    try:
        e5 = pd.read_csv(next((d / "era5_forcing_points.csv")
                              for d in (Path("data/era5"), DERIVED, Path("."))
                              if (d / "era5_forcing_points.csv").is_file()),
                         usecols=["region", "latitude", "longitude"]).drop_duplicates()
        e5 = e5[e5["region"].isin(regions)].set_index("region").reindex(regions)
        rc = e5[["latitude", "longitude"]].to_numpy()
    except StopIteration:
        pass
    ky = 111.0
    kx = 111.0 * np.cos(np.deg2rad(np.nanmean(lat)))
    dists = np.stack([np.hypot((LAT - a) * ky, (LON - b) * kx)
                      for a, b in rc])
    region_idx = np.argmin(dists, axis=0)

    # distance to nearest training site (for the applicability fade)
    sites = table.groupby("site_code")[["lat", "lon"]].median().to_numpy()
    tree = cKDTree(np.c_[sites[:, 0] * ky, sites[:, 1] * kx])
    d_km, _ = tree.query(np.c_[LAT.ravel() * ky, LON.ravel() * kx])
    d_km = d_km.reshape(LAT.shape)
    fade = np.clip((FADE_ZERO_KM - d_km) / (FADE_ZERO_KM - FADE_FULL_KM), 0, 1)

    pos = [a / VMAX for a, _ in CMAP_ANCHORS]
    cmap = LinearSegmentedColormap.from_list(
        "o2", list(zip(pos, [c for _, c in CMAP_ANCHORS])))

    args.out.mkdir(parents=True, exist_ok=True)
    wl = water.ravel()
    base = pd.DataFrame({
        "depth_log": np.log10(np.clip(depth.ravel()[wl], 1, None)),
        "lat": LAT.ravel()[wl], "lon": LON.ravel()[wl],
        "is_cast": 1, "is_omz_ref": 0})
    for i, r in enumerate(regions):
        base[f"reg_{r}"] = (region_idx.ravel()[wl] == i).astype(int)
    for c in reg_cols:
        if c not in base.columns:
            base[c] = 0

    manifest = {"bounds": [[float(lat.min()), float(lon.min())],
                           [float(lat.max()), float(lon.max())]],
                "model_version": MODEL_VERSION,
                "value_mode": "continuous_ml_l",
                "made": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "frames": []}

    for frame in frames:
        rows, doy, label, date = frame_forcing(feat, frame)
        X = base.copy()
        X["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
        X["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
        ridx = region_idx.ravel()[wl]
        for c in forcing_cols:
            vals = rows[c].reindex(regions).to_numpy()
            X[c] = vals[ridx]
        med = models["med"].predict(X[x_cols]).clip(0, 11)
        lo = models["lo"].predict(X[x_cols]).clip(0, 11)
        hi = models["hi"].predict(X[x_cols]).clip(0, 11)
        l, h = np.minimum(lo, hi), np.maximum(lo, hi)
        lo = np.clip(med - BAND_SCALE * (med - l), 0, None)
        hi = med + BAND_SCALE * (h - med)
        width = hi - lo

        V = np.full(LAT.shape, np.nan)
        V[water] = med
        W = np.full(LAT.shape, np.nan)
        W[water] = width
        conf = np.clip(1.15 - W / 3.0, 0.25, 1.0)
        alpha = np.where(water, conf * fade, 0.0)

        rgba = cmap(np.clip(V, 0, VMAX) / VMAX)
        rgba[..., 3] = np.nan_to_num(alpha)
        h_px, w_px = V.shape
        fig = plt.figure(figsize=(w_px / 100, h_px / 100), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.axis("off")
        ax.imshow(rgba, origin="lower",
                  extent=[lon.min(), lon.max(), lat.min(), lat.max()],
                  interpolation="nearest", aspect="auto")
        ax.contour(LON, LAT, np.ma.masked_invalid(V), levels=[1.4, 2.8],
                   colors="#00000059", linewidths=0.7)
        ax.set_xlim(lon.min(), lon.max())
        ax.set_ylim(lat.min(), lat.max())
        png = args.out / f"{frame}.png"
        fig.savefig(png, transparent=True, dpi=100)
        plt.close(fig)

        share = {"hypoxic": float((med < 1.4).mean()),
                 "at_risk": float(((med >= 1.4) & (med < 2.8)).mean()),
                 "good": float((med >= 2.8).mean())}
        print(f"[{frame:>7s}] {label:16s} median {np.median(med):.2f} mL/L | "
              f"hypoxic {100 * share['hypoxic']:.0f}% of water cells")
        entry = {"id": frame, "label": label, "png": png.name}
        if date:
            entry["date"] = date
        manifest["frames"].append(entry)

    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"\nwrote {args.out}/manifest.json + {len(frames)} PNG frame(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
