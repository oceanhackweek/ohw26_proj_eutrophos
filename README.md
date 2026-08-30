# ohw26_proj_eutrophos

This is the project repository from OceanHackWeek 2026 (Bamfield, BC).

* `README.md` — this file, the project overview.
* `contributor_folders` — personal working folders; the project's development history.
* `data` — shared datasets. Large regenerables (raw archives, hourly ERA5, fetch caches) are gitignored; see the Datasets table for what ships in-repo.
* `final_notebooks` — polished, re-runnable notebooks that reproduce the headline claims.
* `scripts` — the pipeline: fetch → derive → classify → model → bake the dashboard.
* `docs` — the dashboard itself (GitHub Pages serves this folder).

## Project Name and one-line description

**VI Near-Bottom O₂** — mapping, classifying, and modeling near-bottom
dissolved oxygen around Vancouver Island, with every claim reproducible
from this repository.

**Live dashboard:** https://oceanhackweek.org/ohw26_proj_eutrophos/

## Collaborators

| Name                | Role                        |
|---------------------|-----------------------------|
| Ernest Ohandja Nomo | Ocean Data Collector        |
| Ashmeet Singh       | Prospective PHD Student     |
| Ejay Aguirre        | Undergraduate Student in DS |

## Planning

* Initial idea: map oxygen status around Vancouver Island from every source
  we could reach, then use a model to fill the space between measurements.
* Working notebooks: [`final_notebooks/`](final_notebooks/) (01 → 03).
* Dashboard build and deployment notes: [`DASHBOARD.md`](DASHBOARD.md).

## Background

Near-bottom oxygen around Vancouver Island is set by a mix of natural
deep-Pacific (oxygen-minimum-zone) water, wind-driven upwelling on the
outer shelf, and stratification and renewal cycles in the inside waters
and fjords. Some basins (Saanich Inlet, Alberni Inlet, Barkley Canyon)
are naturally hypoxic or anoxic for much of the year; others flirt with
thresholds seasonally. We wanted one map that shows the observed status
honestly — and a model that extends it, with its uncertainty on display.

## Goals

1. Assemble every reachable near-bottom O₂ record (DFO, ONC, Community
   Fishers, OOI) into one consistent, mL/L, near-bottom dataset.
2. Classify every site under three lenses (typical low / worst case /
   seasonal) with confidence grades.
3. Train and honestly validate a model predicting O₂ from weather, river
   flow, depth, and season — with calibrated uncertainty.
4. Ship it all as a fetch-free static dashboard anyone can open.

## Datasets

| Dataset | Source | Licence / note |
|---|---|---|
| `dfo_casts.csv` (17,399 casts) | DFO IOS via CIOOS Pacific ERDDAP | OGL-Canada |
| `cf_casts.csv` (846 casts) | ONC Community Fishers | ONC data policy |
| `site_daily.csv` (16 cabled sites) | Ocean Networks Canada | ONC data policy |
| `dfo_moorings_daily.csv` (21 clusters) | DFO BC Shelf Mooring Program via CIOOS | OGL-Canada |
| `ooi_oxygen_daily.csv` (WA line; training only) | NSF OOI Data Explorer | OOI policy |
| `bc_lighthouses_daily.csv` (1914–2019) | DFO BCSOP via CIOOS | OGL-Canada |
| GEBCO 2026 grid subset (`.nc`) | GEBCO/BODC | public domain |
| `training_table.csv` + `forcing_features_daily.csv` | this repo (committed) | MIT; the model retrains from these alone |
| `model_predictions.csv` | regenerated locally by `scripts/train_oxygen_model.py` | not committed (~50 MB); bands ship baked in `data.js` |

Full citations, licences, and required acknowledgments: [`DATA_SOURCES.md`](DATA_SOURCES.md).

## Workflow/Roadmap

```
data/derived/*.csv + GEBCO .nc
        │
        ├── scripts/build_training_table.py ──▶ training_table.csv
        ├── scripts/train_oxygen_model.py ──▶ model_predictions.csv (bands)
        ├── scripts/predict_grid.py ──────▶ docs/model_grid/ frames
        ▼
scripts/build_dashboard.py  ──▶  docs/   (baked, fetch-free static site)
        │                          ▲
        ├── scripts/test_dashboard.js — 92 headless checks (jsdom+Leaflet)
        └── final_notebooks/ 01→03 — provenance, classification
                                      reproduction, band verification
GitHub Pages serves docs/ on every push to main.
```

## Reproduce this work

```bash
pip install -r requirements.txt
python scripts/build_training_table.py     # or skip: table is committed
python scripts/train_oxygen_model.py       # exams + station bands (~3 min)
python scripts/predict_grid.py             # modeled surface frames (~3 min)
python scripts/build_dashboard.py          # bake the site
python -m http.server -d docs 8000         # open localhost:8000
```

Refreshing the *observations* additionally needs credentials (ONC token as
`ONC_TOKEN`, Copernicus `~/.cdsapirc`) and the `scripts/fetch_*.py` chain —
everything is cached and resumable.

## Results/Findings

* An interactive dashboard: 43 classified sites, ~17.6k individual casts,
  GEBCO relief/isobaths, model bands at **656 stations**, and a 5-frame
  modeled surface with confidence shown as transparency.
* **Model skill (station-blocked validation):** typical error
  **0.46 mL/L**, correct 4-class status **80%** at never-seen stations, vs
  **1.08 mL/L** for a region×month climatology; prediction bands
  calibrated (×1.55) to 80% held-out coverage.
* **43/43 sites** reproduce their published classification from the raw
  committed data (`final_notebooks/02`).
* The dashboard's numbers are verified in committed notebooks and a
  92-check headless UI suite — not slides.
* **SEVIP anoxia:** the Saanich sill sensor logged 14 days at or below
  0.1 mL/L in fall 2024 — surfaced by the classification, visible on the
  dashboard, reproduced in `final_notebooks/02`.

## Lessons Learned

* Bake, don't fetch: a static `data.js` outlives every API hiccup.
* Verify claims in committed notebooks, not slides.
* A fetch box is a hard wall — empty map regions can mean "never asked."
* Same filename ≠ same file: print provenance (row/station counts) at
  every pipeline stage.
* Show model uncertainty as transparency; fade to nothing far from data.

## References

Full source citations, licences, and required acknowledgments:
[`DATA_SOURCES.md`](DATA_SOURCES.md).

Contains modified Copernicus Climate Change Service information (2026).

To cite: "OHW26 eutrophos team (2026), VI Near-Bottom O₂, github.com/oceanhackweek/ohw26_proj_eutrophos"
