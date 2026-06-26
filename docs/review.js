/* AI Image Review — pick a preferred AI variant per species.
 * Reads docs/review/manifest.json (written by scripts/regen_flagged.py).
 * Selections are stored in localStorage and exported as choices.json, which
 * scripts/apply_choices.py turns into the live images. */
(function () {
  var KEY = "birdReviewChoices";
  var choices = {};
  try { choices = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}

  function save() { localStorage.setItem(KEY, JSON.stringify(choices)); }

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
      var head = '<div class="head"><span class="name">' + (s.name || code) +
        '</span><span class="sci">' + (s.sci || "") + "</span>" +
        (s.family ? '<span class="fam">' + s.family + "</span>" : "") +
        (s.reason ? '<span class="reason">' + s.reason + "</span>" : "") + "</div>";
      card.innerHTML = head;
      var tiles = document.createElement("div");
      tiles.className = "tiles";
      if (s.before) tiles.appendChild(tile("before", s.before, "Current (live)", ""));
      if (s.ref) tiles.appendChild(tile("ref", s.ref, "Photo (reference)", ""));
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
      grid.appendChild(card);
    });
  }

  document.getElementById("export").onclick = function () {
    // Export every species' effective choice (explicit pick or the auto v0).
    var data = window.__review || { species: {} };
    var out = {};
    Object.keys(data.species).forEach(function (code) {
      out[code] = choices[code] || data.species[code].chosen || "v0";
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
