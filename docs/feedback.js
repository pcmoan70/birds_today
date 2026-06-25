/**
 * Bird Calendar — client-side feedback (thumbs up/down) via EmailJS.
 *
 * Each vote is emailed to the maintainer's Gmail using EmailJS (no backend).
 * Template params sent: image_id, vote ("upvote"/"downvote"/"cleared"),
 * image_hash (SHA-256 of the image bytes), time, plus a machine-readable
 * "blob" line ("BIRDVOTE {json}") that the scheduled pipeline
 * (scripts/feedback_refresh.py) parses over IMAP to replace downvoted images.
 *
 * Suggested EmailJS template:
 *   Subject: Bird_calendar feedback: {{vote}}
 *   Body:
 *     image: {{image_id}}
 *     vote:  {{vote}}
 *     hash:  {{image_hash}}
 *     time:  {{time}}
 *
 *     {{blob}}
 *
 * Setup (see feedback/README.md):
 *   1. Add the EmailJS SDK before this script in index.html:
 *      <script src="https://cdn.jsdelivr.net/npm/@emailjs/browser@4/dist/email.min.js"></script>
 *   2. Fill PUBLIC_KEY / SERVICE_ID / TEMPLATE_ID from your EmailJS dashboard.
 *
 * Exposed as window.BirdFeedback.
 */
window.BirdFeedback = (function () {
  var PUBLIC_KEY = "-5S2PctOrxEViV5Pf";   // EmailJS → Account → General → API Keys
  var SERVICE_ID = "service_n19hwlq";     // EmailJS → Email Services
  var TEMPLATE_ID = "template_diffgq7";   // EmailJS → Email Templates

  function clientId() {
    var k = "bc_client", v = localStorage.getItem(k);
    if (!v) {
      v = Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem(k, v);
    }
    return v;
  }

  function myVotes() {
    try { return JSON.parse(localStorage.getItem("bc_votes") || "{}"); }
    catch (e) { return {}; }
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

  function send(params) {
    if (!(PUBLIC_KEY && SERVICE_ID && TEMPLATE_ID)) {
      console.warn("BirdFeedback: EmailJS keys not set"); return;
    }
    if (!window.emailjs) { console.warn("BirdFeedback: EmailJS SDK not loaded"); return; }
    emailjs.send(SERVICE_ID, TEMPLATE_ID, params, { publicKey: PUBLIC_KEY })
      .catch(function (e) { console.warn("BirdFeedback: send failed", e); });
  }

  // image: "species_code/pose_i.png"; dir: "up" | "down".
  // meta may include { url, species, pose, lang } — url is the image URL to hash
  // (defaults to "birds/<image>").
  function vote(image, dir, meta) {
    meta = meta || {};
    var s = myVotes();
    if (s[image] === dir) { delete s[image]; dir = "clear"; } // toggle off
    else s[image] = dir;
    localStorage.setItem("bc_votes", JSON.stringify(s));

    var label = dir === "up" ? "upvote" : dir === "down" ? "downvote" : "cleared";
    var time = new Date().toISOString();
    var url = meta.url || ("birds/" + image);

    imageHash(url).then(function (hash) {
      var blob = "BIRDVOTE " + JSON.stringify({
        image: image, vote: label, hash: hash,
        species: meta.species || "", pose: meta.pose || "",
        client: clientId(), ts: time,
      });
      send({
        image_id: image, vote: label, image_hash: hash, time: time,
        species: meta.species || "", pose: meta.pose || "",
        lang: meta.lang || "", client: clientId(), blob: blob,
      });
    });
  }

  function myVote(image) { return myVotes()[image] || null; }

  return { vote: vote, myVote: myVote };
})();
