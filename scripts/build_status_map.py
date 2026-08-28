"""
Build the Vancouver Island oxygen-status map from the derived CSVs.

Layers: two classification rules (exposure/p10 shown by default, worst-case
one toggle away), four season layers (DJF/MAM/JJA/SON, recolored by that
season's p10 class), an individual-cast layer, and — only when
model_predictions.csv exists — clearly marked "Modeled" layers. Popups show
the full stat table plus a small time-series chart: daily line for continuous
sites (site_daily.csv), cast dots for visit sites (cf_casts.csv), with the
1.4 / 2.8 mL/L threshold lines. Marker language: color = class, large
dark-ringed circle = continuous sensor, small white-ringed = ship-visit
casts, fill opacity = confidence, dashed outline = ended record. The study
box 48.1-50.1N, 126.4-122.8W is drawn and filters what is shown.

Inputs are auto-found in ./, data/derived/, or next to this script:
  site_classification.csv (required)
  site_daily.csv, cf_casts.csv (optional; popup charts / cast layer)
  model_predictions.csv (optional; skipped silently when absent)

Usage:  python build_status_map.py [dir | path/to/site_classification.csv]
Output: vi_oxygen_status_map.html - one self-contained file (basemap tiles
        and leaflet load from the web when opened).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import folium
from folium import plugins
import pandas as pd

# -- fixed conventions (do not change) ---------------------------------------
ANOXIC, HYPOXIC, AT_RISK = 0.1, 1.4, 2.8          # mL/L
COLORS = {"good": "#2f9e44", "at_risk": "#f59f00",
          "hypoxic": "#e03131", "anoxic": "#5f3dc4",
          "unclassified": "#868e96"}
OPACITY = {"high": 0.95, "medium": 0.70, "low": 0.45}
LABELS = {"good": "Good", "at_risk": "At risk",
          "hypoxic": "Hypoxic", "anoxic": "Anoxic",
          "unclassified": "Unclassified"}
BOX = {"s": 47.8, "n": 51.2, "w": -129.5, "e": -122.5}   # study box
# Expanded 2026-08-27 from the original south-island box (48.1..50.1 N,
# -126.4..-122.8 W) to cover the full VI coast + shelf for bathymetry and
# station-to-station O2 modeling. GEBCO grid should be downloaded with a
# ~0.3 deg margin: N 51.5, S 47.5, W -130.0, E -122.0 (see README).

SEASONS = [("djf", "DJF (winter)"), ("mam", "MAM (spring)"),
           ("jja", "JJA (summer)"), ("son", "SON (fall)")]

# OMZ context shown in the popups of the deep west-coast cabled sites.
OMZ_NOTES = {
    "BACAX": ("OMZ reference: Barkley Canyon axis sits inside the natural "
              "NE Pacific oxygen-minimum zone. Persistently low O&#8322; here "
              "reflects offshore deep water masses, not local deterioration."),
    "NCBC":  ("OMZ reference: this slope site lies on the upper margin of the "
              "NE Pacific oxygen-minimum zone; low O&#8322; is the expected "
              "natural state at this depth."),
}

# Casts flagged QC-suspect (spikes / supersaturation), per data caveats.
# Per-site thresholds so the rule stays explicit and easy to extend.
QC_SUSPECT_ML_L = {"CF226": 9.0}

DATA_FILES = {"cls": "site_classification.csv", "daily": "site_daily.csv",
              "casts": "cf_casts.csv", "model": "model_predictions.csv"}
OUT_NAME = "vi_oxygen_status_map.html"

OBS_LINE = "#1c7ed6"        # observed daily line (not a class color)
MODEL_INK = "#495057"       # modeled line/band (not a class color)


# -- small helpers -----------------------------------------------------------
def fmt(v, nd=2, suffix=""):
    return "-" if pd.isna(v) else f"{v:.{nd}f}{suffix}"


def class_of(v) -> str:
    if pd.isna(v):
        return "unclassified"
    if v < ANOXIC:
        return "anoxic"
    if v < HYPOXIC:
        return "hypoxic"
    if v < AT_RISK:
        return "at_risk"
    return "good"


def find_data_dir(arg: str | None) -> Path:
    """./, data/derived/, or next to the script; or an explicit dir/file."""
    if arg:
        p = Path(arg)
        d = p.parent if p.is_file() else p
        if (d / DATA_FILES["cls"]).is_file():
            return d
        sys.exit(f"error: {DATA_FILES['cls']} not found in {d}")
    for d in (Path("."), Path("data/derived"), Path(__file__).parent):
        if (d / DATA_FILES["cls"]).is_file():
            return d
    sys.exit("error: site_classification.csv not found in ./, data/derived/, "
             "or next to the script.\n"
             "Pass it explicitly: python build_status_map.py path/to/dir")


def load_inputs(d: Path):
    cls = pd.read_csv(d / DATA_FILES["cls"])

    daily = None
    if (d / DATA_FILES["daily"]).is_file():
        daily = pd.read_csv(d / DATA_FILES["daily"], parse_dates=["date"])

    casts = None
    if (d / DATA_FILES["casts"]).is_file():
        casts = pd.read_csv(d / DATA_FILES["casts"])
        casts["time"] = (pd.to_datetime(casts["time"], format="mixed",
                                        utc=True).dt.tz_localize(None))
        thr = casts["site_code"].map(QC_SUSPECT_ML_L)
        casts["qc_suspect"] = casts["near_bottom_o2_ml_l"] > thr.fillna(999.0)
        # lat/lon may be absent in older extracts -> join from classification
        if "lat" not in casts.columns or casts["lat"].isna().any():
            coords = cls.set_index("site_code")[["lat", "lon"]]
            for c in ("lat", "lon"):
                if c not in casts.columns:
                    casts[c] = pd.NA
                casts[c] = casts[c].fillna(casts["site_code"].map(coords[c]))

    model = None
    if (d / DATA_FILES["model"]).is_file():   # absent -> skipped silently
        model = pd.read_csv(d / DATA_FILES["model"], parse_dates=["date"])

    return cls, daily, casts, model


def apply_study_box(cls, daily, casts):
    inside = (cls["lat"].between(BOX["s"], BOX["n"])
              & cls["lon"].between(BOX["w"], BOX["e"]))
    dropped = cls.loc[~inside, "site_code"].tolist()
    cls = cls[inside].copy()
    keep = set(cls["site_code"])
    if daily is not None:
        daily = daily[daily["site_code"].isin(keep)]
    if casts is not None:
        casts = casts[casts["site_code"].isin(keep)]
    return cls, daily, casts, dropped


# -- popup time-series charts (priority 1) -----------------------------------
# Small inline SVGs, stored ONCE in a JS dict and injected into a popup's
# <div data-chart=...> slot on first open, so the six site layers don't each
# embed their own copy of every chart.
CH_W, CH_H = 372, 152
ML, MR, MT, MB = 27, 10, 12, 17


def _segments(ts, vs, max_gap):
    """Split an ordered (ordinal-day, value) series at gaps > max_gap days."""
    seg, prev = [], None
    for t, v in zip(ts, vs):
        if prev is not None and t - prev > max_gap:
            yield seg
            seg = []
        seg.append((t, v))
        prev = t
    if seg:
        yield seg


def _axes(parts, t0, t1, ymax, x, y):
    ih_bot = CH_H - MB
    # year grid + labels
    y0, y1 = pd.Timestamp.fromordinal(t0).year, pd.Timestamp.fromordinal(t1).year
    nyr = max(y1 - y0, 1)
    step = 1 if nyr <= 7 else 2 if nyr <= 15 else 5
    for yr in range(y0 + 1, y1 + 1):
        t = pd.Timestamp(yr, 1, 1).toordinal()
        if not (t0 <= t <= t1):
            continue
        gx = x(t)
        parts.append(f"<line x1='{gx:.1f}' y1='{MT}' x2='{gx:.1f}' "
                     f"y2='{ih_bot}' stroke='#e9ecef' stroke-width='1'/>")
        if (yr - y0) % step == 0:
            lab = str(yr) if nyr <= 9 else f"'{yr % 100:02d}"
            parts.append(f"<text x='{gx:.1f}' y='{CH_H - 5}' font-size='9' "
                         f"fill='#868e96' text-anchor='middle'>{lab}</text>")
    # y ticks
    ystep = 1 if ymax <= 6 else 2
    v = 0
    while v <= ymax + 1e-9:
        gy = y(v)
        parts.append(f"<line x1='{ML}' y1='{gy:.1f}' x2='{CH_W - MR}' "
                     f"y2='{gy:.1f}' stroke='#f1f3f5' stroke-width='1'/>")
        if gy > MT + 7:                  # skip label colliding with 'mL/L'
            parts.append(f"<text x='{ML - 3}' y='{gy + 3:.1f}' font-size='9' "
                         f"fill='#868e96' text-anchor='end'>{v:g}</text>")
        v += ystep
    # thresholds
    for thr, col in ((AT_RISK, COLORS["at_risk"]), (HYPOXIC, COLORS["hypoxic"])):
        if thr < ymax:
            gy = y(thr)
            parts.append(f"<line x1='{ML}' y1='{gy:.1f}' x2='{CH_W - MR}' "
                         f"y2='{gy:.1f}' stroke='{col}' stroke-width='1' "
                         f"stroke-dasharray='4 3' opacity='.9'/>")
            parts.append(f"<text x='{CH_W - MR - 1}' y='{gy - 2:.1f}' "
                         f"font-size='8.5' fill='{col}' "
                         f"text-anchor='end'>{thr}</text>")
    parts.append(f"<line x1='{ML}' y1='{ih_bot}' x2='{CH_W - MR}' "
                 f"y2='{ih_bot}' stroke='#adb5bd' stroke-width='1'/>")
    parts.append(f"<text x='{ML - 22}' y='{MT - 2}' font-size='9' "
                 f"fill='#868e96'>mL/L</text>")


def chart_svg(daily_site=None, casts_site=None, model_site=None) -> str | None:
    """One compact SVG time-series panel for a site popup."""
    ts_pool, v_pool = [], []

    dline = None
    if daily_site is not None and len(daily_site):
        s = daily_site.sort_values("date")
        note = ""
        if len(s) > 2500:                        # long records -> weekly means
            s = (s.set_index("date")["oxygen_ml_l"].resample("W").mean()
                 .dropna().reset_index())
            note, gap = "weekly means", 21
        else:
            s = s[["date", "oxygen_ml_l"]]
            note, gap = "daily means", 10
        dline = ([d.toordinal() for d in s["date"]],
                 s["oxygen_ml_l"].tolist(), note, gap)
        ts_pool += dline[0]
        v_pool += dline[1]

    cpts = None
    if casts_site is not None and len(casts_site):
        c = casts_site.sort_values("time")
        cpts = list(zip([t.toordinal() for t in c["time"]],
                        c["near_bottom_o2_ml_l"], c["method"],
                        c["qc_suspect"], c["time"].dt.strftime("%Y-%m-%d")))
        ts_pool += [p[0] for p in cpts]
        v_pool += [p[1] for p in cpts if not p[3]]   # suspects don't set scale

    mdl = None
    if model_site is not None and len(model_site):
        mo = model_site.sort_values("date")
        mdl = ([d.toordinal() for d in mo["date"]],
               mo["o2_pred_ml_l"].tolist(),
               mo["o2_lo"].tolist(), mo["o2_hi"].tolist())
        ts_pool += mdl[0]
        v_pool += [v for v in mdl[3] if pd.notna(v)] + mdl[1]

    if not ts_pool:
        return None

    t0, t1 = min(ts_pool), max(ts_pool)
    if t1 - t0 < 180:                            # very short/single-cast spans
        mid = (t0 + t1) // 2
        t0, t1 = mid - 200, mid + 200
    pad = max(int((t1 - t0) * 0.02), 4)
    t0, t1 = t0 - pad, t1 + pad
    vmax = max([v for v in v_pool if pd.notna(v)], default=3.0)
    ymax = max(3.6, min(12.0, math.ceil(vmax * 1.08 * 2) / 2))

    def x(t):
        return ML + (t - t0) * (CH_W - ML - MR) / (t1 - t0)

    def y(v):
        return MT + (1 - min(v, ymax) / ymax) * (CH_H - MT - MB)

    parts = [f"<svg viewBox='0 0 {CH_W} {CH_H}' width='{CH_W}' "
             f"height='{CH_H}' xmlns='http://www.w3.org/2000/svg' "
             f"font-family='sans-serif'>"]
    _axes(parts, t0, t1, ymax, x, y)

    if mdl:                                       # band under everything
        mt, mp, ml_, mh = mdl
        band = [p for p in zip(mt, mh) if pd.notna(p[1])]
        band += [p for p in reversed(list(zip(mt, ml_))) if pd.notna(p[1])]
        if band:
            pts = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in band)
            parts.append(f"<polygon points='{pts}' fill='{MODEL_INK}' "
                         f"opacity='.16'/>")
        for seg in _segments(mt, mp, 45):
            pts = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in seg)
            parts.append(f"<polyline points='{pts}' fill='none' "
                         f"stroke='{MODEL_INK}' stroke-width='1.5' "
                         f"stroke-dasharray='6 3'/>")
        parts.append(f"<text x='{ML + 3}' y='{MT + 8}' font-size='9' "
                     f"fill='{MODEL_INK}' font-weight='bold'>modeled "
                     f"(dashed) + band</text>")

    if dline:
        ts, vs, note, gap = dline
        for seg in _segments(ts, vs, gap):
            if len(seg) == 1:
                parts.append(f"<circle cx='{x(seg[0][0]):.1f}' "
                             f"cy='{y(seg[0][1]):.1f}' r='1.3' "
                             f"fill='{OBS_LINE}'/>")
            else:
                pts = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in seg)
                parts.append(f"<polyline points='{pts}' fill='none' "
                             f"stroke='{OBS_LINE}' stroke-width='1.1'/>")
        parts.append(f"<text x='{CH_W - MR}' y='{MT - 2}' font-size='9' "
                     f"fill='{OBS_LINE}' text-anchor='end'>{note}</text>")

    if cpts:
        n_sus = sum(1 for p in cpts if p[3])
        for t, v, meth, sus, dstr in cpts:
            cy = max(y(v), MT + 2)               # clip clamps spikes to top
            title = f"{dstr}: {v:.2f} mL/L ({meth})"
            if sus:
                parts.append(
                    f"<circle cx='{x(t):.1f}' cy='{cy:.1f}' r='2.6' "
                    f"fill='none' stroke='#868e96' stroke-width='1.3'>"
                    f"<title>{title} - QC-suspect (supersaturation)</title>"
                    f"</circle>")
            else:
                parts.append(
                    f"<circle cx='{x(t):.1f}' cy='{cy:.1f}' r='2.7' "
                    f"fill='{COLORS[class_of(v)]}' stroke='white' "
                    f"stroke-width='.7'><title>{title}</title></circle>")
        note = f"{len(cpts)} casts"
        if n_sus:
            note += f" ({n_sus} QC-suspect, hollow)"
        parts.append(f"<text x='{CH_W - MR}' y='{MT - 2}' font-size='9' "
                     f"fill='#495057' text-anchor='end'>{note}</text>")

    parts.append("</svg>")
    return "".join(parts)


def build_charts(cls, daily, casts, model):
    """{key: svg} - key = site_code for observed, 'CODE::model' for modeled."""
    charts = {}
    d_by = dict(tuple(daily.groupby("site_code"))) if daily is not None else {}
    c_by = dict(tuple(casts.groupby("site_code"))) if casts is not None else {}
    for _, r in cls.iterrows():
        code = r["site_code"]
        svg = chart_svg(daily_site=d_by.get(code), casts_site=c_by.get(code))
        if svg:
            charts[code] = svg
    if model is not None and "site_code" in model.columns:
        for code, g in model[model["site_code"].notna()].groupby("site_code"):
            svg = chart_svg(daily_site=d_by.get(code),
                            casts_site=c_by.get(code), model_site=g)
            if svg:
                charts[f"{code}::model"] = svg
    return charts


def chart_slot(key: str) -> str:
    return (f"<div data-chart='{key}' style='margin-top:6px;"
            f"min-height:{CH_H}px;color:#999;font-size:11px'>"
            f"chart loads on open&#8230;</div>")


def runtime_js(map_name: str, charts: dict, sites: dict) -> str:
    """Chart injection + site search. folium renders root-level scripts into
    the <head>, before the map variable exists, so everything is wrapped in a
    retrying attach() that waits for the map and the search input."""
    payload = json.dumps(charts).replace("</", "<\\/")
    return f"""
(function () {{
    var VI_CHARTS = {payload};
    var VI_SITES = {json.dumps(sites)};
    function attach() {{
        var mp = window['{map_name}'];
        var inp = document.getElementById('vi-site-search');
        if (!mp || !inp) {{ setTimeout(attach, 120); return; }}
        mp.on('popupopen', function (e) {{
            var el = e.popup.getElement().querySelector('[data-chart]');
            if (el && !el.dataset.done) {{
                el.innerHTML = VI_CHARTS[el.dataset.chart] || '';
                el.style.minHeight = '0';
                el.dataset.done = '1';
                e.popup.update();
            }}
        }});
        inp.addEventListener('change', function (e) {{
            var code = e.target.value.split(' - ')[0].trim().toUpperCase();
            var p = VI_SITES[code];
            if (p) {{ mp.setView(p, Math.max(mp.getZoom(), 11)); }}
        }});
    }}
    attach();
}})();
"""


# -- popups & site markers ---------------------------------------------------
def method_note(code, casts) -> str:
    """Per-site cast-method truth, surfaced in visit-site popups."""
    if casts is None:
        return ""
    c = casts[casts["site_code"] == code]
    if not len(c):
        return ""
    mix = " + ".join(f"{k}&#215;{v}" for k, v in
                     c["method"].value_counts().items())
    note = f"near-bottom method: {mix}"
    if (c["method"] == "cast_min").any():
        note += " (cast_min = no depth channel; value is the cast minimum)"
    if c["qc_suspect"].any():
        note += (f"; {int(c['qc_suspect'].sum())} casts &gt;"
                 f"{QC_SUSPECT_ML_L.get(code, 9)} mL/L flagged QC-suspect")
    return note


def popup_html(r: pd.Series, charts: dict, season: str | None = None,
               mnote: str = "") -> str:
    years = ""
    if pd.notna(r.get("first")) and pd.notna(r.get("last")):
        years = (f"{pd.to_datetime(r['first'], utc=True).year}-"
                 f"{pd.to_datetime(r['last'], utc=True).year}")
    kind = ("continuous sensor" if r["data_kind"] == "continuous"
            else "ship-visit casts (near-bottom O&#8322;)")
    depth = fmt(r.get("station_depth_m", r.get("maxDepth")), 0, " m")
    rows = [
        ("Type", f"{kind} - {r['final_tier']}"),
        ("Depth", depth),
        ("Record", f"{years} - {r.get('record_status', '')}"),
        ("Observations", f"{r['n_obs']:,} ({r['n_summer_obs']:,} in Jun-Oct)"),
        ("O2 min / p10 / median",
         f"{fmt(r['o2_min'])} / {fmt(r['o2_p10'])} / {fmt(r['o2_median'])} mL/L"),
        ("Below 1.4 / 2.8 mL/L",
         f"{fmt(r['pct_below_hypoxic'], 1)}% / {fmt(r['pct_below_at_risk'], 1)}%"),
        ("Class (worst / exposure / typical)",
         f"{LABELS.get(r['class_worst_case'], '-')} / "
         f"{LABELS.get(r['class_exposure'], '-')} / "
         f"{LABELS.get(r['class_typical'], '-')}"),
        ("Confidence", r["confidence"]),
    ]
    if season:
        sl = dict(SEASONS)[season]
        rows.insert(5, (f"This layer: {sl}",
                        f"n={fmt(r.get(f'{season}_n'), 0)}, "
                        f"p10={fmt(r.get(f'{season}_p10'))}, "
                        f"median={fmt(r.get(f'{season}_median'))} mL/L "
                        f"&#8594; {LABELS.get(r.get(f'{season}_class'), '-')}"))
    body = "".join(
        f"<tr><td style='color:#666;padding-right:8px;white-space:nowrap'>{k}</td>"
        f"<td>{v}</td></tr>" for k, v in rows)
    html = (f"<div style='font-family:sans-serif;font-size:12px'>"
            f"<b style='font-size:13px'>{r['site_name']}</b>"
            f" <span style='color:#888'>({r['site_code']})</span>"
            f"<table style='margin-top:4px'>{body}</table>")
    if mnote:
        html += (f"<div style='margin-top:5px;padding:4px 7px;background:"
                 f"#fff9db;border-left:3px solid #f59f00;font-size:11px;"
                 f"color:#555'>{mnote}</div>")
    if r["site_code"] in OMZ_NOTES:
        html += (f"<div style='margin-top:5px;padding:4px 7px;background:"
                 f"#f1f3f5;border-left:3px solid #5c7cfa;font-size:11px;"
                 f"color:#444'>{OMZ_NOTES[r['site_code']]}</div>")
    if r["site_code"] in charts:
        html += chart_slot(r["site_code"])
    return html + "</div>"


def add_markers(group, df, class_col, charts, casts=None,
                season: str | None = None):
    """Site markers - unchanged visual language, now with chart popups."""
    for _, r in df.iterrows():
        if pd.isna(r["lat"]) or pd.isna(r["lon"]):
            continue
        continuous = r["data_kind"] == "continuous"
        ended = str(r.get("record_status", "")).startswith("ended")
        cls_val = r.get(class_col)
        tip = f"{r['site_name']} - {LABELS.get(cls_val, 'no data')}"
        if season:
            tip += f" ({season.upper()} p10)"
        mnote = "" if continuous else method_note(r["site_code"], casts)
        folium.CircleMarker(
            location=(r["lat"], r["lon"]),
            radius=9 if continuous else 6,
            color="#333333" if continuous else "#ffffff",
            weight=2.5 if continuous else 1.5,
            dash_array="4" if ended else None,
            fill=True,
            fill_color=COLORS.get(cls_val, COLORS["unclassified"]),
            fill_opacity=OPACITY.get(r["confidence"], 0.6),
            tooltip=tip,
            popup=folium.Popup(popup_html(r, charts, season, mnote),
                               max_width=430),
        ).add_to(group)


# -- individual-cast layer (priority 2) --------------------------------------
def _jitter(i: int) -> tuple[float, float]:
    """Time-ordered Archimedean spiral: cast 0 (oldest) sits near the
    station centre and later casts wind outward, so radial position reads
    as chronology (~7 casts per turn, out to ~600 m)."""
    """Deterministic golden-angle spiral (deg). Casts carry only the station's
    nominal coordinate, so identical points would hide each other; the spiral
    spreads them ~100-800 m and is disclosed in tooltip + legend."""
    ang = i * 0.9
    rad = 0.0012 + 0.00012 * ang
    return rad * math.sin(ang), rad * math.cos(ang) / 0.66   # dlat, dlon @49N


def cast_popup(r: pd.Series) -> str:
    rows = [("When", r["time"].strftime("%Y-%m-%d %H:%M UTC")),
            ("Near-bottom O2", f"{r['near_bottom_o2_ml_l']:.2f} mL/L "
             f"&#8594; {LABELS[class_of(r['near_bottom_o2_ml_l'])]}"),
            ("Cast depth", fmt(r["cast_depth_m"], 0, " m")),
            ("Samples", f"{r['n_samples']:,}"),
            ("Method", r["method"])]
    if r["qc_suspect"]:
        rows.append(("QC", "<b style='color:#e03131'>suspect</b> "
                     "(supersaturation spike)"))
    body = "".join(
        f"<tr><td style='color:#666;padding-right:8px'>{k}</td>"
        f"<td>{v}</td></tr>" for k, v in rows)
    return (f"<div style='font-family:sans-serif;font-size:12px'>"
            f"<b>{r['site_name']}</b> <span style='color:#888'>"
            f"({r['site_code']}) - single cast</span>"
            f"<table style='margin-top:4px'>{body}</table>"
            f"<div style='color:#999;font-size:10px;margin-top:3px'>"
            f"dot position jittered for visibility</div></div>")


def build_cast_layer(casts: pd.DataFrame) -> folium.FeatureGroup:
    g = folium.FeatureGroup(name="Casts (individual)", show=False)
    for code, grp in casts.sort_values("time").groupby("site_code"):
        for i, (_, r) in enumerate(grp.iterrows()):
            if pd.isna(r["lat"]) or pd.isna(r["lon"]):
                continue
            dlat, dlon = _jitter(i)
            v = r["near_bottom_o2_ml_l"]
            tip = (f"{code} - {r['time']:%Y-%m-%d} - {v:.2f} mL/L "
                   f"({r['method']})")
            kw = dict(fill=True, fill_color=COLORS[class_of(v)],
                      fill_opacity=0.85, color="#ffffff", weight=0.8)
            if r["qc_suspect"]:
                tip += " - QC-suspect"
                kw = dict(fill=False, color="#868e96", weight=1.4)
            folium.CircleMarker(
                location=(r["lat"] + dlat, r["lon"] + dlon), radius=3,
                tooltip=tip,
                popup=folium.Popup(cast_popup(r), max_width=260),
                **kw).add_to(g)
    return g


# -- modeled layers (priority 3, pre-wired) ----------------------------------
# Renders only when model_predictions.csv is present. The one credibility
# rule: observed and modeled must never be confusable -> hollow diamond
# markers, "MODELED" in every tooltip, dashed line + band in charts.
def _diamond_icon(color: str, size: int = 18) -> folium.DivIcon:
    h = size // 2
    svg = (f"<svg width='{size}' height='{size}' "
           f"xmlns='http://www.w3.org/2000/svg'>"
           f"<polygon points='{h},1.5 {size - 1.5},{h} {h},{size - 1.5} "
           f"1.5,{h}' fill='rgba(255,255,255,.55)' stroke='{color}' "
           f"stroke-width='2.6'/></svg>")
    return folium.DivIcon(html=svg, icon_size=(size, size),
                          icon_anchor=(h, h))


def model_popup(head: str, sub: str, g: pd.DataFrame, chart_key: str,
                charts: dict) -> str:
    p10, med = g["o2_pred_ml_l"].quantile(.1), g["o2_pred_ml_l"].median()
    rows = [("Type", "<b>MODELED</b> - no observations shown as filled"),
            ("Model version", ", ".join(map(str, g["model_version"].unique()))),
            ("Span", f"{g['date'].min():%Y-%m} - {g['date'].max():%Y-%m} "
             f"({len(g)} predictions)"),
            ("Predicted p10 / median", f"{p10:.2f} / {med:.2f} mL/L")]
    body = "".join(
        f"<tr><td style='color:#666;padding-right:8px'>{k}</td>"
        f"<td>{v}</td></tr>" for k, v in rows)
    html = (f"<div style='font-family:sans-serif;font-size:12px'>"
            f"<b style='font-size:13px'>{head}</b> "
            f"<span style='color:#888'>{sub}</span>"
            f"<table style='margin-top:4px'>{body}</table>")
    if chart_key in charts:
        html += chart_slot(chart_key)
    return html + "</div>"


def build_model_layer(model, cls, charts) -> folium.FeatureGroup | None:
    g = folium.FeatureGroup(name="Modeled (predictions)", show=False)
    n_site = n_sp = 0
    coords = cls.set_index("site_code")[["lat", "lon", "site_name"]]

    if "site_code" in model.columns:
        for code, grp in model[model["site_code"].notna()].groupby("site_code"):
            if code not in coords.index:
                continue
            lat, lon, name = coords.loc[code]
            cls_val = class_of(grp["o2_pred_ml_l"].quantile(.1))
            folium.Marker(
                location=(lat, lon),
                icon=_diamond_icon(COLORS[cls_val]),
                tooltip=(f"{name} - MODELED "
                         f"({grp['model_version'].iloc[0]}) - "
                         f"{LABELS[cls_val]} (pred p10)"),
                popup=folium.Popup(
                    model_popup(name, f"({code}) - modeled", grp,
                                f"{code}::model", charts), max_width=430),
            ).add_to(g)
            n_site += 1

    # later spatial rows: lat/lon instead of site_code
    if {"lat", "lon"}.issubset(model.columns):
        sp = model[model["lat"].notna() & model["lon"].notna()]
        if "site_code" in sp.columns:
            sp = sp[sp["site_code"].isna()]
        sp = sp[sp["lat"].between(BOX["s"], BOX["n"])
                & sp["lon"].between(BOX["w"], BOX["e"])]
        for (lat, lon), grp in sp.groupby(["lat", "lon"]):
            key = f"model@{lat:.4f},{lon:.4f}"
            charts.setdefault(key, chart_svg(model_site=grp) or "")
            cls_val = class_of(grp["o2_pred_ml_l"].quantile(.1))
            folium.Marker(
                location=(lat, lon), icon=_diamond_icon(COLORS[cls_val], 14),
                tooltip=(f"MODELED grid point "
                         f"({grp['model_version'].iloc[0]}) - "
                         f"{LABELS[cls_val]} (pred p10)"),
                popup=folium.Popup(
                    model_popup(f"{lat:.3f}N, {abs(lon):.3f}W",
                                "- modeled grid point", grp, key, charts),
                    max_width=430),
            ).add_to(g)
            n_sp += 1

    if not (n_site or n_sp):
        return None
    print(f"modeled layer: {n_site} sites, {n_sp} grid points")
    return g


# -- site search (nice-to-have) ----------------------------------------------
def search_control(cls: pd.DataFrame) -> tuple[str, dict]:
    opts = "".join(f"<option value='{r.site_code} - {r.site_name}'>"
                   for r in cls.itertuples())
    html = f"""
<div style="position:fixed;top:78px;left:12px;z-index:9999;background:white;
  padding:6px 8px;border:1px solid #bbb;border-radius:8px;
  font-family:sans-serif;box-shadow:0 1px 4px rgba(0,0,0,.3)">
<input id="vi-site-search" list="vi-sites" placeholder="Find site&#8230;"
  style="border:1px solid #ccc;border-radius:4px;padding:3px 6px;
  font-size:12px;width:150px">
<datalist id="vi-sites">{opts}</datalist></div>"""
    sites = {r.site_code: [round(r.lat, 5), round(r.lon, 5)]
             for r in cls.itertuples() if pd.notna(r.lat)}
    return html, sites


# -- bathymetry (GEBCO), pre-wired -------------------------------------------
# Drop a GEBCO netCDF grid (any *gebco*.nc) next to the CSVs and rerun.
# Recommended download box (0.3 deg margin around BOX): N 51.5, S 47.5,
# W -130.0, E -122.0 from https://download.gebco.net/ (format: netCDF).
ISOBATHS = [-2000, -1500, -1000, -500, -200, -100]        # metres, ascending
ISO_COLORS = {-100: "#9ecae1", -200: "#6baed6", -500: "#4292c6",
              -1000: "#2171b5", -1500: "#08519c", -2000: "#08306b"}


def find_bathy(data_dir: Path) -> Path | None:
    hits = sorted(p for p in data_dir.glob("*.nc")
                  if "gebco" in p.name.lower())
    return hits[0] if hits else None


def bathy_multilines(path: Path) -> list[dict] | None:
    """Extract isobath polylines from a GEBCO grid as plain data:
    [{depth, color, lines: [[[lon, lat], ...], ...]}, ...]"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from netCDF4 import Dataset
    except ImportError as e:
        print(f"bathymetry: found {path.name} but missing dependency "
              f"({e.name}) - pip install netCDF4 matplotlib")
        return None

    with Dataset(path) as ds:
        lat = ds["lat"][:]
        lon = ds["lon"][:]
        z = ds["elevation"][:]
    # trim to study box (+ small margin) and subsample to keep things fast
    mlat = (lat >= BOX["s"] - .2) & (lat <= BOX["n"] + .2)
    mlon = (lon >= BOX["w"] - .2) & (lon <= BOX["e"] + .2)
    lat, lon, z = lat[mlat], lon[mlon], z[mlat][:, mlon]
    step = max(1, int(max(len(lat), len(lon)) / 700))
    lat, lon, z = lat[::step], lon[::step], z[::step, ::step]

    fig = plt.figure()
    cs = plt.contour(lon, lat, z, levels=ISOBATHS)
    plt.close(fig)

    out = []
    for lev, segs in zip(cs.levels, cs.allsegs):
        lines = []
        for seg in segs:
            seg = seg[::2] if len(seg) > 12 else seg    # thin the vertices
            if len(seg) >= 5:
                lines.append([[round(float(x), 4), round(float(y), 4)]
                              for x, y in seg])
        if lines:
            out.append({"depth": int(-lev),
                        "color": ISO_COLORS.get(int(lev), "#4292c6"),
                        "lines": lines})
    return out or None


def build_bathy_layer(path: Path) -> folium.FeatureGroup | None:
    levels = bathy_multilines(path)
    if not levels:
        return None
    g = folium.FeatureGroup(name="Bathymetry (GEBCO isobaths)", show=True)
    n_seg = 0
    for lev in levels:
        n_seg += len(lev["lines"])
        folium.GeoJson(
            {"type": "Feature", "properties": {},
             "geometry": {"type": "MultiLineString",
                          "coordinates": lev["lines"]}},
            style_function=lambda _, c=lev["color"]:
                {"color": c, "weight": 1.1, "opacity": .75, "fill": False},
            tooltip=f"{lev['depth']} m isobath (GEBCO)",
        ).add_to(g)
    print(f"bathymetry: {n_seg} isobath segments from {path.name} "
          f"(levels {', '.join(str(-l) for l in ISOBATHS)} m)")
    return g


# -- static furniture --------------------------------------------------------
def legend_html(has_model: bool, has_bathy: bool = False) -> str:
    model_line = ("<span style='color:#495057'>&#9671;</span> hollow diamond "
                  "= <b>modeled</b>, never an observation<br>" if has_model
                  else "")
    bathy_line = ("<span style='color:#2171b5'>&#9472;</span> thin blue lines "
                  "= GEBCO isobaths (100&#8211;2000 m)<br>" if has_bathy
                  else "")
    return f"""
<div style="position:fixed;bottom:24px;left:16px;z-index:9999;background:white;
  padding:10px 14px;border:1px solid #bbb;border-radius:8px;
  font-family:sans-serif;font-size:12px;box-shadow:0 1px 4px rgba(0,0,0,.3)">
<b>Near-bottom O&#8322; status</b> (mL/L)<br>
<span style="color:#2f9e44">&#9679;</span> Good &ge; 2.8&nbsp;
<span style="color:#f59f00">&#9679;</span> At risk 1.4-2.8<br>
<span style="color:#e03131">&#9679;</span> Hypoxic &lt; 1.4&nbsp;
<span style="color:#5f3dc4">&#9679;</span> Anoxic &lt; 0.1<br>
<span style="border-bottom:1px solid #ddd;display:block;margin:4px 0"></span>
Large / dark ring = continuous sensor<br>
Small / white ring = ship-visit casts<br>
Tiny dot = one cast, its own value (jittered for<br>
&nbsp;&nbsp;visibility; hollow grey = QC-suspect)<br>
{model_line}{bathy_line}Faded fill = lower confidence; dashed = record ended<br>
Season layers recolor by that season's p10<br>
Click a site for stats + its time series</div>"""


TITLE = """
<div style="position:fixed;top:12px;left:50%;transform:translateX(-50%);
  z-index:9999;background:rgba(255,255,255,.95);padding:8px 18px;
  border-radius:8px;border:1px solid #bbb;font-family:sans-serif;
  box-shadow:0 1px 4px rgba(0,0,0,.3);text-align:center">
<b style="font-size:15px">Vancouver Island oxygen status</b><br>
<span style="font-size:12px;color:#555">ONC continuous platforms + Community
Fishers casts &middot; toggle rule / season / cast layers at top right
&middot; popups include the site time series</span></div>"""


def add_study_box(m):
    folium.Rectangle(
        bounds=[[BOX["s"], BOX["w"]], [BOX["n"], BOX["e"]]],
        color="#495057", weight=1.3, dash_array="6 5", fill=False,
        tooltip="Study box 48.1-50.1N, 126.4-122.8W (sites filtered to this)",
    ).add_to(m)


# -- main --------------------------------------------------------------------
def main() -> int:
    d = find_data_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    cls, daily, casts, model = load_inputs(d)
    cls, daily, casts, dropped = apply_study_box(cls, daily, casts)
    if dropped:
        print(f"outside study box, not shown: {', '.join(dropped)}")

    charts = build_charts(cls, daily, casts, model)
    n_obs_charts = sum(1 for k in charts if "::" not in k)
    print(f"popup charts: {n_obs_charts} observed"
          + (f", {len(charts) - n_obs_charts} modeled" if model is not None
             else ""))

    m = folium.Map(location=(49.35, -124.9), zoom_start=7,
                   tiles="cartodbpositron", control_scale=True)
    folium.TileLayer(
        tiles=("https://server.arcgisonline.com/ArcGIS/rest/services/"
               "Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}"),
        name="Esri Ocean (bathymetry shading)",
        attr=("Tiles &copy; Esri &mdash; Sources: GEBCO, NOAA, CHS, OSU, "
              "UNH, CSUMB, National Geographic, DeLorme, NAVTEQ, Esri"),
        max_native_zoom=13, max_zoom=18).add_to(m)
    folium.TileLayer(
        tiles=("https://server.arcgisonline.com/ArcGIS/rest/services/"
               "World_Imagery/MapServer/tile/{z}/{y}/{x}"),
        name="Esri World Imagery",
        attr=("Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, "
              "AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, "
              "GIS User Community")).add_to(m)
    add_study_box(m)

    # The six status lenses all draw markers at the same coordinates, so they
    # must be mutually exclusive (radio buttons) -> control=False here keeps
    # them out of the normal checkbox control; GroupedLayerControl below owns
    # them.
    exposure = folium.FeatureGroup(name="Rule: exposure (10th percentile)",
                                   show=True, control=False)
    worst = folium.FeatureGroup(name="Rule: worst case (record minimum)",
                                show=False, control=False)
    add_markers(exposure, cls, "class_exposure", charts, casts)
    add_markers(worst, cls, "class_worst_case", charts, casts)
    exposure.add_to(m)
    worst.add_to(m)
    lens_layers = [exposure, worst]

    for key, label in SEASONS:
        g = folium.FeatureGroup(name=f"Season: {label} p10", show=False,
                                control=False)
        add_markers(g, cls, f"{key}_class", charts, casts, season=key)
        g.add_to(m)
        lens_layers.append(g)

    bathy_path = find_bathy(d)
    bathy = build_bathy_layer(bathy_path) if bathy_path else None
    if bathy is not None:
        bathy.add_to(m)

    if model is not None:
        mg = build_model_layer(model, cls, charts)
        if mg is not None:
            mg.add_to(m)

    if casts is not None and len(casts):
        build_cast_layer(casts).add_to(m)
        n_sus = int(casts["qc_suspect"].sum())
        print(f"cast layer: {len(casts)} casts at "
              f"{casts['site_code'].nunique()} stations"
              + (f" ({n_sus} QC-suspect)" if n_sus else ""))

    plugins.GroupedLayerControl(
        groups={"Status lens (pick one)": lens_layers},
        exclusive_groups=True, collapsed=False).add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    has_model = model is not None and any("::model" in k or k.startswith("model@")
                                          for k in charts)
    m.get_root().html.add_child(
        folium.Element(legend_html(has_model, bathy is not None)))
    m.get_root().html.add_child(folium.Element(TITLE))
    search_div, sites = search_control(cls)
    m.get_root().html.add_child(folium.Element(search_div))
    m.get_root().script.add_child(
        folium.Element(runtime_js(m.get_name(), charts, sites)))

    out = Path(OUT_NAME)                 # written to the current directory
    m.save(out)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB) with "
          f"{int(cls['lat'].notna().sum())} sites "
          f"x {2 + len(SEASONS)} site layers")
    return 0


if __name__ == "__main__":
    sys.exit(main())


def bathy_relief(path: Path, out_png: Path, max_px: int = 1500):
    """Render the GEBCO grid as a hillshaded relief PNG for use as a web
    basemap image overlay. Returns [[s, w], [n, e]] bounds or None."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import numpy as np
        from matplotlib.colors import LightSource, ListedColormap
        import matplotlib.pyplot as plt
        from netCDF4 import Dataset
    except ImportError as e:
        print(f"relief: missing dependency ({e.name})")
        return None

    with Dataset(path) as ds:
        lat = ds["lat"][:]
        lon = ds["lon"][:]
        z = np.asarray(ds["elevation"][:], dtype=float)
    step = max(1, int(max(len(lat), len(lon)) / max_px))
    lat, lon, z = lat[::step], lon[::step], z[::step, ::step]

    depth = np.clip(-z, 0, None)
    blues = plt.get_cmap("Blues")
    water_rgb = blues(np.clip(.18 + .82 * (depth / 2800.0) ** .62, 0, 1))
    ls = LightSource(azdeg=315, altdeg=45)
    shaded = ls.shade_rgb(water_rgb[..., :3], -depth,
                          blend_mode="soft", vert_exag=.08)
    land = np.array([.842, .824, .776])                  # muted tan
    land_sh = ls.shade_rgb(np.tile(land, (*z.shape, 1)), np.clip(z, 0, None),
                           blend_mode="soft", vert_exag=.05)
    img = np.where((z >= 0)[..., None], land_sh, shaded)
    if out_png.suffix.lower() == ".png":
        # web overlay: land transparent (basemap shows through) + feathered
        # edges so the image blends into the basemap instead of ending in a
        # hard rectangle
        alpha = np.where(z >= 0, 0.0, 1.0)
        f = 42                                        # feather width, px
        ny, nx = alpha.shape
        ramp_y = np.clip(np.minimum(np.arange(ny), ny - 1 - np.arange(ny))
                         / f, 0, 1)
        ramp_x = np.clip(np.minimum(np.arange(nx), nx - 1 - np.arange(nx))
                         / f, 0, 1)
        alpha *= np.minimum.outer(ramp_y, ramp_x)
        img = np.dstack([img, alpha])
    img = np.flipud(img)                                  # row 0 = north
    if out_png.suffix.lower() == ".png":
        from PIL import Image
        arr = (np.clip(img, 0, 1) * 255).astype("uint8")
        pim = Image.fromarray(arr, "RGBA")
        if pim.width > 1500:
            pim = pim.resize((1500, round(pim.height * 1500 / pim.width)),
                             Image.LANCZOS)
        pim.save(out_png, optimize=True)
    else:
        plt.imsave(out_png, np.clip(img, 0, 1),
                   pil_kwargs={"quality": 88})
    print(f"relief: {out_png.name} {img.shape[1]}x{img.shape[0]}px "
          f"({out_png.stat().st_size / 1e6:.1f} MB)")
    return [[float(lat.min()), float(lon.min())],
            [float(lat.max()), float(lon.max())]]
