/**
 * Bird Calendar — orchestrator.
 *
 * Geolocation → current BirdNET week → one 48-week prediction at the point
 * (BirdNET Geomodel via ONNX worker) → rank the species we have plates for:
 *   Mode A (Residents): size ∝ this week's occurrence probability; sitting plates.
 *   Mode B (Migration): size ∝ arrival score (P[next]−P[prev])/peak; flying plates.
 * Birds are scattered around an empty centre; hover shows the name (language
 * selectable); 👍/👎 sends feedback via EmailJS.
 */
(function () {
  var MODEL_URL = "geomodel_fp16.onnx";
  var LABELS_URL = "labels.txt";
  var TAX_URL = "taxonomy.csv";
  var MANIFEST_URL = "birds/manifest.json";
  var DEFAULT = { lat: 59.33, lon: 18.07, name: "Stockholm (default)" }; // fallback

  var LANG_NAMES = {
    en: "English", sv: "Svenska", de: "Deutsch", fr: "Français", es: "Español",
    nl: "Nederlands", fi: "Suomi", no: "Norsk", da: "Dansk", it: "Italiano",
    pt: "Português", pl: "Polski", ru: "Русский", ja: "日本語", "zh-CN": "中文",
    cs: "Čeština", uk: "Українська", tr: "Türkçe",
  };

  var S = {
    labels: [], codeToIdx: {}, nSpecies: 0,
    tax: {}, langs: [], lang: "en",
    manifest: {}, allProbs: null,
    lat: DEFAULT.lat, lon: DEFAULT.lon, week: 1, mode: "A",
  };

  // ---- Worker / inference ---------------------------------------------------
  var worker = new Worker("inference-worker.js");
  var pending = {}, nextId = 1, workerReady = null;

  function initWorker() {
    workerReady = new Promise(function (resolve, reject) {
      worker.onmessage = function (e) {
        var m = e.data;
        if (m.type === "init") { m.ok ? resolve() : reject(new Error(m.error)); return; }
        if (m.type === "infer") {
          var cb = pending[m.id]; delete pending[m.id];
          if (!cb) return;
          if (m.error) cb.reject(new Error(m.error));
          else cb.resolve(new Float32Array(m.data));
        }
      };
      worker.postMessage({ type: "init", modelUrl: MODEL_URL });
    });
    return workerReady;
  }

  function runInference(flatInputs, batchSize) {
    return new Promise(function (resolve, reject) {
      var id = nextId++;
      pending[id] = { resolve: resolve, reject: reject };
      var buf = flatInputs.buffer;
      worker.postMessage({ type: "infer", id: id, flatInputs: buf,
        batchSize: batchSize, task: "raw" }, [buf]);
    });
  }

  // ---- Data loading ---------------------------------------------------------
  function loadLabels(text) {
    S.labels = text.trim().split("\n").map(function (line, i) {
      var p = line.split("\t");
      return { code: p[0], sci: p[1] || "", common: p[2] || p[1] || "", idx: i };
    });
    S.nSpecies = S.labels.length;
    S.labels.forEach(function (l) { S.codeToIdx[l.code] = l.idx; });
  }

  function parseCsv(text) {
    var rows = [], row = [], f = "", q = false;
    for (var i = 0; i < text.length; i++) {
      var c = text[i];
      if (q) {
        if (c === '"') { if (text[i + 1] === '"') { f += '"'; i++; } else q = false; }
        else f += c;
      } else if (c === '"') q = true;
      else if (c === ",") { row.push(f); f = ""; }
      else if (c === "\n") { row.push(f); f = ""; rows.push(row); row = []; }
      else if (c !== "\r") f += c;
    }
    if (f.length || row.length) { row.push(f); rows.push(row); }
    return rows;
  }

  function loadTaxonomy(text, needed) {
    var rows = parseCsv(text);
    var h = rows[0];
    var codeCol = h.indexOf("species_code"), sciCol = h.indexOf("sci_name");
    var enCol = h.indexOf("com_name");
    var langCol = { en: enCol };
    S.langs = ["en"];
    for (var c = 0; c < h.length; c++) {
      var m = /^common_name_(.+)$/.exec(h[c]);
      if (m) { langCol[m[1]] = c; S.langs.push(m[1]); }
    }
    for (var r = 1; r < rows.length; r++) {
      var code = rows[r][codeCol];
      if (!code || !needed[code]) continue;
      var rec = { sci: rows[r][sciCol] || "", names: {} };
      for (var lg in langCol) {
        var v = rows[r][langCol[lg]];
        if (v) rec.names[lg] = v;
      }
      S.tax[code] = rec;
    }
  }

  // Preferred path: names embedded in the manifest (lets us skip the 10 MB
  // taxonomy.csv download — important for free GitHub Pages bandwidth).
  function useManifestNames() {
    var has = false;
    for (var c in S.manifest) { if (S.manifest[c].names) { has = true; break; } }
    if (!has) return false;
    var langset = {};
    for (var code in S.manifest) {
      var e = S.manifest[code];
      S.tax[code] = { sci: e.sci || "", names: e.names || {} };
      for (var lg in (e.names || {})) langset[lg] = 1;
    }
    S.langs = Object.keys(langset);
    if (S.langs.indexOf("en") < 0) S.langs.unshift("en");
    return true;
  }

  function nameFor(code) {
    var rec = S.tax[code];
    var common = (rec && (rec.names[S.lang] || rec.names.en)) ||
      (S.manifest[code] && S.manifest[code].common) || code;
    var sci = (rec && rec.sci) || (S.manifest[code] && S.manifest[code].sci) || "";
    return { common: common, sci: sci };
  }

  // ---- Metrics --------------------------------------------------------------
  function birdNetWeek(d) {
    var start = new Date(d.getFullYear(), 0, 0);
    var day = Math.floor((d - start) / 86400000);
    return Math.max(1, Math.min(48, Math.floor((day - 1) / 365 * 48) + 1));
  }

  function metrics(code) {
    var idx = S.codeToIdx[code];
    if (idx === undefined || !S.allProbs) return null;
    var n = S.nSpecies, probs = new Array(48), max = 0;
    for (var w = 0; w < 48; w++) {
      var v = S.allProbs[w * n + idx]; probs[w] = v; if (v > max) max = v;
    }
    var wi = S.week - 1;
    var cur = probs[wi];
    var prev = probs[(wi + 47) % 48], next = probs[(wi + 1) % 48];
    var arrival = max < 1e-6 ? 0 : (next - prev) / max;
    return { cur: cur, arrival: arrival, peak: max };
  }

  // ---- Rendering ------------------------------------------------------------
  var stage = document.getElementById("stage");
  var tip = document.getElementById("tip");

  function pickImage(entry, stance) {
    var list = (entry.stances && entry.stances[stance]) || [];
    return list.length ? list[Math.floor(Math.random() * list.length)] : null;
  }

  function render() {
    stage.innerHTML = "";
    var stance = S.mode === "A" ? "sitting" : "flying";
    var items = [];
    Object.keys(S.manifest).forEach(function (code) {
      var entry = S.manifest[code];
      var img = pickImage(entry, stance);
      if (!img) return;
      var mt = metrics(code);
      var value = !mt ? 0.5 : (S.mode === "A" ? mt.cur : Math.max(0, mt.arrival));
      if (S.mode === "B" && mt && mt.arrival <= 0) return; // only arriving species
      if (value <= 0) return;
      items.push({ code: code, img: img, stance: stance, value: value });
    });
    document.getElementById("hint").style.display = items.length ? "none" : "flex";
    if (!items.length) {
      document.getElementById("hint").textContent =
        S.mode === "A" ? "No resident birds to show here." : "No arriving migrants this week.";
    }

    // Residents spiral by probability from the centre; migration uses scatter.
    var layoutFn = S.mode === "A" ? window.BirdLayout.placeSpiral : window.BirdLayout.place;
    var placed = layoutFn(items, window.innerWidth, window.innerHeight);
    placed.forEach(function (it) {
      var el = document.createElement("div");
      el.className = "bird";
      el.style.left = it.x + "px"; el.style.top = it.y + "px";
      el.style.width = it.size + "px";
      var im = document.createElement("img");
      im.src = "birds/" + it.img; im.alt = nameFor(it.code).common;
      // Flip the bird to face the centre of the page.
      var faces = (S.manifest[it.code].faces || {})[it.img];
      var halfW = window.innerWidth / 2;
      if ((it.x > halfW && faces === "right") || (it.x < halfW && faces === "left")) {
        im.style.transform = "scaleX(-1)";
      }
      el.appendChild(im);

      var fb = document.createElement("div");
      fb.className = "fb";
      fb.innerHTML =
        '<button class="up" title="Good">👍</button>' +
        '<button class="down" title="Poor">👎</button>';
      fb.querySelector(".up").onclick = function (e) { e.stopPropagation(); doVote(it, "up", fb); };
      fb.querySelector(".down").onclick = function (e) { e.stopPropagation(); doVote(it, "down", fb); };
      el.appendChild(fb);

      el.addEventListener("mousemove", function (ev) { showTip(ev, it.code); });
      el.addEventListener("mouseleave", function () { tip.classList.remove("show"); });
      // Click a bird → its Macaulay Library species page (code == taxon code).
      el.title = "View " + nameFor(it.code).common + " on Macaulay Library";
      el.addEventListener("click", function () {
        window.open("https://search.macaulaylibrary.org/catalog?taxonCode=" +
          encodeURIComponent(it.code) + "&mediaType=photo", "_blank", "noopener");
      });
      stage.appendChild(el);
    });
    setStatus(items.length);
  }

  function doVote(it, dir, fb) {
    if (window.BirdFeedback) {
      var nm = nameFor(it.code);
      window.BirdFeedback.vote(it.img, dir, {
        species: it.code, common: nm.common, sci: nm.sci,
        pose: it.stance, lang: S.lang, url: "birds/" + it.img,
      });
    }
    // Not sticky: flash the clicked button, then clear it.
    var btn = fb.querySelector(dir === "up" ? ".up" : ".down");
    btn.classList.add("act");
    setTimeout(function () { btn.classList.remove("act"); }, 400);
  }

  function showTip(ev, code) {
    var nm = nameFor(code);
    tip.innerHTML = nm.common + (nm.sci ? '<br><span class="sci">' + nm.sci + "</span>" : "");
    tip.style.left = ev.clientX + "px";
    tip.style.top = (ev.clientY + 18) + "px";
    tip.classList.add("show");
  }

  function setStatus(n) {
    var modeName = S.mode === "A" ? "Residents" : "Migration";
    document.getElementById("status").textContent =
      modeName + " · week " + S.week + " of 48 · " + n + " species";
  }

  // ---- Controls -------------------------------------------------------------
  function setupControls() {
    document.querySelectorAll("#mode button").forEach(function (b) {
      b.onclick = function () {
        document.querySelectorAll("#mode button").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on"); S.mode = b.getAttribute("data-mode"); render();
      };
    });
    var sel = document.getElementById("lang");
    // Only offer languages we have a friendly name for, plus English first.
    var offer = S.langs.filter(function (l) { return LANG_NAMES[l]; });
    if (offer.indexOf("en") < 0) offer.unshift("en");
    sel.innerHTML = offer.map(function (l) {
      return '<option value="' + l + '">' + (LANG_NAMES[l] || l) + "</option>";
    }).join("");
    var def = offer.indexOf("sv") >= 0 ? "sv" : "en";
    S.lang = def; sel.value = def;
    sel.onchange = function () { S.lang = sel.value; render(); };
    window.addEventListener("resize", debounce(render, 200));
  }

  function debounce(fn, ms) {
    var t; return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  // ---- Location + map -------------------------------------------------------
  async function setLocation(lat, lon, name) {
    S.lat = lat; S.lon = lon;
    document.getElementById("place").textContent =
      "📍 " + (name || (lat.toFixed(2) + ", " + lon.toFixed(2)));
    var inputs = new Float32Array(48 * 3);
    for (var w = 0; w < 48; w++) {
      inputs[w * 3] = lat; inputs[w * 3 + 1] = lon; inputs[w * 3 + 2] = w + 1;
    }
    setStatus("…");
    S.allProbs = await runInference(inputs, 48);
    render();
  }

  var _map = null, _marker = null;
  function openMap() {
    var modal = document.getElementById("map-modal");
    modal.hidden = false;
    if (!_map) {
      _map = L.map("map").setView([S.lat, S.lon], 4);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        { maxZoom: 12, attribution: "© OpenStreetMap" }).addTo(_map);
      _marker = L.marker([S.lat, S.lon]).addTo(_map);
      _map.on("click", function (e) {
        _marker.setLatLng(e.latlng);
        modal.hidden = true;
        setLocation(e.latlng.lat, e.latlng.lng, null);
      });
    } else {
      _marker.setLatLng([S.lat, S.lon]); _map.setView([S.lat, S.lon]);
    }
    setTimeout(function () { _map.invalidateSize(); }, 60);  // size known after unhide
  }

  function setupMap() {
    document.getElementById("place").addEventListener("click", openMap);
    document.getElementById("map-close").addEventListener("click", function () {
      document.getElementById("map-modal").hidden = true;
    });
    document.getElementById("map-modal").addEventListener("click", function (e) {
      if (e.target.id === "map-modal") e.currentTarget.hidden = true;
    });
  }

  // ---- Boot -----------------------------------------------------------------
  function getLocation() {
    return new Promise(function (resolve) {
      if (!navigator.geolocation) return resolve(DEFAULT);
      navigator.geolocation.getCurrentPosition(
        function (p) { resolve({ lat: p.coords.latitude, lon: p.coords.longitude, name: null }); },
        function () { resolve(DEFAULT); },
        { timeout: 8000, maximumAge: 3600000 });
    });
  }

  async function boot() {
    try {
      var texts = await Promise.all([
        fetch(LABELS_URL).then(function (r) { return r.text(); }),
        fetch(MANIFEST_URL).then(function (r) { return r.json(); }),
        initWorker(),
      ]);
      loadLabels(texts[0]);
      S.manifest = texts[1];
      // Names come from the manifest when present; otherwise fall back to the
      // (large) taxonomy.csv for backward compatibility.
      if (!useManifestNames()) {
        var taxText = await fetch(TAX_URL).then(function (r) { return r.text(); });
        loadTaxonomy(taxText, S.manifest);
      }
      setupControls();
      setupMap();

      S.week = birdNetWeek(new Date());
      var loc = await getLocation();
      await setLocation(loc.lat, loc.lon, loc.name);
    } catch (err) {
      console.error(err);
      document.getElementById("hint").textContent = "Failed to load: " + err.message;
    }
  }

  boot();
})();
