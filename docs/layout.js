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
  var INNER_X = 0.10, INNER_Y = 0.09;   // small empty-centre ellipse (frac of W,H)
  var MIN_FRAC = 0.06, MAX_FRAC = 0.15; // bird size range (frac of min side)
  var GAP = 8;             // extra px between birds
  var SHRINK = [1, 0.85, 0.72];         // try full size, then a bit smaller
  var ATTEMPTS = 200;

  function place(items, W, H) {
    var minSide = Math.min(W, H);
    var cx = W / 2, cy = (H + TOP) / 2;
    var ix = W * INNER_X, iy = H * INNER_Y;
    var maxV = items.reduce(function (m, it) { return Math.max(m, it.value); }, 1e-6);
    var minPx = Math.max(46, minSide * MIN_FRAC);
    var maxPx = Math.max(90, minSide * MAX_FRAC);

    var sorted = items.slice().sort(function (a, b) { return b.value - a.value; });
    var placed = [];
    sorted.forEach(function (it) {
      var base = minPx + (maxPx - minPx) * (it.value / maxV);  // subtle, data-true
      var spot = null;
      for (var s = 0; s < SHRINK.length && !spot; s++) {
        var size = base * SHRINK[s];
        for (var k = 0; k < ATTEMPTS; k++) {
          var x = size / 2 + Math.random() * (W - size);
          var y = TOP + size / 2 + Math.random() * (H - TOP - size);
          var ex = (x - cx) / (ix + size * 0.5);
          var ey = (y - cy) / (iy + size * 0.5);
          if (ex * ex + ey * ey < 1) continue;   // keep the empty centre clear
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
  return { place: place };
})();
