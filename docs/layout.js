/**
 * Bird Calendar — artistic radial layout.
 *
 * Scatters birds around a small empty centre (see layout.png). Design: medium /
 * balanced density (clear spacing, mild overlap), subtle data-true sizing (size
 * tracks the value directly), small centre. Mirrors scripts/preview_layout.py.
 *
 * window.BirdLayout.place(items, W, H) -> [{...item, x, y, size}]
 * Each item must have { value } (>0 weight). Size scales with value.
 */
window.BirdLayout = (function () {
  var TOP = 70;            // header reserve
  var INNER_X = 0.10, INNER_Y = 0.09;   // small empty-centre ellipse (frac of W,H)
  var MIN_FRAC = 0.06, MAX_FRAC = 0.15; // bird size range (frac of min side)
  var SPACING = 0.55;     // higher = more separation (medium density)
  var ATTEMPTS = 120;

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
      var size = minPx + (maxPx - minPx) * (it.value / maxV);  // subtle, data-true
      var best = null, bestPen = Infinity;
      for (var k = 0; k < ATTEMPTS; k++) {
        var x = size / 2 + Math.random() * (W - size);
        var y = TOP + size / 2 + Math.random() * (H - TOP - size);
        // reject the empty centre (scaled by size so bigger birds stay outside)
        var ex = (x - cx) / (ix + size * 0.5);
        var ey = (y - cy) / (iy + size * 0.5);
        if (ex * ex + ey * ey < 1) continue;
        var pen = 0;
        for (var p = 0; p < placed.length; p++) {
          var q = placed[p];
          var d = Math.hypot(x - q.x, y - q.y);
          var min = (size + q.size) * SPACING;
          if (d < min) pen += min - d;
        }
        if (pen < bestPen) { bestPen = pen; best = { x: x, y: y }; }
        if (pen === 0) break;
      }
      if (best) placed.push({ x: best.x, y: best.y, size: size, item: it });
    });
    return placed.map(function (p) {
      return Object.assign({}, p.item, { x: p.x, y: p.y, size: p.size });
    });
  }
  return { place: place };
})();
