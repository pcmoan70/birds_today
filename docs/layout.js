/**
 * Bird Calendar — artistic radial layout.
 *
 * Places birds in a loose scatter around an empty centre (see layout.png).
 * Larger (higher-score) birds are placed first and tend to sit further out;
 * smaller ones fill the gaps. Light overlap is allowed for an organic feel.
 *
 * window.BirdLayout.place(items, W, H) -> [{...item, x, y, size}]
 * Each item must have { value } (0..1 relative weight). Size scales with value.
 */
window.BirdLayout = (function () {
  function place(items, W, H) {
    var minSide = Math.min(W, H);
    var inner = minSide * 0.16;                 // empty centre radius
    var outer = Math.min(W, H) * 0.52;          // band the birds live in
    var maxV = items.reduce(function (m, it) { return Math.max(m, it.value); }, 1e-6);
    var minPx = Math.max(48, minSide * 0.07);
    var maxPx = Math.max(minPx + 30, minSide * 0.20);

    // Largest first so big birds claim space, small ones fill around them.
    var sorted = items.slice().sort(function (a, b) { return b.value - a.value; });
    var placed = [];
    sorted.forEach(function (it, i) {
      var t = it.value / maxV;                  // 0..1
      var size = minPx + (maxPx - minPx) * Math.sqrt(t);
      var best = null, bestPen = Infinity;
      for (var attempt = 0; attempt < 60; attempt++) {
        // Spread angles using the golden angle + jitter; radius biased outward
        // for larger birds so the centre stays open.
        var ang = (i * 2.399963 + Math.random() * 1.2);
        var rad = inner + size * 0.5 + Math.random() * (outer - inner);
        var x = W / 2 + Math.cos(ang) * rad;
        var y = H / 2 + Math.sin(ang) * rad * 0.82; // slight vertical squash
        // keep on screen
        x = Math.max(size * 0.5, Math.min(W - size * 0.5, x));
        y = Math.max(size * 0.5 + 48, Math.min(H - size * 0.5, y));
        var pen = 0;
        for (var p = 0; p < placed.length; p++) {
          var q = placed[p];
          var d = Math.hypot(x - q.x, y - q.y);
          var min = (size + q.size) * 0.42;      // allow mild overlap
          if (d < min) pen += (min - d);
        }
        if (pen < bestPen) { bestPen = pen; best = { x: x, y: y }; }
        if (pen === 0) break;
      }
      placed.push({ x: best.x, y: best.y, size: size, item: it });
    });
    return placed.map(function (p) {
      return Object.assign({}, p.item, { x: p.x, y: p.y, size: p.size });
    });
  }
  return { place: place };
})();
