(function () {
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
    var all = pts.concat(casts);
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
      var ML = 46, MR = 46, MT = 16, MB = 26;
      svg = document.createElementNS(NS, "svg");
      svg.setAttribute("viewBox", "0 0 " + W + " " + H);
      svg.setAttribute("class", "ichart");
      host.appendChild(svg);
      tip = document.createElement("div");
      tip.className = "ctip hidden";
      host.appendChild(tip);

      var vis = all.filter(function (a) { return a.d >= x0 && a.d <= x1; });
      if (!vis.length) vis = all;
      var vmax = Math.max.apply(null, vis.map(function (a) { return a.v; }));
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
      el("text", {x: ML - 7, y: MT - 4, "text-anchor": "end",
        "class": "clab cend"}).textContent = "mL/L";
      // x ticks: years, thinned to fit
      var yr0 = new Date(x0 * 86400000).getUTCFullYear() + 1;
      var yr1 = new Date(x1 * 86400000).getUTCFullYear();
      var every = Math.max(1, Math.ceil((yr1 - yr0 + 1) /
        Math.floor((W - ML - MR) / 52)));
      for (var yy = yr0; yy <= yr1; yy++) {
        var dd = Date.UTC(yy, 0, 1) / 86400000;
        el("line", {x1: X(dd), x2: X(dd), y1: MT, y2: H - MB,
          "class": "cgrid"});
        if ((yy - yr0) % every === 0)
          el("text", {x: X(dd), y: H - 8, "text-anchor": "middle",
            "class": "clab cmid"})
            .textContent = (x1 - x0 > 1200 ? "'" + String(yy).slice(2) : yy);
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
      // cast dots
      casts.forEach(function (c) {
        if (c.d < x0 || c.d > x1) return;
        el("circle", c.q
          ? {cx: X(c.d), cy: Y(Math.min(c.v, yMax)), r: 3.4,
             "class": "ccast-q"}
          : {cx: X(c.d), cy: Y(c.v), r: 3.4, fill: colors[c.c],
             "class": "ccast"});
      });
      // caption + hint
      el("text", {x: W - MR, y: MT - 4, "text-anchor": "end",
        "class": "clab cend cmut"})
        .textContent = data.series
          ? (data.series.k === "w" ? "weekly means" : "daily")
          : "individual casts";
      el("text", {x: ML, y: MT - 4, "class": "clab cmut"})
        .textContent = "drag to zoom \u00b7 double-click to reset";
      if (x0 > d0 || x1 < d1) {
        var rb = el("text", {x: ML + 224, y: MT - 4,
          "class": "clab creset"});
        rb.textContent = "[reset zoom]";
        rb.addEventListener("click", function () {
          x0 = d0; x1 = d1; render();
        });
      }
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
          n.v.toFixed(2) + " mL/L" +
          (n.c ? " \u00b7 " + (n.q ? "QC-suspect cast" : "cast") : "");
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
    function renderDock(key) {
      var series = VI.series[key] || null;
      var siteCasts = VI.casts.filter(function (c) { return c.s === key; });
      H.chart = IChart(dChart,
        {series: series, casts: siteCasts},
        VI.colors, VI.thresholds);
    }
    function openDetail(html, chartKey, hash) {
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
      }, 180);
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
    var castCanvas = L.canvas({padding: 0.4});
    var castMarkers = VI.casts.map(function (c) {
      var mk = L.circleMarker([c.la, c.lo], c.q
        ? {radius: 3, color: "#868e96", weight: 1.4, fill: false,
           renderer: castCanvas}
        : {radius: 3, color: "#ffffff", weight: 0.8, fill: true,
           fillColor: VI.colors[c.c], fillOpacity: 0.95,
           renderer: castCanvas});
      mk.bindTooltip(c.s + " - " + c.t.slice(0, 10) + " - " +
        c.o.toFixed(2) + " mL/L (" + c.m + ")" +
        (c.f ? " \u00b7 DFO" : "") +
        (c.q ? " - QC-suspect" : ""), {sticky: true});
      mk.on("click", function () { openDetail(castHtml(c), c.s); });
      return mk;
    });
    function castHtml(c) {
      var rows = [["When", c.t + " UTC"],
        ["Near-bottom O&#8322;", c.o.toFixed(2) + " mL/L &#8594; " +
         VI.labels[c.c]],
        ["Cast depth", c.d === null ? "&#8211;" : c.d + " m"],
        ["Samples", c.n.toLocaleString()], ["Method", c.m],
        ["Source", c.f ? "DFO IOS CTD" : "ONC community fishers"]];
      if (c.q) rows.push(["QC", "<b>suspect</b> (&gt; site threshold; " +
        "shown hollow, excluded from stats)"]);
      return "<div style='font-family:sans-serif;font-size:12px'>" +
        "<b style='font-size:13px'>" + c.s + "</b> - single cast" +
        "<table style='margin-top:4px'>" + rows.map(function (r) {
          return "<tr><td style='color:#666;padding-right:8px'>" + r[0] +
            "</td><td>" + r[1] + "</td></tr>";
        }).join("") + "</table>" +
        (c.j ? "<div style='color:#888;margin-top:4px'>Dot position " +
         "jittered ~100&#8211;800 m; all casts at this station share one " +
         "nominal coordinate.</div>" : "") + "</div>";
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
