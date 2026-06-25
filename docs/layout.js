/**
 * Bird Calendar — artistic radial layout.
 *
 * Scatters birds around a small empty centre (see layout.png). Design: medium /
 * balanced density, subtle data-true sizing, small centre, and STRICT no overlap
 * — a bird shrinks a little if it can't fit, and is dropped if it still can't,
 * so birds never sit on top of each other. Highest-value species place first.
 * Mirrors scripts/preview_layout.py.
 *
 * window.BirdLayout.place(items, W, H) -> [{...item, x, y, size}] (subset that fits)
 * Each item must have { value } (>0 weight). Size scales with value.
 */
window.BirdLayout = (function () {
  var TOP = 70;            // header reserve
  var MIN_FRAC = 0.06, MAX_FRAC = 0.15; // bird size range (frac of min side)
  var GAP = 16;            // extra px between birds (room for the name caption)
  var SHRINK = [1, 0.85, 0.72];         // try full size, then a bit smaller
  var ATTEMPTS = 200;

  function place(items, W, H) {
    var minSide = Math.min(W, H);
    var cx = W / 2, cy = (H + TOP) / 2;
    var maxV = items.reduce(function (m, it) { return Math.max(m, it.value); }, 1e-6);
    var minPx = Math.max(46, minSide * MIN_FRAC);
    var maxPx = Math.max(90, minSide * MAX_FRAC);
    var XS = 1.15, YS = 0.82;             // stretch to fill widescreen

    // Highest-value species first, placed from the centre outward: the biggest
    // bird sits in the middle, smaller ones scatter around it (radius grows with
    // rank, sqrt for an even area fill, with angular + radial jitter).
    var sorted = items.slice().sort(function (a, b) { return b.value - a.value; });
    var n = sorted.length;
    var placed = [];
    sorted.forEach(function (it, i) {
      var base = minPx + (maxPx - minPx) * (it.value / maxV);  // subtle, data-true
      if (i === 0) { placed.push({ x: cx, y: cy, size: base, item: it }); return; }
      var spot = null;
      for (var s = 0; s < SHRINK.length && !spot; s++) {
        var size = base * SHRINK[s];
        var ring = Math.sqrt((i + 1) / n) * minSide * 0.62;
        for (var k = 0; k < ATTEMPTS; k++) {
          var rad = ring + (Math.random() - 0.5) * size * 1.4
            + (k / ATTEMPTS) * minSide * 0.30;   // creep outward if blocked
          var ang = Math.random() * Math.PI * 2;
          var x = cx + rad * Math.cos(ang) * XS;
          var y = cy + rad * Math.sin(ang) * YS;
          if (x - size / 2 < 0 || x + size / 2 > W ||
              y - size / 2 < TOP || y + size / 2 > H) continue;
          var ok = true;
          for (var p = 0; p < placed.length; p++) {
            var q = placed[p];
            if (Math.hypot(x - q.x, y - q.y) < (size + q.size) * 0.5 + GAP) { ok = false; break; }
          }
          if (ok) { spot = { x: x, y: y, size: size }; break; }
        }
      }
      if (spot) placed.push({ x: spot.x, y: spot.y, size: spot.size, item: it });
    });
    return placed.map(function (p) {
      return Object.assign({}, p.item, { x: p.x, y: p.y, size: p.size });
    });
  }

  // Residents (Mode A): most probable in the centre, spiralling outward
  // (phyllotaxis) as the value drops. Size ∝ value; strict no overlap.
  function placeSpiral(items, W, H) {
    var minSide = Math.min(W, H);
    var cx = W / 2, cy = (H + TOP) / 2;
    var maxV = items.reduce(function (m, it) { return Math.max(m, it.value); }, 1e-6);
    var minPx = Math.max(46, minSide * MIN_FRAC);
    var maxPx = Math.max(90, minSide * 0.16);
    var golden = Math.PI * (3 - Math.sqrt(5));   // ~2.39996 rad
    var C = (minPx + maxPx) * 0.32;              // spiral tightness
    var XS = 1.15, YS = 0.82;                    // stretch to fill widescreen

    var sorted = items.slice().sort(function (a, b) { return b.value - a.value; });
    var placed = [];
    sorted.forEach(function (it, i) {
      var size = minPx + (maxPx - minPx) * (it.value / maxV);
      if (i === 0) { placed.push({ x: cx, y: cy, size: size, item: it }); return; }
      var spot = null, k = i;
      while (k < i + 6000) {
        var ang = k * golden, rad = C * Math.sqrt(k);
        var x = cx + rad * Math.cos(ang) * XS;
        var y = cy + rad * Math.sin(ang) * YS;
        if (x - size / 2 >= 0 && x + size / 2 <= W &&
            y - size / 2 >= TOP && y + size / 2 <= H) {
          var ok = true;
          for (var p = 0; p < placed.length; p++) {
            var q = placed[p];
            if (Math.hypot(x - q.x, y - q.y) < (size + q.size) * 0.5 + GAP) { ok = false; break; }
          }
          if (ok) { spot = { x: x, y: y, size: size }; break; }
        }
        k++;
      }
      if (spot) placed.push({ x: spot.x, y: spot.y, size: spot.size, item: it });
    });
    return placed.map(function (p) {
      return Object.assign({}, p.item, { x: p.x, y: p.y, size: p.size });
    });
  }

  return { place: place, placeSpiral: placeSpiral };
})();
