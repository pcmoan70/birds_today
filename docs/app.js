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
  var PLATES_URL = "plates/manifest.json";
  var DEFAULT = { lat: 59.33, lon: 18.07, name: "Stockholm (default)" }; // fallback

  var LANG_NAMES = {
    en: "English", sv: "Svenska", de: "Deutsch", fr: "Français", es: "Español",
    nl: "Nederlands", fi: "Suomi", no: "Norsk", da: "Dansk", it: "Italiano",
    pt: "Português", pl: "Polski", ru: "Русский", ja: "日本語", "zh-CN": "中文",
    cs: "Čeština", uk: "Українська", tr: "Türkçe",
  };

  // Localised page title (falls back to English for any other locale).
  var TITLES = {
    en: "Birds Today", sv: "Fåglar idag", de: "Vögel heute",
    fr: "Oiseaux aujourd'hui", es: "Aves hoy", nl: "Vogels vandaag",
    fi: "Linnut tänään", no: "Fugler i dag", da: "Fugle i dag",
    it: "Uccelli oggi", pt: "Aves hoje", pl: "Ptaki dzisiaj",
    ru: "Птицы сегодня", ja: "今日の鳥", "zh-CN": "今日鸟类",
    cs: "Ptáci dnes", uk: "Птахи сьогодні", tr: "Bugün kuşlar",
  };

  // Per-language name casing: eBird already follows each language's convention
  // (Swedish/Finnish/Polish/Czech lowercase; German/French/Danish capitalised),
  // except Norwegian, which it stores lowercase though the Norwegian birding
  // convention capitalises species names ("svarttrost" -> "Svarttrost").
  var CAP_FIRST = { no: 1 };

  // Human-readable source for the image-origin line in the hover tooltip.
  var BOOK_INFO = {
    gould: "John Gould, The Birds of Europe",
    dresser: "H. E. Dresser, A History of the Birds of Europe",
  };

  var S = {
    labels: [], codeToIdx: {}, nSpecies: 0,
    tax: {}, langs: [], lang: "en",
    manifest: {}, plates: {}, allProbs: null,
    lat: DEFAULT.lat, lon: DEFAULT.lon, week: 1, mode: "A", src: "gould",
    aiFallback: true,
  };

  // Images the user downvoted this session — grayed out until the tab closes.
  var DOWNVOTED = (function () {
    try { return new Set(JSON.parse(sessionStorage.getItem("bc_down") || "[]")); }
    catch (e) { return new Set(); }
  })();
  function markDownvoted(img) {
    DOWNVOTED.add(img);
    try { sessionStorage.setItem("bc_down", JSON.stringify([...DOWNVOTED])); }
    catch (e) {}
  }

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

  // Plate-only species aren't in the AI manifest's names; pull their localized
  // names (embedded in plates/manifest.json) into S.tax so the caption is in
  // the chosen locale for every bird.
  function mergePlateNames() {
    var langset = {};
    for (var code in S.plates) {
      var e = S.plates[code];
      if (!S.tax[code] && (e.names || e.sci)) {
        S.tax[code] = { sci: e.sci || "", names: e.names || {} };
      }
      for (var lg in (e.names || {})) langset[lg] = 1;
    }
    for (var l2 in langset) {
      if (S.langs.indexOf(l2) < 0) S.langs.push(l2);
    }
  }

  function nameFor(code) {
    var rec = S.tax[code];
    var common = (rec && (rec.names[S.lang] || rec.names.en)) ||
      (S.manifest[code] && S.manifest[code].common) || code;
    if (CAP_FIRST[S.lang] && common) {
      common = common.charAt(0).toUpperCase() + common.slice(1);
    }
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

  // Pick the image to show for a species given the chosen source.
  //  - "ai": the generated stance cutout (flippable to face centre).
  //  - "gould"/"dresser": the public-domain book plate; the chosen book is
  //    preferred, the other is the fallback, and species with no plate fall
  //    back to the AI cutout so the page stays full. Plates carry their own
  //    labels, so they are never flipped.
  // Returns {src, id, flip} or null. `id` keys downvotes and the faces map.
  function chooseImage(code, stance) {
    function ai() {
      var img = pickImage(S.manifest[code] || {}, stance);
      if (!img) return null;
      var face = ((S.manifest[code] || {}).faces || {})[img];
      return { src: "birds/" + img, id: img, flip: true, face: face,
        origin: "AI-generated field-guide illustration", page: null };
    }
    if (S.src === "ai") return ai();
    var p = S.plates[code];
    if (p) {
      var order = S.src === "dresser" ? ["dresser", "gould"] : ["gould", "dresser"];
      // Prefer a single-species plate (from either book, in source order); only
      // fall back to a multi-species plate — which shows the whole plate, other
      // species included — when no clean single one exists for this species.
      var single = null, sBook = null, whole = null, wBook = null;
      for (var i = 0; i < order.length; i++) {
        var b = order[i], e = p[b];
        if (!e) continue;
        if (e.multi) { if (!whole) { whole = e; wBook = b; } }
        else { single = e; sBook = b; break; }
      }
      var pick = single || whole, book = single ? sBook : wBook;
      if (pick) {
        var origin = (BOOK_INFO[book] || book) +
          (pick.volume ? ", " + pick.volume : "") +
          (pick.multi ? " — plate shows several species" : "");
        return { src: pick.img, id: pick.img, flip: true, face: pick.face,
          origin: origin, page: pick.page_url || null };
      }
    }
    // No plate for this species: fall back to an AI image, unless disabled.
    return S.aiFallback ? ai() : null;
  }

  function render() {
    stage.innerHTML = "";
    var stance = S.mode === "A" ? "sitting" : "flying";
    var items = [];
    // Union of AI-manifest and plate codes: a plate-covered species may not
    // have an AI image yet, and vice versa.
    var codes = {};
    Object.keys(S.manifest).forEach(function (c) { codes[c] = 1; });
    if (S.src !== "ai") Object.keys(S.plates).forEach(function (c) { codes[c] = 1; });
    Object.keys(codes).forEach(function (code) {
      var pick = chooseImage(code, stance);
      if (!pick) return;
      var mt = metrics(code);
      var value = !mt ? 0.5 : (S.mode === "A" ? mt.cur : Math.max(0, mt.arrival));
      if (S.mode === "B" && mt && mt.arrival <= 0) return; // only arriving species
      if (value <= 0) return;
      items.push({ code: code, img: pick.id, src: pick.src, flip: pick.flip,
        face: pick.face, origin: pick.origin, page: pick.page,
        stance: stance, value: value });
    });
    document.getElementById("hint").style.display = items.length ? "none" : "flex";
    if (!items.length) {
      document.getElementById("hint").textContent =
        S.mode === "A" ? "No resident birds to show here." : "No arriving migrants this week.";
    }

    // Dense top-to-bottom packing by probability; the page scrolls. The layout
    // (just maths) is computed for all birds, but DOM elements are created
    // incrementally as the page is scrolled (see buildUpTo).
    var W = stage.clientWidth || window.innerWidth;
    var res = window.BirdLayout.placeScroll(items, W);
    stage.style.height = res.height + "px";
    SCROLL.items = res.placed.slice().sort(function (a, b) { return a.y - b.y; });
    SCROLL.idx = 0; SCROLL.halfW = W / 2;
    window.scrollTo(0, 0);
    buildUpTo(2 * window.innerHeight);   // first screenful (+ one ahead)
    setStatus(items.length);
  }

  // Incremental builder: birds are mounted only once the scroll reaches them.
  var SCROLL = { items: [], idx: 0, halfW: 0 };

  function buildBird(it) {
    var el = document.createElement("div");
    el.className = "bird" + (DOWNVOTED.has(it.img) ? " downvoted" : "");
    el.style.left = it.x + "px"; el.style.top = it.y + "px";
    el.style.width = it.size + "px";
    var im = document.createElement("img");
    im.loading = "lazy"; im.decoding = "async";
    im.src = it.src; im.alt = nameFor(it.code).common;
    // Flip the bird so it faces the centre of the page (beak toward middle).
    if (it.flip && it.face) {
      if ((it.x > SCROLL.halfW && it.face === "right") ||
          (it.x < SCROLL.halfW && it.face === "left")) {
        im.style.transform = "scaleX(-1)";
      }
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

    el.addEventListener("mousemove", function (ev) { showTip(ev, it); });
    el.addEventListener("mouseleave", function () { tip.classList.remove("show"); });
    el.title = nameFor(it.code).common;
    el.addEventListener("click", function () { tip.classList.remove("show"); openBird(it); });
    stage.appendChild(el);
  }

  // Mount every not-yet-built bird whose top edge is above yLimit.
  function buildUpTo(yLimit) {
    var a = SCROLL.items;
    while (SCROLL.idx < a.length && a[SCROLL.idx].y - a[SCROLL.idx].size / 2 <= yLimit) {
      buildBird(a[SCROLL.idx]); SCROLL.idx++;
    }
  }

  var _scrollPending = false;
  function onScroll() {
    if (_scrollPending) return;
    _scrollPending = true;
    requestAnimationFrame(function () {
      _scrollPending = false;
      buildUpTo(window.scrollY + 2 * window.innerHeight);  // one screen ahead
    });
  }

  function doVote(it, dir, fb) {
    if (window.BirdFeedback) {
      var nm = nameFor(it.code);
      window.BirdFeedback.vote(it.img, dir, {
        species: it.code, common: nm.common, sci: nm.sci,
        pose: it.stance, lang: S.lang, src: S.src, url: it.src,
      });
    }
    // Not sticky: flash the clicked button, then clear it.
    var btn = fb.querySelector(dir === "up" ? ".up" : ".down");
    btn.classList.add("act");
    setTimeout(function () { btn.classList.remove("act"); }, 400);
    // Downvote grays the bird out for the rest of the session.
    if (dir === "down") {
      markDownvoted(it.img);
      if (fb.parentElement) fb.parentElement.classList.add("downvoted");
    }
  }

  function showTip(ev, it) {
    var nm = nameFor(it.code);
    var html = nm.common + (nm.sci ? '<br><span class="sci">' + nm.sci + "</span>" : "");
    var meta = [];
    if (it.origin) meta.push("<b>Image:</b> " + it.origin);
    var mt = metrics(it.code);
    if (mt) {
      var pct = Math.round(Math.max(0, Math.min(1, mt.cur)) * 100);
      meta.push("<b>Seen here this week:</b> " + pct + "%");
    }
    if (meta.length) html += '<div class="meta">' + meta.join("<br>") + "</div>";
    html += '<div class="hint2">Click for photos &amp; sounds (Macaulay Library)</div>';
    tip.innerHTML = html;
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
    document.querySelectorAll("#src button").forEach(function (b) {
      b.onclick = function () {
        document.querySelectorAll("#src button").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on"); S.src = b.getAttribute("data-src"); render();
      };
    });
    var sel = document.getElementById("lang");
    // Only offer languages we have a friendly name for, plus English first.
    var offer = S.langs.filter(function (l) { return LANG_NAMES[l]; });
    if (offer.indexOf("en") < 0) offer.unshift("en");
    sel.innerHTML = offer.map(function (l) {
      return '<option value="' + l + '">' + (LANG_NAMES[l] || l) + "</option>";
    }).join("");
    var def = "en";   // default to English
    S.lang = def; sel.value = def; setTitle();
    sel.onchange = function () { S.lang = sel.value; setTitle(); render(); };
    var aifb = document.getElementById("ai-fallback");
    aifb.checked = S.aiFallback;
    aifb.onchange = function () { S.aiFallback = aifb.checked; render(); };
    window.addEventListener("resize", debounce(render, 200));
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  function setTitle() {
    var t = TITLES[S.lang] || TITLES.en;
    document.getElementById("title").textContent = t;
    document.title = t;
  }

  function setupHelp() {
    var modal = document.getElementById("help-modal");
    document.getElementById("help-btn").onclick = function () { modal.hidden = false; };
    document.getElementById("help-close").onclick = function () { modal.hidden = true; };
    modal.addEventListener("click", function (e) {
      if (e.target.id === "help-modal") modal.hidden = true;
    });
  }

  // Full-screen detail view for a clicked bird: larger image, name, and the
  // image source (clicking a book source jumps straight to the scanned page).
  function openBird(it) {
    var nm = nameFor(it.code);
    var img = document.getElementById("bird-img");
    img.src = it.src; img.alt = nm.common;
    document.getElementById("bird-name").textContent = nm.common;
    document.getElementById("bird-sci").textContent = nm.sci || "";

    var srcEl = document.getElementById("bird-src");
    srcEl.textContent = "Source: ";
    if (it.page) {
      var a = document.createElement("a");
      a.href = it.page; a.target = "_blank"; a.rel = "noopener";
      a.textContent = it.origin || "view source page";
      srcEl.appendChild(a);
    } else {
      srcEl.appendChild(document.createTextNode(it.origin || "—"));
    }

    var extra = document.getElementById("bird-extra");
    extra.textContent = "";
    var mt = metrics(it.code);
    if (mt) {
      var pct = Math.round(Math.max(0, Math.min(1, mt.cur)) * 100);
      extra.appendChild(document.createTextNode("Seen here this week: " + pct + "%"));
      extra.appendChild(document.createElement("br"));
    }
    var ml = document.createElement("a");
    ml.href = "https://search.macaulaylibrary.org/catalog?taxonCode=" +
      encodeURIComponent(it.code) + "&mediaType=photo";
    ml.target = "_blank"; ml.rel = "noopener";
    ml.textContent = "Photos & sounds on Macaulay Library →";
    extra.appendChild(ml);

    document.getElementById("bird-modal").hidden = false;
  }

  function setupBirdModal() {
    var modal = document.getElementById("bird-modal");
    document.getElementById("bird-close").onclick = function () { modal.hidden = true; };
    modal.addEventListener("click", function (e) {
      if (e.target === modal) modal.hidden = true;   // click backdrop to close
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") modal.hidden = true;
    });
  }

  // All controls live in one dropdown menu to keep the bar compact.
  function setupMenu() {
    var wrap = document.getElementById("menu-wrap");
    var btn = document.getElementById("menu-btn");
    var menu = document.getElementById("menu");
    function close() { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); }
    btn.onclick = function (e) {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      btn.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
    };
    document.addEventListener("click", function (e) {
      if (!wrap.contains(e.target)) close();
    });
    // Close after picking a view/image/location/about (language stays open).
    menu.querySelectorAll("#mode button, #src button, #place, #help-btn")
      .forEach(function (b) { b.addEventListener("click", close); });
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
        fetch(PLATES_URL).then(function (r) { return r.ok ? r.json() : {}; })
          .catch(function () { return {}; }),
        initWorker(),
      ]);
      loadLabels(texts[0]);
      S.manifest = texts[1];
      S.plates = texts[2] || {};
      // Names come from the manifest when present; otherwise fall back to the
      // (large) taxonomy.csv for backward compatibility.
      if (!useManifestNames()) {
        var taxText = await fetch(TAX_URL).then(function (r) { return r.text(); });
        loadTaxonomy(taxText, S.manifest);
      }
      mergePlateNames();   // localized names for plate-only species
      setupControls();
      setupMap();
      setupHelp();
      setupMenu();
      setupBirdModal();

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
