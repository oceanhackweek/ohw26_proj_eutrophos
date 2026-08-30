"""
Collect dissolved-oxygen records for the Vancouver Island O2-status map.

Reads the site roster (final_map_sites.csv), discovers where oxygen actually
lives at each site (including sub-locations like FGPD.O2), and pulls:

  core / secondary tiers -> daily-averaged oxygen (continuous platforms)
  cf_visit tier          -> native-resolution casts (Community Fishers),
                            with pressure/depth kept so a near-bottom value
                            can be extracted from every cast

then derives the map inputs:

  data/raw/oxygen_long.parquet             every observation, tidy long format
  data/derived/site_daily.csv              one row per (continuous site, day)
  data/derived/cf_casts.csv                one row per (visit site, cast)
  data/derived/site_classification.csv     one row per site -> feeds the map

Ferry routes (surface_transect tier) are skipped: they are moving platforms
and need per-sample coordinates, which is a different pipeline.

Same conventions as the Folger collector: chunked requests, per-chunk Parquet
cache, resumable. Token via ONC_TOKEN, ~/.onc_token, or --token.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from onc import ONC

# ============================== configuration ==============================

DATE_FROM = "2006-01-01T00:00:00.000Z"  # PVIP starts 2006; earlier is wasted calls
DATE_TO = "2026-08-01T00:00:00.000Z"

ROSTER = Path("data") / "sites" / "final_map_sites.csv"
CHUNK_DAYS = 365
RESAMPLE_CONTINUOUS = 86400  # daily server-side averages for cabled/moored sites
TIERS = ("core", "secondary", "cf_visit")

CACHE_DIR = Path("data") / "onc_cache_o2"
OUTPUT_DIR = Path("data")

# Classification thresholds, in mL/L (ONC oxygen 'corrected' units).
# 1.4 mL/L ~ 2 mg/L ~ 61 umol/kg. The one place these numbers live.
ANOXIC = 0.1
HYPOXIC = 1.4
AT_RISK = 2.8

CAST_GAP = pd.Timedelta("2h")   # a gap larger than this starts a new CF cast
NEAR_BOTTOM_M = 5.0             # near-bottom = deepest N metres of a cast

# Only sites inside the study box are collected and classified.
# Final border, matching the project's GEBCO grid.
STUDY_BOX = {"lat": (47.5, 51.5), "lon": (-130.0, -122.0)}

# Meteorological seasons; per-season classes let the map show that fjords
# (worst in fall-winter) and shelf sites (worst in late summer) differ.
SEASONS = {"djf": (12, 1, 2), "mam": (3, 4, 5),
           "jja": (6, 7, 8), "son": (9, 10, 11)}

# Requested variable -> candidate ONC sensorCategoryCodes, best guess first.
WANTED: dict[str, list[str]] = {
    "oxygen_corrected": ["oxygen_corrected", "oxygen"],
    "oxygen_uncorrected": ["oxygen_uncorrected", "oxygen_raw"],
    "temperature": ["temperature", "seawatertemperature"],
    "pressure": ["pressure"],
    "depth": ["depth"],
}

# ============================== token handling ==============================


def get_token(token: str | None = None) -> str:
    """Resolve the ONC API token from an argument, the environment, or a token file."""
    if token:
        return token.strip()
    if os.environ.get("ONC_TOKEN"):
        return os.environ["ONC_TOKEN"].strip()
    for candidate in (Path.home() / ".onc_token", Path(".") / ".onc_token"):
        if candidate.is_file():
            return candidate.read_text().strip()
    raise RuntimeError(
        "No ONC API token found. Set ONC_TOKEN, write one to ~/.onc_token, "
        "or pass --token. Get one at https://data.oceannetworks.ca "
        "(Profile -> Web Services API)."
    )


# ============================== jobs ==============================


@dataclass
class Job:
    """One (location, deviceCategory) to pull oxygen (and depth) from."""

    site_code: str      # roster site, e.g. "FGPD" - stable ID in outputs
    site_name: str
    tier: str           # core / secondary / cf_visit
    location_code: str  # actual query code, may be a sub-location e.g. "FGPD.O2"
    device_category: str
    resample: int | None  # seconds, or None for native sampling


def discover_jobs(onc: ONC, row: pd.Series, date_from: str, date_to: str) -> list[Job]:
    """
    Find every (location, deviceCategory) at this site that carries oxygen.

    Uses getLocations(includeChildren) so instruments hanging off sub-locations
    (FGPD.O2 style) are found, then getDeviceCategories per location. The date
    range matters: without it, retired deployments look nonexistent.

    If discovery fails entirely we still return one best-guess job rather than
    silently dropping the site - discovery is a convenience, not an authority.
    """
    site = row["siteCode"]
    resample = None if row["final_tier"] == "cf_visit" else RESAMPLE_CONTINUOUS

    codes: list[str] = []
    try:
        locs = onc.getLocations(
            {"locationCode": site, "includeChildren": True,
             "propertyCode": "oxygen", "dateFrom": date_from, "dateTo": date_to}
        )
        codes = [loc["locationCode"] for loc in (locs or [])]
    except Exception as exc:
        print(f"  ! location discovery failed for {site}: {str(exc)[:110]}")
    if not codes:
        codes = [site]

    jobs: list[Job] = []
    for code in dict.fromkeys(codes):  # dedupe, keep order
        cats: list[dict] = []
        # At CF sites, oxygen and pressure often ride on *different* device
        # categories (e.g. a DO logger beside the CTD), so query both - the
        # CTD is what carries the depth channel the cast analysis needs.
        props = (["oxygen", "pressure"] if row["final_tier"] == "cf_visit"
                 else ["oxygen"])
        for prop in props:
            try:
                cats += onc.getDeviceCategories(
                    {"locationCode": code, "propertyCode": prop}
                ) or []
            except Exception as exc:
                print(f"  ! device lookup failed for {code}/{prop}: "
                      f"{str(exc)[:110]}")
            time.sleep(0.1)
        seen: set[str] = set()
        for cat in cats:
            dc = cat["deviceCategoryCode"]
            if dc not in seen:
                seen.add(dc)
                jobs.append(Job(site, row["locationName"], row["final_tier"],
                                code, dc, resample))

    if not jobs:
        print(f"  ! nothing discovered at {site}; attempting OXYSENSOR anyway")
        jobs.append(Job(site, row["locationName"], row["final_tier"],
                        site, "OXYSENSOR", resample))
    return jobs


# ============================== sensor resolution ==============================


def resolve_sensors(onc: ONC, job: Job, date_from: str, date_to: str) -> dict[str, str]:
    """
    Map requested variable names to real sensorCategoryCodes for this job.

    Exact candidate match first, then case-insensitive substring. If discovery
    returns nothing, fall back to best-guess codes and let the data call judge.
    Jobs whose resolved set contains no oxygen_* variable are skipped upstream.
    """
    filters = {"locationCode": job.location_code,
               "deviceCategoryCode": job.device_category,
               "dateFrom": date_from, "dateTo": date_to}
    try:
        records = onc.getSensorCategoryCodes(filters)
    except Exception:
        try:
            records = onc.getSensorCategoryCodes(
                {"locationCode": job.location_code,
                 "deviceCategoryCode": job.device_category}
            )
        except Exception as exc:
            print(f"  ! sensor lookup failed for {job.location_code}/"
                  f"{job.device_category}: {str(exc)[:110]}")
            records = None

    have = {r["sensorCategoryCode"] for r in (records or [])}
    if not have:
        return {var: cands[0] for var, cands in WANTED.items()}

    resolved: dict[str, str] = {}
    for variable, candidates in WANTED.items():
        match = next((c for c in candidates if c in have), None)
        if match is None:
            for cand in candidates:
                match = next((h for h in have if cand.lower() in h.lower()), None)
                if match:
                    break
        if match:
            resolved[variable] = match
    return resolved


# ============================== fetching ==============================


def _redact(text: str) -> str:
    """Strip API tokens from error messages before they reach a terminal or log.

    The onc client embeds the token in the query URLs it prints on failure.
    """
    return re.sub(r"token=[0-9a-fA-F-]+", "token=***", text)


def _iter_chunks(date_from: str, date_to: str, chunk_days: int):
    """Yield (start_iso, end_iso) windows covering the span, clipped to now."""
    start = pd.Timestamp(date_from).to_pydatetime().replace(tzinfo=timezone.utc)
    end = pd.Timestamp(date_to).to_pydatetime().replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if end > now:
        end = now
    cursor = start
    while cursor < end:
        stop = min(cursor + timedelta(days=chunk_days), end)
        yield (cursor.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
               stop.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        cursor = stop


def fetch_chunk(onc: ONC, job: Job, sensors: dict[str, str],
                start: str, end: str, retries: int = 3) -> pd.DataFrame:
    """Pull one window for one job and return it in long form."""
    filters = {
        "locationCode": job.location_code,
        "deviceCategoryCode": job.device_category,
        "sensorCategoryCodes": ",".join(sorted(set(sensors.values()))),
        "dateFrom": start,
        "dateTo": end,
        "qualityControl": "clean",
        "metadata": "full",
        "fillGaps": False,
    }
    if job.resample:
        filters["resamplePeriod"] = job.resample
        filters["resampleType"] = "avg"

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            result = onc.getScalardataByLocation(filters, allPages=True)
            break
        except Exception as exc:
            last_exc = exc
            msg = _redact(str(exc))
            # Two flavours of "nothing here", both normal for a multi-year span
            # chopped into yearly chunks:
            #   404 / "No data"  - nothing recorded in this window
            #   400 + Error 127  - device exists at this location but was not
            #                      deployed during this window (a deployment gap)
            # Neither is transient, so retrying only wastes time.
            if ("404" in msg or "No data" in msg or "Error 127" in msg
                    or "not during the provided time range" in msg):
                return pd.DataFrame()
            wait = 2 ** attempt
            print(f"    retry {attempt + 1}/{retries} after {wait}s: {msg[:110]}")
            time.sleep(wait)
    else:
        print(f"    ! giving up on {start[:10]}..{end[:10]}: "
              f"{_redact(str(last_exc))[:200]}")
        return pd.DataFrame()

    return _result_to_long(result, job, sensors)


def _result_to_long(result: dict, job: Job, sensors: dict[str, str]) -> pd.DataFrame:
    """Flatten a response into one row per (time, site, variable)."""
    if not result or not result.get("sensorData"):
        return pd.DataFrame()
    code_to_variable = {code: var for var, code in sensors.items()}

    frames = []
    for sensor in result["sensorData"]:
        code = sensor.get("sensorCategoryCode")
        data = sensor.get("data") or {}
        times = data.get("sampleTimes") or []
        if not times:
            continue
        frames.append(pd.DataFrame({
            "time": pd.to_datetime(times, utc=True, format="ISO8601"),
            "site_code": job.site_code,
            "site_name": job.site_name,
            "tier": job.tier,
            "location_code": job.location_code,
            "device_category": job.device_category,
            "variable": code_to_variable.get(code, code),
            "value": pd.to_numeric(data.get("values") or [], errors="coerce"),
            "unit": sensor.get("unitOfMeasure"),
        }))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ============================== derivations ==============================


def pick_oxygen(wide: pd.DataFrame) -> pd.Series:
    """Prefer corrected oxygen, fall back to uncorrected."""
    o2 = wide.get("oxygen_corrected")
    if o2 is None:
        return wide.get("oxygen_uncorrected", pd.Series(dtype=float))
    if "oxygen_uncorrected" in wide:
        return o2.fillna(wide["oxygen_uncorrected"])
    return o2


def make_site_daily(long_df: pd.DataFrame) -> pd.DataFrame:
    """Continuous tiers -> one row per (site, day) with the daily-mean oxygen."""
    cont = long_df[long_df["tier"].isin(["core", "secondary"])
                   & long_df["variable"].str.startswith("oxygen")]
    if cont.empty:
        return pd.DataFrame()
    cont = cont.assign(time=pd.to_datetime(cont["time"], utc=True))
    wide = cont.pivot_table(index=["site_code", "site_name",
                                   cont["time"].dt.floor("D").rename("date")],
                            columns="variable", values="value",
                            aggfunc="mean").reset_index()
    wide["oxygen_ml_l"] = pick_oxygen(wide)
    return (wide.dropna(subset=["oxygen_ml_l"])
                [["site_code", "site_name", "date", "oxygen_ml_l"]]
                .sort_values(["site_code", "date"]).reset_index(drop=True))


def make_cf_casts(long_df: pd.DataFrame) -> pd.DataFrame:
    """
    Visit tier -> one row per cast, with a near-bottom oxygen value.

    Casts are separated by time gaps > CAST_GAP. Near-bottom = mean oxygen over
    the samples taken in the deepest NEAR_BOTTOM_M metres of the cast (pressure
    in dbar ~ depth in m). Oxygen and depth may come from different devices
    with different timestamps, so the depth channel is interpolated onto the
    oxygen timestamps before selecting. Without any depth channel, the cast
    minimum is used and flagged `cast_min`.
    """
    cf = long_df[long_df["tier"] == "cf_visit"]
    if cf.empty:
        return pd.DataFrame()
    cf = cf.assign(time=pd.to_datetime(cf["time"], utc=True))

    rows = []
    for site, g in cf.groupby("site_code"):
        g = g.sort_values("time")
        g = g.assign(cast=(g["time"].diff() > CAST_GAP).cumsum())
        for _, cframe in g.groupby("cast"):
            piv = (cframe.pivot_table(index="time", columns="variable",
                                      values="value", aggfunc="mean")
                   .sort_index())
            o2 = pick_oxygen(piv).dropna()
            if o2.empty:
                continue
            d = pd.Series(dtype=float)
            for var in ("depth", "pressure"):
                if var in piv:
                    d = piv[var].dropna()
                    break
            if len(d):
                bottom = d.max()
                t0 = o2.index[0]
                osec = (o2.index - t0).total_seconds().to_numpy()
                dsec = (d.index - t0).total_seconds().to_numpy()
                depth_at_o2 = np.interp(osec, dsec, d.to_numpy())
                nb = o2[depth_at_o2 >= bottom - NEAR_BOTTOM_M]
                value = nb.mean() if len(nb) else o2.min()
                method = (f"deepest_{NEAR_BOTTOM_M:g}m" if len(nb)
                          else "cast_min")
            else:
                bottom, value, method = np.nan, o2.min(), "cast_min"
            rows.append({"site_code": site,
                         "site_name": cframe["site_name"].iloc[0],
                         "time": o2.index[len(o2) // 2],
                         "cast_depth_m": bottom,
                         "n_samples": len(o2),
                         "near_bottom_o2_ml_l": value,
                         "method": method})
    return pd.DataFrame(rows).sort_values(["site_code", "time"]).reset_index(drop=True)


def classify(v: float) -> str:
    if pd.isna(v):
        return "unclassified"
    if v < ANOXIC:
        return "anoxic"
    if v < HYPOXIC:
        return "hypoxic"
    if v < AT_RISK:
        return "at_risk"
    return "good"


def _season_stats(times: pd.Series, values: pd.Series, kind: str) -> dict:
    """Per-season n / p10 / median / class. Seasons with too few observations
    stay 'unclassified' rather than pretending three points are a climate."""
    out: dict = {}
    months = times.dt.month
    min_n = 30 if kind == "continuous" else 3
    for name, mm in SEASONS.items():
        v = values[months.isin(mm).values]
        out[f"{name}_n"] = len(v)
        if len(v) >= min_n:
            p10 = v.quantile(0.10)
            out[f"{name}_p10"] = p10
            out[f"{name}_median"] = v.median()
            out[f"{name}_class"] = classify(p10)
        else:
            out[f"{name}_p10"] = np.nan
            out[f"{name}_median"] = np.nan
            out[f"{name}_class"] = "unclassified"
    return out


def _stats(site: str, name: str, kind: str, times: pd.Series,
           values: pd.Series) -> dict:
    mask = values.notna()
    values, times = values[mask], times[mask]
    summer = values[times.dt.month.isin([6, 7, 8, 9, 10]).values]
    n, n_summer = len(values), len(summer)
    if kind == "continuous":
        confidence = ("high" if n >= 730 and n_summer >= 120
                      else "medium" if n >= 180 else "low")
    else:
        confidence = ("high" if n >= 20 and n_summer >= 5
                      else "medium" if n >= 8 else "low")
    return {
        "site_code": site, "site_name": name, "data_kind": kind,
        "n_obs": n, "n_summer_obs": n_summer,
        "first": times.min(), "last": times.max(),
        "o2_min": values.min(), "o2_p10": values.quantile(0.10),
        "o2_median": values.median(),
        "pct_below_hypoxic": 100 * (values < HYPOXIC).mean(),
        "pct_below_at_risk": 100 * (values < AT_RISK).mean(),
        "class_worst_case": classify(values.min()),
        "class_exposure": classify(values.quantile(0.10)),
        "class_typical": classify(values.median()),
        **_season_stats(times, values, kind),
        "confidence": confidence,
    }


def make_classification(daily: pd.DataFrame, casts: pd.DataFrame,
                        roster: pd.DataFrame) -> pd.DataFrame:
    """One row per site: the file the map reads."""
    rows = []
    for site, g in daily.groupby("site_code"):
        rows.append(_stats(site, g["site_name"].iloc[0], "continuous",
                           pd.to_datetime(g["date"]), g["oxygen_ml_l"]))
    for site, g in casts.groupby("site_code"):
        rows.append(_stats(site, g["site_name"].iloc[0], "visit_casts",
                           pd.to_datetime(g["time"]), g["near_bottom_o2_ml_l"]))
    out = pd.DataFrame(rows)
    keep = ["siteCode", "region", "lat", "lon", "minDepth", "maxDepth",
            "final_tier", "record_status"]
    out = (roster[keep].rename(columns={"siteCode": "site_code"})
           .merge(out, on="site_code", how="right")
           .sort_values(["final_tier", "site_code"]).reset_index(drop=True))
    # Station depth: roster depth where known (continuous platforms), else the
    # median cast bottom - the key spatial predictor of near-bottom oxygen.
    if len(casts) and casts["cast_depth_m"].notna().any():
        cast_depth = casts.groupby("site_code")["cast_depth_m"].median()
        out["station_depth_m"] = (out["maxDepth"]
                                  .fillna(out["site_code"].map(cast_depth))
                                  .round(1))
    else:
        out["station_depth_m"] = out["maxDepth"]
    out = out[out["lat"].between(*STUDY_BOX["lat"])
              & out["lon"].between(*STUDY_BOX["lon"])]
    return out.reset_index(drop=True)


# ============================== orchestration ==============================


def collect(token: str | None = None, roster_path: Path = ROSTER,
            tiers: tuple[str, ...] = TIERS, date_from: str = DATE_FROM,
            date_to: str = DATE_TO, chunk_days: int = CHUNK_DAYS,
            cache_dir: Path | None = CACHE_DIR, verbose: bool = True
            ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Discover jobs for every roster site and fetch them. Returns (long, roster)."""
    onc = ONC(get_token(token))
    roster = pd.read_csv(roster_path)
    sites = roster[roster["final_tier"].isin(tiers)]
    skipped = roster[~roster["final_tier"].isin(tiers)]
    if len(skipped) and verbose:
        print(f"Skipping {len(skipped)} sites outside tiers {tiers} "
              f"(includes ferry transects - different pipeline).")
    in_box = (sites["lat"].between(*STUDY_BOX["lat"])
              & sites["lon"].between(*STUDY_BOX["lon"]))
    if (~in_box).any() and verbose:
        print(f"Study box: skipping {(~in_box).sum()} sites outside "
              f"{STUDY_BOX['lat']}N x {STUDY_BOX['lon']}W.")
    sites = sites[in_box]
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    all_frames: list[pd.DataFrame] = []
    for _, row in sites.iterrows():
        print(f"\n=== {row['siteCode']} - {row['locationName']} "
              f"[{row['final_tier']}] ===")
        for job in discover_jobs(onc, row, date_from, date_to):
            sensors = resolve_sensors(onc, job, date_from, date_to)
            has_o2 = any(v.startswith("oxygen") for v in sensors)
            has_depth = any(v in ("pressure", "depth") for v in sensors)
            # Depth-only jobs are kept at visit sites: their pressure channel
            # is what turns cast minima into true near-bottom values.
            if not has_o2 and not (job.tier == "cf_visit" and has_depth):
                print(f"  . {job.location_code}/{job.device_category}: "
                      f"no oxygen sensor resolved, skipping")
                continue
            if not has_o2:
                sensors = {v: c for v, c in sensors.items()
                           if v in ("pressure", "depth")}
            print(f"  {job.location_code}/{job.device_category} -> "
                  f"{sorted(sensors.values())}")

            # Site-level span from the roster keeps us from requesting years
            # before a site existed.
            span_start = pd.Timestamp(date_from, tz="UTC")
            site_first = pd.to_datetime(row.get("first"), utc=True, errors="coerce")
            j_from = max(span_start, site_first) if pd.notna(site_first) else span_start
            for start, end in _iter_chunks(
                    j_from.strftime("%Y-%m-%dT%H:%M:%S.000Z"), date_to, chunk_days):
                slug = (f"{job.site_code}_{job.location_code}_{job.device_category}"
                        f"_{start[:10]}_{end[:10]}.parquet").replace("/", "-")
                cache_file = (cache_dir / slug) if cache_dir else None
                if cache_file and cache_file.exists():
                    df = pd.read_parquet(cache_file)
                    tag = "cache"
                else:
                    df = fetch_chunk(onc, job, sensors, start, end)
                    if cache_file is not None:
                        df.to_parquet(cache_file, index=False)  # cache empties too
                    tag = "fetch"
                if verbose:
                    print(f"  [{tag}] {start[:10]}..{end[:10]}  {len(df):>7,} rows")
                if not df.empty:
                    all_frames.append(df)

    if not all_frames:
        print("\nNo data returned for any site.")
        return pd.DataFrame(), roster
    combined = pd.concat(all_frames, ignore_index=True)
    # Cached parquet chunks and fresh frames can disagree on timestamp flavor
    # (ns vs us, numpy vs arrow); concat then falls back to object dtype and
    # every .dt call downstream breaks. Normalize once here.
    combined["time"] = pd.to_datetime(combined["time"], utc=True)
    combined = (combined
                .drop_duplicates(subset=["time", "site_code", "location_code",
                                         "device_category", "variable"])
                .sort_values(["site_code", "time", "variable"])
                .reset_index(drop=True))
    return combined, roster


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Collect + classify site oxygen records.")
    p.add_argument("--token", default=None)
    p.add_argument("--roster", type=Path, default=ROSTER)
    p.add_argument("--tiers", default=",".join(TIERS))
    p.add_argument("--date-from", default=DATE_FROM)
    p.add_argument("--date-to", default=DATE_TO)
    p.add_argument("--chunk-days", type=int, default=CHUNK_DAYS)
    p.add_argument("--outdir", type=Path, default=OUTPUT_DIR)
    p.add_argument("--no-cache", action="store_true")
    args = p.parse_args(argv)

    long_df, roster = collect(
        token=args.token, roster_path=args.roster,
        tiers=tuple(args.tiers.split(",")), date_from=args.date_from,
        date_to=args.date_to, chunk_days=args.chunk_days,
        cache_dir=None if args.no_cache else CACHE_DIR,
    )
    if long_df.empty:
        return 1

    raw_dir, derived_dir = args.outdir / "raw", args.outdir / "derived"
    raw_dir.mkdir(parents=True, exist_ok=True)
    derived_dir.mkdir(parents=True, exist_ok=True)

    try:
        long_df.to_parquet(raw_dir / "oxygen_long.parquet", index=False)
        print(f"\nWrote {len(long_df):,} rows -> {raw_dir / 'oxygen_long.parquet'}")
    except ImportError:
        long_df.to_csv(raw_dir / "oxygen_long.csv", index=False)
        print(f"\nWrote {len(long_df):,} rows -> {raw_dir / 'oxygen_long.csv'}")

    # sanity check: thresholds assume mL/L
    units = (long_df[long_df["variable"].str.startswith("oxygen")]["unit"]
             .dropna().str.lower().unique())
    if len(units) and not any("ml" in u for u in units):
        print(f"! oxygen units are {list(units)} - thresholds assume mL/L, "
              f"convert before classifying")

    daily = make_site_daily(long_df)
    casts = make_cf_casts(long_df)
    coords = roster.rename(columns={"siteCode": "site_code"})[
        ["site_code", "lat", "lon"]]
    if len(daily):
        daily = daily.merge(coords, on="site_code", how="left")
    if len(casts):
        casts = casts.merge(coords, on="site_code", how="left")
    daily.to_csv(derived_dir / "site_daily.csv", index=False)
    casts.to_csv(derived_dir / "cf_casts.csv", index=False)
    print(f"Wrote {len(daily):,} site-days -> site_daily.csv")
    print(f"Wrote {len(casts):,} casts -> cf_casts.csv")

    classification = make_classification(daily, casts, roster)
    classification.to_csv(derived_dir / "site_classification.csv", index=False)
    print(f"Wrote {len(classification)} sites -> site_classification.csv\n")
    print(classification[["site_code", "final_tier", "n_obs", "o2_min",
                          "class_worst_case", "class_exposure", "confidence"]
                         ].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())