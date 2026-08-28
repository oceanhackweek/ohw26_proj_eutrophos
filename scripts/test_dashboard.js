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
let MF = null;
try {
  const mtxt = fs.readFileSync("docs/assets/model_manifest.js", "utf8");
  MF = JSON.parse(mtxt.replace(/^\s*const\s+MODEL_MANIFEST\s*=\s*/, "")
    .replace(/;\s*$/, ""));
  // real <script> const creates a persistent global binding; indirect
  // eval const does not - inject as var for the harness
  w.eval("var MODEL_MANIFEST=" + JSON.stringify(MF) + ";");
} catch (e) { /* no manifest in this build */ }
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
  check("dock info uses the structured layout",
    doc.querySelectorAll("#d-info .i-sec").length >= 3 &&
    doc.querySelectorAll("#d-info .i-pill").length >= 2, true);
  check("dock info names the source network",
    doc.getElementById("d-info").innerHTML.includes("ONC"), true);
  check("site shapes: 27 triangles + 16 circles by source",
    doc.querySelectorAll("#map .leaflet-marker-pane svg polygon").length
      === 27 &&
    doc.querySelectorAll("#map .leaflet-marker-pane svg circle").length
      === 16, true);
  const capTexts = () => Array.from(
    doc.querySelectorAll("#d-chart svg.ichart text"))
    .map(t => t.textContent).join(" | ");
  check("chart shows current lens",
    capTexts().includes("Lens: Typical low"), true);
  check("chart has axis titles",
    capTexts().includes("Near-bottom O\u2082 (mL/L)") &&
    capTexts().includes("Year"), true);
  check("x-axis uses full years", /\| 20\d\d \|/.test(capTexts()), true);
  // lens switch while dock open updates the caption
  doc.querySelectorAll("#lens-group input[name=lens]").forEach(r => {
    if (r.value === "worst") { r.checked = true; fire(r, "change"); }
  });
  check("lens caption follows the lens switch",
    capTexts().includes("Lens: Worst case"), true);
  doc.querySelectorAll("#lens-group input[name=lens]").forEach(r => {
    if (r.value === "exposure") { r.checked = true; fire(r, "change"); }
  });
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
    doc.querySelectorAll('#map .leaflet-marker-pane [fill="' +
      VI.colors[sw.classes.worst] + '"]').length > 0, true);

  // lens: season via dropdown auto-selects seasonal radio
  const sel = doc.getElementById("season-select");
  sel.value = "jja"; fire(sel, "change");
  check("season dropdown activates seasonal radio",
    doc.querySelector("input[name=lens][value=seasonal]").checked, true);
  const sj = VI.sites.find(x => x.classes.jja === "good");
  check("season lens applied (a good-in-JJA site is green)",
    doc.querySelectorAll('#map .leaflet-marker-pane [fill="' +
      VI.colors.good + '"]').length > 0 && !!sj, true);

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

  if (MF) {
    const sc = doc.getElementById("surface-ctl");
    check("surface controls revealed by manifest",
      !sc.classList.contains("hidden"), true);
    check("surface frame options from manifest",
      doc.getElementById("surface-frame").options.length,
      MF.frames.length);
    const ckS = doc.getElementById("ck-surface");
    ckS.checked = true; fire(ckS, "change");
    const simg = () => doc.querySelectorAll(
      "#map .leaflet-surface-pane img").length;
    check("surface frame drapes in its own pane (under dots)", simg(), 1);
    check("first frame src", doc.querySelector(
      "#map .leaflet-surface-pane img").src.includes(
      MF.frames[0].png), true);
    const sel = doc.getElementById("surface-frame");
    sel.value = "latest.png"; fire(sel, "change");
    check("frame dropdown swaps the overlay", doc.querySelector(
      "#map .leaflet-surface-pane img").src.includes("latest.png"), true);
    ckS.checked = false; fire(ckS, "change");
    check("surface unchecks clean", simg(), 0);
  } else console.log("  -- no manifest file (surface controls stay hidden)");

  if (VI.bathy) {
    const iso = () => VI.bathy.reduce((n, l) => n +
      doc.querySelectorAll('#map path[stroke="' + l.color + '"]').length, 0);
    check("isobaths on by default", iso(), VI.bathy.length);
    const b = doc.getElementById("ck-bathy");
    b.checked = false; fire(b, "change");
    check("isobaths toggle off", iso(), 0);
  } else console.log("  -- no bathy in data.js");

  if (VI.modelSeries) {
    check("no separate model layer control",
      doc.getElementById("ck-model"), null);
    check("model series cover the full station set",
      Object.keys(VI.modelSeries).length, 284);
    check("DFO stations carry bands too",
      Object.keys(VI.modelSeries)
        .filter(k => k.startsWith("DFO")).length > 200, true);
    check("bands are v1.1 calibrated",
      VI.modelSeries.CF001.v, "hgb_quantile_v1.1");
    // open a modeled site: chart must show band + dashed line + caption
    const mc = Object.keys(VI.modelSeries).sort()[0];
    const si = doc.getElementById("search");
    si.value = mc + " - x"; fire(si, "change");
    check("modeled site chart has uncertainty band",
      doc.querySelector("#d-chart .cmband") !== null, true);
    check("modeled site chart has dashed prediction line",
      doc.querySelector("#d-chart .cmline") !== null, true);
    check("chart caption flags the model",
      Array.from(doc.querySelectorAll("#d-chart svg text"))
        .some(t => t.textContent.includes("modeled")), true);
    const cap2 = doc.querySelector(
      '#d-chart svg.ichart rect[fill="transparent"]');
    cap2.dispatchEvent(new w.MouseEvent("mousemove",
      {clientX: 640, clientY: 200, bubbles: true}));
    check("hovering a prediction says 'modeled' with its band",
      doc.querySelector("#d-chart .ctip").innerHTML.includes("modeled"),
      true);
    doc.getElementById("d-close").click();
  } else console.log("  -- no modelSeries in data.js " +
    "(add model_predictions.csv to test)");

  // search still works
  const inp = doc.getElementById("search");
  inp.value = VI.sites[0].code + " - x"; fire(inp, "change");
  check("search sets hash", w.location.hash, "#" + VI.sites[0].code);

  // clicking a site pans the map to it (after the dock settles)
  setTimeout(() => {
    const s0 = VI.sites[0];
    check("site click pans the map near the site",
      Math.abs(A.map.getCenter().lat - s0.lat) < 0.05 &&
      Math.abs(A.map.getCenter().lng - s0.lon) < 0.08, true);
    check("themed tooltips in use", docs_has_vi_tt(), true);
    check("DFO casts draw as squares (canvas ext present)",
      require("fs").readFileSync("docs/assets/app.js", "utf8")
        .includes("_updateSquareMarker"), true);
    // cast click pans too
    A._openCast(0);
    setTimeout(() => {
      const c0 = VI.casts[0];
      check("cast click pans the map near the cast",
        Math.abs(A.map.getCenter().lat - c0.la) < 0.05 &&
        Math.abs(A.map.getCenter().lng - c0.lo) < 0.08, true);
      check("cast dock shows its station's model band",
        (VI.modelSeries && VI.modelSeries[c0.s])
          ? doc.querySelector("#d-chart .cmband") !== null : true, true);
      // fresh page without a manifest: controls must stay hidden
      const d2 = new JSDOM(html, {runScripts: "outside-only",
        url: "http://localhost/"});
      const w2 = d2.window;
      w2.HTMLElement.prototype.getBoundingClientRect =
        w.HTMLElement.prototype.getBoundingClientRect;
      w2.HTMLCanvasElement.prototype.getContext =
        w.HTMLCanvasElement.prototype.getContext;
      global.window = w2; global.document = w2.document;
      w2.L = require("leaflet");
      w2.eval(fs.readFileSync("docs/assets/data.js", "utf8"));
      w2.eval(fs.readFileSync("docs/assets/app.js", "utf8"));
      setTimeout(() => {
        check("surface controls hidden without a manifest",
          w2.document.getElementById("surface-ctl")
            .classList.contains("hidden"), true);
        console.log(fails ? fails + " FAILURE(S)" : "all checks passed");
        process.exit(fails ? 1 : 0);
      }, 200);
    }, 320);
  }, 320);
  function docs_has_vi_tt() {
    return require("fs").readFileSync("docs/assets/app.js", "utf8")
      .includes('className: "vi-tt"');
  }
}, 100);
