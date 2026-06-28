/* AI Image Review — pick a preferred AI variant per species, mark it
 * "satisfied" or "none good enough", flag a bad reference photo, and leave
 * a free-text note.
 * Reads docs/review/manifest.json (written by scripts/regen_flagged.py).
 * State is stored in localStorage and exported as choices.json, which
 * scripts/apply_choices.py turns into the live images. */
(function () {
  var KEY = "birdReviewChoices";   // {code: variantId}
  var MKEY = "birdReviewMeta";     // {code: {badRef, noneGood, note}}
  var choices = {}, meta = {};
  try { choices = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
  try { meta = JSON.parse(localStorage.getItem(MKEY) || "{}"); } catch (e) {}

  function save() { localStorage.setItem(KEY, JSON.stringify(choices)); }
  function saveMeta() { localStorage.setItem(MKEY, JSON.stringify(meta)); }
  function m(code) { return meta[code] || (meta[code] = {}); }

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
    // Show current-recipe (v4) images that haven't been reviewed yet. A species
    // drops off once feedback is applied, and returns only when a new image is
    // generated for it (regeneration writes a fresh, unreviewed entry).
    var codes = Object.keys(data.species || {}).filter(function (c) {
      var s = data.species[c];
      return (s.recipe || "").indexOf("v4") === 0 && !s.reviewed;
    });
    document.getElementById("count").textContent = codes.length + " species";
    if (!codes.length) { document.getElementById("empty").hidden = false; return; }
    grid.innerHTML = "";
    codes.sort(function (a, b) {
      return (data.species[a].name || a).localeCompare(data.species[b].name || b);
    });
    codes.forEach(function (code) {
      var s = data.species[code];
      var sel = choices[code] || s.chosen || "v0";
      var card = document.createElement("div");
      card.className = "card";

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
      if (s.before) tiles.appendChild(tile("before", s.before, "Current (live)", ""));

      // ---- reference tile with a "flag bad photo" toggle --------------
      if (s.ref) {
        var refSub = (s.ref_source === "whobird") ? "© Macaulay Library" : "";
        var rt = tile("ref" + (m(code).badRef ? " badref" : ""), s.ref,
                      "Photo (reference)", refSub);
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

      var sep = document.createElement("div"); sep.className = "sep"; tiles.appendChild(sep);
      (s.variants || []).forEach(function (v) {
        var sub = "sim " + v.sim + " · pose " + v.pose;
        var t = tile(v.id === sel ? "var chosen" : "var", v.img,
          v.id + (v.id === (s.chosen || "v0") ? " (auto)" : ""), sub,
          function () {
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
      satBtn.onclick = function () {
        m(code).satisfied = !m(code).satisfied;
        if (m(code).satisfied) m(code).noneGood = false;
        saveMeta(); syncVerdict();
      };
      noneBtn.onclick = function () {
        m(code).noneGood = !m(code).noneGood;
        if (m(code).noneGood) m(code).satisfied = false;
        saveMeta(); syncVerdict();
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

  document.getElementById("export").onclick = function () {
    // Per species: plain variant id when nothing extra is flagged, else an
    // object {choice, badRef?, noneGood?, satisfied?, id?, note?}.
    // apply_choices.py reads both.
    var data = window.__review || { species: {} };
    var out = {};
    Object.keys(data.species).forEach(function (code) {
      var choice = choices[code] || data.species[code].chosen || "v0";
      var mm = meta[code] || {};
      var origId = (data.species[code].id || "").trim();
      var idEdited = typeof mm.idEdit === "string" && mm.idEdit.trim() !== origId;
      if (mm.badRef || mm.noneGood || mm.satisfied || mm.note || idEdited) {
        out[code] = { choice: choice };
        if (mm.badRef) out[code].badRef = true;
        if (mm.noneGood) out[code].noneGood = true;
        if (mm.satisfied) out[code].satisfied = true;
        if (idEdited) out[code].id = mm.idEdit.trim();
        if (mm.note) out[code].note = mm.note;
      } else {
        out[code] = choice;
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
    .then(function (d) { window.__review = d; render(d); })
    .catch(function () { document.getElementById("empty").hidden = false; });
})();
