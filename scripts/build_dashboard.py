#!/usr/bin/env python3
"""Build the VI oxygen dashboard: a static single-page site in docs/.

Reuses the loaders, chart builder, and layer logic from build_status_map.py
(the classic single-file map still works via that script). The dashboard is
plain Leaflet + a hand-rolled sidebar; everything is client-side, so it can
be hosted for free on GitHub Pages (Settings -> Pages -> main + /docs).

Usage:  python build_dashboard.py [data_dir_or_file]
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

import build_status_map as core

OUT_DIR = Path("docs")
SEASON_NAMES = {"djf": "Winter", "mam": "Spring",
                "jja": "Summer", "son": "Fall"}
LENSES = ([("exposure", "Typical low (p10)", "class_exposure", ""),
           ("worst", "Worst case (min)", "class_worst_case", "worst case")]
          + [(k, f"{SEASON_NAMES[k]} ({k.upper()})", f"{k}_class",
              f"{SEASON_NAMES[k]} p10")
             for k, _ in core.SEASONS])


# -- payload -----------------------------------------------------------------
def _pill(label: str, cls_key) -> str:
    lab = core.LABELS.get(cls_key, "&#8211;")
    col = core.COLORS.get(cls_key, core.COLORS["unclassified"])
    return (f'<span class="i-pill"><span class="i-dot" '
            f'style="background:{col}"></span>{label}: <b>{lab}</b></span>')


def site_info_html(r: pd.Series, mnote: str) -> str:
    f = core.fmt
    years = ""
    if pd.notna(r.get("first")) and pd.notna(r.get("last")):
        years = (f"{pd.to_datetime(r['first'], utc=True).year}&#8211;"
                 f"{pd.to_datetime(r['last'], utc=True).year}")
    cont = r["data_kind"] == "continuous"
    depth = f(r.get("station_depth_m", r.get("maxDepth")), 0)
    sub = " &#183; ".join(x for x in [
        r["site_code"],
        "continuous sensor" if cont else "ship-visit station",
        str(r.get("final_tier", "")),
        f"{depth} m" if depth != "-" else "",
        f"{r['confidence']} confidence"] if x)
    h = [f'<div class="i-h">{r["site_name"]}</div>',
         f'<div class="i-subh">{sub}</div>',
         '<div class="i-sec"><div class="i-lab">Status</div>'
         '<div class="i-pills">'
         + _pill("Typical low", r["class_exposure"])
         + _pill("Worst case", r["class_worst_case"])
         + "</div></div>",
         '<div class="i-sec"><div class="i-lab">Oxygen (mL/L)</div>'
         '<div class="i-stats">'
         f'<div class="i-st"><b>{f(r["o2_min"])}</b><span>min</span></div>'
         f'<div class="i-st"><b>{f(r["o2_p10"])}</b><span>p10</span></div>'
         f'<div class="i-st"><b>{f(r["o2_median"])}</b><span>median</span>'
         "</div></div></div>",
         '<div class="i-sec"><div class="i-lab">Time below threshold</div>'
         f'<div class="i-row">&lt; 1.4 mL/L: '
         f'<b>{f(r["pct_below_hypoxic"], 1)}%</b> &nbsp;&#183;&nbsp; '
         f'&lt; 2.8: <b>{f(r["pct_below_at_risk"], 1)}%</b></div></div>',
         '<div class="i-sec"><div class="i-lab">Record</div>'
         f'<div class="i-row">{years} &#183; {r.get("record_status", "")}'
         "</div>"
         f'<div class="i-row">{r["n_obs"]:,} '
         f'{"days" if cont else "casts"} '
         f'({r["n_summer_obs"]:,} in Jun&#8211;Oct)</div></div>']
    if mnote:
        h.append(f'<div class="i-note warn">{mnote}</div>')
    if r["site_code"] in core.OMZ_NOTES:
        h.append(f'<div class="i-note info">'
                 f'{core.OMZ_NOTES[r["site_code"]]}</div>')
    return "".join(h)


def site_records(cls: pd.DataFrame,
                 casts: pd.DataFrame | None) -> list[dict]:
    out = []
    for _, r in cls.iterrows():
        if pd.isna(r["lat"]) or pd.isna(r["lon"]):
            continue
        continuous = r["data_kind"] == "continuous"
        mnote = "" if continuous else core.method_note(r["site_code"], casts)
        out.append({
            "code": r["site_code"], "name": r["site_name"],
            "lat": round(float(r["lat"]), 5),
            "lon": round(float(r["lon"]), 5),
            "cont": continuous,
            "ended": str(r.get("record_status", "")).startswith("ended"),
            "conf": r["confidence"],
            "classes": {key: (r.get(col) if pd.notna(r.get(col)) else None)
                        for key, _, col, _ in LENSES},
            "detail": site_info_html(r, mnote),
        })
    return out


def cast_records(casts: pd.DataFrame | None) -> list[dict]:
    if casts is None or not len(casts):
        return []
    out = []
    for code, grp in casts.sort_values("time").groupby("site_code"):
        nominal = (grp["lat"].round(5).nunique() == 1
                   and grp["lon"].round(5).nunique() == 1 and len(grp) > 1)
        for i, (_, r) in enumerate(grp.iterrows()):
            dlat, dlon = core._jitter(i) if nominal else (0.0, 0.0)
            v = float(r["near_bottom_o2_ml_l"])
            out.append({
                "s": code, "t": r["time"].strftime("%Y-%m-%d %H:%M"),
                "y": int(r["time"].year), "o": round(v, 2),
                "c": core.class_of(v),
                "d": (None if pd.isna(r["cast_depth_m"])
                      else round(float(r["cast_depth_m"]))),
                "n": int(r["n_samples"]), "m": r["method"],
                "q": int(bool(r["qc_suspect"])),
                "j": int(nominal),
                "f": int(bool(r.get("dfo", False))),
                "la": round(float(r["lat"]) + dlat, 5),
                "lo": round(float(r["lon"]) + dlon, 5),
            })
    return out


def series_records(cls, daily) -> dict:
    """Raw per-site series for the client-side interactive chart:
    {code: {k: 'w'|'d', p: [[days_since_epoch, value], ...]}}. Long records
    are weekly means (same rule as the classic chart)."""
    if daily is None:
        return {}
    out = {}
    epoch = pd.Timestamp("1970-01-01")
    vcol = next(c for c in daily.columns
                if "o2" in c.lower() or "oxygen" in c.lower())
    for code, g in daily.groupby("site_code"):
        g = g.dropna(subset=[vcol]).sort_values("date").copy()
        if getattr(g["date"].dt, "tz", None) is not None:
            g["date"] = g["date"].dt.tz_convert("UTC").dt.tz_localize(None)
        if not len(g):
            continue
        if len(g) > 2500:
            w = (g.set_index("date")[vcol].resample("W").mean().dropna())
            pts = [[int((t - epoch).days), round(float(v), 2)]
                   for t, v in w.items()]
            kind = "w"
        else:
            pts = [[int((t - epoch).days), round(float(v), 2)]
                   for t, v in zip(g["date"], g[vcol])]
            kind = "d"
        out[code] = {"k": kind, "p": pts}
    return out


def model_series_records(model: pd.DataFrame | None) -> dict:
    """Weekly-mean predicted series per site for the dock chart:
    {code: {v: version, p: [[days_epoch, pred, lo, hi], ...]}}"""
    if model is None or "site_code" not in model.columns:
        return {}
    out = {}
    epoch = pd.Timestamp("1970-01-01")
    m = model[model["site_code"].notna()].copy()
    m["date"] = pd.to_datetime(m["date"], utc=True).dt.tz_localize(None)
    for code, g in m.groupby("site_code"):
        w = (g.set_index("date")[["o2_pred_ml_l", "o2_lo", "o2_hi"]]
             .resample("W").mean().dropna())
        out[code] = {
            "v": str(g["model_version"].iloc[0]),
            "p": [[int((t - epoch).days), round(r.o2_pred_ml_l, 2),
                   round(r.o2_lo, 2), round(r.o2_hi, 2)]
                  for t, r in w.iterrows()],
        }
    return out


def model_records(model: pd.DataFrame | None, cls: pd.DataFrame,
                  mseries: dict) -> list[dict]:
    """Hollow-diamond map markers, one per modeled site."""
    if model is None or not mseries:
        return []
    out = []
    coords = cls.set_index("site_code")[["lat", "lon", "site_name"]]
    m = model[model["site_code"].notna()].copy()
    for code, g in m.groupby("site_code"):
        if code not in coords.index:
            continue
        lat, lon, name = coords.loc[code]
        pred = g["o2_pred_ml_l"]
        p10, med = pred.quantile(.1), pred.median()
        cv = core.class_of(p10)
        pct14 = (pred < 1.4).mean() * 100
        span = (f"{pd.to_datetime(g['date'].min(), utc=True):%Y-%m}"
                f"&#8211;{pd.to_datetime(g['date'].max(), utc=True):%Y-%m}")
        ver = str(g["model_version"].iloc[0])
        detail = (
            f'<div class="i-h">{name}</div>'
            f'<div class="i-subh">{code} &#183; <b>MODELED</b> &#183; '
            f'{ver}</div>'
            '<div class="i-sec"><div class="i-lab">Predicted status</div>'
            '<div class="i-pills">' + _pill("Typical low (pred p10)", cv)
            + "</div></div>"
            '<div class="i-sec"><div class="i-lab">Predicted oxygen '
            "(mL/L)</div>"
            '<div class="i-stats">'
            f'<div class="i-st"><b>{p10:.2f}</b><span>p10</span></div>'
            f'<div class="i-st"><b>{med:.2f}</b><span>median</span></div>'
            f'<div class="i-st"><b>{pct14:.0f}%</b><span>&lt;1.4</span>'
            "</div></div></div>"
            '<div class="i-sec"><div class="i-lab">Prediction record</div>'
            f'<div class="i-row">{span} &#183; daily &#183; '
            f'{len(g):,} predictions</div></div>'
            '<div class="i-note info">Every value here is a <b>model '
            "prediction</b>, never an observation. The chart shows the "
            "prediction as a dashed line with its uncertainty band, over "
            "the station's real casts.</div>")
        out.append({"lat": round(float(lat), 5),
                    "lon": round(float(lon), 5), "cls": cv,
                    "tip": ("<div class='tt-h'>" + str(name) +
                            " <span class='tt-b'>MODELED</span></div>"
                            "<div class='tt-r'><span class='tt-dot' "
                            "style='background:" + core.COLORS[cv] +
                            "'></span>" + core.LABELS[cv] +
                            " <span class='tt-mut'>(pred p10 &#183; " +
                            ver + ")</span></div>"),
                    "detail": detail, "key": code})
    return out


# -- static assets -----------------------------------------------------------
def _v(path: Path) -> str:
    import hashlib
    return hashlib.md5(path.read_bytes()).hexdigest()[:8]


def write_assets(vi: dict) -> None:
    a = OUT_DIR / "assets"
    a.mkdir(parents=True, exist_ok=True)
    relief = a / "gebco_relief.png"
    if vi.get("relief") and relief.exists():
        vi["relief"]["url"] = f"assets/gebco_relief.png?v={_v(relief)}"
    payload = json.dumps(vi, separators=(",", ":")).replace("</", "<\\/")
    (a / "data.js").write_text("window.VI=" + payload + ";\n")
    (a / "style.css").write_text(STYLE)
    (a / "app.js").write_text(APP_JS)
    vi["asset_v"] = {"data": _v(a / "data.js"),
                     "style": _v(a / "style.css"),
                     "app": _v(a / "app.js")}
    (OUT_DIR / "index.html").write_text(index_html(vi))


def index_html(vi: dict) -> str:
    season_opts = "\n".join(
        f'<option value="{k}">{SEASON_NAMES[k]} ({k.upper()})</option>'
        for k, _ in core.SEASONS)
    conf_inputs = "\n".join(
        f'<label><input type="checkbox" name="conf" value="{c}" checked> '
        f'{c.capitalize()}</label>' for c in ["high", "medium", "low"])
    y0, y1 = vi["meta"]["cast_years"]
    G = "#9db8c9"
    def _g(svg, name, sub=""):
        sub = f'<div class="lg-sub">{sub}</div>' if sub else ""
        return (f'<div class="lg-row"><svg class="lg-g" width="26" '
                f'height="26" viewBox="0 0 26 26">{svg}</svg>'
                f'<div class="lg-t">{name}{sub}</div></div>')
    year_opts0 = "".join(
        f'<option value="{y}"{" selected" if y == y0 else ""}>{y}</option>'
        for y in range(y0, y1 + 1))
    year_opts1 = "".join(
        f'<option value="{y}"{" selected" if y == y1 else ""}>{y}</option>'
        for y in range(y0, y1 + 1))
    relief_ctl = ('<label title="Hillshaded GEBCO grid drawn over the '
                  'basemap"><input type="checkbox" id="ck-relief" checked> '
                  'GEBCO shaded relief</label>' if vi.get("relief") else "")
    bathy_ctl = ('<label><input type="checkbox" id="ck-bathy" checked> '
                 'Isobaths (100&#8211;2000 m)</label>'
                 if vi.get("bathy") else "")
    model_ctl = ('<label><input type="checkbox" id="ck-model"> '
                 'Modeled predictions</label>' if vi.get("model") else "")
    model_leg = (_g('<polygon points="13,4 22,13 13,22 4,13" fill="none" '
                    'stroke="#495057" stroke-width="2.4"/>',
                    "Modeled prediction",
                    "hollow diamond &#8212; never an observation")
                 if vi.get("model") else "")
    bathy_leg = (_g('<line x1="3" y1="13" x2="23" y2="13" stroke="#4292c6" '
                    'stroke-width="2"/>', "GEBCO isobath",
                    "depth contours, 100&#8211;2000 m")
                 if vi.get("bathy") else "")
    chips = "".join(
        f'<button class="chip" data-class="{c}" aria-pressed="true">'
        f'<span class="sw" style="background:{core.COLORS[c]}"></span>'
        f'<span class="chip-name">{core.LABELS[c]}</span>'
        f'<span class="chip-rng">{rng}</span></button>' for c, rng in
        [("good", "&#8805; 2.8 mL/L"), ("at_risk", "1.4 &#8211; 2.8"),
         ("hypoxic", "0.1 &#8211; 1.4"), ("anoxic", "&lt; 0.1"),
         ("unclassified", "no data")])
    glyph_rows = "".join([
        _g(f'<circle cx="13" cy="13" r="8" fill="{G}" stroke="#333" '
           f'stroke-width="2.5"/>', "Continuous sensor site",
           "records every day"),
        _g(f'<circle cx="13" cy="13" r="5.5" fill="{G}" stroke="#fff" '
           f'stroke-width="1.6"/>', "Ship-visit station",
           "sampled a few times a year"),
        _g(f'<circle cx="13" cy="13" r="3.2" fill="{G}" stroke="#fff" '
           f'stroke-width="1"/>', "One CTD cast",
           "colored by its own value"),
        _g('<circle cx="13" cy="13" r="3.4" fill="none" stroke="#868e96" '
           'stroke-width="1.6"/>', "QC-flagged cast",
           "implausible reading, kept out of stats"),
        _g(f'<circle cx="13" cy="13" r="8" fill="{G}" stroke="#333" '
           f'stroke-width="2.5" stroke-dasharray="4 3"/>',
           "Dashed = record ended"),
        _g(f'<circle cx="13" cy="13" r="8" fill="{G}" opacity=".4" '
           f'stroke="#333" stroke-width="2"/>',
           "Faded = lower confidence"),
    ])
    opts = "".join(f'<option value="{s["code"]} - {s["name"]}">'
                   for s in vi["sites"])
    m = vi["meta"]
    vv = vi.get("asset_v", {"data": "0", "style": "0", "app": "0"})
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vancouver Island near-bottom oxygen</title>
<link rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.min.css">
<link rel="stylesheet" href="assets/style.css?v={vv['style']}">
</head>
<body>
<button id="sb-toggle" aria-label="Toggle sidebar">&#9776;</button>
<aside id="sidebar">
  <header>
    <div class="hrow">
      <h1>VI near-bottom O&#8322;</h1>
      <button id="theme-btn" aria-label="Switch color theme"
        title="Light / dark mode">&#9789;</button>
    </div>
    <p class="sub">{m['n_sites']} sites &#183; {m['n_casts']} casts &#183;
      built {m['generated']}</p>
  </header>
  <section>
    <input id="search" list="site-list" placeholder="Find a site&#8230;"
      autocomplete="off" aria-label="Find a site">
    <datalist id="site-list">{opts}</datalist>
  </section>
  <section>
    <h2>Status lens</h2>
    <div class="stack" id="lens-group">
      <label data-tip="10th percentile of all daily near-bottom O&#8322;
values &#8212; oxygen dips below this only ~10% of days. A stable measure
of how low it typically gets.">
        <input type="radio" name="lens" value="exposure" checked>
        Typical low <span class="hint" tabindex="0" aria-label="What is
typical low?">?</span></label>
      <label data-tip="The single lowest daily value ever recorded at the
site &#8212; a one-day extreme, sensitive to outliers.">
        <input type="radio" name="lens" value="worst">
        Worst case <span class="hint" tabindex="0" aria-label="What is
worst case?">?</span></label>
      <label data-tip="Same 10th-percentile idea, computed only within the
chosen three-month season." class="lseason">
        <input type="radio" name="lens" value="seasonal"> Seasonal
        <select id="season-select" aria-label="Season">{season_opts}</select>
      </label>
    </div>
  </section>
  <section>
    <h2>Basemap</h2>
    <div class="stack" id="base-group">
      <label><input type="radio" name="base" value="ocean" checked>
        Esri Ocean</label>
      <label><input type="radio" name="base" value="imagery">
        Esri Imagery</label>
      <label><input type="radio" name="base" value="dark">
        Esri Dark Gray</label>
    </div>
  </section>
  <section>
    <h2>Overlays</h2>
    <div class="stack">
      {relief_ctl}
      {bathy_ctl}
      <label><input type="checkbox" id="ck-casts" checked>
        CTD casts</label>
      <div id="cast-filters" class="indent">
        <label data-tip="A few ONC casts read above 9 mL/L &#8212;
physically implausible supersaturation, most likely a sensor problem.
They are drawn as hollow grey dots and already excluded from all site
statistics; untick to hide them from the map too.">
          <input type="checkbox" id="ck-suspect" checked>
          Show QC-flagged casts <span class="hint" tabindex="0"
          aria-label="What are QC-flagged casts?">?</span></label>
        <div class="years">
          <span>Years</span>
          <select id="yr0" aria-label="First year">{year_opts0}</select>
          <span>&#8211;</span>
          <select id="yr1" aria-label="Last year">{year_opts1}</select>
        </div>
        <div class="presets">
          <button type="button" class="preset" data-span="all">All years
          </button>
          <button type="button" class="preset" data-span="5">Last 5 yrs
          </button>
          <button type="button" class="preset" data-span="1">Last year
          </button>
        </div>
      </div>
      {model_ctl}
    </div>
  </section>
  <section>
    <h2>Site confidence</h2>
    <div class="row" id="conf-group">{conf_inputs}</div>
  </section>
  <footer>Study box {m['box']} &#183; thresholds 1.4 / 2.8 mL/L</footer>
</aside>
<main id="map-wrap">
  <div id="map"></div>
  <div class="map-ui">
    <button id="fit-btn" title="Zoom to fit all visible data">&#8689; Fit
      data</button>
    <button id="legend-btn" aria-expanded="false"
      title="Legend &amp; visibility">Legend &#9662;</button>
    <div id="legend-panel" class="hidden">
      <div class="lhead">Near-bottom oxygen status</div>
      <div class="lsub2">Click a status to hide or show those points</div>
      <div class="chips">{chips}</div>
      <div class="lhead">What the markers mean</div>
      {glyph_rows}
      {model_leg}{bathy_leg}
      <div class="lfoot">Click any marker to open its stats and full time
        series below the map.</div>
    </div>
  </div>
</main>
<div id="detail" class="hidden" role="region" aria-label="Site details">
  <button id="d-close" aria-label="Close">&#215;</button>
  <div class="d-cols">
    <div id="d-info"></div>
    <div id="d-chart"></div>
  </div>
</div>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.min.js">
</script>
<script src="assets/data.js?v={vv['data']}"></script>
<script src="assets/app.js?v={vv['app']}"></script>
</body>
</html>
"""


STYLE = """:root{--sbw:296px}
html[data-theme=light]{--bg:#eef6f9;--panel:#ffffff;--panel2:#f2f8fb;
--ink:#0f2a3d;--mut:#557089;--line:#d3e4ee;--accent:#0e7c9c;
--accent-ink:#ffffff;--shadow:0 10px 30px rgba(15,42,61,.16)}
html[data-theme=dark]{--bg:#0a1826;--panel:#102638;--panel2:#0d2030;
--ink:#e7f2f8;--mut:#8fabc1;--line:#1f3d55;--accent:#3ec2dd;
--accent-ink:#04222c;--shadow:0 12px 34px rgba(0,0,0,.55)}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,
Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}
button,input,select{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
#map-wrap{position:fixed;inset:0 0 0 var(--sbw)}
#map{position:absolute;inset:0;background:var(--bg)}
#sidebar{position:fixed;inset:0 auto 0 0;width:var(--sbw);
background:var(--panel);border-right:1px solid var(--line);
overflow-y:auto;padding:14px 16px 10px;z-index:1100;display:flex;
flex-direction:column;gap:12px}
.hrow{display:flex;align-items:center;justify-content:space-between}
#sidebar h1{font-size:17px;margin:0;letter-spacing:.01em}
#theme-btn{width:32px;height:32px;border:1px solid var(--line);
border-radius:9px;background:var(--panel2);cursor:pointer;font-size:15px}
#theme-btn:hover{border-color:var(--accent)}
#sidebar .sub{margin:3px 0 0;color:var(--mut);font-size:12px}
#sidebar h2{font-size:11px;letter-spacing:.09em;text-transform:uppercase;
color:var(--accent);margin:0 0 7px}
#sidebar section{border-top:1px solid var(--line);padding-top:11px}
.stack{display:flex;flex-direction:column;gap:7px}
.row{display:flex;gap:14px;flex-wrap:wrap}
.stack label,.row label{display:flex;gap:8px;align-items:center;
cursor:pointer;min-height:24px}
input[type=radio],input[type=checkbox]{accent-color:var(--accent);
width:15px;height:15px}
.indent{margin-left:23px;display:flex;flex-direction:column;gap:7px}
.hint{display:inline-flex;align-items:center;justify-content:center;
width:15px;height:15px;border-radius:50%;background:var(--panel2);
border:1px solid var(--line);color:var(--mut);font-size:10px;cursor:help}
label[data-tip]{position:relative;cursor:help}
label[data-tip]:hover::after,label[data-tip]:focus-within::after{
content:attr(data-tip);position:absolute;top:calc(100% + 6px);left:22px;
width:236px;background:var(--panel);color:var(--ink);border:1px solid
var(--accent);border-radius:10px;padding:9px 11px;font-size:12px;
line-height:1.45;box-shadow:var(--shadow);z-index:2000;white-space:normal;
pointer-events:none}
.lseason select{margin-left:2px;padding:3px 6px;border:1px solid
var(--line);border-radius:7px;background:var(--panel2);max-width:150px}
#search{width:100%;padding:8px 10px;border:1px solid var(--line);
border-radius:9px;font-size:13px;background:var(--panel2)}
#search::placeholder{color:var(--mut)}
.years{color:var(--mut);font-size:12px;display:flex;gap:7px;
align-items:center}
.years select{padding:3px 6px;border:1px solid var(--line);
border-radius:7px;background:var(--panel2)}
.presets{display:flex;gap:6px;flex-wrap:wrap}
.preset{border:1px solid var(--line);background:var(--panel2);
border-radius:999px;padding:3px 10px;font-size:11.5px;cursor:pointer;
color:var(--mut)}
.preset:hover{border-color:var(--accent);color:var(--ink)}
#sidebar footer{margin-top:auto;color:var(--mut);font-size:11px;
border-top:1px solid var(--line);padding-top:9px}
.map-ui{position:absolute;top:12px;right:12px;z-index:1000;display:flex;
gap:8px;align-items:flex-start;flex-wrap:wrap;justify-content:flex-end}
.map-ui>button{border:1px solid var(--line);background:var(--panel);
border-radius:10px;padding:7px 11px;cursor:pointer;font-size:13px;
box-shadow:var(--shadow)}
.map-ui>button:hover{border-color:var(--accent)}
#legend-btn[aria-expanded=true]{background:var(--accent);
color:var(--accent-ink);border-color:var(--accent)}
#legend-panel{position:absolute;top:44px;right:0;width:342px;
max-width:calc(100vw - 24px);background:var(--panel);border:1px solid
var(--line);border-radius:13px;box-shadow:var(--shadow);
padding:13px 14px;font-size:12.5px}
.lhead{font-weight:700;margin:9px 0 6px;font-size:11px;letter-spacing:.08em;
text-transform:uppercase;color:var(--accent)}
.lhead:first-child{margin-top:0}
.lsub{font-weight:400;text-transform:none;letter-spacing:0;
color:var(--mut)}
.lsub2{color:var(--mut);font-size:11.5px;margin:-3px 0 7px}
.chips{display:flex;flex-direction:column;gap:5px}
.chip{display:flex;gap:9px;align-items:center;border:1px solid
var(--line);background:var(--panel2);border-radius:9px;
padding:5px 10px;cursor:pointer;font-size:12.5px;text-align:left}
.chip:hover{border-color:var(--accent)}
.chip[aria-pressed=false]{opacity:.42}
.chip[aria-pressed=false] .chip-name{text-decoration:line-through}
.chip-name{font-weight:600;min-width:88px}
.chip-rng{color:var(--mut);margin-left:auto}
.lg-row{display:flex;gap:10px;align-items:center;margin:6px 0}
.lg-g{flex:0 0 26px}
.lg-t{font-size:12.5px;line-height:1.25}
.lg-sub{color:var(--mut);font-size:11.5px}
.lfoot{color:var(--mut);font-size:11.5px;border-top:1px solid var(--line);
margin-top:9px;padding-top:8px}
.sw{display:inline-block;width:11px;height:11px;border-radius:50%;
box-shadow:inset 0 0 0 1px rgba(0,0,0,.25)}
.sw-line{display:inline-block;width:16px;height:0;border-top:2px solid
#4292c6;vertical-align:3px;margin-right:2px}
.sw-diamond{color:var(--mut);font-weight:700}
.lrow{margin:4px 0;color:var(--ink)}
.ring{display:inline-block;width:11px;height:11px;border-radius:50%;
background:#b6c9d8;vertical-align:-1px;margin-right:3px}
.ring.cont{border:2.5px solid #333}
.ring.visit{border:1.5px solid #fff;box-shadow:0 0 0 1px #8aa}
#detail{position:fixed;left:0;right:0;bottom:0;height:312px;
background:var(--panel);border-top:1px solid var(--line);
box-shadow:0 -10px 28px rgba(6,20,32,.28);padding:12px 46px 10px 16px;
z-index:1150;color:var(--ink)}
body.detail-open #map-wrap{bottom:312px}
body.detail-open #sidebar{bottom:312px}
#sidebar{transition:bottom .15s}
.d-cols{display:flex;gap:18px;height:100%}
#d-info{width:calc(var(--sbw) - 18px);min-width:250px;overflow:auto;
padding-right:10px;border-right:1px solid var(--line)}
#d-chart{flex:1;display:flex;align-items:center;justify-content:center;
min-width:0}
#d-chart{position:relative}
#d-chart svg.ichart{width:100%;height:100%;max-height:284px;display:block}
#d-chart .nochart{color:var(--mut);font-size:13px}
.cgrid{stroke:var(--line);stroke-width:1}
.cline{fill:none;stroke:#1c7ed6;stroke-width:1.7}
html[data-theme=dark] .cline{stroke:#57b6ff}
.cline-dot{fill:#1c7ed6}
.ccast{stroke:#ffffff;stroke-width:.8}
.ccast-q{fill:none;stroke:#868e96;stroke-width:1.3}
.cthr{stroke-dasharray:5 4;stroke-width:1.3}
.cthr.red{stroke:#e03131}.cthr.amber{stroke:#f59f00}
.clab{font-size:12.5px;fill:var(--mut);
font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.caxis{font-size:13px;font-weight:600;fill:var(--mut);
font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.lens-cap{fill:var(--accent);font-weight:700}
.clab.red{fill:#e03131}.clab.amber{fill:#f59f00}
.cend{text-anchor:end}.cmid{text-anchor:middle}.cmut{opacity:.75}
.creset{fill:var(--accent);cursor:pointer;font-weight:700}
.cmband{fill:#495057;opacity:.15;stroke:none}
html[data-theme=dark] .cmband{fill:#b7cbdb;opacity:.18}
.cmline{stroke:#495057;stroke-width:1.8;stroke-dasharray:6 4}
html[data-theme=dark] .cmline{stroke:#c6d8e6}
.ccross{stroke:var(--mut);stroke-width:1;stroke-dasharray:3 3}
.cfocus{fill:none;stroke:var(--accent);stroke-width:2}
.cbrush{fill:var(--accent);opacity:.16}
.hiddenattr{visibility:hidden}
.ctip{position:absolute;background:var(--panel);border:1px solid
var(--accent);border-radius:8px;padding:5px 9px;font-size:12px;
pointer-events:none;box-shadow:var(--shadow);white-space:nowrap;z-index:50}
#detail table td{color:var(--ink)}
.i-h{font-size:16px;font-weight:700;margin:2px 0 1px}
.i-subh{color:var(--mut);font-size:12px;margin-bottom:4px}
.i-sec{border-top:1px solid var(--line);padding:8px 0 6px}
.i-lab{font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;
color:var(--accent);margin-bottom:4px}
.i-row{font-size:12.5px;margin:2px 0}
.i-pills{display:flex;flex-direction:column;gap:5px}
.i-pill{display:inline-flex;align-items:center;gap:7px;background:
var(--panel2);border:1px solid var(--line);border-radius:999px;
padding:4px 11px;font-size:12.5px;width:fit-content}
.i-dot{width:10px;height:10px;border-radius:50%;
box-shadow:inset 0 0 0 1px rgba(0,0,0,.25)}
.i-stats{display:flex;gap:8px}
.i-st{flex:1;background:var(--panel2);border:1px solid var(--line);
border-radius:10px;padding:7px 4px;text-align:center}
.i-st b{display:block;font-size:15px}
.i-st span{color:var(--mut);font-size:10.5px;text-transform:uppercase;
letter-spacing:.06em}
.i-note{margin-top:8px;padding:7px 10px;border-radius:8px;font-size:11.5px;
line-height:1.45;background:var(--panel2);color:var(--ink)}
.i-note.warn{border-left:3px solid #f59f00}
.i-note.info{border-left:3px solid #5c7cfa}
.leaflet-tooltip.vi-tt{background:var(--panel);color:var(--ink);
border:1px solid var(--line);border-radius:9px;
box-shadow:var(--shadow);padding:7px 10px;font:12px/1.4 inherit}
.leaflet-tooltip.vi-tt::before{display:none}
.tt-h{font-weight:700;font-size:12.5px}
.tt-r{display:flex;align-items:center;gap:6px;margin-top:2px;
color:var(--ink)}
.tt-dot{width:9px;height:9px;border-radius:50%;
box-shadow:inset 0 0 0 1px rgba(0,0,0,.25);flex:0 0 9px}
.tt-mut{color:var(--mut)}
.tt-b{background:var(--panel2);border:1px solid var(--line);
border-radius:6px;padding:0 5px;font-size:10.5px;color:var(--mut)}
#d-close{position:absolute;top:8px;right:12px;border:1px solid
var(--line);border-radius:8px;background:var(--panel2);width:28px;
height:28px;font-size:16px;cursor:pointer;color:var(--mut)}
#d-close:hover{border-color:var(--accent);color:var(--ink)}
.hidden{display:none!important}
#sb-toggle{display:none;position:fixed;top:10px;left:10px;z-index:1300;
width:38px;height:38px;border:1px solid var(--line);border-radius:10px;
background:var(--panel);font-size:17px;cursor:pointer;
box-shadow:var(--shadow)}
html[data-theme=dark] .leaflet-container{background:#0a1826}
html[data-theme=dark] #detail div[style*='font-family']{color:var(--ink)}
html[data-theme=dark] #d-chart svg text{fill:#dbe9f2}
html[data-theme=dark] #d-body td[style*='color:#666']{color:#8fabc1!important}
html[data-theme=dark] #d-body div[style*='color:#888']{color:#8fabc1!important}
@media(max-width:880px){
#map-wrap{inset:0}
#sidebar{transform:translateX(-100%);transition:transform .18s;width:302px;
box-shadow:var(--shadow)}
#sidebar.open{transform:none}
#sb-toggle{display:block}
.map-ui{top:10px;right:10px}
#detail{left:0;height:56vh;padding:10px 44px 8px 12px}
body.detail-open #map-wrap{bottom:56vh}
body.detail-open #sidebar{bottom:0}
.d-cols{flex-direction:column;gap:10px}
#d-info{width:auto;min-width:0;order:2;flex:1;border-right:0;
border-top:1px solid var(--line);padding-top:8px}
#d-chart{order:1;min-height:150px;max-height:40%}}
"""


APP_JS = r"""(function () {
  "use strict";
  // pure helpers (exposed for tests)
  var H = {
    classForLens: function (site, lens) {
      return site.classes[lens] || null;
    },
    siteVisible: function (site, lens, confs, classes) {
      var c = site.classes[lens] || "unclassified";
      return confs.indexOf(site.conf) !== -1 && classes.indexOf(c) !== -1;
    },
    castVisible: function (c, f, classes) {
      return c.y >= f.y0 && c.y <= f.y1 && (f.suspect || !c.q) &&
        classes.indexOf(c.c) !== -1;
    }
  };
  window.VIAPP = H;

  // ---- interactive time-series chart (hover readout, drag-zoom) ----
  function IChart(host, data, colors, thresholds) {
    var NS = "http://www.w3.org/2000/svg";
    var pts = (data.series ? data.series.p : []).map(function (p) {
      return {d: p[0], v: p[1]};
    });
    var casts = (data.casts || []).map(function (c) {
      return {d: Date.parse(c.t.replace(" ", "T") + ":00Z") / 86400000,
              v: c.o, c: c.c, q: c.q, t: c.t.slice(0, 10)};
    });
    var mod = (data.model ? data.model.p : []).map(function (p) {
      return {d: p[0], v: p[1], lo: p[2], hi: p[3], mo: 1};
    });
    var all = pts.concat(casts).concat(mod);
    if (!all.length) { host.innerHTML =
      "<div class='nochart'>No time series for this point</div>"; return null; }
    var d0 = Math.min.apply(null, all.map(function (a) { return a.d; }));
    var d1 = Math.max.apply(null, all.map(function (a) { return a.d; }));
    if (d1 - d0 < 30) { d0 -= 15; d1 += 15; }
    var x0 = d0, x1 = d1, gap = data.series && data.series.k === "d" ? 10 : 21;
    var svg, tip, brushA = null, api;

    function el(tag, at, parent) {
      var e = document.createElementNS(NS, tag);
      for (var k in at) e.setAttribute(k, at[k]);
      (parent || svg).appendChild(e);
      return e;
    }
    function fmtDate(days) {
      return new Date(days * 86400000).toISOString().slice(0, 10);
    }
    function render() {
      host.innerHTML = "";
      var W = Math.max(320, host.getBoundingClientRect().width || 900);
      var H = Math.max(170, host.getBoundingClientRect().height || 260);
      var ML = 70, MR = 52, MT = 24, MB = 46;
      svg = document.createElementNS(NS, "svg");
      svg.setAttribute("viewBox", "0 0 " + W + " " + H);
      svg.setAttribute("class", "ichart");
      host.appendChild(svg);
      tip = document.createElement("div");
      tip.className = "ctip hidden";
      host.appendChild(tip);

      var vis = all.filter(function (a) { return a.d >= x0 && a.d <= x1; });
      if (!vis.length) vis = all;
      var vmax = Math.max.apply(null, vis.map(function (a) {
        return a.hi !== undefined ? Math.max(a.v, a.hi) : a.v;
      }));
      var yMax = Math.max(vmax * 1.12, 1.6), yMin = 0;
      var X = function (d) {
        return ML + (d - x0) / (x1 - x0) * (W - ML - MR);
      };
      var Y = function (v) {
        return MT + (1 - (v - yMin) / (yMax - yMin)) * (H - MT - MB);
      };
      var IX = function (px) {
        return x0 + (px - ML) / (W - ML - MR) * (x1 - x0);
      };

      // y grid + ticks
      var step = yMax > 6 ? 2 : yMax > 2.6 ? 1 : 0.5;
      for (var v = 0; v <= yMax + 1e-9; v += step) {
        el("line", {x1: ML, x2: W - MR, y1: Y(v), y2: Y(v),
          "class": "cgrid"});
        el("text", {x: ML - 7, y: Y(v) + 4, "text-anchor": "end",
          "class": "clab cend"})
          .textContent = (step < 1 ? v.toFixed(1) : v.toFixed(0));
      }
      var yt = el("text", {x: 18, y: (MT + H - MB) / 2,
        "text-anchor": "middle", "class": "caxis"});
      yt.setAttribute("transform", "rotate(-90 18 " +
        ((MT + H - MB) / 2) + ")");
      yt.textContent = "Near-bottom O\u2082 (mL/L)";
      el("text", {x: ML + (W - ML - MR) / 2, y: H - 7,
        "text-anchor": "middle", "class": "caxis"}).textContent = "Year";
      // x ticks: years, thinned to fit
      var yr0 = new Date(x0 * 86400000).getUTCFullYear() + 1;
      var yr1 = new Date(x1 * 86400000).getUTCFullYear();
      var every = Math.max(1, Math.ceil((yr1 - yr0 + 1) /
        Math.max(2, Math.floor((W - ML - MR) / 60))));
      for (var yy = yr0; yy <= yr1; yy++) {
        var dd = Date.UTC(yy, 0, 1) / 86400000;
        el("line", {x1: X(dd), x2: X(dd), y1: MT, y2: H - MB,
          "class": "cgrid"});
        if ((yy - yr0) % every === 0)
          el("text", {x: X(dd), y: H - MB + 18, "text-anchor": "middle",
            "class": "clab cmid"}).textContent = yy;
      }
      // thresholds
      thresholds.forEach(function (t, i) {
        if (t > yMax) return;
        el("line", {x1: ML, x2: W - MR, y1: Y(t), y2: Y(t),
          "class": i ? "cthr amber" : "cthr red"});
        el("text", {x: W - MR + 5, y: Y(t) + 4,
          "class": "clab " + (i ? "amber" : "red")}).textContent = t;
      });
      // line segments (gap-split)
      var seg = [];
      function flush() {
        if (seg.length > 1) {
          el("polyline", {fill: "none", points: seg.map(function (p) {
            return X(p.d) + "," + Y(p.v);
          }).join(" "), "class": "cline"});
        } else if (seg.length === 1) {
          el("circle", {cx: X(seg[0].d), cy: Y(seg[0].v), r: 2,
            "class": "cline-dot"});
        }
        seg = [];
      }
      pts.forEach(function (p) {
        if (p.d < x0 || p.d > x1) { flush(); return; }
        if (seg.length && p.d - seg[seg.length - 1].d > gap) flush();
        seg.push(p);
      });
      flush();
      // model band + dashed prediction line (never confusable with obs)
      if (mod.length) {
        var mvis = mod.filter(function (p) {
          return p.d >= x0 && p.d <= x1;
        });
        if (mvis.length > 1) {
          var up = mvis.map(function (p) {
            return X(p.d) + "," + Y(Math.min(p.hi, yMax));
          });
          var dn = mvis.slice().reverse().map(function (p) {
            return X(p.d) + "," + Y(Math.max(p.lo, yMin));
          });
          el("polygon", {points: up.concat(dn).join(" "),
            "class": "cmband"});
          el("polyline", {fill: "none", points: mvis.map(function (p) {
            return X(p.d) + "," + Y(p.v);
          }).join(" "), "class": "cmline"});
        }
      }
      // cast dots
      casts.forEach(function (c) {
        if (c.d < x0 || c.d > x1) return;
        el("circle", c.q
          ? {cx: X(c.d), cy: Y(Math.min(c.v, yMax)), r: 3.4,
             "class": "ccast-q"}
          : {cx: X(c.d), cy: Y(c.v), r: 3.4, fill: colors[c.c],
             "class": "ccast"});
      });
      // captions: lens (left), sampling kind or reset (right), hint (btm)
      if (data.lens)
        el("text", {x: ML, y: MT - 8, "class": "clab lens-cap"})
          .textContent = "Lens: " + data.lens;
      if (x0 > d0 || x1 < d1) {
        var rb = el("text", {x: W - MR, y: MT - 8, "text-anchor": "end",
          "class": "clab creset"});
        rb.textContent = "[reset zoom]";
        rb.addEventListener("click", function () {
          x0 = d0; x1 = d1; render();
        });
      } else {
        el("text", {x: W - MR, y: MT - 8, "text-anchor": "end",
          "class": "clab cend cmut"})
          .textContent = (data.series
            ? (data.series.k === "w" ? "weekly means" : "daily values")
            : "individual casts") +
            (data.model ? " \u00b7 modeled (dashed + band)" : "");
      }
      el("text", {x: W - MR, y: H - 7, "text-anchor": "end",
        "class": "clab cmut"})
        .textContent = "drag to zoom \u00b7 double-click resets";
      // interaction layers
      var cross = el("line", {x1: 0, x2: 0, y1: MT, y2: H - MB,
        "class": "ccross hiddenattr"});
      var focus = el("circle", {r: 4.4, "class": "cfocus hiddenattr"});
      var brush = el("rect", {y: MT, height: H - MT - MB,
        "class": "cbrush hiddenattr"});
      var cap = el("rect", {x: 0, y: 0, width: W, height: H,
        fill: "transparent"});

      function nearest(day) {
        var best = null, bd = 1e18;
        all.forEach(function (a) {
          if (a.d < x0 || a.d > x1) return;
          var dd = Math.abs(a.d - day);
          if (dd < bd) { bd = dd; best = a; }
        });
        return best;
      }
      function pxOf(ev) {
        var r = svg.getBoundingClientRect();
        return (ev.clientX - r.left) * (W / (r.width || W));
      }
      cap.addEventListener("mousemove", function (ev) {
        var px = pxOf(ev);
        if (brushA !== null) {
          var a = Math.min(brushA, px), b = Math.max(brushA, px);
          brush.setAttribute("x", a);
          brush.setAttribute("width", b - a);
          brush.classList.remove("hiddenattr");
        }
        var n = nearest(IX(px));
        if (!n) return;
        cross.setAttribute("x1", X(n.d));
        cross.setAttribute("x2", X(n.d));
        cross.classList.remove("hiddenattr");
        focus.setAttribute("cx", X(n.d));
        focus.setAttribute("cy", Y(Math.min(n.v, yMax)));
        focus.classList.remove("hiddenattr");
        tip.innerHTML = "<b>" + (n.t || fmtDate(n.d)) + "</b> \u00b7 " +
          (n.mo
            ? "modeled " + n.v.toFixed(2) + " (" + n.lo.toFixed(2) +
              "\u2013" + n.hi.toFixed(2) + ") mL/L"
            : n.v.toFixed(2) + " mL/L" +
              (n.c ? " \u00b7 " + (n.q ? "QC-suspect cast" : "cast") : ""));
        tip.classList.remove("hidden");
        var hr = host.getBoundingClientRect();
        var tx = (ev.clientX - hr.left) + 14;
        tip.style.left = Math.min(tx, (hr.width || W) - 170) + "px";
        tip.style.top = Math.max(6, ev.clientY - hr.top - 34) + "px";
      });
      cap.addEventListener("mouseleave", function () {
        cross.classList.add("hiddenattr");
        focus.classList.add("hiddenattr");
        tip.classList.add("hidden");
        brushA = null;
        brush.classList.add("hiddenattr");
      });
      cap.addEventListener("mousedown", function (ev) {
        brushA = pxOf(ev);
        ev.preventDefault();
      });
      cap.addEventListener("mouseup", function (ev) {
        if (brushA === null) return;
        var a = Math.min(brushA, pxOf(ev)), b = Math.max(brushA, pxOf(ev));
        brushA = null;
        if (b - a > 6) { x0 = IX(a); x1 = IX(b); render(); }
        else brush.classList.add("hiddenattr");
      });
      cap.addEventListener("dblclick", function () {
        x0 = d0; x1 = d1; render();
      });
    }
    render();
    api = {
      render: render,
      getDomain: function () { return [x0, x1]; },
      getFull: function () { return [d0, d1]; },
      setDomain: function (a, b) { x0 = a; x1 = b; render(); }
    };
    return api;
  }

  function initTheme() {
    var t;
    try { t = localStorage.getItem("vi-theme"); } catch (e) {}
    if (!t) t = (window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches)
      ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", t);
    return t;
  }
  var theme = initTheme();

  function boot() {
    if (!window.L || !document.getElementById("map")) return;
    var VI = window.VI;
    var map = L.map("map", {zoomControl: true})
      .setView([49.35, -124.9], 7);
    L.control.scale({position: "bottomright"}).addTo(map);
    H.map = map;

    var bases = {
      ocean: L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/" +
        "World_Ocean_Base/MapServer/tile/{z}/{y}/{x}",
        {attribution: "Tiles &copy; Esri &mdash; GEBCO, NOAA, CHS, OSU, " +
         "UNH, CSUMB, National Geographic, DeLorme, NAVTEQ, Esri",
         maxNativeZoom: 13, maxZoom: 18}),
      imagery: L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/" +
        "World_Imagery/MapServer/tile/{z}/{y}/{x}",
        {attribution: "Tiles &copy; Esri &mdash; Esri, i-cubed, USDA, " +
         "USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, " +
         "GIS User Community"}),
      dark: L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/" +
        "World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        {attribution: "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ",
         maxNativeZoom: 16, maxZoom: 18})
    };
    bases.ocean.addTo(map);

    map.createPane("relief").style.zIndex = 350;
    var reliefLayer = null;
    if (VI.relief) {
      reliefLayer = L.imageOverlay(VI.relief.url, VI.relief.bounds,
        {pane: "relief", opacity: 0.97,
         attribution: "GEBCO 2026 Grid (public domain)"}).addTo(map);
    }

    L.rectangle([[VI.box.s, VI.box.w], [VI.box.n, VI.box.e]],
      {color: "#557", weight: 1.2, dashArray: "6 4", fill: false,
       interactive: false}).addTo(map);

    // ---- detail dock (info left, time series right) ----
    var detail = document.getElementById("detail");
    var dInfo = document.getElementById("d-info");
    var dChart = document.getElementById("d-chart");
    var lastKey = null;
    function lensName() { return VI.lensNames[lens] || lens; }
    function renderDock(key) {
      lastKey = key;
      var series = VI.series[key] || null;
      var siteCasts = VI.casts.filter(function (c) { return c.s === key; });
      H.chart = IChart(dChart,
        {series: series, casts: siteCasts,
         model: (VI.modelSeries || {})[key] || null,
         lens: lensName()},
        VI.colors, VI.thresholds);
    }
    function openDetail(html, chartKey, hash, latlng) {
      dInfo.innerHTML = html;
      var slot = dInfo.querySelector("[data-chart]");
      if (slot) slot.remove();
      renderDock(chartKey || "");
      detail.classList.remove("hidden");
      document.body.classList.add("detail-open");
      map.invalidateSize();
      setTimeout(function () {
        map.invalidateSize();
        if (H.chart) H.chart.render();
        if (latlng) map.setView(latlng,
          Math.max(map.getZoom(), 9), {animate: false});
      }, 190);
      if (hash !== undefined) location.hash = hash;
    }
    window.addEventListener("resize", function () {
      if (H.chart && !detail.classList.contains("hidden")) H.chart.render();
    });
    document.getElementById("d-close").onclick = function () {
      detail.classList.add("hidden");
      document.body.classList.remove("detail-open");
      map.invalidateSize();
      if (location.hash) history.replaceState(null, "",
        location.pathname + location.search);
    };

    // ---- state ----
    var lens = "exposure";
    var confs = ["high", "medium", "low"];
    var classes = ["good", "at_risk", "hypoxic", "anoxic", "unclassified"];

    // ---- site markers ----
    var siteLayer = L.layerGroup().addTo(map);
    var siteMarkers = {};
    VI.sites.forEach(function (s) {
      var mk = L.circleMarker([s.lat, s.lon], {
        radius: s.cont ? 9 : 6,
        color: s.cont ? "#333333" : "#ffffff",
        weight: s.cont ? 2.5 : 1.5,
        dashArray: s.ended ? "4" : null,
        fillOpacity: VI.opacity[s.conf] || 0.6,
        fill: true
      });
      mk.on("click", function () {
        openDetail(s.detail, s.code, s.code, [s.lat, s.lon]);
      });
      mk.bindTooltip("", {sticky: true, className: "vi-tt"});
      siteMarkers[s.code] = mk;
    });

    function restyleSites() {
      var tag = VI.lensTags[lens];
      VI.sites.forEach(function (s) {
        var mk = siteMarkers[s.code];
        var c = H.classForLens(s, lens);
        mk.setStyle({fillColor: VI.colors[c] || VI.colors.unclassified});
        mk.setTooltipContent(
          "<div class='tt-h'>" + s.name + "</div>" +
          "<div class='tt-r'><span class='tt-dot' style='background:" +
          (VI.colors[c] || VI.colors.unclassified) + "'></span>" +
          (VI.labels[c] || "no data") +
          (tag ? " <span class='tt-mut'>(" + tag + ")</span>" : "") +
          "</div>");
        var on = H.siteVisible(s, lens, confs, classes);
        if (on && !siteLayer.hasLayer(mk)) siteLayer.addLayer(mk);
        if (!on && siteLayer.hasLayer(mk)) siteLayer.removeLayer(mk);
      });
    }
    restyleSites();

    // ---- casts ----
    var castLayer = L.layerGroup().addTo(map);
    var castOn = true;
    var castCanvas = L.canvas({padding: 0.4});
    var castMarkers = VI.casts.map(function (c) {
      var mk = L.circleMarker([c.la, c.lo], c.q
        ? {radius: 3, color: "#868e96", weight: 1.4, fill: false,
           renderer: castCanvas}
        : {radius: 3, color: "#ffffff", weight: 0.8, fill: true,
           fillColor: VI.colors[c.c], fillOpacity: 0.95,
           renderer: castCanvas});
      mk.bindTooltip(
        "<div class='tt-h'>" + c.s +
        (c.f ? " <span class='tt-b'>DFO</span>" : "") +
        (c.q ? " <span class='tt-b'>QC</span>" : "") + "</div>" +
        "<div class='tt-r'><span class='tt-dot' style='background:" +
        (c.q ? "#868e96" : VI.colors[c.c]) + "'></span><b>" +
        c.o.toFixed(2) + " mL/L</b>&nbsp;<span class='tt-mut'>" +
        VI.labels[c.c] + "</span></div>" +
        "<div class='tt-r tt-mut'>" + c.t.slice(0, 10) + "</div>",
        {sticky: true, className: "vi-tt"});
      mk.on("click", function () { openDetail(castHtml(c), c.s); });
      return mk;
    });
    function castHtml(c) {
      var h = "<div class='i-h'>" + c.s + "</div>" +
        "<div class='i-subh'>single CTD cast \u00b7 " +
        (c.f ? "DFO IOS CTD" : "ONC community fishers") + "</div>" +
        "<div class='i-sec'><div class='i-lab'>Reading</div>" +
        "<div class='i-pills'><span class='i-pill'>" +
        "<span class='i-dot' style='background:" +
        (c.q ? "#868e96" : VI.colors[c.c]) + "'></span><b>" +
        c.o.toFixed(2) + " mL/L</b>&nbsp;\u2192 " + VI.labels[c.c] +
        "</span></div></div>" +
        "<div class='i-sec'><div class='i-lab'>Cast</div>" +
        "<div class='i-row'>" + c.t + " UTC</div>" +
        "<div class='i-row'>depth " +
        (c.d === null ? "\u2013" : c.d + " m") + " \u00b7 " +
        c.n.toLocaleString() + " samples \u00b7 " + c.m + "</div></div>";
      if (c.q) h += "<div class='i-note warn'><b>QC-flagged:</b> reads " +
        "above the plausible range for this site; shown hollow and " +
        "excluded from all statistics.</div>";
      if (c.j) h += "<div class='i-note info'>Dot position jittered " +
        "~100\u2013800 m; all casts at this station share one nominal " +
        "coordinate.</div>";
      return h;
    }
    var castFilter = {y0: VI.meta.cast_years[0], y1: VI.meta.cast_years[1],
                      suspect: true};
    refreshCasts();
    function refreshCasts() {
      VI.casts.forEach(function (c, i) {
        var on = castOn && H.castVisible(c, castFilter, classes);
        var mk = castMarkers[i];
        if (on && !castLayer.hasLayer(mk)) castLayer.addLayer(mk);
        if (!on && castLayer.hasLayer(mk)) castLayer.removeLayer(mk);
      });
    }

    // ---- bathymetry isobaths ----
    var bathyLayer = null;
    if (VI.bathy) {
      bathyLayer = L.layerGroup(VI.bathy.map(function (lev) {
        return L.geoJSON({type: "Feature", properties: {},
          geometry: {type: "MultiLineString", coordinates: lev.lines}},
          {style: {color: lev.color, weight: 1.1, opacity: 0.75}})
          .bindTooltip(lev.depth + " m isobath (GEBCO)", {sticky: true});
      })).addTo(map);
    }

    // ---- modeled ----
    var modelLayer = null;
    if (VI.model) {
      modelLayer = L.layerGroup(VI.model.map(function (p) {
        var sz = p.small ? 14 : 18, h = sz / 2;
        var svg = "<svg width='" + sz + "' height='" + sz + "' xmlns='" +
          "http://www.w3.org/2000/svg'><polygon points='" + h + ",1.5 " +
          (sz - 1.5) + "," + h + " " + h + "," + (sz - 1.5) + " 1.5," + h +
          "' fill='rgba(255,255,255,.55)' stroke='" + VI.colors[p.cls] +
          "' stroke-width='2.6'/></svg>";
        var mk = L.marker([p.lat, p.lon], {icon: L.divIcon({html: svg,
          className: "", iconSize: [sz, sz], iconAnchor: [h, h]})});
        mk.bindTooltip(p.tip, {sticky: true});
        mk.on("click", function () { openDetail(p.detail, p.key); });
        return mk;
      }));
    }

    // ---- fit-to-data ----
    function fitData() {
      var pts = [];
      VI.sites.forEach(function (s) {
        if (H.siteVisible(s, lens, confs, classes)) pts.push([s.lat, s.lon]);
      });
      if (castOn) VI.casts.forEach(function (c) {
        if (H.castVisible(c, castFilter, classes)) pts.push([c.la, c.lo]);
      });
      if (!pts.length) return;
      map.fitBounds(L.latLngBounds(pts).pad(0.07));
    }
    document.getElementById("fit-btn").addEventListener("click", fitData);

    // ---- legend dropdown + class chips ----
    var lBtn = document.getElementById("legend-btn");
    var lPanel = document.getElementById("legend-panel");
    lBtn.addEventListener("click", function () {
      var open = lPanel.classList.toggle("hidden") === false;
      lBtn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !lPanel.classList.contains("hidden")) {
        lPanel.classList.add("hidden");
        lBtn.setAttribute("aria-expanded", "false");
      }
    });
    document.querySelectorAll(".chip").forEach(function (chip) {
      chip.addEventListener("click", function () {
        var c = chip.dataset.class;
        var i = classes.indexOf(c);
        if (i === -1) classes.push(c);
        else classes.splice(i, 1);
        chip.setAttribute("aria-pressed", i === -1 ? "true" : "false");
        restyleSites();
        refreshCasts();
      });
    });

    // ---- sidebar wiring ----
    var seasonSel = document.getElementById("season-select");
    function currentLens() {
      var r = document.querySelector("input[name=lens]:checked").value;
      return r === "seasonal" ? seasonSel.value : r;
    }
    function lensChanged() {
      restyleSites(); refreshCasts();
      if (lastKey && !detail.classList.contains("hidden"))
        renderDock(lastKey);
    }
    document.getElementById("lens-group").addEventListener("change",
      function () { lens = currentLens(); lensChanged(); });
    seasonSel.addEventListener("change", function () {
      document.querySelector("input[name=lens][value=seasonal]")
        .checked = true;
      lens = seasonSel.value;
      lensChanged();
    });
    document.getElementById("base-group").addEventListener("change",
      function (e) {
        Object.keys(bases).forEach(function (k) {
          if (map.hasLayer(bases[k])) map.removeLayer(bases[k]);
        });
        bases[e.target.value].addTo(map);
      });
    document.querySelectorAll("input[name=conf]").forEach(function (el) {
      el.addEventListener("change", function () {
        confs = Array.prototype.slice.call(
          document.querySelectorAll("input[name=conf]:checked"))
          .map(function (x) { return x.value; });
        restyleSites();
      });
    });
    var ckCasts = document.getElementById("ck-casts");
    ckCasts.addEventListener("change", function () {
      castOn = ckCasts.checked;
      document.getElementById("cast-filters").classList
        .toggle("hidden", !castOn);
      if (castOn) castLayer.addTo(map);
      else map.removeLayer(castLayer);
      refreshCasts();
    });
    document.getElementById("ck-suspect").addEventListener("change",
      function (e) { castFilter.suspect = e.target.checked; refreshCasts(); });
    var yr0 = document.getElementById("yr0"),
        yr1 = document.getElementById("yr1");
    function years() {
      var a = +yr0.value, b = +yr1.value;
      castFilter.y0 = Math.min(a, b);
      castFilter.y1 = Math.max(a, b);
      refreshCasts();
    }
    yr0.addEventListener("change", years);
    yr1.addEventListener("change", years);
    document.querySelectorAll(".preset").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var yMax = VI.meta.cast_years[1], yMin = VI.meta.cast_years[0];
        var span = btn.dataset.span;
        var a = span === "all" ? yMin : Math.max(yMin, yMax - (+span) + 1);
        yr0.value = String(a);
        yr1.value = String(yMax);
        years();
      });
    });
    var ckRelief = document.getElementById("ck-relief");
    if (ckRelief && reliefLayer) ckRelief.addEventListener("change",
      function (e) {
        if (e.target.checked) reliefLayer.addTo(map);
        else map.removeLayer(reliefLayer);
      });
    var ckBathy = document.getElementById("ck-bathy");
    if (ckBathy && bathyLayer) ckBathy.addEventListener("change",
      function (e) {
        if (e.target.checked) bathyLayer.addTo(map);
        else map.removeLayer(bathyLayer);
      });
    var ckModel = document.getElementById("ck-model");
    if (ckModel && modelLayer) ckModel.addEventListener("change",
      function (e) {
        if (e.target.checked) modelLayer.addTo(map);
        else map.removeLayer(modelLayer);
      });

    // ---- theme toggle ----
    var tBtn = document.getElementById("theme-btn");
    function paintThemeBtn() {
      tBtn.innerHTML = theme === "dark" ? "&#9788;" : "&#9789;";
    }
    paintThemeBtn();
    tBtn.addEventListener("click", function () {
      theme = theme === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", theme);
      try { localStorage.setItem("vi-theme", theme); } catch (e) {}
      paintThemeBtn();
    });

    // ---- search + deep link ----
    var byCode = {};
    VI.sites.forEach(function (s) { byCode[s.code] = s; });
    document.getElementById("search").addEventListener("change",
      function (e) {
        var code = e.target.value.split(" - ")[0].trim().toUpperCase();
        var s = byCode[code];
        if (!s) return;
        openDetail(s.detail, s.code, s.code, [s.lat, s.lon]);
      });
    // a #CODE link pans to the site and pulses its tooltip, but the dock
    // only opens on an actual click
    var initial = decodeURIComponent(location.hash.replace("#", ""))
      .toUpperCase();
    if (byCode[initial]) {
      var s0 = byCode[initial];
      map.setView([s0.lat, s0.lon], 11);
      var mk0 = siteMarkers[s0.code];
      if (mk0) {
        setTimeout(function () { mk0.openTooltip(); }, 300);
        setTimeout(function () { mk0.closeTooltip(); }, 3200);
      }
    }

    H.counts = function () {
      return {sites: siteLayer.getLayers().length,
              casts: castLayer.getLayers().length};
    };
    document.getElementById("sb-toggle").onclick = function () {
      document.getElementById("sidebar").classList.toggle("open");
    };
  }

  if (document.readyState !== "loading") boot();
  else document.addEventListener("DOMContentLoaded", boot);
})();
"""


def load_dfo_casts(d):
    p = d / "dfo_casts.csv"
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["time"])
    df["time"] = df["time"].dt.tz_localize(None) if df["time"].dt.tz is None \
        else df["time"].dt.tz_convert("UTC").dt.tz_localize(None)
    df["qc_suspect"] = False        # none exceed the >9 mL/L screen (max 8.96)
    df["dfo"] = True
    keep = ["site_code", "time", "cast_depth_m", "n_samples",
            "near_bottom_o2_ml_l", "method", "lat", "lon", "qc_suspect",
            "dfo"]
    return df[keep]


def main() -> int:
    d = core.find_data_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    cls, daily, casts, model = core.load_inputs(d)
    cls, daily, casts, dropped = core.apply_study_box(cls, daily, casts)
    if dropped:
        print(f"outside study box, not shown: {', '.join(dropped)}")

    dfo = load_dfo_casts(d)
    if dfo is not None:
        inb = dfo["lat"].between(core.BOX["s"], core.BOX["n"]) \
            & dfo["lon"].between(core.BOX["w"], core.BOX["e"])
        dfo = dfo[inb]
        casts = casts.assign(dfo=False) if casts is not None else None
        casts = pd.concat([casts, dfo], ignore_index=True) \
            if casts is not None else dfo
        print(f"merged dfo_casts.csv: +{len(dfo):,} casts at "
              f"{dfo['site_code'].nunique()} DFO stations")

    charts = {}      # dock charts are now rendered client-side from series
    sites = site_records(cls, casts)
    series = series_records(cls, daily)
    casts_rec = cast_records(casts)
    mseries = model_series_records(model)
    model_rec = model_records(model, cls, mseries)
    bathy_path = core.find_bathy(d)
    bathy = core.bathy_multilines(bathy_path) if bathy_path else None
    relief = None
    if bathy_path:
        (OUT_DIR / "assets").mkdir(parents=True, exist_ok=True)
        rb = core.bathy_relief(bathy_path,
                               OUT_DIR / "assets" / "gebco_relief.png")
        if rb:
            relief = {"url": "assets/gebco_relief.png", "bounds": rb}

    yrs = ([c["y"] for c in casts_rec] or [2019, date.today().year])
    vi = {
        "meta": {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                 "n_sites": len(sites), "n_casts": len(casts_rec),
                 "cast_years": [min(yrs), max(yrs)],
                 "box": (f"{core.BOX['s']}&#8211;{core.BOX['n']} N, "
                         f"{-core.BOX['w']}&#8211;{-core.BOX['e']} W")},
        "box": core.BOX, "colors": core.COLORS, "labels": core.LABELS,
        "opacity": core.OPACITY,
        "lensTags": {k: tag for k, _, _, tag in LENSES},
        "lensNames": {k: lbl for k, lbl, _, _ in LENSES},
        "sites": sites, "casts": casts_rec, "series": series,
        "thresholds": [1.4, 2.8],
        "model": model_rec or None, "modelSeries": mseries or None,
        "bathy": bathy, "relief": relief,
    }
    write_assets(vi)
    size = sum(p.stat().st_size for p in OUT_DIR.rglob("*") if p.is_file())
    print(f"wrote {OUT_DIR}/ ({size / 1e6:.1f} MB): index.html + assets "
          f"({len(sites)} sites, {len(casts_rec):,} casts, "
          f"{len(series)} series"
          + (f", {len(model_rec)} modeled sites" if model_rec else "")
          + (f", {sum(len(b['lines']) for b in bathy)} isobath segments"
             if bathy else "") + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
