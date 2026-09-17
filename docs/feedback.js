/**
 * Bird Calendar — client-side feedback (thumbs up/down), filed into Google Drive.
 *
 * Each vote is POSTed to a Google Apps Script web app (ENDPOINT) which writes it
 * as one small JSON file into a Drive folder the maintainer owns. The scheduled
 * pipeline (scripts/feedback_refresh.py) reads that folder and replaces
 * downvoted images. No backend, no API key in the page, and nothing for the
 * visitor to sign into — the script runs as its owner.
 *
 * The payload is the same object the old EmailJS "BIRDVOTE {json}" line carried,
 * so the pipeline's tallying is unchanged:
 *   { image, vote, hash, species, sci, common, pose, lang, src, client, ts }
 *
 * Voting is not sticky: every click sends a fresh vote, including repeated
 * clicks of the same direction.
 *
 * Delivery: Apps Script web apps don't answer CORS preflight, so the POST is
 * sent as a simple no-cors request — the browser will not let us read the
 * response, so a vote the server rejects looks the same as one it accepted.
 * What we can detect is the network failing outright (offline, DNS, blocked),
 * and those votes stay in a localStorage outbox and are retried on the next
 * page load, so a vote cast on a train is not lost.
 *
 * Setup (see feedback/README.md):
 *   1. Deploy feedback/appsscript/Code.gs as a web app (execute as you, access
 *      "anyone"), which creates the Drive folder on first use.
 *   2. Paste the /exec URL into ENDPOINT below.
 *
 * Exposed as window.BirdFeedback.
 */
window.BirdFeedback = (function () {
  // Apps Script web-app URL, ".../exec" (feedback/appsscript/Code.gs).
  var ENDPOINT = "";
  var OUTBOX = "bc_outbox";       // votes the network refused, to retry
  var OUTBOX_MAX = 200;           // don't grow without bound on a dead endpoint

  function clientId() {
    var k = "bc_client", v = localStorage.getItem(k);
    if (!v) {
      v = Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem(k, v);
    }
    return v;
  }

  // SHA-256 of the image bytes (hex). Lets the pipeline confirm the rated image
  // is still the current one. Resolves to "" if the image can't be fetched.
  function imageHash(url) {
    if (!(window.crypto && crypto.subtle && url)) return Promise.resolve("");
    return fetch(url).then(function (r) { return r.arrayBuffer(); })
      .then(function (buf) { return crypto.subtle.digest("SHA-256", buf); })
      .then(function (h) {
        return Array.from(new Uint8Array(h))
          .map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
      })
      .catch(function () { return ""; });
  }

  function outbox() {
    try { return JSON.parse(localStorage.getItem(OUTBOX) || "[]"); }
    catch (e) { return []; }
  }

  function saveOutbox(list) {
    try { localStorage.setItem(OUTBOX, JSON.stringify(list.slice(-OUTBOX_MAX))); }
    catch (e) {}
  }

  function post(payload) {
    if (!ENDPOINT) {
      console.warn("BirdFeedback: ENDPOINT not set; vote kept locally");
      return Promise.reject(new Error("no endpoint"));
    }
    // text/plain keeps this a "simple" request, so the browser sends it without
    // a preflight Apps Script would not answer.
    return fetch(ENDPOINT, {
      method: "POST", mode: "no-cors", keepalive: true,
      headers: { "Content-Type": "text/plain;charset=utf-8" },
      body: JSON.stringify(payload),
    });
  }

  // Retry whatever the network refused earlier, oldest first. Each success
  // drops that vote from the outbox; the first failure stops the run so we
  // don't hammer a dead endpoint on every page load.
  function flush() {
    var pending = outbox();
    if (!pending.length || !ENDPOINT) return;
    var next = function () {
      if (!pending.length) { saveOutbox(pending); return; }
      var v = pending[0];
      post(v).then(function () {
        pending.shift();
        saveOutbox(pending);
        next();
      }).catch(function () { saveOutbox(pending); });
    };
    next();
  }

  // image: "species_code/pose_i.png"; dir: "up" | "down".
  // meta may include { url, species, sci, common, pose, lang, src } — url is the
  // image URL to hash (defaults to "birds/<image>"); src is the image source
  // ("gould"/"dresser"/"ai"). Voting is NOT sticky: every click sends a fresh
  // vote, including repeated clicks of the same dir.
  function vote(image, dir, meta) {
    meta = meta || {};
    var label = dir === "up" ? "upvote" : "downvote";
    var time = new Date().toISOString();
    var url = meta.url || ("birds/" + image);

    imageHash(url).then(function (hash) {
      var payload = {
        image: image, vote: label, hash: hash,
        species: meta.species || "", sci: meta.sci || "", common: meta.common || "",
        pose: meta.pose || "", lang: meta.lang || "", src: meta.src || "",
        client: clientId(), ts: time,
      };
      post(payload).catch(function (e) {
        console.warn("BirdFeedback: vote queued for retry", e);
        var q = outbox(); q.push(payload); saveOutbox(q);
      });
    });
  }

  flush();
  window.addEventListener("online", flush);

  return { vote: vote, flush: flush, pending: function () { return outbox().length; } };
})();
