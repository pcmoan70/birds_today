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
  var GAP = 8;             // min horizontal gap between birds

  // How aggressively size tracks probability. Size = maxPx * (value/maxV)^POW;
  // POW > 1 makes probable birds clearly bigger and improbable ones much
  // smaller (POW = 1 is linear, POW = 0.5 was the old square-root that flattened
  // the range). Birds whose size falls below MIN_SHOW are too improbable to be
  // worth showing and are dropped entirely.
  var POW = 1.45;
  var MIN_SHOW = 36;       // px; smaller than this -> hidden

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
    var maxPx = Math.min(230, Math.max(120, W * 0.18));

    // Probability -> size, strongly. sorted is descending, so once a bird is
    // below MIN_SHOW every following one is too: stop there (hides the tail).
    var sized = [];
    for (var t = 0; t < sorted.length; t++) {
      var s = maxPx * Math.pow(sorted[t].value / maxV, POW);
      if (s < MIN_SHOW) break;
      sized.push({ it: sorted[t], s: Math.min(s, W * 0.92) });
    }

    var placed = [];
    var y = TOP;
    var i = 0;
    while (i < sized.length) {
      // Greedily fill a row (in probability order) until it would overflow W.
      var row = [], wsum = 0;
      while (i < sized.length) {
        var sz = sized[i].s;
        if (row.length && wsum + GAP + sz > W) break;
        wsum += (row.length ? GAP : 0) + sz;
        row.push(sized[i]);
        i++;
      }
      var rowH = row.reduce(function (m, r) { return Math.max(m, r.s); }, 0);

      // Scatter the birds: spread the leftover width as widely-varying random
      // gaps, shuffle their left-to-right order, and jitter each vertically so
      // the rows read as a random drift rather than a grid.
      var slots = row.length + 1;
      var weights = [], wtot = 0;
      for (var g = 0; g < slots; g++) { var r0 = 0.1 + 1.9 * rand(); weights.push(r0); wtot += r0; }
      var extra = Math.max(0, W - wsum);
      var order = row.slice();
      for (var k = order.length - 1; k > 0; k--) {
        var j = Math.floor(rand() * (k + 1)); var tmp = order[k]; order[k] = order[j]; order[j] = tmp;
      }

      var x = extra * weights[0] / wtot;
      order.forEach(function (r, idx) {
        var cx = x + r.s / 2;
        var jit = (rand() - 0.5) * Math.min(0.7 * (rowH - r.s) + 22, 32);
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
