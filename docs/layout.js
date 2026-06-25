/**
 * Bird Calendar — scrollable, probability-ordered packing.
 *
 * The page is a tall, scrollable canvas. Birds are laid out strictly by
 * probability: the most probable sit at the top, the least probable at the
 * bottom. To avoid a rigid grid/line look they are packed in masonry rows with
 * sizes scaled by probability, randomised horizontal gaps, shuffled positions
 * within each row, and a little vertical jitter — dense and organic, but the
 * top-to-bottom order always follows probability.
 *
 * window.BirdLayout.placeScroll(items, W)
 *   -> { placed: [{...item, x, y, size}], height }   (x,y = centre; height = px)
 */
window.BirdLayout = (function () {
  var TOP = 72;            // clear the fixed header
  var GAP = 8;             // min horizontal/vertical gap between birds

  // Deterministic PRNG so the same items + width always pack identically —
  // re-renders (e.g. a mobile address-bar resize) must not shuffle the birds.
  function rng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function placeScroll(items, W) {
    var sorted = items.slice().sort(function (a, b) { return b.value - a.value; });
    var rand = rng(0x9E3779B1 ^ items.length);
    var maxV = sorted.length ? Math.max(sorted[0].value, 1e-6) : 1e-6;
    var minPx = Math.max(58, W * 0.07);
    var maxPx = Math.min(190, Math.max(100, W * 0.15));

    var placed = [];
    var y = TOP;
    var i = 0;
    while (i < sorted.length) {
      // Greedily fill a row (in probability order) until it would overflow W.
      var row = [], wsum = 0;
      while (i < sorted.length) {
        var s = minPx + (maxPx - minPx) * Math.sqrt(sorted[i].value / maxV);
        s = Math.min(s, W * 0.92);
        if (row.length && wsum + GAP + s > W) break;
        wsum += (row.length ? GAP : 0) + s;
        row.push({ it: sorted[i], s: s });
        i++;
      }
      var rowH = row.reduce(function (m, r) { return Math.max(m, r.s); }, 0);

      // Spread the leftover width as random gaps before/between/after the birds,
      // and shuffle their left-to-right order so the row isn't a value ramp.
      var slots = row.length + 1;
      var weights = [], wtot = 0;
      for (var g = 0; g < slots; g++) { var r0 = 0.3 + rand(); weights.push(r0); wtot += r0; }
      var extra = Math.max(0, W - wsum);
      var order = row.slice();
      for (var k = order.length - 1; k > 0; k--) {
        var j = Math.floor(rand() * (k + 1)); var tmp = order[k]; order[k] = order[j]; order[j] = tmp;
      }

      var x = extra * weights[0] / wtot;
      order.forEach(function (r, idx) {
        var cx = x + r.s / 2;
        var jit = (rand() - 0.5) * Math.min(0.5 * (rowH - r.s) + 12, 22);
        var cy = y + rowH / 2 + jit;
        placed.push(Object.assign({}, r.it, { x: cx, y: cy, size: r.s }));
        x += r.s + extra * weights[idx + 1] / wtot;
      });
      y += rowH + GAP;
    }

    return { placed: placed, height: y + GAP };
  }

  return { placeScroll: placeScroll };
})();
