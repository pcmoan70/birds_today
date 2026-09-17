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
  // Species the app has no image for at all (scripts/build_missing.py) — the
  // curated set, with Macaulay reference photos attached where the lookup could
  // reach them. Beyond that set, ANY bird the model predicts here without an
  // image gets a placeholder too (aves.txt lists the model's bird codes; their
  // names come from labels.txt), so the calendar shows the gaps wherever you
  // point it, not just where images already exist.
  var MISSING_URL = "missing.json";
  var AVES_URL = "aves.txt";
  var DEFAULT = { lat: 59.33, lon: 18.07, name: "Stockholm (default)" }; // fallback

  var LANG_NAMES = {
    en: "English", sv: "Svenska", de: "Deutsch", fr: "Français", es: "Español",
    nl: "Nederlands", fi: "Suomi", no: "Norsk", da: "Dansk", it: "Italiano",
    pt: "Português", pl: "Polski", ru: "Русский", ja: "日本語", "zh-CN": "中文",
    cs: "Čeština", uk: "Українська", tr: "Türkçe",
  };

  // Localised page title (falls back to English for any other locale).
  var TITLES = {
    en: "Birds here today", sv: "Fåglar här idag", de: "Vögel heute hier",
    fr: "Oiseaux ici aujourd'hui", es: "Aves aquí hoy", nl: "Vogels hier vandaag",
    fi: "Linnut täällä tänään", no: "Fugler her i dag", da: "Fugle her i dag",
    it: "Uccelli qui oggi", pt: "Aves aqui hoje", pl: "Ptaki tutaj dzisiaj",
    ru: "Птицы здесь сегодня", ja: "今日ここの鳥", "zh-CN": "此地今日鸟类",
    cs: "Ptáci tady dnes", uk: "Птахи тут сьогодні", tr: "Bugün buradaki kuşlar",
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
    vonwright: "M. & W. von Wright, Svenska fåglar",
    lilford: "Lord Lilford, Coloured Figures of the Birds of the British Islands (Thorburn & Keulemans)",
  };
  // Book sources in default preference order; short labels for the detail view.
  var BOOKS = ["gould", "dresser", "vonwright", "lilford"];
  var BOOK_LABEL = { gould: "Gould", dresser: "Dresser", vonwright: "von Wright", lilford: "Lilford" };

  var S = {
    labels: [], codeToIdx: {}, nSpecies: 0,
    tax: {}, langs: [], lang: "en",
    manifest: {}, plates: {}, missing: {}, aves: null, photos: null, ml: null, probs: {},
    fieldId: null,                         // field_id.json, fetched on first detail view
    lat: DEFAULT.lat, lon: DEFAULT.lon, week: 1, mode: "A", src: "gould",
    aiBW: false,
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
    // Species with no image at all carry their own names too, so a placeholder
    // is labelled in the chosen language.
    [S.plates, S.missing].forEach(function (src) {
      for (var code in src) {
        var e = src[code];
        if (!S.tax[code] && (e.names || e.sci)) {
          S.tax[code] = { sci: e.sci || "", names: e.names || {} };
        }
        for (var lg in (e.names || {})) langset[lg] = 1;
      }
    });
    for (var l2 in langset) {
      if (S.langs.indexOf(l2) < 0) S.langs.push(l2);
    }
  }

  // Open the image review tool for a species. A species that has no images at
  // all isn't in the review manifest, so it is passed as a request (with its
  // name, which that page can't look up) and the tool offers to add images.
  function openReview(code) {
    var url = "review.html#" + encodeURIComponent(code);
    if (!S.manifest[code] && !S.plates[code]) {
      var nm = nameFor(code);
      var shown = REF_CACHE[code];        // the CC photo on the card, if any
      var ml = mlAsset(code);             // the curated Macaulay seed, if any
      // One click is the whole request: record it here (the review tool reads
      // the same store) so that page opens with the species already ticked and
      // only the export is left to do. The seed the generator should start from
      // travels with it — the Macaulay asset when there is one (it is the best
      // reference and stays local to the pipeline), else the CC photo shown.
      try {
        var rq = JSON.parse(localStorage.getItem("birdReviewRequests") || "{}");
        rq[code] = { name: nm.common, sci: nm.sci || "", requested: true,
          ts: new Date().toISOString(),
          seed_asset: ml ? ml.id : "",
          seed: !ml && shown && shown.url ? shown.url : "",
          seed_src: ml ? "macaulay" : (shown ? shown.src || "" : "") };
        localStorage.setItem("birdReviewRequests", JSON.stringify(rq));
      } catch (e) {}
      url = "review.html?add=" + encodeURIComponent(code) +
        "&name=" + encodeURIComponent(nm.common) +
        "&sci=" + encodeURIComponent(nm.sci || "");
      if (ml) url += "&seedasset=" + encodeURIComponent(ml.id);
      else if (shown && shown.url) {
        url += "&seed=" + encodeURIComponent(shown.url) +
          "&seedsrc=" + encodeURIComponent(shown.src || "");
      }
    }
    window.open(url, "_blank", "noopener");
  }

  function nameFor(code) {
    var rec = S.tax[code];
    // labels.txt names every species the model knows, so a placeholder for a
    // bird outside the curated set still gets a name (English + scientific).
    var lab = S.labels[S.codeToIdx[code]];
    var common = (rec && (rec.names[S.lang] || rec.names.en)) ||
      (S.manifest[code] && S.manifest[code].common) ||
      (lab && lab.common) || code;
    if (CAP_FIRST[S.lang] && common) {
      common = common.charAt(0).toUpperCase() + common.slice(1);
    }
    var sci = (rec && rec.sci) || (S.manifest[code] && S.manifest[code].sci) ||
      (lab && lab.sci) || "";
    return { common: common, sci: sci };
  }

  // ---- Metrics --------------------------------------------------------------
  function birdNetWeek(d) {
    var start = new Date(d.getFullYear(), 0, 0);
    var day = Math.floor((d - start) / 86400000);
    return Math.max(1, Math.min(48, Math.floor((day - 1) / 365 * 48) + 1));
  }

  // Weeks the current mode needs: Residents only needs this week's occurrence;
  // Migration needs the neighbouring weeks too, to measure the rising trend.
  function neededWeeks() {
    var wi = S.week - 1;
    return S.mode === "A" ? [wi] : [(wi + 47) % 48, wi, (wi + 1) % 48];
  }

  function metrics(code) {
    var idx = S.codeToIdx[code];
    if (idx === undefined) return null;
    var wi = S.week - 1;
    var cur = S.probs[wi] ? S.probs[wi][idx] : 0;
    if (S.mode === "A") return { cur: cur, arrival: 0 };
    // Migration: rising sharpness from prev->next, normalised by the local
    // window (we no longer run all 48 weeks just to get the annual peak).
    var pw = S.probs[(wi + 47) % 48], nw = S.probs[(wi + 1) % 48];
    var prev = pw ? pw[idx] : cur, next = nw ? nw[idx] : cur;
    var norm = Math.max(cur, prev, next, 1e-6);
    return { cur: cur, arrival: (next - prev) / norm };
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
  //  - a book in BOOKS: the public-domain book plate; the chosen book is
  //    preferred, the other is the fallback, and species with no plate fall
  //    back to the AI cutout so the page stays full. Plates carry their own
  //    labels, so they are never flipped.
  // Returns {src, id, flip} or null. `id` keys downvotes and the faces map.
  function chooseImage(code, stance) {
    function ai() {
      var img = pickImage(S.manifest[code] || {}, stance);
      if (!img) return null;
      var face = ((S.manifest[code] || {}).faces || {})[img];
      return { src: "birds/" + img, id: img, flip: true, face: face, ai: true,
        origin: "AI-generated field-guide illustration", page: null };
    }
    if (S.src === "ai") return ai();
    var p = S.plates[code];
    if (p) {
      var order = [S.src].concat(BOOKS.filter(function (b) { return b !== S.src; }));
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
    return ai();   // no plate for this species: fall back to an AI image
  }

  // ---- Reference photos ---------------------------------------------------
  // Photographs shown by the app — in a placeholder card and in the Photos grid
  // — must be ones we are allowed to show: photos.json carries the Wikimedia /
  // iNaturalist / GBIF references (CC or public domain, with the licence and
  // photographer), and anything else is looked up live from iNaturalist, whose
  // API is CORS-open and whose photos are CC-licensed.
  //
  // Macaulay Library photos are deliberately NOT displayed: they are copyright
  // their photographers, all rights reserved. The app links out to the
  // catalogue entry, and "+ add images" passes the asset id to the local
  // generation pipeline, where the photo is a transient img2img reference that
  // is never published — the same arrangement scripts/sources/whobird.py sets out.
  // photos.json, fetched the first time the Photos grid or a photo is needed.
  var _photosReq = null;
  function loadPhotos() {
    if (!_photosReq) {
      _photosReq = Promise.all([
        fetch(PHOTOS_URL).then(function (r) { return r.ok ? r.json() : {}; })
          .catch(function () { return {}; }),
        fetch(ML_URL).then(function (r) { return r.ok ? r.json() : {}; })
          .catch(function () { return {}; }),
      ]).then(function (a) { S.photos = a[0]; S.ml = a[1]; return a[0]; });
    }
    return _photosReq;
  }

  // The curated Macaulay asset for a species — used ONLY to pass a seed to the
  // local generation pipeline and to link to the catalogue. Never rendered.
  // A one-line credit for the tile: "© Name · CC BY-NC". iNaturalist's own
  // attribution string ("(c) Name, some rights reserved (CC BY-NC)") is long
  // enough to swamp a small card, so it is trimmed here and kept in full in the
  // tile's tooltip (see fullCredit).
  function creditLine(rec) {
    var who = (rec.by || "").replace(/^\(c\)\s*/i, "")
      .replace(/,?\s*(some|no|all) rights reserved.*$/i, "").trim();
    var lic = (rec.license || "").toUpperCase().replace(/-/g, " ");
    var bits = [];
    if (who) bits.push("© " + who);
    if (lic) bits.push(lic);
    if (!bits.length) bits.push(rec.credit || "iNaturalist");
    return bits.join(" · ");
  }

  function fullCredit(rec) {
    return (rec.by || rec.credit || "iNaturalist") +
      (rec.license && (rec.by || "").indexOf(rec.license) < 0
        ? " (" + rec.license.toUpperCase() + ")" : "");
  }

  // iNaturalist serves each photo at square / small / medium / large; the grid
  // wants "large" (sharp on a retina tile), the little placeholder card in the
  // scatter only needs "medium".
  function sizedPhoto(url, size) {
    return (url || "").replace(/\/(square|small|medium|large)\.(jpe?g|png)/i,
                               "/" + size + ".$2");
  }

  function mlAsset(code) {
    var aid = (S.ml || {})[code];
    if (!aid) return null;
    return { id: aid, page: "https://macaulaylibrary.org/asset/" + aid,
      seed: ML_CDN + aid + "/900" };
  }

  // code -> {url, by, license, page, src} | null. Kept for the tab as well, so
  // flipping between sources or locations doesn't ask iNaturalist again.
  // Kept across visits, not just the tab: every species costs one lookup ever,
  // so a grid you have seen before fills instantly and iNaturalist is spared.
  // Versioned: the key changes whenever the source of the photos changes, so a
  // returning visitor is not stuck for ever with what the previous version of
  // the app looked up. ("bc_refs" held the iNaturalist observation photos.)
  var REF_KEY = "bc_refs2";
  var REF_CACHE = (function () {
    try { localStorage.removeItem("bc_refs"); } catch (e) {}
    try { return JSON.parse(localStorage.getItem(REF_KEY) || "{}"); }
    catch (e) { return {}; }
  })();
  var _refSaveTimer = null;
  function saveRefCache() {
    clearTimeout(_refSaveTimer);
    _refSaveTimer = setTimeout(function () {
      try { localStorage.setItem(REF_KEY, JSON.stringify(REF_CACHE)); }
      catch (e) {}
    }, 500);
  }
  // iNaturalist asks for about one request a second, 100/min at the very most.
  // Going wider gets everything throttled, which reads on screen as "no photo".
  var _refQueue = [], _refBusy = 0, REF_PARALLEL = 2, REF_GAP = 900, _refLast = 0;

  function refPhoto(code, cb) {
    // The photo indexes are shared with the Photos grid; pull them in on the
    // first placeholder that needs one.
    if (!S.ml) { loadPhotos().then(function () { refPhoto(code, cb); }); return; }
    // photos.json ships with the app, so it is the authority: a curated species
    // must show what this version of the app curated for it, never an older
    // lookup the browser happens to be holding.
    var known = (S.photos || {})[code];
    if (!known && REF_CACHE[code] !== undefined) { cb(REF_CACHE[code]); return; }
    if (known) {
      REF_CACHE[code] = { url: known.url, by: known.by || "",
        page: known.page || null, license: known.license || "",
        credit: known.credit, src: "open" };
      cb(REF_CACHE[code]);
      return;
    }
    _refQueue.push([code, cb]);
    pumpRefQueue();
  }

  function pumpRefQueue() {
    if (_refBusy >= REF_PARALLEL || !_refQueue.length) return;
    var wait = Math.max(0, REF_GAP - (Date.now() - _refLast));
    setTimeout(function () {
      if (!_refQueue.length) return;
      var job = _refQueue.shift();
      _refBusy++; _refLast = Date.now();
      lookupPhoto(job[0], job[1]);
      pumpRefQueue();
    }, wait);
  }

  // Only openly licensed photos are shown: every candidate must carry a
  // licence code, and a taxon's own "default photo" is skipped when it does not
  // (it can be all rights reserved).
  // Same order as scripts/build_photos.py: the species article's lead photograph
  // first — editors put the best portrait at the top, and the file is on Commons
  // with a licence and a named photographer — then iNaturalist's curated set.
  // Both APIs allow cross-origin reads with origin=*.
  function lookupPhoto(code, cb) {
    var finish = function (rec, keep) {
      if (rec || keep) { REF_CACHE[code] = rec || null; saveRefCache(); }
      _refBusy--; cb(rec); pumpRefQueue();
    };
    fetchWikiLead(code,
      function () { fetchInat(code, cb); },     // no article photo: try iNaturalist
      finish);
  }

  function fetchWikiLead(code, next, done) {
    var nm = nameFor(code);
    var titles = [nm.sci, nm.common].filter(Boolean);
    if (!titles.length) { next(); return; }
    // Try the scientific name first and the common name only if that article
    // has no lead photo: a common name can land on a disambiguation page, the
    // binomial never does.
    var tryTitle = function (i) {
      if (i >= titles.length) { next(); return; }
      wikiLeadFor(titles[i], function () { tryTitle(i + 1); }, done);
    };
    tryTitle(0);
  }

  // Commons hands back thumb.wikimedia.org for a generated thumbnail; the
  // canonical host serves the same bytes and is the reliable one.
  function commonsHost(url) {
    return (url || "").replace(/^https:\/\/thumb\.wikimedia\.org\//,
                               "https://upload.wikimedia.org/");
  }

  function wikiLeadFor(title, next, done) {
    fetch("https://en.wikipedia.org/w/api.php?origin=*&format=json&redirects=1" +
          "&action=query&prop=pageimages&piprop=name&titles=" +
          encodeURIComponent(title))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        var pages = ((j || {}).query || {}).pages || {};
        var name = null;
        for (var k in pages) { if (pages[k].pageimage) { name = pages[k].pageimage; break; } }
        if (!name) { next(); return; }
        return fetch("https://commons.wikimedia.org/w/api.php?origin=*&format=json" +
                     "&action=query&prop=imageinfo&iiprop=extmetadata|url|size|user" +
                     "&iiurlwidth=1024&titles=" + encodeURIComponent("File:" + name))
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (c) {
            var cp = ((c || {}).query || {}).pages || {};
            var ii = null;
            for (var k2 in cp) { if (cp[k2].imageinfo) { ii = cp[k2].imageinfo[0]; break; } }
            var em = (ii || {}).extmetadata || {};
            var lic = ((em.LicenseShortName || {}).value || "").trim();
            var url = (ii || {}).thumburl || (ii || {}).url || "";
            if (!lic || !url || /fair use|non-free/i.test(lic)) { next(); return; }
            done({ url: commonsHost(url.split("?")[0]),
              credit: "Wikimedia Commons", license: lic,
              // A few files carry no Artist field though attribution is still
              // required; the uploader is the credit in that case.
              by: (((em.Artist || {}).value || "").replace(/<[^>]+>/g, "").trim()
                   || ii.user || ""),
              page: (ii || {}).descriptionurl || "", src: "commons" });
          });
      })
      .catch(function () { next(); });
  }

  function fetchInat(code, cb) {
    var sci = nameFor(code).sci;
    // `keep` distinguishes "this species has no openly licensed photo" (cache
    // it) from "the lookup failed / was throttled" (leave it out, so scrolling
    // back tries again instead of showing an empty card for ever).
    var done = function (rec, keep) {
      if (rec || keep) { REF_CACHE[code] = rec || null; saveRefCache(); }
      _refBusy--; cb(rec); pumpRefQueue();
    };
    if (!sci) { done(null, true); return; }
    // The same curated list scripts/build_photos.py uses, done live for a
    // species outside the curated set: iNaturalist keeps an ordered set of
    // representative photos per taxon, and the first openly licensed one is a
    // portrait of the bird rather than whatever observation happened to come
    // back — which is what made the grid full of distant birds on wires.
    fetch("https://api.inaturalist.org/v1/taxa?rank=species&per_page=5&q=" +
          encodeURIComponent(sci))
      .then(function (r) {
        if (r.status === 429 || r.status >= 500) return "throttled";
        return r.ok ? r.json() : null;
      })
      .then(function (j) {
        if (j === "throttled") { done(null, false); return; }
        var want = sci.toLowerCase();
        var t = ((j && j.results) || []).filter(function (x) {
          return (x.name || "").toLowerCase() === want;
        })[0];
        if (!t) { done(null, !!j); return; }
        return fetch("https://api.inaturalist.org/v1/taxa/" + t.id)
          .then(function (r2) {
            if (r2.status === 429 || r2.status >= 500) return "throttled";
            return r2.ok ? r2.json() : null;
          })
          .then(function (d) {
            if (d === "throttled") { done(null, false); return; }
            var res = (d && d.results && d.results[0]) || {};
            var list = res.taxon_photos || [];
            for (var i = 0; i < list.length; i++) {
              var ph = (list[i] || {}).photo || {};
              var lic = (ph.license_code || "").toLowerCase();
              var url = ph.medium_url || ph.url || "";
              if (!lic || !url) continue;          // all rights reserved: skip
              done({ url: (ph.large_url || url).replace("/square.", "/large.")
                              .replace("/small.", "/large.").replace("/medium.", "/large."),
                by: ph.attribution || "", license: lic.toUpperCase(),
                credit: "iNaturalist", src: "inat",
                page: "https://www.inaturalist.org/photos/" + (ph.id || "") });
              return;
            }
            done(null, !!d);
          });
      })
      .catch(function () { done(null, false); });   // network hiccup: retry later
  }

  // Stand-in for a species with no image in ANY source — the bird still belongs
  // on the page, and the card links into the review tool to ask for images.
  // Only missing.json qualifies: a species that merely lacks an image for the
  // current source or stance has pictures elsewhere and is left out as before,
  // rather than being shown as if it had none.
  function placeholderFor(code) {
    // In "ai" mode a plate-covered species isn't imageless — it just isn't an
    // AI drawing — so only species with nothing anywhere qualify.
    if (S.manifest[code] || S.plates[code]) return null;
    if (!S.missing[code] && !(S.aves && S.avesSet[code])) return null;
    return { src: null, id: "missing/" + code, missing: true, flip: false,
      origin: "No image yet — add one in the image review tool", page: null };
  }

  // The Photos source is a gallery, not a scatter of cutouts: same species,
  // same ranking, laid out as an even grid of photographs.
  function renderPhotoGrid() {
    stage.innerHTML = "";
    stage.classList.add("grid");
    stage.style.height = "";
    // Drop the scatter's pending layout. Without this, scrolling the grid makes
    // the incremental builder mount leftover bird cutouts on top of the photos.
    SCROLL.items = []; SCROLL.idx = 0;
    window.scrollTo(0, 0);      // a fresh layout starts at the top, as the scatter does
    var rows = [];
    var seen = {};
    var add = function (code) {
      if (seen[code]) return;
      seen[code] = 1;
      var mt = metrics(code);
      if (!mt) return;
      if (mt.cur <= 0.01) return;
      if (S.mode === "B" && mt.arrival <= 0) return;
      rows.push({ code: code, value: S.mode === "A" ? mt.cur : Math.max(0, mt.arrival) });
    };
    Object.keys(S.photos || {}).forEach(add);
    Object.keys(S.manifest).forEach(add);
    Object.keys(S.plates).forEach(add);
    Object.keys(S.missing).forEach(add);
    if (S.aves) S.aves.forEach(add);
    rows.sort(function (a, b) { return b.value - a.value; });

    document.getElementById("hint").style.display = rows.length ? "none" : "flex";
    rows.forEach(function (it) {
      var card = document.createElement("figure");
      card.className = "gcard";
      var im = document.createElement("img");
      im.loading = "lazy"; im.decoding = "async"; im.referrerPolicy = "no-referrer";
      im.alt = nameFor(it.code).common;
      card.appendChild(im);
      var cap = document.createElement("figcaption");
      var nm = nameFor(it.code);
      cap.innerHTML = '<span class="gname"></span><span class="gcredit"></span>';
      cap.querySelector(".gname").textContent = nm.common;
      card.appendChild(cap);
      card.title = nm.common + (nm.sci ? " — " + nm.sci : "");
      card.onclick = function () {
        openBird({ code: it.code, src: im.src || null, id: "photo/" + it.code,
          origin: card.dataset.origin || "Reference photograph",
          page: card.dataset.page || null, stance: "sitting" });
      };
      stage.appendChild(card);
      // Look the photo up only when the tile comes into view: a location can
      // have 450 species, and asking for all of them at once would both waste
      // the requests and trip iNaturalist's rate limit.
      whenVisible(card, function () { fillCard(it, card, im, cap); });
    });
    setStatus(rows.length);
  }

  var _seeObserver = null;
  function whenVisible(el, fn) {
    if (!window.IntersectionObserver) { fn(); return; }
    if (!_seeObserver) {
      _seeObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (!e.isIntersecting) return;
          _seeObserver.unobserve(e.target);
          var cb = e.target._onSee;
          if (cb) { e.target._onSee = null; cb(); }
        });
      }, { rootMargin: "400px" });
    }
    el._onSee = fn;
    _seeObserver.observe(el);
  }

  function fillCard(it, card, im, cap, tries) {
    var nm = nameFor(it.code);
    card.classList.add("loading");
    refPhoto(it.code, function (rec) {
        card.classList.remove("loading");
        if (!rec) {
          // Only call it empty once the lookup actually answered "nothing" —
          // a throttled or failed request leaves the species uncached, so try
          // again shortly rather than branding the tile.
          if (REF_CACHE[it.code] === undefined && (tries || 0) < 2) {
            setTimeout(function () { fillCard(it, card, im, cap, (tries || 0) + 1); },
                       1500 * ((tries || 0) + 1));
            return;
          }
          card.classList.add("nophoto");
          cap.querySelector(".gcredit").textContent = "no photo";
          return;
        }
        im.src = rec.url;
        // A stored thumbnail can go missing (they are pruned as species are
        // finalised) — fall back to a live lookup instead of a broken tile.
        im.onerror = function () {
          im.onerror = null;
          REF_CACHE[it.code] = undefined;
          if (S.photos) delete S.photos[it.code];
          refPhoto(it.code, function (r2) {
            if (r2 && r2.url) {
              im.src = r2.url;
              cap.querySelector(".gcredit").textContent = creditLine(r2);
              card.title = nm.common + "\nPhoto: " + fullCredit(r2);
            } else {
              card.classList.add("nophoto");
              cap.querySelector(".gcredit").textContent = "no photo";
            }
          });
        };
        card.dataset.origin = (rec.credit || "Reference photograph") +
          (rec.by ? " — " + rec.by : "");
        if (rec.page) card.dataset.page = rec.page;
        cap.querySelector(".gcredit").textContent = creditLine(rec);
        card.title = nm.common + (nm.sci ? " — " + nm.sci : "") +
          "\nPhoto: " + fullCredit(rec);
    });
  }

  function render() {
    stage.innerHTML = "";
    stage.classList.remove("grid");
    if (S.src === "photos") {
      if (!S.photos) { loadPhotos().then(render); return; }
      renderPhotoGrid();
      return;
    }
    var stance = S.mode === "A" ? "sitting" : "flying";
    var items = [];
    // Union of AI-manifest and plate codes: a plate-covered species may not
    // have an AI image yet, and vice versa.
    var codes = {};
    Object.keys(S.manifest).forEach(function (c) { codes[c] = 1; });
    if (S.src !== "ai") Object.keys(S.plates).forEach(function (c) { codes[c] = 1; });
    Object.keys(S.missing).forEach(function (c) { codes[c] = 1; });
    // Every other bird the model knows: it only reaches the page if it clears
    // the occurrence filter below, and then only as a placeholder.
    if (S.aves) S.aves.forEach(function (c) { codes[c] = 1; });
    Object.keys(codes).forEach(function (code) {
      var pick = chooseImage(code, stance) || placeholderFor(code);
      if (!pick) return;
      var mt = metrics(code);
      if (mt && mt.cur <= 0.01) return;   // only species with >1% occurrence here
      var value = !mt ? 0.5 : (S.mode === "A" ? mt.cur : Math.max(0, mt.arrival));
      if (S.mode === "B" && mt && mt.arrival <= 0) return; // only arriving species
      if (value <= 0) return;
      items.push({ code: code, img: pick.id, src: pick.src, flip: pick.flip,
        face: pick.face, origin: pick.origin, page: pick.page, ai: pick.ai,
        missing: pick.missing, stance: stance, value: value });
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
    stage.classList.toggle("aibw", S.aiBW);   // grayscale AI images when chosen
    window.scrollTo(0, 0);
    buildUpTo(2 * window.innerHeight);   // first screenful (+ one ahead)
    setStatus(items.length);
  }

  // Incremental builder: birds are mounted only once the scroll reaches them.
  var SCROLL = { items: [], idx: 0, halfW: 0 };

  function buildBird(it) {
    var el = document.createElement("div");
    el.className = "bird" + (DOWNVOTED.has(it.img) ? " downvoted" : "") +
      (it.ai ? " ai" : "");
    el.style.left = it.x + "px"; el.style.top = it.y + "px";
    el.style.width = it.size + "px"; el.style.height = it.size + "px";

    if (it.missing) {                      // no image anywhere: draw a card
      el.className += " missing";
      var ph = document.createElement("div");
      ph.className = "ph";
      ph.title = "No image yet for " + nameFor(it.code).common;
      ph.innerHTML = '<span class="ph-glyph" aria-hidden="true">🪶</span>' +
        '<span class="ph-name"></span>' +
        '<button type="button" class="ph-add">+ add images</button>';
      ph.querySelector(".ph-name").textContent = nameFor(it.code).common;
      // The card opens the species like any other bird; only this button leaves
      // for the image tool, carrying the photo as the generation seed.
      ph.querySelector(".ph-add").onclick = function (e) {
        e.stopPropagation();
        openReview(it.code);
      };
      el.appendChild(ph);
      // Fill the card with a real photograph of the bird once it arrives.
      refPhoto(it.code, function (rec) {
        if (!rec || !el.isConnected) return;
        it.ref = rec;
        var pic = document.createElement("img");
        pic.className = "ph-photo";
        pic.loading = "lazy"; pic.decoding = "async";
        pic.referrerPolicy = "no-referrer";
        pic.alt = nameFor(it.code).common;
        pic.src = sizedPhoto(rec.url, "medium");   // a small card; save the bytes
        pic.onload = function () { ph.classList.add("has-photo"); };
        pic.onerror = function () { pic.remove(); };
        ph.insertBefore(pic, ph.firstChild);
        var cr = document.createElement("span");
        cr.className = "ph-credit";
        cr.textContent = (rec.license || "").toUpperCase() ||
          rec.credit || "iNaturalist";
        cr.title = "Photo: " + (rec.by || rec.credit || "iNaturalist") +
          (rec.license ? " (" + rec.license + ")" : "");
        ph.appendChild(cr);
      });
      el.addEventListener("mousemove", function (ev) { showTip(ev, it); });
      el.addEventListener("mouseleave", function () { tip.classList.remove("show"); });
      el.addEventListener("click", function () {
        tip.classList.remove("show"); openBird(it);
      });
      stage.appendChild(el);
      return;
    }

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
      '<button class="down" title="Poor">👎</button>' +
      (it.ai ? '<button class="rev" title="Improve this drawing (review)">✎</button>' : '');
    fb.querySelector(".up").onclick = function (e) { e.stopPropagation(); doVote(it, "up", fb); };
    fb.querySelector(".down").onclick = function (e) { e.stopPropagation(); doVote(it, "down", fb); };
    if (it.ai) {
      fb.querySelector(".rev").onclick = function (e) {
        e.stopPropagation();
        openReview(it.code);
      };
    }
    el.appendChild(fb);

    el.addEventListener("mousemove", function (ev) { showTip(ev, it); });
    el.addEventListener("mouseleave", function () { tip.classList.remove("show"); });
    el.title = nameFor(it.code).common;
    el.addEventListener("click", function () { tip.classList.remove("show"); openBird(it); });
    stage.appendChild(el);
  }

  // Mount every not-yet-built bird whose top edge is above yLimit.
  function buildUpTo(yLimit) {
    if (stage.classList.contains("grid")) return;   // the grid has no scatter to build
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
      b.onclick = async function () {
        document.querySelectorAll("#mode button").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on"); S.mode = b.getAttribute("data-mode");
        setStatus("…");
        await ensureProbs();   // Migration needs neighbouring weeks; compute if missing
        render();
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
    // AI images colour vs black & white — toggled live without a rebuild.
    document.querySelectorAll("#aimode button").forEach(function (b) {
      b.onclick = function () {
        document.querySelectorAll("#aimode button").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on");
        S.aiBW = b.getAttribute("data-aimode") === "bw";
        stage.classList.toggle("aibw", S.aiBW);
      };
    });
    // Only reflow when the WIDTH changes — height-only resizes (mobile address
    // bar showing/hiding while scrolling) must not reshuffle the layout.
    var lastW = window.innerWidth;
    window.addEventListener("resize", debounce(function () {
      if (window.innerWidth === lastW) return;
      lastW = window.innerWidth; render();
    }, 200));
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  function setTitle() {
    var t = TITLES[S.lang] || TITLES.en;
    document.getElementById("title").textContent = t;
    document.title = S.place ? (t + " — " + S.place) : t;
  }

  function setupHelp() {
    var modal = document.getElementById("help-modal");
    document.getElementById("help-btn").onclick = function () { modal.hidden = false; };
    document.getElementById("help-close").onclick = function () { modal.hidden = true; };
    modal.addEventListener("click", function (e) {
      if (e.target.id === "help-modal") modal.hidden = true;
    });
  }

  // Full-screen detail view for a clicked bird. The image cycles through the
  // available sources (Gould, Dresser, von Wright, AI) when clicked; a book source links
  // straight to the scanned page.
  var BIRD = { code: null, variants: [], idx: 0 };

  // ---- Identification text -------------------------------------------------
  // field_id.json carries a sourced description per species (Wikipedia, CC
  // BY-SA, cross-referenced between three language editions — see
  // scripts/fetch_field_id.py). It is shown in an editable box under the large
  // image; edits stay in this browser and can be exported as field_id_edits.json
  // for scripts/apply_field_id_edits.py to fold back into the dataset.
  // One photograph per species — the reference photos the generator picked
  // (mostly Macaulay Library), shown as a grid under the "Photos" source.
  var PHOTOS_URL = "photos.json";
  // code -> Macaulay asset id, resolved offline from the whoBIRD list
  // (scripts/build_ml_assets.py): a curated photo for 6,378 of the model's
  // birds, so a placeholder or a grid tile can show the bird itself.
  var ML_URL = "ml_assets.json";
  var ML_CDN = "https://cdn.download.ams.birds.cornell.edu/api/v2/asset/";
  var FIELDID_URL = "field_id.json";
  var DESC_KEY = "birdcal.desc.edits";
  var descEdits = {};
  try { descEdits = JSON.parse(localStorage.getItem(DESC_KEY) || "{}"); } catch (e) {}

  function saveDescEdits() {
    try { localStorage.setItem(DESC_KEY, JSON.stringify(descEdits)); } catch (e) {}
  }

  function descEntry(code) { return (S.fieldId && S.fieldId[code]) || null; }

  // ~600 kB, and only needed once a bird is opened — fetched on first use.
  var _fieldIdReq = null;
  function loadFieldId() {
    if (!_fieldIdReq) {
      _fieldIdReq = fetch(FIELDID_URL)
        .then(function (r) { return r.ok ? r.json() : {}; })
        .catch(function () { return {}; })
        .then(function (j) { S.fieldId = j; return j; });
    }
    return _fieldIdReq;
  }

  function descText(code) {
    var edit = descEdits[code];
    if (edit && typeof edit.text === "string") return edit.text;
    var e = descEntry(code);
    return e ? e.text : "";
  }

  function downloadDescEdits() {
    var out = {};
    Object.keys(descEdits).forEach(function (c) {
      out[c] = { text: descEdits[c].text, ts: descEdits[c].ts,
        base_revid: descEdits[c].base_revid || null, lang: descEdits[c].lang || "" };
    });
    var blob = new Blob([JSON.stringify(out, null, 1)], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "field_id_edits.json";
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  // Render the description box for the species in the open detail view.
  function showDescription(code) {
    var wrap = document.getElementById("bird-desc-wrap");
    var box = document.getElementById("bird-desc");
    var entry = descEntry(code);
    if (!entry && !descEdits[code]) { wrap.hidden = true; return; }
    wrap.hidden = false;
    box.value = descText(code);
    box.dataset.code = code;
    showDescriptionFooter(code);
  }

  // The status line and links below the box — redrawn on every edit, while the
  // textarea itself is left alone so the caret doesn't jump.
  function showDescriptionFooter(code) {
    var state = document.getElementById("bird-desc-state");
    var foot = document.getElementById("bird-desc-foot");
    var entry = descEntry(code);
    var edited = !!descEdits[code];
    state.textContent = edited ? "edited here" :
      (entry && entry.check === "conflict" ? "sources disagree on size" :
        (entry && entry.check === "ok" ? "cross-checked" : ""));
    // Field notes are line-based and long; size the box to its content (up to
    // 44% of the window) instead of leaving most of it hidden behind a scroll.
    var box2 = document.getElementById("bird-desc");
    box2.style.height = "auto";
    box2.style.height = Math.min(window.innerHeight * 0.44,
                                 box2.scrollHeight + 4) + "px";
    // ... and let the plate give up some height when there is a note to read.
    var card = document.querySelector(".bird-card");
    if (card) card.classList.toggle("has-notes", !!(entry && entry.notes));

    foot.textContent = "";
    var m = entry && entry.measures;
    if (m) {
      var bits = [];
      if (m.length) bits.push("length " + fmtRange(m.length, "cm"));
      if (m.wingspan) bits.push("wingspan " + fmtRange(m.wingspan, "cm"));
      if (m.mass) bits.push("weight " + fmtRange(m.mass, "g"));
      if (bits.length) foot.appendChild(document.createTextNode(bits.join(" · ")));
    }
    if (entry && entry.url) {
      var a = document.createElement("a");
      a.href = entry.url; a.target = "_blank"; a.rel = "noopener";
      // Field notes are compiled from every edition consulted; a plain
      // description quotes the one edition it came from.
      a.textContent = entry.notes
        ? "Compiled from Wikipedia (" + (entry.langs || ["en"]).join(", ") + "), CC BY-SA →"
        : "Source: Wikipedia (" + (entry.lang || "en") + "), CC BY-SA →";
      foot.appendChild(a);
    }
    if (edited) {
      var revert = document.createElement("button");
      revert.type = "button";
      revert.textContent = "Revert to source";
      revert.onclick = function () {
        delete descEdits[code]; saveDescEdits(); showDescription(code);
      };
      foot.appendChild(revert);
      var dl = document.createElement("button");
      dl.type = "button";
      dl.textContent = "Export all edits (" + Object.keys(descEdits).length + ")";
      dl.onclick = downloadDescEdits;
      foot.appendChild(dl);
    }
  }

  function fmtRange(r, unit) {
    var lo = Math.round(r[0] * 10) / 10, hi = Math.round(r[1] * 10) / 10;
    return (lo === hi ? lo : lo + "–" + hi) + " " + unit;
  }

  function birdVariants(code) {
    var out = [];
    var p = S.plates[code] || {};
    BOOKS.forEach(function (b) {
      var e = p[b];
      if (!e) return;
      out.push({
        label: BOOK_LABEL[b],
        src: e.img, page: e.page_url || null, ai: false,
        origin: (BOOK_INFO[b] || b) + (e.volume ? ", " + e.volume : "") +
          (e.multi ? " — plate shows several species" : ""),
      });
    });
    var m = S.manifest[code];
    if (m && m.stances) {
      var img = null;
      ["sitting", "flying"].forEach(function (s) {
        if (!img && m.stances[s] && m.stances[s].length) img = m.stances[s][0];
      });
      for (var k in m.stances) {
        if (!img && m.stances[k] && m.stances[k].length) img = m.stances[k][0];
      }
      if (img) {
        // The drawing is made from a reference photograph; when that photo is
        // openly licensed, its photographer is credited here.
        var cr = (m.credits || {})[img] || {};
        var from = cr.drawn_from;
        var origin = "AI drawing, from a reference photograph";
        var page = null;
        if (from && (from.author || from.source)) {
          origin = "AI drawing, after a photo by " + (from.author || from.source) +
            (from.license ? " (" + from.license + ")" : "") +
            (from.source && from.author ? " — " + from.source : "");
          page = from.page_url || null;
        }
        out.push({ label: "AI", src: "birds/" + img, page: page, ai: true,
          origin: origin });
      }
    }
    return out;
  }

  function showBirdVariant() {
    var nm = nameFor(BIRD.code);
    var v = BIRD.variants[BIRD.idx];
    var img = document.getElementById("bird-img");
    // A species with no image at all shows the placeholder panel instead.
    var noImage = !v.src;
    img.hidden = noImage;
    document.getElementById("bird-missing").hidden = !noImage;
    if (noImage) { img.removeAttribute("src"); } else { img.src = v.src; }
    img.alt = nm.common;
    img.style.filter = (v.ai && S.aiBW)
      ? "grayscale(1) sepia(.22) contrast(.62) brightness(1.12)" : "";
    img.style.cursor = BIRD.variants.length > 1 ? "pointer" : "default";
    document.getElementById("bird-name").textContent = nm.common;
    document.getElementById("bird-sci").textContent = nm.sci || "";
    showDescription(BIRD.code);

    var srcEl = document.getElementById("bird-src");
    srcEl.textContent = "Source: ";
    if (v.page) {
      var a = document.createElement("a");
      a.href = v.page; a.target = "_blank"; a.rel = "noopener";
      a.textContent = v.origin;
      srcEl.appendChild(a);
    } else {
      srcEl.appendChild(document.createTextNode(v.origin));
    }

    var extra = document.getElementById("bird-extra");
    extra.textContent = "";
    if (noImage) {
      var add = document.createElement("a");
      add.href = "#";
      add.className = "add-images";
      add.textContent = "Add images for this species →";
      add.onclick = function (e) { e.preventDefault(); openReview(BIRD.code); };
      extra.appendChild(add);
      extra.appendChild(document.createElement("br"));
    }
    if (BIRD.variants.length > 1) {
      var sources = BIRD.variants.map(function (x) { return x.label; }).join(" · ");
      extra.appendChild(document.createTextNode("Tap the image to switch source (" + sources + ")"));
      extra.appendChild(document.createElement("br"));
    }
    var mt = metrics(BIRD.code);
    if (mt) {
      var pct = Math.round(Math.max(0, Math.min(1, mt.cur)) * 100);
      extra.appendChild(document.createTextNode("Seen here this week: " + pct + "%"));
      extra.appendChild(document.createElement("br"));
    }
    var ml = document.createElement("a");
    ml.href = "https://search.macaulaylibrary.org/catalog?taxonCode=" +
      encodeURIComponent(BIRD.code) + "&mediaType=photo";
    ml.target = "_blank"; ml.rel = "noopener";
    ml.textContent = "Photos & sounds on Macaulay Library →";
    extra.appendChild(ml);
    // Link to this species' call recordings on xeno-canto.
    if (nm.sci) {
      extra.appendChild(document.createElement("br"));
      var xc = document.createElement("a");
      xc.href = "https://xeno-canto.org/species/" + nm.sci.trim().replace(/\s+/g, "-");
      xc.target = "_blank"; xc.rel = "noopener";
      xc.textContent = "Listen to its calls on xeno-canto →";
      extra.appendChild(xc);
    }
    // For AI drawings, deep-link to this species' card on the review page.
    if (v.ai) {
      extra.appendChild(document.createElement("br"));
      var rv = document.createElement("a");
      rv.href = "review.html#" + encodeURIComponent(BIRD.code);
      rv.target = "_blank"; rv.rel = "noopener";
      rv.textContent = "Improve this drawing (review) →";
      extra.appendChild(rv);
    }
  }

  function openBird(it) {
    BIRD.code = it.code;
    BIRD.variants = birdVariants(it.code);
    if (!BIRD.variants.length) {
      BIRD.variants = [{ label: "", src: it.src, page: it.page,
        origin: it.origin || "—", ai: !!it.ai }];
    }
    var at = -1;
    for (var i = 0; i < BIRD.variants.length; i++) {
      if (BIRD.variants[i].src === it.src) { at = i; break; }
    }
    BIRD.idx = at >= 0 ? at : 0;
    showBirdVariant();
    document.getElementById("bird-modal").hidden = false;
    if (!S.fieldId) {
      loadFieldId().then(function () {
        if (BIRD.code === it.code) showDescription(it.code);
      });
    }
  }

  function setupBirdModal() {
    var modal = document.getElementById("bird-modal");
    document.getElementById("bird-close").onclick = function () { modal.hidden = true; };
    document.getElementById("bird-img").addEventListener("click", function () {
      if (BIRD.variants.length > 1) {
        BIRD.idx = (BIRD.idx + 1) % BIRD.variants.length;
        showBirdVariant();
      }
    });
    // Edits are kept per species in this browser; an unchanged box stores nothing.
    var box = document.getElementById("bird-desc");
    var pending = null;
    box.addEventListener("input", function () {
      var code = box.dataset.code;
      if (!code) return;
      clearTimeout(pending);
      pending = setTimeout(function () {
        var entry = descEntry(code);
        var text = box.value;
        if (entry && text.trim() === (entry.text || "").trim()) delete descEdits[code];
        else descEdits[code] = { text: text, ts: new Date().toISOString(),
          base_revid: entry ? entry.revid : null, lang: entry ? entry.lang : "" };
        saveDescEdits();
        showDescriptionFooter(code);
      }, 400);
    });
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
  // Run the geomodel only for the weeks not already computed at this location.
  async function ensureProbs() {
    var missing = neededWeeks().filter(function (w) { return !S.probs[w]; });
    if (!missing.length) return;
    var inputs = new Float32Array(missing.length * 3);
    missing.forEach(function (w, i) {
      inputs[i * 3] = S.lat; inputs[i * 3 + 1] = S.lon; inputs[i * 3 + 2] = w + 1;
    });
    var out = await runInference(inputs, missing.length);
    var n = S.nSpecies;
    missing.forEach(function (w, i) { S.probs[w] = out.subarray(i * n, (i + 1) * n); });
  }

  async function setLocation(lat, lon, name) {
    S.lat = lat; S.lon = lon;
    S.place = name || (lat.toFixed(2) + ", " + lon.toFixed(2));
    document.getElementById("place").textContent = "📍 " + S.place;
    setTitle();     // reflect the selected place in the page/tab title
    S.probs = {};   // location changed — previous weeks no longer valid
    setStatus("…");
    await ensureProbs();
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
        fetch(MISSING_URL).then(function (r) { return r.ok ? r.json() : {}; })
          .catch(function () { return {}; }),
        fetch(AVES_URL).then(function (r) { return r.ok ? r.text() : ""; })
          .catch(function () { return ""; }),
        initWorker(),
      ]);
      loadLabels(texts[0]);
      S.manifest = texts[1];
      S.plates = texts[2] || {};
      S.missing = texts[3] || {};
      S.aves = (texts[4] || "").split("\n")
        .map(function (c) { return c.trim(); }).filter(Boolean);
      S.avesSet = {};
      S.aves.forEach(function (c) { S.avesSet[c] = 1; });
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
