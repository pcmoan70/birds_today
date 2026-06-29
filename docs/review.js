/* AI Image Review — pick a preferred AI variant per species, mark it
 * "satisfied" or "none good enough", flag a bad reference photo, and leave
 * a free-text note.
 * Reads docs/review/manifest.json (written by scripts/regen_flagged.py).
 * State is stored in localStorage and exported as choices.json, which
 * scripts/apply_choices.py turns into the live images. */
(function () {
  var KEY = "birdReviewChoices";   // {code: variantId}
  var MKEY = "birdReviewMeta";     // {code: {badRef, noneGood, satisfied, idEdit, note}}
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

  // A species is "resolved" for this round once the reviewer marks it Satisfied
  // (finalize) or None good enough (regenerate) — either way there's nothing
  // more to decide until new candidates arrive, so it drops off the list.
  function resolved(code) {
    var mm = meta[code];
    return !!(mm && (mm.satisfied || mm.noneGood));
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
    return !!(mm && (mm.badRef || mm.noneGood || mm.satisfied || mm.note ||
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
      var sel = choices[code] || null;   // nothing auto-selected; reviewer picks
      var card = document.createElement("div");
      card.className = "card";
      card.id = "sp-" + code;            // deep-link anchor (review.html#<code>)

      // ---- header: name + a "none good enough" toggle -----------------
      var head = document.createElement("div");
      head.className = "head";
      head.innerHTML = '<span class="name">' + (s.name || code) + "</span>" +
        '<span class="sci">' + (s.sci || "") + "</span>" +
        (s.family ? '<span class="fam">' + s.family + "</span>" : "") +
        (s.reason ? '<span class="reason">' + s.reason + "</span>" : "");
      var satBtn = document.createElement("button");
      satBtn.className = "flag sat" + (m(code).satisfied ? " on" : "");
      satBtn.title = "Mark this species as good — confirms you're happy with the chosen image";
      head.appendChild(satBtn);
      var noneBtn = document.createElement("button");
      noneBtn.className = "flag none" + (m(code).noneGood ? " on" : "");
      noneBtn.title = "Mark when no variant is acceptable — keeps the current live image";
      head.appendChild(noneBtn);
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
      // ---- model input (the isolated image actually fed to img2img) -------
      if (s.ref) tiles.appendChild(tile("input", s.ref, "Model input", ""));
      // ---- current live image --------------------------------------------
      if (s.before) tiles.appendChild(tile("before", s.before, "Current (live)", ""));

      var sep = document.createElement("div"); sep.className = "sep"; tiles.appendChild(sep);
      (s.variants || []).forEach(function (v) {
        var sub = "sim " + v.sim + " · pose " + v.pose;
        var t = tile(v.id === sel ? "var chosen" : "var", v.img, v.id, sub,
          function () {
            if (choices[code] === v.id) {        // click the picked one again to unselect
              delete choices[code]; save();
              t.classList.remove("chosen");
              return;
            }
            choices[code] = v.id; save();
            tiles.querySelectorAll(".tile.var").forEach(function (e) {
              e.classList.remove("chosen");
            });
            t.classList.add("chosen");
          });
        t.dataset.id = v.id;
        tiles.appendChild(t);
      });
      card.appendChild(tiles);

      // "Satisfied" and "None good enough" are opposite verdicts — only one
      // can be set. The variant row dims when nothing is acceptable.
      function syncVerdict() {
        var none = !!m(code).noneGood, sat = !!m(code).satisfied;
        tiles.classList.toggle("disabled", none);
        noneBtn.classList.toggle("on", none);
        noneBtn.textContent = none ? "None good enough ✓" : "None good enough";
        satBtn.classList.toggle("on", sat);
        satBtn.textContent = sat ? "👍 Satisfied ✓" : "👍 Satisfied";
      }
      // A resolved verdict (Satisfied / None good) drops the card off the list
      // for this round; "Show resolved" keeps it visible so a mis-click can be
      // undone. New candidates clear the verdict and bring it back clean.
      function afterVerdict() {
        if (resolved(code) && !showResolved) card.style.display = "none";
        else card.style.display = "";
        refreshCounts();
      }
      satBtn.onclick = function () {
        m(code).satisfied = !m(code).satisfied;
        if (m(code).satisfied) m(code).noneGood = false;
        saveMeta(); syncVerdict(); afterVerdict();
      };
      noneBtn.onclick = function () {
        m(code).noneGood = !m(code).noneGood;
        if (m(code).noneGood) m(code).satisfied = false;
        saveMeta(); syncVerdict(); afterVerdict();
      };
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
    if (!n) { alert("No variant selections to clear."); return; }
    if (!confirm("Clear all " + n + " variant selections? (Flags and notes are kept.)")) return;
    choices = {}; save();
    render(window.__review || { species: {} });
  };

  document.getElementById("export").onclick = function () {
    // Per species: plain variant id when nothing extra is flagged, else an
    // object {choice, badRef?, noneGood?, satisfied?, id?, note?}.
    // apply_choices.py reads both.
    var data = window.__review || { species: {} };
    var out = {};
    // Only export species the user has actually given feedback on, so a partial
    // download (and the apply that follows) doesn't mark the whole list reviewed.
    var codes = Object.keys(data.species).filter(touched);
    if (!codes.length) {
      alert("No feedback to export yet — pick a variant or set a flag first.");
      return;
    }
    codes.forEach(function (code) {
      var picked = choices[code] || null;   // only an explicit pick, no default
      var mm = meta[code] || {};
      var origId = (data.species[code].id || "").trim();
      var idEdited = typeof mm.idEdit === "string" && mm.idEdit.trim() !== origId;
      if (mm.badRef || mm.noneGood || mm.satisfied || mm.note || idEdited) {
        out[code] = {};
        if (picked) out[code].choice = picked;
        if (mm.badRef) out[code].badRef = true;
        if (mm.noneGood) out[code].noneGood = true;
        if (mm.satisfied) out[code].satisfied = true;
        if (idEdited) out[code].id = mm.idEdit.trim();
        if (mm.note) out[code].note = mm.note;
      } else {
        out[code] = picked;   // plain pick (variant id string)
      }
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
