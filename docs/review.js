/* AI Image Review — pick a preferred AI variant per species, flag a bad
 * reference photo or "none good enough", and leave a free-text note.
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
    var codes = Object.keys(data.species || {});
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
        var rt = tile("ref" + (m(code).badRef ? " badref" : ""), s.ref,
                      "Photo (reference)", "");
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

      // dim the variant row when "none good enough" is set
      function syncNone() {
        var on = !!m(code).noneGood;
        tiles.classList.toggle("disabled", on);
        noneBtn.classList.toggle("on", on);
        noneBtn.textContent = on ? "None good enough ✓" : "None good enough";
      }
      noneBtn.onclick = function () {
        m(code).noneGood = !m(code).noneGood; saveMeta(); syncNone();
      };
      syncNone();

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
    // object {choice, badRef?, noneGood?, note?}. apply_choices.py reads both.
    var data = window.__review || { species: {} };
    var out = {};
    Object.keys(data.species).forEach(function (code) {
      var choice = choices[code] || data.species[code].chosen || "v0";
      var mm = meta[code] || {};
      if (mm.badRef || mm.noneGood || mm.note) {
        out[code] = { choice: choice };
        if (mm.badRef) out[code].badRef = true;
        if (mm.noneGood) out[code].noneGood = true;
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
