# The oxygen dashboard (build & extend)

Part of [ohw26_proj_eutrophos](https://github.com/oceanhackweek/ohw26_proj_eutrophos); deployment steps live in [GITHUB_DEPLOY.md](GITHUB_DEPLOY.md).

Interactive dashboard of near-bottom dissolved oxygen around Vancouver
Island (OceanHackWeek 2026, Bamfield BC). A static single-page site
(`docs/`): sidebar with status lenses, basemap picker, overlay toggles,
confidence / cast-year / QC filters and site search; click any marker for a
detail panel with its full time series. Deep links work (`#SEVIP`). Free to
host on GitHub Pages; also opens straight from the filesystem.

## Quickstart

Run everything from the repo root:

```bash
pip install -r requirements.txt
python scripts/build_dashboard.py     # reads data/derived/, writes docs/
# open docs/index.html directly, or serve it:
cd docs && python -m http.server 8000    # -> http://localhost:8000/
```

Both scripts look for the CSVs in `.`, then `data/derived/`, then next to
the script — in this repo that means `data/derived/`.
`python scripts/build_status_map.py` still produces the classic
single-file `vi_oxygen_status_map.html` from the same data if you want a
one-file hand-off.

### Headless tests (optional, for development)

```bash
npm i jsdom leaflet
node scripts/test_dashboard.js   # boots the app in jsdom + real Leaflet
```

## Data inputs

Required: `site_classification.csv`, `site_daily.csv`, `cf_casts.csv`.

Optional (auto-detected, silently skipped when absent):

- `model_predictions.csv` — columns `site_code, date, o2_pred_ml_l, o2_lo,
  o2_hi, model_version` (spatial rows: `lat, lon` instead of `site_code`).
  Renders hollow-diamond "Modeled" markers + dashed line/band in charts.
- `*gebco*.nc` — GEBCO grid, renders 100–2000 m isobaths.

## GEBCO bathymetry (exact download box)

1. Go to <https://download.gebco.net/>.
2. Enter the boundaries: **North 51.5, South 47.5, West -130.0, East -122.0**
   (this is the study box 47.8–51.2 N / 129.5–122.5 W plus a 0.3° margin so
   contours don't clip at the edges).
3. Select the **GEBCO 2026 Grid** (ice-surface elevation is fine; leave the
   TID grid unchecked) and format **netCDF** — for ArcGIS Pro work, also grab
   **GeoTIFF**.
4. Download, unzip, and drop the `.nc` file (keep `gebco` in the filename)
   into `data/derived/`. Rerun the script — the "Bathymetry (GEBCO isobaths)"
   layer appears, plus a **"GEBCO shaded relief" basemap** (hillshaded from
   your grid, and the default basemap when present). This repo already
   includes the grid for the recommended box, so both are on out of the
   box.

At 15 arc-seconds this box is ~960 × 1920 cells (a few MB).

## Hosting

See [GITHUB_DEPLOY.md](GITHUB_DEPLOY.md) for the click-by-click deployment of this dashboard into the project repo and GitHub Pages,
including the commit sequence and the data-licence note for the CSVs.

## Basemaps and ArcGIS

The dashboard's basemap picker offers **GEBCO shaded relief** (rendered
locally from the `.nc`, default when present), **Esri Ocean Base** (native detail to zoom ~13), **Esri World Imagery**, and CartoDB Light.
The Esri layers use public tile services with attribution and cost
nothing; the GEBCO Grid is public domain.

Your UofU ArcGIS licence is a separate, optional path: import the CSVs +
GEBCO GeoTIFF into ArcGIS Pro / ArcGIS Online for analysis or an Esri-hosted
web map. It isn't needed for anything in this repo.

## Accessibility notes

- The six status lenses are **radio buttons** (one at a time) — markers
  from different lenses can never stack invisibly on top of each other.
- Sidebar filters: confidence (high/medium/low), cast year range, and a
  QC-suspect toggle. Search pans + opens any station; `#CODE` deep links
  are shareable.
- Known issue: the fixed status palette pairs red and green, which is hard
  for deuteranopia; a shape-per-class encoding is on the roadmap.

## Roadmap

Station-to-station oxygen interpolation along bathymetry (the modeled layer
+ GEBCO grid are the inputs for it) and a shape-per-class marker encoding.
