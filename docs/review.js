/* AI Image Review — per species, pick the preferred image (Current live, the
 * Model input, or an AI alternative), set a single verdict (👍 Satisfied to
 * finalise / 👎 Not good enough to regenerate), flag a bad reference photo,
 * edit the field-marks prompt, and leave a free-text note.
 * Reads docs/review/manifest.json (written by scripts/regen_flagged.py).
 * State is stored in localStorage and exported as choices.json, which
 * scripts/apply_choices.py turns into the live images. */
(function () {
  var KEY = "birdReviewChoices";   // {code: variantId}
  var MKEY = "birdReviewMeta";     // {code: {badRef, verdict, idEdit, note}}
  var SKEY = "birdReviewScroll";   // last scroll position (px)
  var GKEY = "birdReviewGen";      // {code: generation stamp we hold state for}
  var RESKEY = "birdReviewShowRes"; // "1" while resolved cards are revealed
  // Deep-link target: the main page links a displayed bird to review.html#<code>
  // so the user lands on that species' card.
  var hashCode = "";
  try { hashCode = decodeURIComponent((location.hash || "").replace(/^#/, "")); } catch (e) {}
  var choices = {}, meta = {}, gens = {};
  try { choices = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
  try { meta = JSON.parse(localStorage.getItem(MKEY) || "{}"); } catch (e) {}
  try { gens = JSON.parse(localStorage.getItem(GKEY) || "{}"); } catch (e) {}
  var showResolved = localStorage.getItem(RESKEY) === "1";

  function save() { localStorage.setItem(KEY, JSON.stringify(choices)); updateProgress(); }
  function saveMeta() { localStorage.setItem(MKEY, JSON.stringify(meta)); updateProgress(); }
  function saveGens() { localStorage.setItem(GKEY, JSON.stringify(gens)); }
  function m(code) { return meta[code] || (meta[code] = {}); }

  // A species is "resolved" for this round once the reviewer sets a verdict —
  // 👍 Satisfied (finalize) or 👎 Not good enough (regenerate) — either way
  // there's nothing more to decide until new candidates arrive, so it drops off.
  function resolved(code) {
    var mm = meta[code];
    return !!(mm && mm.verdict);
  }

  // When an entry's generation stamp advances (the worker produced fresh
  // candidates), wipe any picks/toggles we held for it so the new round shows
  // a clean slate. Legacy entries (no gen) are left as-is.
  function reconcileGen(data) {
    var sp = data.species || {}, dirty = false;
    Object.keys(sp).forEach(function (code) {
      var g = sp[code].gen;
      if (g == null) return;
      if (gens[code] !== g) {
        if (Object.prototype.hasOwnProperty.call(choices, code)) delete choices[code];
        if (meta[code]) delete meta[code];
        gens[code] = g; dirty = true;
      }
    });
    if (dirty) {
      localStorage.setItem(KEY, JSON.stringify(choices));
      localStorage.setItem(MKEY, JSON.stringify(meta));
      saveGens();
    }
  }

  // Codes currently visible: current-recipe, not reviewed/pending, and (unless
  // "Show resolved" is on) not resolved. A deep-linked species always shows.
  // Species with a job in the generation queue (awaiting re-gen) are hidden
  // until the worker produces their new candidates.
  function queuedSet() {
    var data = window.__review || {};
    return (data.queued || []).reduce(function (acc, c) { acc[c] = 1; return acc; }, {});
  }

  function visibleCodes() {
    var data = window.__review || { species: {} };
    var q = queuedSet();
    return Object.keys(data.species || {}).filter(function (c) {
      var s = data.species[c];
      if (c === hashCode) return true;
      if (!((s.recipe || "").indexOf("v4") === 0 && !s.reviewed && !s.pending)) return false;
      if (q[c]) return false;                       // awaiting (re)generation
      if (resolved(c) && !showResolved) return false;
      return true;
    });
  }

  function resolvedCount() {
    var data = window.__review || { species: {} };
    var q = queuedSet();
    return Object.keys(data.species || {}).filter(function (c) {
      var s = data.species[c];
      if (!((s.recipe || "").indexOf("v4") === 0 && !s.reviewed && !s.pending)) return false;
      if (q[c]) return false;
      return resolved(c);
    }).length;
  }

  function refreshCounts() {
    var ce = document.getElementById("count");
    if (ce) ce.textContent = visibleCodes().length + " species";
    var rb = document.getElementById("showres");
    if (rb) {
      var n = resolvedCount();
      rb.hidden = n === 0;
      rb.textContent = (showResolved ? "Hide resolved" : "Show resolved") + " (" + n + ")";
      rb.classList.toggle("on", showResolved);
    }
  }

  // A species is "touched" once the user picks a variant or sets any flag/note/
  // prompt edit — i.e. has actually given feedback on it.
  function hasMeta(code) {
    var mm = meta[code];
    return !!(mm && (mm.badRef || mm.verdict || mm.note ||
                     typeof mm.idEdit === "string"));
  }
  function touched(code) {
    return Object.prototype.hasOwnProperty.call(choices, code) || hasMeta(code);
  }
  function updateProgress() {
    var data = window.__review || { species: {} };
    var n = Object.keys(data.species).filter(touched).length;
    var el = document.getElementById("progress");
    if (el) el.textContent = n ? "· " + n + " with feedback" : "";
  }

  // Remember the user's place on the long page so they can export partway and
  // resume later. Tile heights are fixed in CSS, so the layout is stable and
  // the saved offset lands on the same species after a reload.
  var scrollTimer;
  window.addEventListener("scroll", function () {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(function () {
      try { localStorage.setItem(SKEY, String(Math.round(window.scrollY))); } catch (e) {}
    }, 200);
  });
  function restoreScroll() {
    var y = parseInt(localStorage.getItem(SKEY) || "0", 10);
    if (y > 0) window.scrollTo(0, y);
  }
  // Scroll a deep-linked species into view and flash it.
  function focusSpecies(code) {
    var card = document.getElementById("sp-" + code);
    if (!card) return false;
    card.scrollIntoView({ block: "center" });
    card.classList.add("flash");
    setTimeout(function () { card.classList.remove("flash"); }, 2400);
    return true;
  }

  function tile(cls, img, label, sub, onclick) {
    var d = document.createElement("div");
    d.className = "tile " + cls;
    d.innerHTML = '<span class="pick">✓</span>' +
      '<img loading="lazy" src="' + img + '" alt="">' +
      '<div class="lab">' + label + "</div>" +
      (sub ? '<div class="scores">' + sub + "</div>" : "");
    if (onclick) d.onclick = onclick;
    return d;
  }

  function render(data) {
    var grid = document.getElementById("grid");
    // Drop stale picks/toggles for any entry that has been regenerated, so new
    // candidates show with nothing selected.
    reconcileGen(data);
    // Show current-recipe (v4) images that haven't been reviewed/are not pending
    // and aren't resolved (Satisfied / None good) — those drop off until fresh
    // candidates are generated (which clears the verdict and brings them back).
    var codes = visibleCodes();
    document.getElementById("empty").hidden = true;
    refreshCounts();
    if (!codes.length) { document.getElementById("empty").hidden = false; return; }
    grid.innerHTML = "";
    codes.sort(function (a, b) {
      return (data.species[a].name || a).localeCompare(data.species[b].name || b);
    });
    codes.forEach(function (code) {
      var s = data.species[code];
      var card = document.createElement("div");
      card.className = "card";
      card.id = "sp-" + code;            // deep-link anchor (review.html#<code>)

      // ---- header: name + a single verdict toggle --------------------
      var head = document.createElement("div");
      head.className = "head";
      head.innerHTML = '<span class="name">' + (s.name || code) + "</span>" +
        '<span class="sci">' + (s.sci || "") + "</span>" +
        (s.family ? '<span class="fam">' + s.family + "</span>" : "") +
        (s.reason ? '<span class="reason">' + s.reason + "</span>" : "");
      // Explicit binary verdict — the reviewer must say whether a generated
      // image is good or needs more iterations. Two buttons (placed in a bar at
      // the bottom of the card); exactly one can be active (click the active one
      // again to clear = no feedback).
      var goodBtn = document.createElement("button");
      goodBtn.className = "flag good";
      goodBtn.textContent = "👍 Good";
      goodBtn.title = "This image is good — finalise the picked image";
      var iterBtn = document.createElement("button");
      iterBtn.className = "flag iter";
      iterBtn.textContent = "🔁 More iterations";
      iterBtn.title = "Not good enough — keep the picked image and generate more";
      card.appendChild(head);

      var tiles = document.createElement("div");
      tiles.className = "tiles";

      // ---- reference photo (the real source, cropped to the model-input
      // square), carrying the "flag bad photo" toggle. Only shown when a real
      // photo is available — never the isolated model-input cutout.
      if (s.photo) {
        var refSub = (s.ref_source === "whobird") ? "© Macaulay Library" : "";
        var rt = tile("photo" + (m(code).badRef ? " badref" : ""), s.photo,
                      "Reference photo", refSub);
        var fb = document.createElement("button");
        fb.className = "reff" + (m(code).badRef ? " on" : "");
        fb.textContent = m(code).badRef ? "⚑ bad photo" : "⚐ flag photo";
        fb.title = "Flag this reference photo as bad (cropped, wrong species, etc.)";
        fb.onclick = function (e) {
          e.stopPropagation();
          m(code).badRef = !m(code).badRef; saveMeta();
          rt.classList.toggle("badref", m(code).badRef);
          fb.classList.toggle("on", m(code).badRef);
          fb.textContent = m(code).badRef ? "⚑ bad photo" : "⚐ flag photo";
        };
        rt.appendChild(fb);
        tiles.appendChild(rt);
      }
      // Separate the reference photo (context, not choosable) from the
      // choosable options that follow.
      if (s.photo) {
        var sep = document.createElement("div"); sep.className = "sep";
        tiles.appendChild(sep);
      }

      // Single-select across the choosable tiles — the Current (live) image,
      // the Model input (a regeneration seed only; never published), and the
      // generated alternatives. Click the chosen tile again to clear it.
      function choose(t, id) {
        t.classList.add("sel");
        t.dataset.id = id;
        if (choices[code] === id) t.classList.add("chosen");
        t.onclick = function () {
          if (choices[code] === id) {            // click the picked one again to unselect
            delete choices[code]; save(); t.classList.remove("chosen"); return;
          }
          choices[code] = id; save();
          tiles.querySelectorAll(".tile.sel").forEach(function (e) {
            e.classList.remove("chosen");
          });
          t.classList.add("chosen");
        };
      }

      if (s.before) { var lt = tile("before", s.before, "Current (live)", ""); choose(lt, "live"); tiles.appendChild(lt); }
      if (s.ref) { var it = tile("input", s.ref, "Model input", "regen seed"); choose(it, "input"); tiles.appendChild(it); }
      (s.variants || []).forEach(function (v) {
        var t = tile("var", v.img, v.id, "sim " + v.sim + " · pose " + v.pose);
        choose(t, v.id);
        tiles.appendChild(t);
      });
      card.appendChild(tiles);

      // Exactly one verdict can be active: "satisfied" (Good) or "notgood"
      // (More iterations). Neither active = no feedback for this species.
      function syncVerdict() {
        var v = m(code).verdict;
        goodBtn.classList.toggle("on", v === "satisfied");
        iterBtn.classList.toggle("on", v === "notgood");
      }
      // Setting a verdict resolves the card for this round, so it drops off the
      // list; "Show resolved" keeps it visible so a mis-click can be undone. New
      // candidates clear the verdict and bring it back clean.
      function afterVerdict() {
        if (resolved(code) && !showResolved) card.style.display = "none";
        else card.style.display = "";
        refreshCounts();
      }
      function setVerdict(v) {
        m(code).verdict = (m(code).verdict === v) ? undefined : v;
        if (!m(code).verdict) delete m(code).verdict;
        saveMeta(); syncVerdict(); afterVerdict();
      }
      goodBtn.onclick = function () { setVerdict("satisfied"); };
      iterBtn.onclick = function () { setVerdict("notgood"); };
      syncVerdict();

      // ---- editable species-specific prompt (ID field marks) ----------
      // This is the per-species clause fed to img2img ("emphasise these field
      // marks: …"). Editing it here and applying updates id_features.json so
      // the next regeneration uses the improved description.
      var origId = (s.id || "");
      var idLab = document.createElement("div");
      idLab.className = "idlab";
      idLab.textContent = "Prompt — field marks (editable):";
      card.appendChild(idLab);
      var idBox = document.createElement("textarea");
      idBox.className = "idtext";
      idBox.placeholder = "Visual field marks emphasised in the drawing…";
      idBox.value = (typeof m(code).idEdit === "string") ? m(code).idEdit : origId;
      idBox.oninput = function () {
        if (idBox.value.trim() !== origId.trim()) m(code).idEdit = idBox.value;
        else delete m(code).idEdit;
        idBox.classList.toggle("edited", idBox.value.trim() !== origId.trim());
        saveMeta();
      };
      idBox.classList.toggle("edited", idBox.value.trim() !== origId.trim());
      card.appendChild(idBox);

      // ---- free-text feedback box ------------------------------------
      var note = document.createElement("textarea");
      note.className = "note";
      note.placeholder = "Feedback for this species (optional)…";
      note.value = m(code).note || "";
      note.oninput = function () {
        var val = note.value.trim();
        if (val) m(code).note = val; else delete m(code).note;
        saveMeta();
      };
      card.appendChild(note);

      // ---- verdict bar at the bottom: 👍 Good | 🔁 More iterations ----
      var vbar = document.createElement("div");
      vbar.className = "verdict-bar";
      vbar.appendChild(goodBtn);
      vbar.appendChild(iterBtn);
      card.appendChild(vbar);

      grid.appendChild(card);
    });
  }

  var resBtn = document.getElementById("showres");
  if (resBtn) resBtn.onclick = function () {
    showResolved = !showResolved;
    localStorage.setItem(RESKEY, showResolved ? "1" : "0");
    render(window.__review || { species: {} });
  };

  var clearBtn = document.getElementById("clearsel");
  if (clearBtn) clearBtn.onclick = function () {
    var n = Object.keys(choices).length;
    if (!n) { alert("No image picks to clear."); return; }
    if (!confirm("Clear all " + n + " image picks? (Verdicts, flags and notes are kept.)")) return;
    choices = {}; save();
    render(window.__review || { species: {} });
  };

  document.getElementById("export").onclick = function () {
    // Per species: an object {choice?, verdict?, badRef?, id?, note?}, where
    // choice ∈ {"live","input","v0",…} and verdict ∈ {"satisfied","notgood"}.
    var data = window.__review || { species: {} };
    var out = {};
    // Only export species the user has actually given feedback on, so a partial
    // download (and the apply that follows) doesn't mark the whole list reviewed.
    var codes = Object.keys(data.species).filter(touched);
    if (!codes.length) {
      alert("No feedback to export yet — set a verdict, pick an image, or flag the photo first.");
      return;
    }
    codes.forEach(function (code) {
      var picked = choices[code] || null;   // "live" / "input" / "vN" / null
      var mm = meta[code] || {};
      var origId = (data.species[code].id || "").trim();
      var idEdited = typeof mm.idEdit === "string" && mm.idEdit.trim() !== origId;
      out[code] = {};
      if (picked) out[code].choice = picked;
      if (mm.verdict) out[code].verdict = mm.verdict;
      if (mm.badRef) out[code].badRef = true;
      if (idEdited) out[code].id = mm.idEdit.trim();
      if (mm.note) out[code].note = mm.note;
    });
    var blob = new Blob([JSON.stringify(out, null, 1)], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "choices.json";
    a.click();
  };

  fetch("review/manifest.json?_=" + Date.now())
    .then(function (r) { return r.json(); })
    .then(function (d) {
      window.__review = d; render(d); updateProgress();
      if (hashCode && d.species && d.species[hashCode]) {
        requestAnimationFrame(function () { focusSpecies(hashCode); });
      } else {
        if (hashCode) alert("That species hasn't been generated for review yet.");
        requestAnimationFrame(restoreScroll);
      }
    })
    .catch(function () { document.getElementById("empty").hidden = false; });
})();
