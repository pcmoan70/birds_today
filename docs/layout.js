/**
 * Bird Calendar — scrollable dense packing.
 *
 * The page is a tall, scrollable canvas filled top-to-bottom by probability:
 * the most probable species sit near the top (and a touch larger), tapering down
 * to the least probable. Birds are packed densely and organically — never in a
 * grid or rows — using a skyline packer with randomised candidate positions, so
 * each bird rests as high as it can on the current frontier with a little
 * overlap allowed (transparent edges keep them from visually colliding).
 *
 * window.BirdLayout.placeScroll(items, W)
 *   -> { placed: [{...item, x, y, size}], height }   (x,y = centre; height = px)
 */
window.BirdLayout = (function () {
  var TOP = 72;            // clear the fixed header
  var GAP = 3;             // vertical breathing room between stacked birds
  var OVERLAP = 0.90;      // skyline advance < size => slight overlap (denser)
  var TRIES = 22;          // random candidate positions per bird

  function placeScroll(items, W) {
    var sorted = items.slice().sort(function (a, b) { return b.value - a.value; });
    var maxV = sorted.length ? Math.max(sorted[0].value, 1e-6) : 1e-6;
    var minPx = Math.max(60, W * 0.07);
    var maxPx = Math.min(190, Math.max(104, W * 0.15));

    var bin = 6;                                  // skyline resolution (px)
    var nb = Math.max(1, Math.ceil(W / bin));
    var sky = new Array(nb).fill(TOP);            // current filled height per column

    function rangeMax(b0, b1) {
      var m = 0;
      for (var i = b0; i <= b1 && i < nb; i++) if (sky[i] > m) m = sky[i];
      return m;
    }

    var placed = [];
    sorted.forEach(function (it) {
      // size tapers with probability (sqrt = gentler), capped to the width
      var s = minPx + (maxPx - minPx) * Math.sqrt(it.value / maxV);
      s = Math.min(s, W * 0.92);
      var bw = Math.max(1, Math.round(s / bin));
      var maxB0 = Math.max(0, nb - bw);

      // try several random windows, keep the one that rests highest (densest)
      var best = null;
      for (var t = 0; t < TRIES; t++) {
        var b0 = maxB0 ? Math.floor(Math.random() * (maxB0 + 1)) : 0;
        var top = rangeMax(b0, b0 + bw - 1);
        if (best === null || top < best.top) best = { b0: b0, top: top };
      }

      var x = Math.max(s / 2, Math.min(W - s / 2, best.b0 * bin + s / 2));
      var y = best.top + s / 2;
      var adv = best.top + s * OVERLAP + GAP;
      for (var i = best.b0; i < best.b0 + bw && i < nb; i++) sky[i] = adv;
      placed.push(Object.assign({}, it, { x: x, y: y, size: s }));
    });

    var height = sky.reduce(function (m, v) { return Math.max(m, v); }, TOP) + GAP;
    return { placed: placed, height: height };
  }

  return { placeScroll: placeScroll };
})();
