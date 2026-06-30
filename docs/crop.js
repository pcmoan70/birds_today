/* Photo Crop Tool — pick the best candidate photo per bad-photo species and
 * draw a crop box. Exports crop_choices.json {code: {cand, box:[x,y,w,h]} | {cand, full}}
 * which scripts/apply_crops.py turns into a pinned reference + regeneration. */
(function () {
  var KEY = "birdCropChoices";          // {code: {cand, picked, box, full}}
  var RKEY = "birdCropRejects";         // {photoUrl: true} — bad photos, by URL
  var sel = {}, rejects = {};
  try { sel = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) {}
  try { rejects = JSON.parse(localStorage.getItem(RKEY) || "{}"); } catch (e) {}
  function save() { localStorage.setItem(KEY, JSON.stringify(sel)); updateCount(); }
  function saveRej() { localStorage.setItem(RKEY, JSON.stringify(rejects)); }
  function rkey(c) { return c.url || c.img; }   // stable identity for a photo
  function st(code) { return sel[code] || (sel[code] = { cand: 0 }); }
  function isSet(code) { var x = sel[code]; return !!(x && x.picked); }

  function updateCount() {
    var n = Object.keys(sel).filter(isSet).length;
    var el = document.getElementById("count");
    if (el) el.textContent = n ? n + " set" : "";
    Object.keys(sel).forEach(function (code) {
      var card = document.getElementById("c-" + code);
      if (card) {
        card.classList.toggle("done", isSet(code));
        var s = card.querySelector(".state");
        if (s) s.textContent = isSet(code)
          ? (sel[code].full ? "✓ full image" : "✓ cropped") : "";
      }
    });
  }

  function drawStage(code, data, wrap) {
    var s = st(code);
    var cand = data.cands[s.cand] || data.cands[0];
    wrap.innerHTML = "";
    var stage = document.createElement("div"); stage.className = "stage";
    var img = document.createElement("img"); img.src = cand.img; img.draggable = false;
    stage.appendChild(img);
    var rect = document.createElement("div"); rect.className = "croprect";
    stage.appendChild(rect);
    function showRect() {
      if (s.full || !s.box) { rect.style.display = "none"; return; }
      rect.style.display = "block";
      rect.style.left = (s.box[0] * 100) + "%"; rect.style.top = (s.box[1] * 100) + "%";
      rect.style.width = (s.box[2] * 100) + "%"; rect.style.height = (s.box[3] * 100) + "%";
    }
    var drag = null;
    function pt(e) {
      var r = stage.getBoundingClientRect();
      return [Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)),
              Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))];
    }
    stage.addEventListener("pointerdown", function (e) {
      e.preventDefault(); drag = pt(e); s.full = false; s.picked = true;
      try { stage.setPointerCapture(e.pointerId); } catch (x) {}
    });
    stage.addEventListener("pointermove", function (e) {
      if (!drag) return;
      var p = pt(e);
      s.box = [Math.min(drag[0], p[0]), Math.min(drag[1], p[1]),
               Math.abs(p[0] - drag[0]), Math.abs(p[1] - drag[1])];
      showRect();
    });
    function end() {
      if (!drag) return;
      drag = null;
      if (s.box && (s.box[2] < 0.02 || s.box[3] < 0.02)) s.box = null;  // a click, not a drag
      save(); showRect();
    }
    stage.addEventListener("pointerup", end);
    stage.addEventListener("pointercancel", end);
    showRect();
    wrap.appendChild(stage);
    if (cand.author || cand.source) {
      var cr = document.createElement("div"); cr.className = "credit";
      cr.textContent = (cand.source || "") + (cand.author ? " · © " + cand.author : "");
      wrap.appendChild(cr);
    }
  }

  function render(d) {
    var grid = document.getElementById("grid");
    var codes = Object.keys(d.species || {});
    if (!codes.length) { document.getElementById("empty").hidden = false; return; }
    codes.sort(function (a, b) {
      return (d.species[a].name || a).localeCompare(d.species[b].name || b);
    });
    codes.forEach(function (code) {
      var data = d.species[code];
      var s = st(code);
      var card = document.createElement("div");
      card.className = "card"; card.id = "c-" + code;
      var head = document.createElement("div"); head.className = "head";
      head.innerHTML = '<span class="name">' + (data.name || code) + "</span>" +
        '<span class="sci">' + (data.sci || "") + "</span>" +
        '<span class="state"></span>';
      card.appendChild(head);

      var cols = document.createElement("div"); cols.className = "cols";
      var thumbs = document.createElement("div"); thumbs.className = "thumbs";
      var wrap = document.createElement("div"); wrap.className = "stagewrap";
      (data.cands || []).forEach(function (c, i) {
        var k = rkey(c);
        var w = document.createElement("div"); w.className = "thumb";
        if (rejects[k]) w.classList.add("rejected");
        var t = document.createElement("img");
        t.src = c.img; t.loading = "lazy";
        if (i === s.cand && s.picked && !rejects[k]) t.classList.add("sel");
        t.onclick = function () {
          if (rejects[k]) return;            // a rejected photo can't be chosen
          s.cand = i; s.picked = true; s.box = null; s.full = false; save();
          thumbs.querySelectorAll("img").forEach(function (e) { e.classList.remove("sel"); });
          t.classList.add("sel");
          drawStage(code, data, wrap);
        };
        var rej = document.createElement("button");
        rej.className = "rejbtn"; rej.textContent = "🚫";
        rej.title = "Mark this photo bad — it won't be shown again; new photos load next time";
        rej.onclick = function (e) {
          e.stopPropagation();
          if (rejects[k]) { delete rejects[k]; }
          else {
            rejects[k] = true;
            if (s.cand === i && s.picked) { s.picked = false; save(); }  // un-pick if it was chosen
          }
          saveRej();
          w.classList.toggle("rejected", !!rejects[k]);
          t.classList.remove("sel");
          drawStage(code, data, wrap);
        };
        w.appendChild(t); w.appendChild(rej);
        thumbs.appendChild(w);
      });
      cols.appendChild(thumbs);
      cols.appendChild(wrap);
      card.appendChild(cols);

      var tools = document.createElement("div"); tools.className = "tools";
      var full = document.createElement("button"); full.textContent = "Use full image";
      full.onclick = function () { s.picked = true; s.full = true; s.box = null; save(); drawStage(code, data, wrap); };
      var clear = document.createElement("button"); clear.textContent = "Clear crop";
      clear.onclick = function () { s.box = null; s.full = false; save(); drawStage(code, data, wrap); };
      var hint = document.createElement("span"); hint.className = "hint";
      hint.textContent = "Drag on the large image to crop.";
      tools.appendChild(full); tools.appendChild(clear); tools.appendChild(hint);
      card.appendChild(tools);

      grid.appendChild(card);
      drawStage(code, data, wrap);
    });
    updateCount();
  }

  document.getElementById("export").onclick = function () {
    var d = window.__crop || { species: {} };
    var out = {};
    Object.keys(sel).forEach(function (code) {
      var s = sel[code];
      if (!s || !s.picked) return;
      var c = ((d.species[code] || {}).cands || [])[s.cand] || {};
      // Identify the chosen photo (source/link/id + hosted copy) and the crop
      // region — instructions only, never the cropped pixels.
      var e = {
        cand: s.cand,
        img: c.img || ("crop/" + code + "/cand" + s.cand + ".jpg"),
        source: c.source || "", src_id: c.src_id || "",
        url: c.url || "", page_url: c.page_url || "", author: c.author || ""
      };
      if (s.full || !s.box) e.full = true;
      else e.box = s.box.map(function (n) { return Math.round(n * 1000) / 1000; });
      out[code] = e;
    });
    // Bad photos (by URL) — recorded so they're never shown again and fresh
    // candidates are fetched next prep. Reserved key (no species code starts "_").
    var bad = Object.keys(rejects);
    if (bad.length) out._rejected = bad;
    if (!Object.keys(out).length) { alert("Nothing to export yet — pick a photo, crop it, or mark a bad one first."); return; }
    var blob = new Blob([JSON.stringify(out, null, 1)], { type: "application/json" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "crop_choices.json"; a.click();
  };

  fetch("crop/manifest.json?_=" + Date.now())
    .then(function (r) { return r.json(); })
    .then(function (d) { window.__crop = d; render(d); })
    .catch(function () { document.getElementById("empty").hidden = false; });
})();
