/* Headless smoke test for docs/ (no browser needed).
   Setup once:  npm i jsdom leaflet     Run:  node test_dashboard.js */
"use strict";
const fs = require("fs");
const { JSDOM } = require("jsdom");
let fails = 0;
function check(label, got, want) {
  const ok = got === want;
  if (!ok) fails++;
  console.log((ok ? "  ok " : "FAIL ") + label + ": " + got +
    (ok ? "" : " (expected " + want + ")"));
}
let html = fs.readFileSync("docs/index.html", "utf8")
  .replace(/<script src="https:[^"]*leaflet[^"]*">\s*<\/script>/, "")
  .replace(/<link rel="stylesheet"\s*href="https:[^"]*">/g, "");
const dom = new JSDOM(html, { runScripts: "outside-only",
  url: "http://localhost/#BACAX" });
const w = dom.window;
w.HTMLElement.prototype.getBoundingClientRect = function () {
  return { x: 0, y: 0, top: 0, left: 0, width: 1200, height: 800,
    right: 1200, bottom: 800 };
};
// jsdom lacks canvas: give Leaflet's canvas renderer a permissive no-op 2D
// context (any method call is a no-op, any property is settable)
w.HTMLCanvasElement.prototype.getContext = function () {
  return new Proxy({}, {
    get: (t, k) => (k in t ? t[k] : function () {}),
    set: (t, k, v) => { t[k] = v; return true; }
  });
};
global.window = w; global.document = w.document;
Object.defineProperty(global, "navigator",
  { value: w.navigator, configurable: true });
global.Element = w.Element; global.HTMLElement = w.HTMLElement;
global.SVGElement = w.SVGElement;
const L = require("leaflet");
w.L = L;
w.eval(fs.readFileSync("docs/assets/data.js", "utf8"));
w.eval(fs.readFileSync("docs/assets/app.js", "utf8"));

setTimeout(() => {
  const doc = w.document, VI = w.VI, A = w.VIAPP;
  const ALL = ["good", "at_risk", "hypoxic", "anoxic", "unclassified"];
  const CONFS = ["high", "medium", "low"];
  const counts = () => A.counts();
  const fire = (el, type) =>
    el.dispatchEvent(new w.Event(type, { bubbles: true }));

  check("site markers on boot", counts().sites, VI.sites.length);
  check("cast years start at DFO era", VI.meta.cast_years[0], 2006);
  check("CTD casts on by default", counts().casts, VI.casts.length);
  check("cast filters visible by default",
    !doc.getElementById("cast-filters").classList.contains("hidden"), true);
  const bac = VI.sites.find(x => x.code === "BACAX");
  check("deep link pans to the site (no dock)",
    Math.abs(A.map.getCenter().lat - bac.lat) < 0.05 &&
    doc.getElementById("detail").classList.contains("hidden") &&
    !doc.body.classList.contains("detail-open"), true);
  // open the dock the intended way: a user action
  const s1 = doc.getElementById("search");
  s1.value = "BACAX - x"; fire(s1, "change");
  check("dock opens on user action",
    !doc.getElementById("detail").classList.contains("hidden"), true);
  check("dock resizes map (body.detail-open)",
    doc.body.classList.contains("detail-open"), true);
  check("dock renders interactive chart",
    doc.querySelector("#d-chart svg.ichart") !== null, true);
  check("dock info pane has the stats table",
    doc.getElementById("d-info").querySelector("table") !== null, true);
  check("chart slot removed from info pane",
    doc.getElementById("d-info").querySelector("[data-chart]"), null);
  // hover readout
  let cap = doc.querySelector('#d-chart svg.ichart rect[fill="transparent"]');
  cap.dispatchEvent(new w.MouseEvent("mousemove",
    {clientX: 600, clientY: 200, bubbles: true}));
  const tipEl = doc.querySelector("#d-chart .ctip");
  check("hover shows a value readout",
    !tipEl.classList.contains("hidden") &&
    tipEl.innerHTML.includes("mL/L"), true);
  // drag-zoom
  const fullSpan = A.chart.getFull()[1] - A.chart.getFull()[0];
  cap.dispatchEvent(new w.MouseEvent("mousedown",
    {clientX: 400, clientY: 200, bubbles: true}));
  cap.dispatchEvent(new w.MouseEvent("mousemove",
    {clientX: 700, clientY: 200, bubbles: true}));
  cap.dispatchEvent(new w.MouseEvent("mouseup",
    {clientX: 700, clientY: 200, bubbles: true}));
  const zoomSpan = A.chart.getDomain()[1] - A.chart.getDomain()[0];
  check("drag zooms the chart", zoomSpan < fullSpan * 0.6, true);
  cap = doc.querySelector('#d-chart svg.ichart rect[fill="transparent"]');
  cap.dispatchEvent(new w.MouseEvent("dblclick", {bubbles: true}));
  check("double-click resets zoom",
    Math.round(A.chart.getDomain()[1] - A.chart.getDomain()[0]),
    Math.round(fullSpan));
  doc.getElementById("d-close").click();
  check("close clears detail-open",
    doc.body.classList.contains("detail-open"), false);
  check("lens tooltips present",
    doc.querySelectorAll("#lens-group label[data-tip]").length, 3);
  check("theme attribute set",
    ["light", "dark"].includes(
      doc.documentElement.getAttribute("data-theme")), true);

  // theme toggle
  const t0 = doc.documentElement.getAttribute("data-theme");
  doc.getElementById("theme-btn").click();
  check("theme toggles",
    doc.documentElement.getAttribute("data-theme") !== t0, true);

  // lens: worst via radio
  doc.querySelectorAll("#lens-group input[name=lens]").forEach(r => {
    if (r.value === "worst") { r.checked = true; fire(r, "change"); }
  });
  const sw = VI.sites.find(x => x.classes.exposure !== x.classes.worst);
  check("lens switch to worst restyles",
    doc.querySelectorAll(
      '#map path[fill="' + VI.colors[sw.classes.worst] + '"]').length > 0,
    true);

  // lens: season via dropdown auto-selects seasonal radio
  const sel = doc.getElementById("season-select");
  sel.value = "jja"; fire(sel, "change");
  check("season dropdown activates seasonal radio",
    doc.querySelector("input[name=lens][value=seasonal]").checked, true);
  const sj = VI.sites.find(x => x.classes.jja === "good");
  check("season lens applied (a good-in-JJA site is green)",
    doc.querySelectorAll(
      '#map path[fill="' + VI.colors.good + '"]').length > 0 && !!sj, true);

  // legend dropdown + class chips (hide 'good' under JJA lens)
  const lb = doc.getElementById("legend-btn");
  lb.click();
  check("legend panel opens", lb.getAttribute("aria-expanded"), "true");
  doc.body.dispatchEvent(new w.MouseEvent("click", {bubbles: true}));
  check("legend stays pinned on outside click",
    !doc.getElementById("legend-panel").classList.contains("hidden"), true);
  lb.click();
  check("legend closes on second click",
    doc.getElementById("legend-panel").classList.contains("hidden"), true);
  lb.click();  // reopen for chip tests
  check("legend glyph rows present",
    doc.querySelectorAll("#legend-panel .lg-row").length >= 6, true);
  check("QC-flagged explainer present",
    doc.querySelector("#cast-filters label[data-tip]") !== null, true);
  const nGoodJJA = VI.sites.filter(s =>
    A.siteVisible(s, "jja", CONFS, ALL) &&
    (s.classes.jja || "unclassified") === "good").length;
  const before = counts().sites;
  doc.querySelector('.chip[data-class="good"]').click();
  check("chip hides good-class sites", before - counts().sites, nGoodJJA);
  doc.querySelector('.chip[data-class="good"]').click();   // restore
  check("chip restores them", counts().sites, before);

  // confidence filter under current lens
  doc.querySelectorAll("input[name=conf]").forEach(el => {
    if (el.value !== "high") { el.checked = false; fire(el, "change"); }
  });
  check("confidence filter", counts().sites,
    VI.sites.filter(s => A.siteVisible(s, "jja", ["high"], ALL)).length);
  doc.querySelectorAll("input[name=conf]").forEach(el => {
    el.checked = true; fire(el, "change");
  });

  // casts + year/suspect/class filters
  check("cast layer (ONC + DFO)", counts().casts, VI.casts.length);
  check("DFO casts present",
    VI.casts.filter(c => c.f).length > 10000, true);
  doc.getElementById("yr0").value = String(VI.meta.cast_years[1]);
  fire(doc.getElementById("yr0"), "change");
  const f1 = { y0: VI.meta.cast_years[1], y1: VI.meta.cast_years[1],
    suspect: true };
  check("year filter", counts().casts,
    VI.casts.filter(c => A.castVisible(c, f1, ALL)).length);
  // presets
  doc.querySelector('.preset[data-span="5"]').click();
  const p5 = { y0: VI.meta.cast_years[1] - 4, y1: VI.meta.cast_years[1],
    suspect: false };
  check("preset 'Last 5 yrs'", counts().casts,
    VI.casts.filter(c => A.castVisible(c, {...p5, suspect: true}, ALL))
      .length);
  doc.querySelector('.preset[data-span="all"]').click();
  check("preset 'All years' restores", counts().casts, VI.casts.length);
  doc.getElementById("yr0").value = String(VI.meta.cast_years[1]);
  fire(doc.getElementById("yr0"), "change");
  const sus = doc.getElementById("ck-suspect");
  sus.checked = false; fire(sus, "change");
  check("suspect filter", counts().casts,
    VI.casts.filter(c => A.castVisible(c, { ...f1, suspect: false }, ALL))
      .length);

  // fit-to-data
  const c0 = A.map.getCenter();
  doc.getElementById("fit-btn").click();
  const c1 = A.map.getCenter();
  check("fit button moves the view",
    Math.abs(c1.lat - c0.lat) + Math.abs(c1.lng - c0.lng) > 1e-4, true);

  // basemaps: no carto, dark exists, switching works
  check("Light (CartoDB) removed",
    doc.querySelector('#base-group input[value="carto"]'), null);
  const dk = doc.querySelector('#base-group input[value="dark"]');
  check("dark basemap offered", !!dk, true);
  dk.checked = true; fire(dk, "change");
  check("basemap switch survives", true, true);

  // relief overlay checkbox
  if (VI.relief) {
    check("relief overlay on by default",
      doc.querySelectorAll("#map .leaflet-image-layer").length, 1);
    const r = doc.getElementById("ck-relief");
    r.checked = false; fire(r, "change");
    check("relief toggles off",
      doc.querySelectorAll("#map .leaflet-image-layer").length, 0);
  } else console.log("  -- no relief in data.js");

  if (VI.bathy) {
    const iso = () => VI.bathy.reduce((n, l) => n +
      doc.querySelectorAll('#map path[stroke="' + l.color + '"]').length, 0);
    check("isobaths on by default", iso(), VI.bathy.length);
    const b = doc.getElementById("ck-bathy");
    b.checked = false; fire(b, "change");
    check("isobaths toggle off", iso(), 0);
  } else console.log("  -- no bathy in data.js");

  if (VI.model) {
    const m = doc.getElementById("ck-model");
    m.checked = true; fire(m, "change");
    check("model markers",
      doc.querySelectorAll("#map .leaflet-marker-pane svg polygon").length,
      VI.model.length);
  } else console.log("  -- no model in data.js " +
    "(add model_predictions.csv to test)");

  // search still works
  const inp = doc.getElementById("search");
  inp.value = VI.sites[0].code + " - x"; fire(inp, "change");
  check("search sets hash", w.location.hash, "#" + VI.sites[0].code);

  console.log(fails ? fails + " FAILURE(S)" : "all checks passed");
  process.exit(fails ? 1 : 0);
}, 100);
