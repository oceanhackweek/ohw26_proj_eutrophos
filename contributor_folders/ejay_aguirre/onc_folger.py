"""
Collect Folger Passage (Ocean Networks Canada) sensor data into one tidy dataset.

Covers two locations under Folger Passage:

  Folger Deep (FGPD)
    - Oxygen Sensor (OXYSENSOR, an SBE63 at sub-location FGPD.O2)
        oxygen concentration corrected, oxygen concentration uncorrected, temperature
    - CTD
        conductivity, practical salinity, pressure, temperature, depth

  Folger Pinnacle (FGPPN)
    - CTD
        conductivity, practical salinity, pressure, temperature, depth
    - Fluorometer Turbidity (FLNTU)
        chlorophyll, turbidity

The result is a single long/tidy DataFrame with `location` and `instrument`
columns so both dimensions travel with every measurement.

Requires an ONC API token: https://data.oceannetworks.ca -> Profile -> Web Services API.
Supply it via the ONC_TOKEN environment variable, a token file, or the `token`
argument to `collect()`.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from onc import ONC

# ============================== configuration ==============================

# Full requested span. ONC returns nothing past "now"; the script clips to it.
DATE_FROM = "2014-08-01T00:00:00.000Z"
DATE_TO = "2026-08-01T00:00:00.000Z"

# Server-side averaging window, in seconds. 3600 = hourly.
# None disables resampling and returns native sampling (very large - see README note).
RESAMPLE_PERIOD = 3600

# Request the API in slices this many days wide. Keeps individual calls small
# enough to avoid gateway timeouts and makes the run resumable.
CHUNK_DAYS = 365

# Where per-chunk cache files and the final outputs are written.
CACHE_DIR = Path(__file__).parent / "data" / "onc_cache"
OUTPUT_DIR = Path(__file__).parent / "data"

# ============================== target definitions ==============================


@dataclass
class Target:
    """One (location, instrument) pair to pull a set of sensors from."""

    location: str  # human-readable label, e.g. "Folger Deep"
    location_code: str  # ONC location code used for the query, e.g. "FGPD.O2"
    site_code: str  # parent site code, e.g. "FGPD" - stable ID for the location column
    instrument: str  # human-readable label, e.g. "Oxygen Sensor"
    device_category: str  # ONC deviceCategoryCode, e.g. "OXYSENSOR"
    # Requested variable -> candidate ONC sensorCategoryCodes, best guess first.
    # The script resolves these against what the API actually reports.
    wanted: dict[str, list[str]] = field(default_factory=dict)


TARGETS: list[Target] = [
    Target(
        location="Folger Deep",
        # The SBE63 oxygen sensor hangs off the FGPD.O2 sub-location, not FGPD itself.
        location_code="FGPD.O2",
        site_code="FGPD",
        instrument="Oxygen Sensor",
        device_category="OXYSENSOR",
        wanted={
            "oxygen_concentration_corrected": ["oxygen_corrected", "oxygen"],
            "oxygen_concentration_uncorrected": ["oxygen_uncorrected", "oxygen_raw"],
            "temperature": ["temperature", "seawatertemperature"],
        },
    ),
    Target(
        location="Folger Deep",
        location_code="FGPD",
        site_code="FGPD",
        instrument="CTD",
        device_category="CTD",
        wanted={
            "conductivity": ["conductivity"],
            "practical_salinity": ["salinity", "practicalsalinity"],
            "pressure": ["pressure"],
            "temperature": ["temperature", "seawatertemperature"],
            "depth": ["depth"],
        },
    ),
    Target(
        location="Folger Pinnacle",
        location_code="FGPPN",
        site_code="FGPPN",
        instrument="CTD",
        device_category="CTD",
        wanted={
            "conductivity": ["conductivity"],
            "practical_salinity": ["salinity", "practicalsalinity"],
            "pressure": ["pressure"],
            "temperature": ["temperature", "seawatertemperature"],
            "depth": ["depth"],
        },
    ),
    Target(
        location="Folger Pinnacle",
        location_code="FGPPN",
        site_code="FGPPN",
        instrument="Fluorometer Turbidity",
        device_category="FLNTU",
        wanted={
            "chlorophyll": ["chlorophyll"],
            "turbidity": ["turbidityntu", "turbidity"],
        },
    ),
]

# ============================== token handling ==============================


def get_token(token: str | None = None) -> str:
    """Resolve the ONC API token from an argument, the environment, or a token file."""
    if token:
        return token.strip()
    if os.environ.get("ONC_TOKEN"):
        return os.environ["ONC_TOKEN"].strip()
    for candidate in (Path.home() / ".onc_token", Path(__file__).parent / ".onc_token"):
        if candidate.is_file():
            return candidate.read_text().strip()
    raise RuntimeError(
        "No ONC API token found. Set ONC_TOKEN, write one to ~/.onc_token, "
        "or pass token=... . Get a token at https://data.oceannetworks.ca "
        "(Profile -> Web Services API)."
    )


# ============================== sensor discovery ==============================


def available_sensors(onc: ONC, target: Target) -> dict[str, dict]:
    """
    Ask the API which sensorCategoryCodes actually exist for this location+instrument.

    Returns a mapping of sensorCategoryCode -> the API's metadata record for it.
    Returns {} if the location/instrument pair reports nothing.
    """
    try:
        records = onc.getSensorCategoryCodes(
            {
                "locationCode": target.location_code,
                "deviceCategoryCode": target.device_category,
            }
        )
    except Exception as exc:
        print(f"  ! sensor lookup failed for {target.location_code}/"
              f"{target.device_category}: {exc}")
        return {}
    return {r["sensorCategoryCode"]: r for r in records or []}


def resolve_sensors(onc: ONC, target: Target) -> dict[str, str]:
    """
    Map each requested variable name to a real sensorCategoryCode.

    Tries the candidate codes in order, then falls back to a case-insensitive
    substring match so renamed sensors (e.g. turbidityntu -> turbidity) still
    resolve. Prints a warning for anything that cannot be matched.
    """
    have = available_sensors(onc, target)
    if not have:
        return {}

    resolved: dict[str, str] = {}
    for variable, candidates in target.wanted.items():
        match = next((c for c in candidates if c in have), None)
        if match is None:
            # Loosen to substring matching against whatever the API reported.
            for cand in candidates:
                match = next(
                    (code for code in have if cand.lower() in code.lower()), None
                )
                if match:
                    break
        if match is None:
            print(f"  ! no sensor found for '{variable}' at {target.location_code}/"
                  f"{target.device_category}; available: {sorted(have)}")
            continue
        resolved[variable] = match
    return resolved


# ============================== fetching ==============================


def _iter_chunks(date_from: str, date_to: str, chunk_days: int):
    """Yield (start_iso, end_iso) windows covering the span, clipped to now."""
    start = pd.Timestamp(date_from).to_pydatetime().replace(tzinfo=timezone.utc)
    end = pd.Timestamp(date_to).to_pydatetime().replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if end > now:
        end = now  # nothing exists in the future; asking for it just wastes calls
    cursor = start
    while cursor < end:
        stop = min(cursor + timedelta(days=chunk_days), end)
        yield (
            cursor.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            stop.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
        cursor = stop


def fetch_chunk(
    onc: ONC,
    target: Target,
    sensors: dict[str, str],
    start: str,
    end: str,
    resample_period: int | None,
    retries: int = 3,
) -> pd.DataFrame:
    """
    Pull one time window for one (location, instrument) and return it in long form.

    `sensors` maps our variable name -> ONC sensorCategoryCode. All sensors for
    the target are requested in a single call. allPages=True lets the client
    library walk the API's pagination for us.
    """
    filters = {
        "locationCode": target.location_code,
        "deviceCategoryCode": target.device_category,
        "sensorCategoryCodes": ",".join(sorted(set(sensors.values()))),
        "dateFrom": start,
        "dateTo": end,
        "qualityControl": "clean",
        "metadata": "full",
        "fillGaps": False,
    }
    if resample_period:
        filters["resamplePeriod"] = resample_period
        filters["resampleType"] = "avg"

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            result = onc.getScalardataByLocation(filters, allPages=True)
            break
        except Exception as exc:
            last_exc = exc
            msg = str(exc)
            # A 404 here means "no deployment in this window", which is normal
            # for a 12-year span - treat it as an empty result, not an error.
            if "404" in msg or "No data" in msg:
                return pd.DataFrame()
            wait = 2 ** attempt
            print(f"    retry {attempt + 1}/{retries} after {wait}s: {msg[:120]}")
            time.sleep(wait)
    else:
        print(f"    ! giving up on {start[:10]}..{end[:10]}: {last_exc}")
        return pd.DataFrame()

    return _result_to_long(result, target, sensors)


def _result_to_long(result: dict, target: Target, sensors: dict[str, str]) -> pd.DataFrame:
    """
    Flatten a getScalardataByLocation response into one row per
    (time, location, instrument, variable).
    """
    if not result or not result.get("sensorData"):
        return pd.DataFrame()

    # Invert so we can label each returned sensor with our requested variable name.
    code_to_variable = {code: var for var, code in sensors.items()}

    frames = []
    for sensor in result["sensorData"]:
        code = sensor.get("sensorCategoryCode")
        variable = code_to_variable.get(code, code)
        data = sensor.get("data") or {}
        times = data.get("sampleTimes") or []
        values = data.get("values") or []
        if not times:
            continue
        flags = data.get("qaqcFlags") or [None] * len(times)

        frames.append(
            pd.DataFrame(
                {
                    "time": pd.to_datetime(times, utc=True, format="ISO8601"),
                    "location": target.location,
                    "location_code": target.site_code,
                    "instrument": target.instrument,
                    "device_category": target.device_category,
                    "variable": variable,
                    "sensor_category_code": code,
                    "value": pd.to_numeric(values, errors="coerce"),
                    "unit": sensor.get("unitOfMeasure"),
                    "qaqc_flag": flags,
                }
            )
        )

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ============================== orchestration ==============================


def collect(
    token: str | None = None,
    date_from: str = DATE_FROM,
    date_to: str = DATE_TO,
    resample_period: int | None = RESAMPLE_PERIOD,
    chunk_days: int = CHUNK_DAYS,
    targets: list[Target] | None = None,
    cache_dir: Path | None = CACHE_DIR,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Walk every target and time chunk and return one tidy DataFrame.

    Completed chunks are cached as Parquet under `cache_dir`, so re-running after
    an interruption only fetches what is missing. Pass cache_dir=None to disable.
    """
    onc = ONC(get_token(token))
    targets = targets if targets is not None else TARGETS
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)

    chunks = list(_iter_chunks(date_from, date_to, chunk_days))
    all_frames: list[pd.DataFrame] = []

    for target in targets:
        label = f"{target.location} / {target.instrument}"
        if verbose:
            print(f"\n=== {label} ({target.location_code}, {target.device_category}) ===")

        sensors = resolve_sensors(onc, target)
        if not sensors:
            print(f"  ! skipping {label}: no usable sensors resolved")
            continue
        if verbose:
            for var, code in sensors.items():
                print(f"  {var:34s} -> {code}")

        for start, end in chunks:
            slug = (
                f"{target.site_code}_{target.device_category}_"
                f"{target.instrument.replace(' ', '')}_{start[:10]}_{end[:10]}.parquet"
            )
            cache_file = (cache_dir / slug) if cache_dir else None

            if cache_file and cache_file.exists():
                df = pd.read_parquet(cache_file)
                if verbose:
                    print(f"  [cache] {start[:10]}..{end[:10]}  {len(df):>7,} rows")
            else:
                df = fetch_chunk(onc, target, sensors, start, end, resample_period)
                if cache_file:
                    # Cache empty results too, so gaps are not re-requested forever.
                    df.to_parquet(cache_file, index=False)
                if verbose:
                    print(f"  [fetch] {start[:10]}..{end[:10]}  {len(df):>7,} rows")

            if not df.empty:
                all_frames.append(df)

    if not all_frames:
        print("\nNo data returned for any target.")
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.drop_duplicates(
        subset=["time", "location_code", "instrument", "variable"]
    )
    combined = combined.sort_values(
        ["time", "location", "instrument", "variable"]
    ).reset_index(drop=True)
    return combined


def to_wide(long_df: pd.DataFrame) -> pd.DataFrame:
    """
    Pivot the tidy table to one row per (time, location), with instrument-prefixed
    columns such as `oxygen_sensor__temperature` and `ctd__temperature`.

    The instrument prefix matters: both the SBE63 and the CTD report a
    temperature, and they are different measurements.
    """
    if long_df.empty:
        return long_df

    df = long_df.copy()
    df["column"] = (
        df["instrument"].str.lower().str.replace(" ", "_", regex=False)
        + "__"
        + df["variable"]
    )
    wide = df.pivot_table(
        index=["time", "location", "location_code"],
        columns="column",
        values="value",
        aggfunc="mean",
    ).reset_index()
    wide.columns.name = None
    return wide.sort_values(["time", "location"]).reset_index(drop=True)


def summarize(long_df: pd.DataFrame) -> pd.DataFrame:
    """Per (location, instrument, variable): row count, time span, and value range."""
    if long_df.empty:
        return long_df
    return (
        long_df.groupby(["location", "instrument", "variable", "unit"], dropna=False)
        .agg(
            n=("value", "size"),
            n_valid=("value", "count"),
            start=("time", "min"),
            end=("time", "max"),
            vmin=("value", "min"),
            vmax=("value", "max"),
        )
        .reset_index()
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Collect ONC Folger Passage sensor data.")
    p.add_argument("--token", default=None, help="ONC API token (else $ONC_TOKEN)")
    p.add_argument("--date-from", default=DATE_FROM)
    p.add_argument("--date-to", default=DATE_TO)
    p.add_argument(
        "--resample",
        type=int,
        default=RESAMPLE_PERIOD,
        help="server-side averaging period in seconds; 0 for native sampling",
    )
    p.add_argument("--chunk-days", type=int, default=CHUNK_DAYS)
    p.add_argument("--outdir", type=Path, default=OUTPUT_DIR)
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--wide", action="store_true", help="also write the pivoted table")
    args = p.parse_args(argv)

    long_df = collect(
        token=args.token,
        date_from=args.date_from,
        date_to=args.date_to,
        resample_period=args.resample or None,
        chunk_days=args.chunk_days,
        cache_dir=None if args.no_cache else CACHE_DIR,
    )
    if long_df.empty:
        return 1

    args.outdir.mkdir(parents=True, exist_ok=True)
    long_path = args.outdir / "folger_passage_long.csv"
    long_df.to_csv(long_path, index=False)
    print(f"\nWrote {len(long_df):,} rows -> {long_path}")

    if args.wide:
        wide_df = to_wide(long_df)
        wide_path = args.outdir / "folger_passage_wide.csv"
        wide_df.to_csv(wide_path, index=False)
        print(f"Wrote {len(wide_df):,} rows x {wide_df.shape[1]} cols -> {wide_path}")

    print("\n" + summarize(long_df).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
