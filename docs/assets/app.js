(function () {
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
