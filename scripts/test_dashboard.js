/* Headless smoke test for docs/ (no browser needed).
   Setup once:  npm i jsdom leaflet     Run:  node test_dashboard.js
   Boots the real app in jsdom with real Leaflet and asserts marker counts,
   lens switching, confidence/year/suspect filters, search, deep links, and
   (when present) the bathymetry and modeled layers. Exits non-zero on any
   failed check. */
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
  const doc = w.document, VI = w.VI;
  const marks = () => doc.querySelectorAll("#map path.leaflet-interactive")
    .length - (VI.bathy ? VI.bathy.length : 0);
  const fire = (el, type) => el.dispatchEvent(new w.Event(type,
    { bubbles: true }));

  check("site markers on boot", marks(), VI.sites.length);
  check("deep link opens detail",
    !doc.getElementById("detail").classList.contains("hidden"), true);
  check("deep link chart injected",
    doc.querySelector("#d-body [data-chart]").innerHTML.startsWith("<svg"),
    true);

  doc.querySelectorAll("#lens-group input").forEach(r => {
    if (r.value === "worst") { r.checked = true; fire(r, "change"); }
  });
  const s = VI.sites.find(x => x.classes.exposure !== x.classes.worst);
  const col = VI.colors[s.classes.worst];
  check("lens switch restyles (has worst-color markers)",
    doc.querySelectorAll('#map path[fill="' + col + '"]').length > 0, true);

  doc.querySelectorAll("input[name=conf]").forEach(el => {
    if (el.value !== "high") { el.checked = false; fire(el, "change"); }
  });
  check("confidence filter", marks(),
    VI.sites.filter(x => x.conf === "high").length);
  doc.querySelectorAll("input[name=conf]").forEach(el => {
    el.checked = true; fire(el, "change");
  });

  const nSites = VI.sites.length;
  const ck = doc.getElementById("ck-casts");
  ck.checked = true; fire(ck, "change");
  check("cast layer", marks() - nSites, VI.casts.length);
  doc.getElementById("yr0").value = String(VI.meta.cast_years[1]);
  fire(doc.getElementById("yr0"), "input");
  check("year filter", marks() - nSites,
    VI.casts.filter(c => c.y >= VI.meta.cast_years[1]).length);
  const sus = doc.getElementById("ck-suspect");
  sus.checked = false; fire(sus, "change");
  check("suspect filter", marks() - nSites,
    VI.casts.filter(c => c.y >= VI.meta.cast_years[1] && !c.q).length);

  const inp = doc.getElementById("search");
  inp.value = VI.sites[0].code + " - x"; fire(inp, "change");
  check("search sets hash", w.location.hash, "#" + VI.sites[0].code);

  if (VI.relief) {
    check("relief basemap is default (image overlay on)",
      doc.querySelectorAll("#map .leaflet-image-layer").length, 1);
    doc.querySelectorAll("#base-group input").forEach(r => {
      if (r.value === "imagery") { r.checked = true; fire(r, "change"); }
    });
    check("relief removed on basemap switch",
      doc.querySelectorAll("#map .leaflet-image-layer").length, 0);
  } else console.log("  -- no relief in data.js");

  if (VI.bathy) {
    const iso = () => VI.bathy.reduce((n, l) => n +
      doc.querySelectorAll('#map path[stroke="' + l.color + '"]').length, 0);
    check("isobaths on by default", iso(), VI.bathy.length);
    const b = doc.getElementById("ck-bathy");
    b.checked = false; fire(b, "change");
    check("isobaths toggle off", iso(), 0);
  } else console.log("  -- no bathy in data.js (add a *gebco*.nc to test)");

  if (VI.model) {
    const m = doc.getElementById("ck-model");
    m.checked = true; fire(m, "change");
    check("model markers",
      doc.querySelectorAll("#map .leaflet-marker-pane svg polygon").length,
      VI.model.length);
  } else console.log("  -- no model in data.js " +
    "(add model_predictions.csv to test)");

  console.log(fails ? fails + " FAILURE(S)" : "all checks passed");
  process.exit(fails ? 1 : 0);
}, 100);
