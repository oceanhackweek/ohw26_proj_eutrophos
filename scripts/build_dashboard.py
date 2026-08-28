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
from datetime import date
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
def site_records(cls: pd.DataFrame, charts: dict,
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
            "detail": core.popup_html(r, charts, None, mnote),
        })
    return out


def cast_records(casts: pd.DataFrame | None) -> list[dict]:
    if casts is None or not len(casts):
        return []
    out = []
    for code, grp in casts.sort_values("time").groupby("site_code"):
        for i, (_, r) in enumerate(grp.iterrows()):
            dlat, dlon = core._jitter(i)
            v = float(r["near_bottom_o2_ml_l"])
            out.append({
                "s": code, "t": r["time"].strftime("%Y-%m-%d %H:%M"),
                "y": int(r["time"].year), "o": round(v, 2),
                "c": core.class_of(v),
                "d": (None if pd.isna(r["cast_depth_m"])
                      else round(float(r["cast_depth_m"]))),
                "n": int(r["n_samples"]), "m": r["method"],
                "q": int(bool(r["qc_suspect"])),
                "la": round(float(r["lat"]) + dlat, 5),
                "lo": round(float(r["lon"]) + dlon, 5),
            })
    return out


def model_records(model: pd.DataFrame | None, cls: pd.DataFrame,
                  charts: dict) -> list[dict]:
    if model is None:
        return []
    out = []
    coords = cls.set_index("site_code")[["lat", "lon", "site_name"]]
    if "site_code" in model.columns:
        for code, grp in model[model["site_code"].notna()].groupby("site_code"):
            if code not in coords.index:
                continue
            lat, lon, name = coords.loc[code]
            cv = core.class_of(grp["o2_pred_ml_l"].quantile(.1))
            out.append({"lat": round(float(lat), 5),
                        "lon": round(float(lon), 5), "cls": cv,
                        "tip": (f"{name} - MODELED "
                                f"({grp['model_version'].iloc[0]}) - "
                                f"{core.LABELS[cv]} (pred p10)"),
                        "detail": core.model_popup(
                            name, f"({code}) - modeled", grp,
                            f"{code}::model", charts),
                        "key": f"{code}::model"})
    if {"lat", "lon"}.issubset(model.columns):
        sp = model[model["lat"].notna() & model["lon"].notna()]
        if "site_code" in sp.columns:
            sp = sp[sp["site_code"].isna()]
        sp = sp[sp["lat"].between(core.BOX["s"], core.BOX["n"])
                & sp["lon"].between(core.BOX["w"], core.BOX["e"])]
        for (lat, lon), grp in sp.groupby(["lat", "lon"]):
            key = f"model@{lat:.4f},{lon:.4f}"
            charts.setdefault(key, core.chart_svg(model_site=grp) or "")
            cv = core.class_of(grp["o2_pred_ml_l"].quantile(.1))
            out.append({"lat": round(float(lat), 5),
                        "lon": round(float(lon), 5), "cls": cv, "small": 1,
                        "tip": (f"MODELED grid point "
                                f"({grp['model_version'].iloc[0]}) - "
                                f"{core.LABELS[cv]} (pred p10)"),
                        "detail": core.model_popup(
                            f"{lat:.3f}N, {abs(lon):.3f}W",
                            "- modeled grid point", grp, key, charts),
                        "key": key})
    return out


# -- static assets -----------------------------------------------------------
def write_assets(vi: dict) -> None:
    a = OUT_DIR / "assets"
    a.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(vi, separators=(",", ":")).replace("</", "<\\/")
    (a / "data.js").write_text("window.VI=" + payload + ";\n")
    (a / "style.css").write_text(STYLE)
    (a / "app.js").write_text(APP_JS)
    (OUT_DIR / "index.html").write_text(index_html(vi))


def index_html(vi: dict) -> str:
    season_opts = "\n".join(
        f'<option value="{k}">{SEASON_NAMES[k]} ({k.upper()})</option>'
        for k, _ in core.SEASONS)
    conf_inputs = "\n".join(
        f'<label><input type="checkbox" name="conf" value="{c}" checked> '
        f'{c.capitalize()}</label>' for c in ["high", "medium", "low"])
    y0, y1 = vi["meta"]["cast_years"]
    relief_ctl = ('<label title="Hillshaded GEBCO grid drawn over the '
                  'basemap"><input type="checkbox" id="ck-relief" checked> '
                  'GEBCO shaded relief</label>' if vi.get("relief") else "")
    bathy_ctl = ('<label><input type="checkbox" id="ck-bathy" checked> '
                 'Isobaths (100&#8211;2000 m)</label>'
                 if vi.get("bathy") else "")
    model_ctl = ('<label><input type="checkbox" id="ck-model"> '
                 'Modeled predictions</label>' if vi.get("model") else "")
    model_leg = ('<div class="lrow"><span class="sw-diamond">&#9671;</span> '
                 'hollow diamond = <b>modeled</b>, never an observation</div>'
                 if vi.get("model") else "")
    bathy_leg = ('<div class="lrow"><span class="sw-line"></span> thin blue '
                 'lines = GEBCO isobaths</div>' if vi.get("bathy") else "")
    chips = "".join(
        f'<button class="chip" data-class="{c}" aria-pressed="true" '
        f'title="Click to hide/show {core.LABELS[c].lower()} points">'
        f'<span class="sw" style="background:{core.COLORS[c]}"></span>'
        f'{core.LABELS[c]}{thr}</button>' for c, thr in
        [("good", " &#8805;2.8"), ("at_risk", " &lt;2.8"),
         ("hypoxic", " &lt;1.4"), ("anoxic", " &lt;0.1"),
         ("unclassified", "")])
    opts = "".join(f'<option value="{s["code"]} - {s["name"]}">'
                   for s in vi["sites"])
    m = vi["meta"]
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vancouver Island near-bottom oxygen</title>
<link rel="stylesheet"
  href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.min.css">
<link rel="stylesheet" href="assets/style.css">
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
      <label><input type="checkbox" id="ck-casts"> Casts (individual)</label>
      <div id="cast-filters" class="indent hidden">
        <label><input type="checkbox" id="ck-suspect" checked>
          Show QC-suspect</label>
        <div class="years">Years <output id="yr-out">{y0}&#8211;{y1}</output>
          <input type="range" id="yr0" min="{y0}" max="{y1}" value="{y0}"
            aria-label="First year">
          <input type="range" id="yr1" min="{y0}" max="{y1}" value="{y1}"
            aria-label="Last year">
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
      <div class="lhead">Status classes <span class="lsub">(mL/L &#183;
        click to hide/show)</span></div>
      <div class="chips">{chips}</div>
      <div class="lhead">Reading the markers</div>
      <div class="lrow"><span class="ring cont"></span> big dark ring =
        continuous sensor</div>
      <div class="lrow"><span class="ring visit"></span> small white ring =
        ship-visit station</div>
      <div class="lrow">tiny dot = one cast (jittered &#177;&lt;1 km);
        hollow grey = QC-suspect</div>
      {model_leg}{bathy_leg}
      <div class="lrow">faded fill = lower confidence &#183; dashed = record
        ended</div>
      <div class="lrow">click any marker for stats + its time series</div>
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
<script src="assets/data.js"></script>
<script src="assets/app.js"></script>
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
.years{color:var(--mut);font-size:12px}
.years output{color:var(--ink);font-weight:600;margin-left:4px}
.years input[type=range]{width:100%;accent-color:var(--accent)}
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
#legend-panel{position:absolute;top:44px;right:0;width:322px;
max-width:calc(100vw - 24px);background:var(--panel);border:1px solid
var(--line);border-radius:13px;box-shadow:var(--shadow);
padding:13px 14px;font-size:12.5px}
.lhead{font-weight:700;margin:9px 0 6px;font-size:11px;letter-spacing:.08em;
text-transform:uppercase;color:var(--accent)}
.lhead:first-child{margin-top:0}
.lsub{font-weight:400;text-transform:none;letter-spacing:0;
color:var(--mut)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:inline-flex;gap:6px;align-items:center;border:1px solid
var(--line);background:var(--panel2);border-radius:999px;
padding:4px 10px;cursor:pointer;font-size:12px}
.chip:hover{border-color:var(--accent)}
.chip[aria-pressed=false]{opacity:.42;text-decoration:line-through}
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
#detail{position:fixed;left:var(--sbw);right:0;bottom:0;height:312px;
background:var(--panel);border-top:1px solid var(--line);
box-shadow:0 -10px 28px rgba(6,20,32,.28);padding:12px 46px 10px 16px;
z-index:1150;color:var(--ink)}
body.detail-open #map-wrap{bottom:312px}
.d-cols{display:flex;gap:18px;height:100%}
#d-info{width:344px;min-width:290px;overflow:auto;padding-right:8px;
border-right:1px solid var(--line)}
#d-chart{flex:1;display:flex;align-items:center;justify-content:center;
min-width:0}
#d-chart svg{width:100%;height:100%;max-height:280px}
#d-chart .nochart{color:var(--mut);font-size:13px}
#detail table td{color:var(--ink)}
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
    function openDetail(html, chartKey, hash) {
      dInfo.innerHTML = html;
      var slot = dInfo.querySelector("[data-chart]");
      var key = chartKey || (slot && slot.dataset.chart);
      if (slot) slot.remove();
      dChart.innerHTML = (key && VI.charts[key]) ? VI.charts[key]
        : "<div class='nochart'>No time series for this point</div>";
      detail.classList.remove("hidden");
      document.body.classList.add("detail-open");
      map.invalidateSize();
      setTimeout(function () { map.invalidateSize(); }, 180);
      if (hash !== undefined) location.hash = hash;
    }
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
      mk.on("click", function () { openDetail(s.detail, s.code, s.code); });
      mk.bindTooltip("", {sticky: true});
      siteMarkers[s.code] = mk;
    });

    function restyleSites() {
      var tag = VI.lensTags[lens];
      VI.sites.forEach(function (s) {
        var mk = siteMarkers[s.code];
        var c = H.classForLens(s, lens);
        mk.setStyle({fillColor: VI.colors[c] || VI.colors.unclassified});
        mk.setTooltipContent(s.name + " - " +
          (VI.labels[c] || "no data") + (tag ? " (" + tag + ")" : ""));
        var on = H.siteVisible(s, lens, confs, classes);
        if (on && !siteLayer.hasLayer(mk)) siteLayer.addLayer(mk);
        if (!on && siteLayer.hasLayer(mk)) siteLayer.removeLayer(mk);
      });
    }
    restyleSites();

    // ---- casts ----
    var castLayer = L.layerGroup();
    var castOn = false;
    var castMarkers = VI.casts.map(function (c) {
      var mk = L.circleMarker([c.la, c.lo], c.q
        ? {radius: 3, color: "#868e96", weight: 1.4, fill: false}
        : {radius: 3, color: "#ffffff", weight: 0.8, fill: true,
           fillColor: VI.colors[c.c], fillOpacity: 0.95});
      mk.bindTooltip(c.s + " - " + c.t.slice(0, 10) + " - " +
        c.o.toFixed(2) + " mL/L (" + c.m + ")" +
        (c.q ? " - QC-suspect" : ""), {sticky: true});
      mk.on("click", function () { openDetail(castHtml(c), c.s); });
      return mk;
    });
    function castHtml(c) {
      var rows = [["When", c.t + " UTC"],
        ["Near-bottom O&#8322;", c.o.toFixed(2) + " mL/L &#8594; " +
         VI.labels[c.c]],
        ["Cast depth", c.d === null ? "&#8211;" : c.d + " m"],
        ["Samples", c.n.toLocaleString()], ["Method", c.m]];
      if (c.q) rows.push(["QC", "<b>suspect</b> (&gt; site threshold; " +
        "shown hollow, excluded from stats)"]);
      return "<div style='font-family:sans-serif;font-size:12px'>" +
        "<b style='font-size:13px'>" + c.s + "</b> - single cast" +
        "<table style='margin-top:4px'>" + rows.map(function (r) {
          return "<tr><td style='color:#666;padding-right:8px'>" + r[0] +
            "</td><td>" + r[1] + "</td></tr>";
        }).join("") + "</table>" +
        "<div style='color:#888;margin-top:4px'>Dot position jittered " +
        "~100&#8211;800 m; all casts share the station's nominal " +
        "coordinate.</div></div>";
    }
    var castFilter = {y0: VI.meta.cast_years[0], y1: VI.meta.cast_years[1],
                      suspect: true};
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
    lBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = lPanel.classList.toggle("hidden") === false;
      lBtn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("click", function (e) {
      if (!lPanel.classList.contains("hidden") &&
          !lPanel.contains(e.target) && e.target !== lBtn) {
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
    document.getElementById("lens-group").addEventListener("change",
      function () { lens = currentLens(); restyleSites(); refreshCasts(); });
    seasonSel.addEventListener("change", function () {
      document.querySelector("input[name=lens][value=seasonal]")
        .checked = true;
      lens = seasonSel.value;
      restyleSites(); refreshCasts();
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
        yr1 = document.getElementById("yr1"),
        yrOut = document.getElementById("yr-out");
    function years() {
      var a = +yr0.value, b = +yr1.value;
      castFilter.y0 = Math.min(a, b);
      castFilter.y1 = Math.max(a, b);
      yrOut.textContent = castFilter.y0 + "\u2013" + castFilter.y1;
      refreshCasts();
    }
    yr0.addEventListener("input", years);
    yr1.addEventListener("input", years);
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
        map.setView([s.lat, s.lon], Math.max(map.getZoom(), 11));
        openDetail(s.detail, s.code, s.code);
      });
    var initial = decodeURIComponent(location.hash.replace("#", ""))
      .toUpperCase();
    if (byCode[initial]) {
      var s0 = byCode[initial];
      map.setView([s0.lat, s0.lon], 11);
      openDetail(s0.detail, s0.code);
    }

    document.getElementById("sb-toggle").onclick = function () {
      document.getElementById("sidebar").classList.toggle("open");
    };
  }

  if (document.readyState !== "loading") boot();
  else document.addEventListener("DOMContentLoaded", boot);
})();
"""


def main() -> int:
    d = core.find_data_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    cls, daily, casts, model = core.load_inputs(d)
    cls, daily, casts, dropped = core.apply_study_box(cls, daily, casts)
    if dropped:
        print(f"outside study box, not shown: {', '.join(dropped)}")

    charts = core.build_charts(cls, daily, casts, model)
    sites = site_records(cls, charts, casts)
    casts_rec = cast_records(casts)
    model_rec = model_records(model, cls, charts)
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
        "meta": {"generated": date.today().isoformat(),
                 "n_sites": len(sites), "n_casts": len(casts_rec),
                 "cast_years": [min(yrs), max(yrs)],
                 "box": (f"{core.BOX['s']}&#8211;{core.BOX['n']} N, "
                         f"{-core.BOX['w']}&#8211;{-core.BOX['e']} W")},
        "box": core.BOX, "colors": core.COLORS, "labels": core.LABELS,
        "opacity": core.OPACITY,
        "lensTags": {k: tag for k, _, _, tag in LENSES},
        "sites": sites, "casts": casts_rec, "charts": charts,
        "model": model_rec or None, "bathy": bathy, "relief": relief,
    }
    write_assets(vi)
    size = sum(p.stat().st_size for p in OUT_DIR.rglob("*") if p.is_file())
    print(f"wrote {OUT_DIR}/ ({size / 1e6:.1f} MB): index.html + assets "
          f"({len(sites)} sites, {len(casts_rec)} casts, "
          f"{len(charts)} charts"
          + (f", {len(model_rec)} model markers" if model_rec else "")
          + (f", {sum(len(b['lines']) for b in bathy)} isobath segments"
             if bathy else "") + ")")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
