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
LENSES = ([("exposure", "Exposure (10th percentile)", "class_exposure", ""),
           ("worst", "Worst case (record minimum)", "class_worst_case",
            "worst case")]
          + [(k, f"Season: {lbl} p10", f"{k}_class", f"{k.upper()} p10")
             for k, lbl in core.SEASONS])


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
    lens_inputs = "\n".join(
        f'<label><input type="radio" name="lens" value="{k}"'
        f'{" checked" if k == "exposure" else ""}> {lbl}</label>'
        for k, lbl, _, _ in LENSES)
    conf_inputs = "\n".join(
        f'<label><input type="checkbox" name="conf" value="{c}" checked> '
        f'{c.capitalize()} confidence</label>' for c in
        ["high", "medium", "low"])
    y0, y1 = vi["meta"]["cast_years"]
    bathy_ctl = ('<label><input type="checkbox" id="ck-bathy" checked> '
                 'Bathymetry (GEBCO isobaths)</label>'
                 if vi.get("bathy") else "")
    model_ctl = ('<label><input type="checkbox" id="ck-model"> '
                 'Modeled (predictions)</label>' if vi.get("model") else "")
    bathy_leg = ('<div><span class="sw-line"></span> GEBCO isobaths '
                 '(100&#8211;2000 m)</div>' if vi.get("bathy") else "")
    model_leg = ('<div><span class="sw-diamond">&#9671;</span> hollow diamond '
                 '= <b>modeled</b>, never an observation</div>'
                 if vi.get("model") else "")
    class_rows = "".join(
        f'<div><span class="sw" style="background:{core.COLORS[c]}"></span> '
        f'{core.LABELS[c]}{thr}</div>' for c, thr in
        [("good", " (&#8805; 2.8 mL/L)"), ("at_risk", " (&lt; 2.8)"),
         ("hypoxic", " (&lt; 1.4)"), ("anoxic", " (&lt; 0.1)"),
         ("unclassified", "")])
    bases = ([("gebco", "GEBCO shaded relief (local data)")]
             if vi.get("relief") else [])
    bases += [("ocean", "Esri Ocean (bathymetry shading)"),
              ("imagery", "Esri World Imagery"),
              ("carto", "Light (CartoDB)")]
    base_inputs = "\n".join(
        f'<label><input type="radio" name="base" value="{v}"'
        f'{" checked" if i == 0 else ""}> {lbl}</label>'
        for i, (v, lbl) in enumerate(bases))
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
    <h1>VI near-bottom O&#8322;</h1>
    <p class="sub">{m['n_sites']} sites &#183; {m['n_casts']} casts &#183;
      built {m['generated']}</p>
  </header>
  <section>
    <input id="search" list="site-list" placeholder="Find a site&#8230;"
      autocomplete="off">
    <datalist id="site-list">{opts}</datalist>
  </section>
  <section>
    <h2>Status lens</h2>
    <div class="stack" id="lens-group">{lens_inputs}</div>
  </section>
  <section>
    <h2>Basemap</h2>
    <div class="stack" id="base-group">{base_inputs}</div>
  </section>
  <section>
    <h2>Overlays &amp; filters</h2>
    <div class="stack">
      {bathy_ctl}
      <label><input type="checkbox" id="ck-casts"> Casts (individual)</label>
      <div id="cast-filters" class="indent hidden">
        <label><input type="checkbox" id="ck-suspect" checked>
          Show QC-suspect casts</label>
        <div class="years">Years <output id="yr-out">{y0}&#8211;{y1}</output>
          <input type="range" id="yr0" min="{y0}" max="{y1}" value="{y0}">
          <input type="range" id="yr1" min="{y0}" max="{y1}" value="{y1}">
        </div>
      </div>
      {model_ctl}
      <div class="gap"></div>
      {conf_inputs}
    </div>
  </section>
  <details open>
    <summary>Legend</summary>
    <div class="legend">
      {class_rows}
      <div class="gap"></div>
      <div><span class="ring cont"></span> large dark ring = continuous
        sensor</div>
      <div><span class="ring visit"></span> small white ring = ship-visit
        station</div>
      <div>Tiny dot = one cast (jittered for visibility); hollow grey =
        QC-suspect</div>
      {model_leg}{bathy_leg}
      <div>Faded fill = lower confidence; dashed = record ended</div>
      <div class="gap"></div>
      <div>Click any marker for stats + its time series.</div>
    </div>
  </details>
  <footer>Study box {m['box']} &#183; thresholds 1.4 / 2.8 mL/L &#183;
    seasons use p10</footer>
</aside>
<main id="map"></main>
<div id="detail" class="hidden">
  <button id="d-close" aria-label="Close">&#215;</button>
  <div id="d-body"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.min.js">
</script>
<script src="assets/data.js"></script>
<script src="assets/app.js"></script>
</body>
</html>
"""


STYLE = """:root{--bg:#fff;--ink:#1f2430;--mut:#6b7280;--line:#e5e7eb;
--accent:#1c7ed6;--sbw:292px}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,
Helvetica,Arial,sans-serif;color:var(--ink)}
#map{position:fixed;inset:0 0 0 var(--sbw)}
#sidebar{position:fixed;inset:0 auto 0 0;width:var(--sbw);background:var(--bg);
border-right:1px solid var(--line);overflow-y:auto;padding:14px 16px 10px;
z-index:1100;display:flex;flex-direction:column;gap:12px}
#sidebar header h1{font-size:17px;margin:0}
#sidebar .sub{margin:2px 0 0;color:var(--mut);font-size:12px}
#sidebar h2{font-size:11px;letter-spacing:.08em;text-transform:uppercase;
color:var(--mut);margin:0 0 6px}
#sidebar section{border-top:1px solid var(--line);padding-top:10px}
.stack{display:flex;flex-direction:column;gap:6px}
.stack label{display:flex;gap:8px;align-items:center;cursor:pointer}
.indent{margin-left:22px;display:flex;flex-direction:column;gap:6px}
.gap{height:6px}
#search{width:100%;padding:7px 9px;border:1px solid var(--line);
border-radius:8px;font-size:13px}
.years{color:var(--mut);font-size:12px}
.years output{color:var(--ink);font-weight:600;margin-left:4px}
.years input[type=range]{width:100%;accent-color:var(--accent)}
details{border-top:1px solid var(--line);padding-top:10px}
summary{cursor:pointer;font-size:11px;letter-spacing:.08em;
text-transform:uppercase;color:var(--mut)}
.legend{font-size:12px;display:flex;flex-direction:column;gap:4px;
margin-top:8px}
.sw{display:inline-block;width:11px;height:11px;border-radius:50%;
vertical-align:-1px;margin-right:4px}
.sw-line{display:inline-block;width:16px;height:0;border-top:2px solid
#2171b5;vertical-align:3px;margin-right:4px}
.sw-diamond{color:#495057;font-weight:700;margin-right:2px}
.ring{display:inline-block;width:11px;height:11px;border-radius:50%;
background:#cbd5e1;vertical-align:-1px;margin-right:4px}
.ring.cont{border:2.5px solid #333}
.ring.visit{border:1.5px solid #fff;box-shadow:0 0 0 1px #999}
#sidebar footer{margin-top:auto;color:var(--mut);font-size:11px;
border-top:1px solid var(--line);padding-top:8px}
#detail{position:fixed;top:14px;right:14px;width:430px;max-width:calc(100vw -
 28px);max-height:calc(100vh - 28px);overflow:auto;background:#fff;
border:1px solid var(--line);border-radius:12px;
box-shadow:0 8px 30px rgba(0,0,0,.18);padding:14px 16px;z-index:1200}
#d-close{position:absolute;top:6px;right:8px;border:0;background:none;
font-size:20px;cursor:pointer;color:var(--mut)}
.hidden{display:none!important}
#sb-toggle{display:none;position:fixed;top:10px;left:10px;z-index:1300;
width:38px;height:38px;border:1px solid var(--line);border-radius:9px;
background:#fff;font-size:17px;cursor:pointer;
box-shadow:0 1px 5px rgba(0,0,0,.2)}
@media(max-width:860px){
#map{inset:0}
#sidebar{transform:translateX(-100%);transition:transform .2s;width:300px;
box-shadow:6px 0 24px rgba(0,0,0,.18)}
#sidebar.open{transform:none}
#sb-toggle{display:block}
#detail{top:auto;bottom:12px;right:12px;left:12px;width:auto;
max-height:62vh}}
"""


APP_JS = r"""(function () {
  "use strict";
  // pure helpers (exposed for tests)
  var H = {
    classForLens: function (site, lens) {
      return site.classes[lens] || null;
    },
    siteVisible: function (site, confs) {
      return confs.indexOf(site.conf) !== -1;
    },
    castVisible: function (c, f) {
      return c.y >= f.y0 && c.y <= f.y1 && (f.suspect || !c.q);
    }
  };
  window.VIAPP = H;

  function boot() {
    if (!window.L || !document.getElementById("map")) return;
    var VI = window.VI;
    var map = L.map("map", {zoomControl: true})
      .setView([49.35, -124.9], 7);
    L.control.scale().addTo(map);

    function cartoTile() {
      return L.tileLayer(
        "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        {attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
         maxZoom: 19});
    }
    var bases = {
      carto: L.layerGroup([cartoTile()]),
      ocean: L.layerGroup([L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/" +
        "World_Ocean_Base/MapServer/tile/{z}/{y}/{x}",
        {attribution: "Tiles &copy; Esri &mdash; GEBCO, NOAA, CHS, OSU, " +
         "UNH, CSUMB, National Geographic, DeLorme, NAVTEQ, Esri",
         maxNativeZoom: 13, maxZoom: 18})]),
      imagery: L.layerGroup([L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/" +
        "World_Imagery/MapServer/tile/{z}/{y}/{x}",
        {attribution: "Tiles &copy; Esri &mdash; Esri, i-cubed, USDA, " +
         "USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, " +
         "GIS User Community"})])
    };
    map.createPane("relief").style.zIndex = 350;   // above tiles, below vectors
    if (VI.relief) {
      bases.gebco = L.layerGroup([cartoTile(),
        L.imageOverlay(VI.relief.url, VI.relief.bounds,
          {pane: "relief", attribution: "GEBCO 2026 Grid (public domain)"})]);
    }
    var startBase = document.querySelector("#base-group input:checked");
    bases[startBase ? startBase.value : "ocean"].addTo(map);

    // study box
    L.rectangle([[VI.box.s, VI.box.w], [VI.box.n, VI.box.e]],
      {color: "#555", weight: 1.2, dashArray: "6 4", fill: false,
       interactive: false}).addTo(map);

    // ---- detail panel ----
    var detail = document.getElementById("detail");
    var dBody = document.getElementById("d-body");
    function openDetail(html, chartKey, hash) {
      dBody.innerHTML = html;
      var slot = dBody.querySelector("[data-chart]");
      var key = chartKey || (slot && slot.dataset.chart);
      if (slot && key && VI.charts[key]) {
        slot.innerHTML = VI.charts[key];
        slot.style.minHeight = "0";
      }
      detail.classList.remove("hidden");
      if (hash !== undefined) location.hash = hash;
    }
    document.getElementById("d-close").onclick = function () {
      detail.classList.add("hidden");
      if (location.hash) history.replaceState(null, "",
        location.pathname + location.search);
    };

    // ---- site markers ----
    var lens = "exposure";
    var confs = ["high", "medium", "low"];
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
        openDetail(s.detail, s.code, s.code);
      });
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
        var on = H.siteVisible(s, confs);
        if (on && !siteLayer.hasLayer(mk)) siteLayer.addLayer(mk);
        if (!on && siteLayer.hasLayer(mk)) siteLayer.removeLayer(mk);
      });
    }
    restyleSites();

    // ---- casts ----
    var castLayer = L.layerGroup();
    var castMarkers = VI.casts.map(function (c) {
      var mk = L.circleMarker([c.la, c.lo], c.q
        ? {radius: 3, color: "#868e96", weight: 1.4, fill: false}
        : {radius: 3, color: "#ffffff", weight: 0.8, fill: true,
           fillColor: VI.colors[c.c], fillOpacity: 0.95});
      mk.bindTooltip(c.s + " - " + c.t.slice(0, 10) + " - " +
        c.o.toFixed(2) + " mL/L (" + c.m + ")" +
        (c.q ? " - QC-suspect" : ""), {sticky: true});
      mk.on("click", function () { openDetail(castHtml(c)); });
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
        var on = H.castVisible(c, castFilter);
        var mk = castMarkers[i];
        if (on && !castLayer.hasLayer(mk)) castLayer.addLayer(mk);
        if (!on && castLayer.hasLayer(mk)) castLayer.removeLayer(mk);
      });
    }
    refreshCasts();

    // ---- bathymetry ----
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

    // ---- sidebar wiring ----
    document.getElementById("lens-group").addEventListener("change",
      function (e) { lens = e.target.value; restyleSites(); });
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
      document.getElementById("cast-filters").classList
        .toggle("hidden", !ckCasts.checked);
      if (ckCasts.checked) castLayer.addTo(map);
      else map.removeLayer(castLayer);
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
                               OUT_DIR / "assets" / "gebco_relief.jpg")
        if rb:
            relief = {"url": "assets/gebco_relief.jpg", "bounds": rb}

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
