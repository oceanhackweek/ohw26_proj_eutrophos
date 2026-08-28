"""
Train and honestly evaluate the near-bottom oxygen model.

Reads data/derived/training_table.csv (from build_training_table.py) and:

  1. Runs the model ladder under TWO exams:
       - leave-sites-out  (GroupKFold by station: can it predict new places?)
       - leave-years-out  (GroupKFold by year: did it learn weather or the
         calendar?)
     Models: region-month climatology (the one to beat), ridge regression,
     and histogram gradient-boosted trees.
  2. Reports MAE (mL/L), R2, and 4-class status accuracy - overall, per
     region, per data kind - plus the season-flip test (does the model know
     which stations get worse from summer to fall?).
  3. Fits the final boosted model three times (10th/50th/90th percentile)
     and writes daily gap-fill predictions for every Community Fishers
     station in the map's schema.

Outputs (data/derived/):
  model_metrics.csv        every model x exam x slice
  feature_importance.csv   permutation importance under the site exam
  model_predictions.csv    site_code, date, o2_pred_ml_l, o2_lo, o2_hi,
                           model_version  -> feeds the map's modeled layers

Usage:
  python train_oxygen_model.py                # full run
  python train_oxygen_model.py --skip-predictions
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

DERIVED = Path("data/derived")
MODEL_VERSION = "hgb_quantile_v1"
ANOXIC, HYPOXIC, AT_RISK = 0.1, 1.4, 2.8


def classify(v: np.ndarray) -> np.ndarray:
    return np.select([v < ANOXIC, v < HYPOXIC, v < AT_RISK],
                     ["anoxic", "hypoxic", "at_risk"], "good")


def feature_columns(table: pd.DataFrame) -> list[str]:
    forcing = [c for c in table.columns
               if c.endswith(("3d", "7d", "14d", "30d"))]
    static = ["doy_sin", "doy_cos", "depth_log", "lat", "lon",
              "is_cast", "is_omz_ref"]
    regions = [c for c in table.columns if c.startswith("reg_")]
    return forcing + static + regions


def make_models():
    return {
        "climatology": None,  # handled specially
        "ridge": make_pipeline(SimpleImputer(strategy="median"),
                               StandardScaler(), Ridge(alpha=1.0)),
        "boosted_trees": HistGradientBoostingRegressor(
            max_iter=400, learning_rate=0.06, max_leaf_nodes=31,
            l2_regularization=1.0, random_state=0),
    }


def climatology_predict(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    key = train.groupby(["region", "month"])["o2"].mean()
    reg = train.groupby("region")["o2"].mean()
    glob = train["o2"].mean()
    idx = pd.MultiIndex.from_frame(test[["region", "month"]])
    pred = pd.Series(key.reindex(idx).to_numpy(), index=test.index)
    pred = pred.fillna(test["region"].map(reg)).fillna(glob)
    return pred.to_numpy()


def run_exam(table: pd.DataFrame, X: pd.DataFrame, group_col: str,
             exam_name: str, n_splits: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Grouped K-fold; returns metric rows and out-of-fold predictions."""
    y = table["o2"].to_numpy()
    groups = table[group_col].to_numpy()
    n_splits = min(n_splits, len(np.unique(groups)))
    oof = pd.DataFrame(index=table.index,
                       columns=list(make_models()), dtype=float)

    for tr, te in GroupKFold(n_splits=n_splits).split(X, y, groups):
        train_t, test_t = table.iloc[tr], table.iloc[te]
        for name, model in make_models().items():
            if name == "climatology":
                oof.iloc[te, oof.columns.get_loc(name)] = \
                    climatology_predict(train_t, test_t)
            else:
                model.fit(X.iloc[tr], y[tr])
                oof.iloc[te, oof.columns.get_loc(name)] = \
                    model.predict(X.iloc[te])

    rows = []
    def score(mask: np.ndarray, slice_name: str):
        for name in oof.columns:
            p, t = oof.loc[mask, name].to_numpy(), y[mask]
            if len(t) < 30:
                continue
            rows.append({"exam": exam_name, "slice": slice_name,
                         "model": name, "n": int(len(t)),
                         "mae_ml_l": round(mean_absolute_error(t, p), 3),
                         "r2": round(r2_score(t, p), 3),
                         "class_accuracy":
                             round((classify(p) == classify(t)).mean(), 3)})
    score(np.ones(len(table), bool), "overall")
    for region, g in table.groupby("region"):
        score(table.index.isin(g.index), f"region:{region}")
    for kind, g in table.groupby("data_kind"):
        score(table.index.isin(g.index), f"kind:{kind}")
    return pd.DataFrame(rows), oof


def season_flip(table: pd.DataFrame, oof: pd.DataFrame, min_n: int = 6) -> str:
    """Do predictions agree with observations on the JJA -> SON direction?"""
    t = table.assign(pred=oof["boosted_trees"],
                     season=table["month"].map(
                         {6: "JJA", 7: "JJA", 8: "JJA",
                          9: "SON", 10: "SON", 11: "SON"}))
    t = t.dropna(subset=["season", "pred"])
    agree, total = 0, 0
    for site, g in t.groupby("site_code"):
        piv_o = g.groupby("season")["o2"].agg(["mean", "size"])
        if not {"JJA", "SON"}.issubset(piv_o.index):
            continue
        if piv_o["size"].min() < min_n:
            continue
        obs_dir = np.sign(piv_o.loc["SON", "mean"] - piv_o.loc["JJA", "mean"])
        piv_p = g.groupby("season")["pred"].mean()
        pred_dir = np.sign(piv_p["SON"] - piv_p["JJA"])
        total += 1
        agree += int(obs_dir == pred_dir)
    return (f"{agree}/{total} stations ({100 * agree / max(total, 1):.0f}%)"
            if total else "not enough two-season stations")


def gap_fill(table: pd.DataFrame, X_cols: list[str], stations_mode: str = "cf",
             min_casts: int = 10, cadence: str = "daily") -> pd.DataFrame:
    """Quantile predictions for cast stations (CF, optionally DFO)."""
    feat = pd.read_csv(DERIVED / "forcing_features_daily.csv")
    feat["date"] = pd.to_datetime(feat["date"], format="mixed", utc=True)
    if cadence == "weekly":
        epoch = pd.Timestamp("2006-01-05", tz="UTC")
        feat = feat[((feat["date"] - epoch).dt.days % 7) == 0]

    quantile_models = {}
    y = table["o2"].to_numpy()
    for q, tag in [(0.1, "o2_lo"), (0.5, "o2_pred_ml_l"), (0.9, "o2_hi")]:
        m = HistGradientBoostingRegressor(
            loss="quantile", quantile=q, max_iter=400, learning_rate=0.06,
            max_leaf_nodes=31, l2_regularization=1.0, random_state=0)
        m.fit(table[X_cols], y)
        quantile_models[tag] = m

    sel = table["source"] == "ONC_CF"
    if stations_mode == "cf+dfo":
        counts = table[table["source"] == "DFO_CAST"].groupby("site_code").size()
        sel |= table["site_code"].isin(counts[counts >= min_casts].index)
    stations = (table[sel]
                .groupby("site_code")
                .agg(region=("region", "first"), lat=("lat", "median"),
                     lon=("lon", "median"), depth_log=("depth_log", "median"),
                     is_omz_ref=("is_omz_ref", "first")).reset_index())
    if stations.empty:
        print("no CF stations in the table - skipping predictions")
        return pd.DataFrame()

    frames = []
    for _, st in stations.iterrows():
        f = feat[(feat["region"] == st["region"])
                 & (feat["date"] >= pd.Timestamp("2015-01-01", tz="UTC"))].copy()
        doy = f["date"].dt.dayofyear
        f["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
        f["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
        for k in ("lat", "lon", "depth_log", "is_omz_ref"):
            f[k] = st[k]
        f["is_cast"] = 1
        for rc in [c for c in X_cols if c.startswith("reg_")]:
            f[rc] = int(rc == f"reg_{st['region']}")
        out = f[["date"]].copy()
        out["site_code"] = st["site_code"]
        for tag, m in quantile_models.items():
            out[tag] = m.predict(f[X_cols]).clip(min=0)
        frames.append(out)
    pred = pd.concat(frames, ignore_index=True)
    lo, hi = pred[["o2_lo", "o2_hi"]].min(axis=1), pred[["o2_lo", "o2_hi"]].max(axis=1)
    pred["o2_lo"], pred["o2_hi"] = lo, hi   # quantile crossing guard
    pred["model_version"] = MODEL_VERSION
    return pred[["site_code", "date", "o2_pred_ml_l", "o2_lo", "o2_hi",
                 "model_version"]]


def main() -> int:
    p = argparse.ArgumentParser(description="Train the oxygen model.")
    p.add_argument("--skip-predictions", action="store_true")
    p.add_argument("--stations", choices=["cf", "cf+dfo"], default="cf")
    p.add_argument("--min-casts", type=int, default=10)
    p.add_argument("--cadence", choices=["daily", "weekly"], default="daily")
    args = p.parse_args()

    table = pd.read_csv(DERIVED / "training_table.csv")
    table["date"] = pd.to_datetime(table["date"], format="mixed", utc=True)
    table["month"] = table["date"].dt.month
    table["year"] = table["date"].dt.year
    table = pd.concat([table, pd.get_dummies(table["region"], prefix="reg",
                                             dtype=int)], axis=1)
    X_cols = feature_columns(table)
    X = table[X_cols]
    print(f"{len(table):,} observations, {table['site_code'].nunique()} sites, "
          f"{len(X_cols)} features\n")

    site_rows, site_oof = run_exam(table, X, "site_code",
                                   "leave_sites_out", 10)
    year_rows, _ = run_exam(table, X, "year", "leave_years_out", 8)
    metrics = pd.concat([site_rows, year_rows], ignore_index=True)
    metrics.to_csv(DERIVED / "model_metrics.csv", index=False)

    print("== leave-sites-out, overall ==")
    print(site_rows[site_rows["slice"] == "overall"].to_string(index=False))
    print("\n== leave-years-out, overall ==")
    print(year_rows[year_rows["slice"] == "overall"].to_string(index=False))
    print("\n== boosted trees by region (leave-sites-out) ==")
    print(site_rows[(site_rows["model"] == "boosted_trees")
                    & site_rows["slice"].str.startswith("region")]
          .to_string(index=False))
    print("\nseason-flip agreement (JJA -> SON direction):",
          season_flip(table, site_oof))

    hgb = make_models()["boosted_trees"]
    tr, te = next(GroupKFold(5).split(X, table["o2"], table["site_code"]))
    hgb.fit(X.iloc[tr], table["o2"].iloc[tr])
    imp = permutation_importance(hgb, X.iloc[te], table["o2"].iloc[te],
                                 n_repeats=3, random_state=0, n_jobs=-1)
    fi = (pd.DataFrame({"feature": X_cols, "importance": imp.importances_mean})
          .sort_values("importance", ascending=False))
    fi.to_csv(DERIVED / "feature_importance.csv", index=False)
    print("\ntop features (permutation importance, held-out sites):")
    print(fi.head(8).round(3).to_string(index=False))

    if not args.skip_predictions:
        pred = gap_fill(table, X_cols, args.stations,
                        args.min_casts, args.cadence)
        if len(pred):
            pred.to_csv(DERIVED / "model_predictions.csv", index=False)
            print(f"\nwrote model_predictions.csv: {len(pred):,} station-days "
                  f"for {pred['site_code'].nunique()} CF stations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
