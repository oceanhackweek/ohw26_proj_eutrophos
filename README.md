# Vancouver Island Near-Bottom Oxygen

Live dashboard: https://oceanhackweek.org/ohw26_proj_eutrophos/

Mapping, classifying, and modeling near-bottom dissolved oxygen around Vancouver Island, BC.
This is built at OceanHackWeek 2026 (Bamfield Marine Sciences Centre).

**Folder Structure**

* `contributor_folders` (optional) Each contributor can make a folder here and 
push their work here during the week. This will allow everyone to see each others work but prevent any merge conflicts. It is good if participants are new to collaborative coding.
* `final_notebooks` When the team develops shared final notebooks, they 
can be shared here. Make sure to communicate so that you limit merge conflicts.
* `scripts` Shared scripts or functions can be added here.
* `data` Shared dataset can be shared here. Note, do not put large datasets on GitHub. Speak to the organizers if you 
need to share large datasets. Each team member can have a version of the dataset locally in the same folder to 
preserve relative paths, but the dataset does not need to be added to git/GitHub (you can use `.gitignore`).

You can start with a simple structure and as you progress you can refine it to contain more components. [Here](https://cookiecutter-data-science.drivendata.org/#directory-structure) is an example of a more elaborate structure for a data science project.

## Project Name and one-line description

EUTROPHOS - Evaluation of Coastal Eutrophication precursors : Mapping oxygen levels around Vancouver Island

Mapping, classifying, and modeling near-bottom dissolved oxygen around Vancouver Island, BC.

## Collaborators

| Name                | Role                        |
|---------------------|-----------------------------|
| Ernest Ohandja Nomo | Ocean Data Collector        |
| Ashmeet Singh       | Prospective PHD Student     |
| Ejay Aguirre        | Undergraduate Student in DS |


## Planning

* Initial idea: "Map where and when near-bottom oxygen around Vancouver
  Island falls to at-risk, hypoxic, or anoxic levels, using only open
  data, then make it explorable by anyone in a browser."
* Final presentation: https://docs.google.com/presentation/d/1w_wfZXWYwS6Kx_Zjpd_JW6HHjlVOeE2qxacQUV-vH5w/edit?usp=sharing

## Background
Dissolved oxygen near the seafloor controls habitat quality for
groundfish, crab, and prawn. Around Vancouver Island, low-oxygen water
has two faces: the natural NE Pacific oxygen-minimum zone touching the
shelf and canyons (e.g. Barkley Canyon), and seasonal deoxygenation in
inlets and straits (e.g. Saanich Inlet). 
Observations exist — cabled sensors, government CTD surveys, community science casts;
however, they are scattered across portals, formats, and sampling styles. 
We classify every
observing site with common thresholds, in mL/L: 
**< 0.1 anoxic**, **< 1.4 hypoxic**, **< 2.8 at risk**, **≥ 2.8 good**.

## Goals

1. One map that answers "how low does oxygen get here?" under several
   lenses: typical low (10th percentile), worst case (record minimum),
   and each season.
2. Fuse daily sensors with sparse casts without hiding their differences
   — marker shapes encode source, badges name the provider, opacity
   encodes confidence.
3. Add a machine-learning model with *calibrated* uncertainty (station
   bands + a gridded surface), kept visually unmistakable from
   observations everywhere.
4. Ship it all as a free, static, reproducible website.

## Datasets

| File (`data/derived/`) | Source | Licence / terms |
|---|---|---|
| `site_classification.csv`, `site_daily.csv` | Ocean Networks Canada cabled observatories & moorings | ONC data policy (attribution) |
| `cf_casts.csv` | ONC **Community Fishers** CTD program | ONC data policy |
| `dfo_casts.csv`, `dfo_moorings_daily.csv` | Fisheries & Oceans Canada (IOS CTD archive, moorings) | Open Government Licence – Canada |
| `gebco_2026_*.nc` | GEBCO 2026 Grid (15 arc-sec bathymetry) | Public domain |
| `model_predictions.csv` | this repo — `scripts/train_oxygen_model.py` | MIT; ~50 MB, regenerable |

Basemap tiles © Esri, served with required attribution. Profiled but not
displayed: OOI Endurance (outside the study box), BC lighthouses (no O₂).

## Workflow/Roadmap

```
data/derived/*.csv + GEBCO .nc
        │
        ├── scripts/train_oxygen_model.py ──▶ model_predictions.csv
        │                                      + docs/model_grid/ frames
        ▼
scripts/build_dashboard.py  ──▶  docs/   (baked, fetch-free static site)
        │                          ▲
        ├── scripts/test_dashboard.js — 92 headless checks (jsdom+Leaflet)
        └── final_notebooks/ 01→03 — provenance, classification
                                      reproduction, band verification
GitHub Pages serves docs/ on every push to main.
```

Reproduce everything:

```bash
conda env create -f environment.yml && conda activate ohw26-eutrophos
python scripts/build_dashboard.py # rebake the site from data
npm i jsdom leaflet && node scripts/test_dashboard.js
jupyter lab final_notebooks/ # run 01 → 02 → 03
```

Roadmap beyond the hackweek: fold the 21 DFO moorings in as classified
site markers, decide on extending the study box south (OOI / Puget
Sound), and use the per-row model `confidence` columns in the UI.

## Results/Findings

* **The dashboard** — 43 classified sites, 17,612 CTD casts (ONC + DFO),
  GEBCO relief/isobaths, model bands at **620 stations**, and a 5-frame
  modeled oxygen surface: <https://oceanhackweek.org/ohw26_proj_eutrophos/>
  (deep links work, e.g. `…/#SEVIP`).
* **Classification is fully reproducible**: notebook 02 recomputes every
  site's status from raw inputs with the same functions the build uses —
  **43/43 agreement** on both the typical-low and worst-case lenses.
* **Saanich Inlet 2024**: 14 days below 0.1 mL/L (anoxic) at SEVIP.
* **Barkley Canyon (BACAX, 983 m)** sits in persistent OMZ hypoxia —
  100% of its record below 1.4 mL/L — reflecting offshore water masses,
  not local deterioration.
* **The model's 80% band holds up**: notebook 03 checks 9,656 real casts
  against the calibrated band — **87.8% fall inside** (high-confidence
  stations: 80.0%, on target; medium 90.7%; low 91.4%). In-sample
  diagnostic, stated as such.

## Lessons Learned

* Bake, don't fetch: one static `data.js` made hosting free and reviews
  trivial; Pages gzip absorbs the payload cost.
* Encode provenance visually (shapes + badges) before anyone has to ask
  "whose data is this?".
* Modeled vs observed must be unconfusable *everywhere*: dashed lines,
  bands, hollow markers, and the word "modeled" in every tooltip.
* Calibrate, then verify: widened quantile bands only became credible
  once a notebook measured their real coverage.
* Verify claims in committed notebooks, not slides.

## References

* GEBCO Compilation Group (2026). *GEBCO 2026 Grid.*
* Ocean Networks Canada — Oceans 3.0 data portal & Community Fishers. 
* Fisheries & Oceans Canada — Institute of Ocean Sciences data archive.
* OceanHackWeek 2026, Bamfield Marine Sciences Centre.
