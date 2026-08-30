"""
Fetch daily mean river discharge from ECCC's hydrometric archive (HYDAT)
via the GeoMet OGC API -> data/derived/fraser_discharge_daily.csv.

Default station: 08MF005, Fraser River at Hope - the freshwater control on
Salish Sea stratification, and the reviewer's requested predictor #4. Use
--station for any other gauge (e.g. a Somass River station for Alberni).

The API pages at up to 10,000 features per request, so ~20 years of daily
means arrives in one or two requests. Note: HYDAT is the quality-controlled
archive and typically lags the present by several months to two years -
check the printed 'last' date; recent months being absent is normal.

Usage:
    python fetch_fraser_discharge.py
    python fetch_fraser_discharge.py --station 08HB002 --date-from 1990-01-01
"""

from __future__ import annotations

import argparse
import io
import sys
import time as time_mod
from pathlib import Path

import pandas as pd
import requests

BASE = "https://api.weather.gc.ca/collections/hydrometric-daily-mean/items"
STATION = "08MF005"          # Fraser River at Hope
DATE_FROM = "2006-01-01"
PAGE = 10000                 # API maximum per request

OUT_PATH = Path("data") / "derived" / "fraser_discharge_daily.csv"


def fetch_pages(station: str, date_from: str) -> pd.DataFrame:
    frames, offset = [], 0
    while True:
        url = (f"{BASE}?f=csv&STATION_NUMBER={station}"
               f"&datetime={date_from}/.."
               f"&sortby=DATE&limit={PAGE}&offset={offset}")
        page = None
        for attempt in range(3):
            try:
                r = requests.get(url, timeout=120)
                r.raise_for_status()
                page = pd.read_csv(io.StringIO(r.text))
                break
            except Exception as exc:
                print(f"    retry {attempt + 1}/3: {str(exc)[:100]}")
                time_mod.sleep(5 * (attempt + 1))
        if page is None:
            sys.exit(f"giving up at offset {offset} - try again later")
        print(f"[fetch] offset {offset}: {len(page):,} rows")
        if len(page):
            frames.append(page)
        if len(page) < PAGE:
            break
        offset += PAGE
        time_mod.sleep(0.5)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def tidy(raw: pd.DataFrame) -> pd.DataFrame:
    cols = {c.lower(): c for c in raw.columns}
    need = {"date": cols.get("date"), "discharge": cols.get("discharge"),
            "station_number": cols.get("station_number"),
            "station_name": cols.get("station_name")}
    missing = [k for k, v in need.items() if v is None]
    if missing:
        sys.exit(f"unexpected columns {list(raw.columns)} - missing {missing}")
    flag_col = cols.get("discharge_symbol_en")

    out = pd.DataFrame({
        "station_number": raw[need["station_number"]],
        "station_name": raw[need["station_name"]],
        "date": pd.to_datetime(raw[need["date"]], utc=True, errors="coerce"),
        "discharge_m3s": pd.to_numeric(raw[need["discharge"]], errors="coerce"),
        "flag": raw[flag_col] if flag_col else "",
    })
    out = (out.dropna(subset=["date", "discharge_m3s"])
              .drop_duplicates(subset=["station_number", "date"])
              .sort_values("date").reset_index(drop=True))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch ECCC daily river discharge.")
    p.add_argument("--station", default=STATION)
    p.add_argument("--date-from", default=DATE_FROM)
    p.add_argument("--out", type=Path, default=OUT_PATH)
    args = p.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    raw = fetch_pages(args.station, args.date_from)
    if raw.empty:
        sys.exit(f"no rows for station {args.station} since {args.date_from}")
    out = tidy(raw)
    out.to_csv(args.out, index=False)

    span_days = (out["date"].max() - out["date"].min()).days + 1
    print(f"\nwrote {args.out}: {len(out):,} days for "
          f"{out['station_name'].iloc[0]} ({args.station})")
    print(f"span {out['date'].min().date()} -> {out['date'].max().date()} "
          f"({len(out):,}/{span_days:,} days present)")
    m = out.assign(month=out["date"].dt.month).groupby("month")["discharge_m3s"].median()
    print("monthly median discharge (m3/s) - expect the freshet in Jun:")
    print(m.round(0).astype(int).to_string())
    print("\nNOTE: HYDAT lags the present; absent recent months are normal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
