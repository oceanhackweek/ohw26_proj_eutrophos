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
      // captions: lens (left), glyph key (right), hint + reset (bottom)
      if (data.lens)
        el("text", {x: ML, y: MT - 8, "class": "clab lens-cap"})
          .textContent = "Lens: " + data.lens;
      var kx = W - MR;
      function keyItem(label, glyph) {
        var t = el("text", {x: kx, y: MT - 8, "text-anchor": "end",
          "class": "clab"});
        t.textContent = label;
        kx -= label.length * 6.6 + 6;
        glyph(kx);
        kx -= 24;
      }
      if (data.model) {
        keyItem("model median", function (x) {
          el("line", {x1: x - 18, x2: x, y1: MT - 12, y2: MT - 12,
            "class": "cmline"});
        });
        keyItem("80% band", function (x) {
          el("rect", {x: x - 18, y: MT - 17, width: 18, height: 10,
            "class": "cmband"});
        });
      }
      if (casts.length) {
        keyItem("casts (status color)", function (x) {
          el("circle", {cx: x - 9, cy: MT - 12, r: 3.4,
            fill: colors.good, "class": "ccast"});
        });
      }
      if (pts.length) {
        keyItem(data.series.k === "w" ? "observed (weekly)"
          : "observed (daily)", function (x) {
          el("line", {x1: x - 18, x2: x, y1: MT - 12, y2: MT - 12,
            "class": "cline"});
        });
      }
      el("text", {x: W - MR, y: H - 7, "text-anchor": "end",
        "class": "clab cmut"})
        .textContent = "drag to zoom \u00b7 double-click resets";
      if (x0 > d0 || x1 < d1) {
        var rb = el("text", {x: ML, y: H - 7, "class": "clab creset"});
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
          (n.mo
            ? "model median " + n.v.toFixed(2) + " \u00b7 80% band " +
              n.lo.toFixed(2) + "\u2013" + n.hi.toFixed(2) + " mL/L"
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
    map.createPane("scrim").style.zIndex = 300;     // fades the basemap
    map.createPane("surface").style.zIndex = 360;   // above relief, below
                                                    // isobaths + cast dots
    map.createPane("coast").style.zIndex = 365;     // coastline over surface
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
    function siteIcon(s, color) {
      var op = VI.opacity[s.conf] || 0.6;
      var ring = s.cont ? "#333333" : "#ffffff";
      var rw = s.cont ? 2.5 : 1.6;
      var dash = s.ended ? " stroke-dasharray='4 3'" : "";
      var body = s.shp === "tri"
        ? "<polygon points='13,3.5 23.5,21.5 2.5,21.5'"
        : "<circle cx='13' cy='13' r='9'";
      return L.divIcon({
        html: "<svg width='26' height='26' " +
          "xmlns='http://www.w3.org/2000/svg'>" + body +
          " fill='" + color + "' fill-opacity='" + op +
          "' stroke='" + ring + "' stroke-width='" + rw + "'" + dash +
          "/></svg>",
        className: "", iconSize: [26, 26], iconAnchor: [13, 13]});
    }
    VI.sites.forEach(function (s) {
      var mk = L.marker([s.lat, s.lon],
        {icon: siteIcon(s, VI.colors.unclassified), keyboard: false});
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
        mk.setIcon(siteIcon(s, VI.colors[c] || VI.colors.unclassified));
        mk.setTooltipContent(
          "<div class='tt-h'>" + s.name +
          " <span class='tt-b'>" + s.org + "</span></div>" +
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
    // DFO casts render as small squares on the shared canvas
    L.Canvas.include({
      _updateSquareMarker: function (layer) {
        if (!this._drawing || layer._empty()) return;
        var p = layer._point, ctx = this._ctx,
            r = Math.max(Math.round(layer._radius), 1);
        ctx.beginPath();
        ctx.rect(p.x - r, p.y - r, r * 2, r * 2);
        this._fillStroke(ctx, layer);
      }
    });
    var SquareMarker = L.CircleMarker.extend({
      _updatePath: function () {
        this._renderer._updateSquareMarker(this);
      }
    });
    var castMarkers = VI.casts.map(function (c) {
      var Ctor = c.f ? SquareMarker : L.circleMarker;
      var mk = new (c.f ? SquareMarker : L.CircleMarker)([c.la, c.lo], c.q
        ? {radius: 3, color: "#868e96", weight: 1.4, fill: false,
           renderer: castCanvas}
        : {radius: 3, color: "#ffffff", weight: 0.8, fill: true,
           fillColor: VI.colors[c.c], fillOpacity: 0.95,
           renderer: castCanvas});
      mk.bindTooltip(
        "<div class='tt-h'>" + c.s +
        " <span class='tt-b'>" + (c.f ? "DFO" : "ONC") + "</span>" +
        (c.q ? " <span class='tt-b'>QC</span>" : "") + "</div>" +
        "<div class='tt-r'><span class='tt-dot' style='background:" +
        (c.q ? "#868e96" : VI.colors[c.c]) + "'></span><b>" +
        c.o.toFixed(2) + " mL/L</b>&nbsp;<span class='tt-mut'>" +
        VI.labels[c.c] + "</span></div>" +
        "<div class='tt-r tt-mut'>" + c.t.slice(0, 10) + "</div>",
        {sticky: true, className: "vi-tt"});
      mk.on("click", function () {
        openDetail(castHtml(c), c.s, undefined, [c.la, c.lo]);
      });
      return mk;
    });
    H._openCast = function (i) {
      var c = VI.casts[i];
      openDetail(castHtml(c), c.s, undefined, [c.la, c.lo]);
    };
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
      if (c.j) h += "<div class='i-note info'>All casts at this " +
        "station share one nominal coordinate, so dots are arranged in " +
        "a time-ordered spiral (oldest at the centre, newest ~600 m " +
        "out).</div>";
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

    // ---- modeled surface (imageOverlay frames from MODEL_MANIFEST) ----
    var surfaceLayer = null;
    var surfaceSel = document.getElementById("surface-frame");
    var surfaceCtl = document.getElementById("surface-ctl");
    if (typeof MODEL_MANIFEST !== "undefined" && surfaceSel &&
        MODEL_MANIFEST.frames && MODEL_MANIFEST.frames.length) {
      surfaceCtl.classList.remove("hidden");
      MODEL_MANIFEST.frames.forEach(function (f) {
        var o = document.createElement("option");
        o.value = f.png;
        o.textContent = f.label;
        surfaceSel.appendChild(o);
      });
      // while the surface is on, fade the basemap and draw the coastline
      // so the frame's colors never fight the map's own greens
      var scrimLayer = L.rectangle([[40, -145], [58, -105]],
        {pane: "scrim", stroke: false, fill: true, fillOpacity: 1,
         interactive: false, className: "map-scrim"});
      var coastLayer = VI.coast ? L.geoJSON(
        {type: "Feature", properties: {},
         geometry: {type: "MultiLineString", coordinates: VI.coast}},
        {pane: "coast", interactive: false,
         style: {weight: 1, opacity: .9, fill: false,
                 className: "coastline"}}) : null;
      var showSurface = function () {
        if (surfaceLayer) map.removeLayer(surfaceLayer);
        surfaceLayer = L.imageOverlay("model_grid/" + surfaceSel.value,
          MODEL_MANIFEST.bounds,
          {pane: "surface", opacity: 0.9,
           attribution: "Modeled \u00b7 " + MODEL_MANIFEST.model_version});
        surfaceLayer.addTo(map);
        scrimLayer.addTo(map);
        if (coastLayer) coastLayer.addTo(map);
      };
      var hideSurface = function () {
        if (surfaceLayer) map.removeLayer(surfaceLayer);
        map.removeLayer(scrimLayer);
        if (coastLayer) map.removeLayer(coastLayer);
      };
      var ckS = document.getElementById("ck-surface");
      ckS.addEventListener("change", function (e) {
        if (e.target.checked) showSurface();
        else hideSurface();
      });
      surfaceSel.addEventListener("change", function () {
        if (ckS.checked) showSurface();
      });
    }

    // ---- modeled-station triangles (hollow; toggle, default off) ----
    var byCode = {};
    VI.sites.forEach(function (s) { byCode[s.code] = s; });
    var modeledLayer = L.layerGroup();
    var KIND_LABEL = {dfo: "DFO cast station",
      continuous: "continuous site", onc_cf: "ONC CF station"};
    function modIcon(cls) {
      var col = VI.colors[cls] || VI.colors.unclassified;
      return L.divIcon({html: "<svg width='22' height='22' xmlns='" +
        "http://www.w3.org/2000/svg'><polygon points='11,3.5 19.5,18.5 " +
        "2.5,18.5' fill='rgba(255,255,255,.28)' stroke='" + col +
        "' stroke-width='2.2'/></svg>",
        className: "", iconSize: [22, 22], iconAnchor: [11, 11]});
    }
    function modStationHtml(code, kind, cls, nCasts) {
      return "<div class='i-h'>" + code +
        " <span class='tt-b'>MODELED</span></div>" +
        "<div class='i-subh'>" + (KIND_LABEL[kind] || kind) +
        " \u00b7 hgb_quantile_v1.1</div>" +
        "<div class='i-sec'><div class='i-lab'>Predicted status</div>" +
        "<div class='i-pills'><span class='i-pill'><span class='i-dot' " +
        "style='background:" + (VI.colors[cls] || "#868e96") + "'></span>" +
        "Typical low (pred p10): <b>" + (VI.labels[cls] || "\u2013") +
        "</b></span></div></div>" +
        "<div class='i-sec'><div class='i-lab'>Observed here</div>" +
        "<div class='i-row'>" + nCasts + " CTD cast" +
        (nCasts === 1 ? "" : "s") + " (drawn on the chart)</div></div>" +
        "<div class='i-note info'>Every value from this marker is a " +
        "<b>model prediction</b>. The chart shows the model median " +
        "(dashed) with its calibrated 80% band; the station's real casts " +
        "sit on top.</div>";
    }
    if (VI.modelStations) {
      Object.keys(VI.modelStations).forEach(function (code) {
        var st = VI.modelStations[code];
        var kind = st[2], cls = st[3];
        if (kind === "onc_cf") return;              // site triangle exists
        if (kind === "continuous" && byCode[code]) return;  // circle exists
        var mk = L.marker([st[0], st[1]],
          {icon: modIcon(cls), keyboard: false});
        mk.bindTooltip("<div class='tt-h'>" + code +
          " <span class='tt-b'>MODELED</span></div>" +
          "<div class='tt-r'><span class='tt-dot' style='background:" +
          (VI.colors[cls] || "#868e96") + "'></span>" +
          (VI.labels[cls] || "") +
          " <span class='tt-mut'>(pred p10 \u00b7 v1.1)</span></div>",
          {sticky: true, className: "vi-tt"});
        mk.on("click", function () {
          var n = VI.casts.filter(function (c) {
            return c.s === code;
          }).length;
          openDetail(modStationHtml(code, kind, cls, n), code,
            undefined, [st[0], st[1]]);
        });
        modeledLayer.addLayer(mk);
      });
      var ckM = document.getElementById("ck-modelst");
      if (ckM) ckM.addEventListener("change", function (e) {
        if (e.target.checked) modeledLayer.addTo(map);
        else map.removeLayer(modeledLayer);
      });
    }
    H._openModeled = function (code) {
      var st = VI.modelStations[code];
      openDetail(modStationHtml(code, st[2], st[3],
        VI.casts.filter(function (c) { return c.s === code; }).length),
        code, undefined, [st[0], st[1]]);
    };

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
              casts: castLayer.getLayers().length,
              modeled: map.hasLayer(modeledLayer)
                ? modeledLayer.getLayers().length : 0};
    };
    document.getElementById("sb-toggle").onclick = function () {
      document.getElementById("sidebar").classList.toggle("open");
    };
  }

  if (document.readyState !== "loading") boot();
  else document.addEventListener("DOMContentLoaded", boot);
})();
