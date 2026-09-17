# Bird Calendar — Implementation Plan

A location-aware, artistic bird calendar. Two modes:
- **Mode A — Residents/Presence:** AI occurrence probability → bird **size**, shown as **sitting** birds.
- **Mode B — Migration:** arrival score (increase/decrease in numbers) → bird **size**, shown as **flying** birds.

Engine reused from `..\migration_calendar` (BirdNET Geomodel, in-browser ONNX). New work: the
bird-image pipeline and the artistic front-end.

Decisions (confirmed): **auto-fetched** images, **two pose sets** (sitting + flying),
**image pipeline built first**, and the site is **non-commercial**.

Image sourcing (hybrid, all copyright-clean):
- **Primary — public-domain plates** (artistic, plain backgrounds → easy cutouts):
  von Wright *Svenska fåglar*, Naumann (Central Europe), Gould, Audubon, Fuertes — pulled via
  **Wikimedia Commons** (per-species categories) and **Biodiversity Heritage Library (BHL)**.
- **Fallback — photos:** **iNaturalist** research-grade, and since the site is non-commercial we
  can use **CC0 / CC-BY / CC-BY-NC** (`photo_license=cc0,cc-by,cc-by-nc`). **Flickr-CC** for the
  harder-to-find **flying** poses (free-text "in flight" tags).
- Drop Macaulay (© photographers). Keep attribution (source/photographer/license) in the manifest
  regardless. Note **CC-BY-SA** derivatives may need share-alike — prefer CC0/CC-BY/PD where possible.

---

## Pieces imported from migration_calendar (copy into `docs/`)
- [ ] `geomodel_fp16.onnx` — model weights (~7 MB)
- [ ] `inference-worker.js` — ONNX Runtime Web worker (use as-is)
- [ ] `vendor/ort/*` — vendored ONNX runtime (offline)
- [ ] `labels.txt` — output index → `species_code` / sci / common (12,012 rows)
- [ ] `taxonomy.csv` — multilingual names + `class_name` (filter to `aves` only)
- [ ] Extract the **minimal** migration math (NOT the whole analysis.js UI):
      - 48-week point prediction: build `Float32Array(48*3)` of `[lat,lon,week]`, one inference.
      - `arrivalAt(probs,w,maxYear) = (P[next]-P[prev]) / maxYear`, with `maxYear` = per-species annual peak.
  Note: model input is raw `[lat, lon, week(1-48)]`, batch×3; output batch×nSpecies. No normalization.

---

## Phase 1 — Image pipeline (Python) — FIRST DELIVERABLE
Output: `docs/birds/<species_code>/{s_,f_}*.png` transparent cutouts + `docs/birds/manifest.json`.

- [ ] `scripts/requirements.txt` — `requests`, `rembg`, `onnxruntime`, `Pillow`, `numpy`.
- [ ] `scripts/sources/` — one fetch adapter per source, common interface
      `fetch(species) -> [ {url, pose, source, author, license, src_id} ]`:
      - `wikimedia.py` — Commons API by species (sci name → category/search); PD plates first.
      - `bhl.py` — Biodiversity Heritage Library API / BHL Flickr feed (PD natural-history plates).
      - `inat.py` — iNaturalist API, `quality_grade=research`, `photo_license=cc0,cc-by,cc-by-nc`.
        Reuse the species↔taxon name-matching already in migration_calendar (`aggregate.js` logic).
      - `flickr.py` — Flickr CC search (license set + "in flight" tags) for the **flying** pose.
- [ ] `scripts/fetch_images.py` — orchestrator
      - For each `species_code`: try sources in priority order until N per pose collected
        (configurable, e.g. 6). Plates → sitting; flight plates / Flickr "in flight" → flying.
      - Save to `raw/<species_code>/` + sidecar JSON (source, author, license, src_id) for attribution.
      - Cache + polite rate-limiting; resume-safe (skip already-downloaded).
- [ ] `scripts/cutout.py`
      - `rembg` (u2net) → alpha; auto-crop to alpha bbox; trim, normalize longest edge; save PNG.
      - Drop low-quality cutouts (tiny alpha area / bird not isolated).
- [ ] `scripts/build_manifest.py`
      - Emit `manifest.json`: `{ species_code: { sci, common, sitting:[...], flying:[...], credits:{...} } }`.
      - Only include species with ≥1 usable cutout in the relevant pose.
- [ ] Run on a small species subset first to validate quality end-to-end before scaling.

## Phase 2 — Web app (static, GitHub Pages `docs/`)
- [ ] `docs/index.html` — mount point + ordered `<script>` tags + mode toggle (A/B).
- [ ] `docs/app.js`
      - Geolocation → `(lat,lon)`; current BirdNET week (1–48) from today's date.
      - Init worker, load labels+taxonomy, run the single 48-week prediction at the point.
      - Filter to birds (`class_name==aves`) AND to species present in `manifest.json`.
      - **Mode A:** rank by current-week probability; size ∝ probability; pick sitting cutouts.
      - **Mode B:** compute arrival score; keep increasing species; size ∝ arrival; pick flying cutouts.
      - Randomly cycle among the N images per species on each load.
- [ ] `docs/layout.js` — artistic placement engine
      - Radial scatter around an empty center (matches `layout.png`); weighted/Poisson-disc
        placement to limit heavy overlap; larger (higher-score) birds anchored, smaller filling gaps.
- [ ] `docs/style.css` — simple, stylistic, clean fonts; full-bleed canvas.
- [ ] **Hover species names + language setting** (requested): hovering a bird shows its name;
      a settings control picks the display language. taxonomy.csv already carries common names in
      ~30 languages (column per language) — map the selected language to its `common_name_xx`
      column, fall back to `com_name` (English). Reuse the language list from
      migration_calendar `i18n/strings.js` (15 UI languages) for the picker.

## Phase 3 — Polish & deploy
- [ ] Loading/empty states (no geolocation, no birds above threshold).
- [ ] Attribution surface (photographers / Macaulay / BirdNET licensing).
- [ ] `docs/.nojekyll`; GitHub Pages from `main` `/docs`.
- [ ] Update `README.md` with run/build instructions.

---

## Open risks / notes
- **Licensing:** site is non-commercial → CC0 / CC-BY / CC-BY-NC / PD all OK. Still store
  source + author + license per cutout and show attribution. Avoid CC-BY-SA where its share-alike
  clause is awkward. PD scans: prefer sources that explicitly assert PD/CC0 (Wikimedia, BHL, Smithsonian).
- **Coverage:** PD plates skew to N. America + Europe (great for a Nordic location calendar) and
  don't cover all species → iNaturalist photo fallback fills gaps; flag any species with no usable image.
- **Pose tagging:** flight poses are scarce in PD plates and untagged on iNat → rely on Flickr
  "in flight" tags + flight plates; flag species lacking a flying set rather than faking it.
- **Per-bird cropping:** many plates show 2+ birds / captions / branches → cutout needs a
  crop-or-segment-per-bird step; expect a manual review/cull pass on the first batch.
- **Model weights license:** `geomodel_fp16.onnx` is CC BY-SA 4.0 — preserve attribution.

## DIRECTION CHANGE (2026-06-25) — cartoon/illustrated style
Photo cutouts are mediocre and stylistically inconsistent. New direction: generate a
**consistent cartoon/illustrated style** per species that emphasises distinctive field marks.
Proposed pipeline: bird name → (Claude) field-mark description + reference images (reuse the
existing fetch pipeline) → local image generation (RTX 3090) with a shared style LoRA, grounded
on the reference via IP-Adapter/ControlNet → cutout/manifest as before. The feedback loop still
applies (downvote → regenerate with new seed/reference). See discussion for model options.
- [x] Model stack: **FLUX.1-dev (fp8/quanto)**; grounding: **reference-image (img2img)**;
      style: **Audubon plate**. Hardware confirmed: RTX 3090 24 GB, torch+CUDA, diffusers stack.
- [x] `scripts/generate.py` (FLUX img2img fp8, ref-grounded, Audubon prompt, optional --lora),
      `scripts/species_prompts.json` (field marks, 12 test species), `cutout.cut_pil()` shared.
- [~] First validation render in progress (FLUX.1-dev downloading ~24 GB, then gretit1+eurgol1).
- [ ] Judge plates; tune --strength/--guidance/--steps; consider training an Audubon style LoRA.
- [x] `scripts/select_species.py` — model-driven regional selection (peak prob across Swedish
      points). Sweden has ~250 regular species; `selected_species.txt` = top 100 most-likely.
- [~] Scale-up running: fetch refs + FLUX generate (num 1) + manifest for top 100 (~3h, resumable).
      `--codes-file` added to fetch_images/generate; generate prompt now works without field marks.
- [ ] Continue to full regional set (~250, threshold 0.2) after top-100 lands; deepen variants (num 2).
- [ ] `scripts/describe.py` (Claude → field marks at scale, for all species).
- [ ] Repoint feedback_refresh to regenerate (FLUX) instead of refetch.
- [ ] More reference sources (for grounding, esp. Nordic species + landing/takeoff poses):
      **artsdatabanken.no** (Norway; has media API, some CC-BY — verify license), and as a
      last resort **artportalen.se** (Sweden/SLU; licensing often restrictive). Add as fetch
      adapters alongside wikimedia/inat/flickr.

## Phase 2 web app (built — viewable page)
- `docs/index.html` (mode toggle A/B, language picker), `docs/style.css` (full-bleed, empty centre),
  `docs/layout.js` (radial scatter, size ∝ value), `docs/app.js` (geolocation → week → ONNX
  inference → Mode A probability / Mode B arrival → render; hover names w/ language; 👍/👎 feedback).
- Engine copied: `geomodel_fp16.onnx`, `inference-worker.js`, `vendor/ort/*`; `.nojekyll` added.
- Serve `docs/` (GitHub Pages /docs, or `python -m http.server`). All JS syntax-checked. ✅
- [ ] Reverse-geocode place name (currently shows lat/lon). [ ] richer layout vs layout.png.

## Feedback loop (done — generation-agnostic)
- **Channel = EmailJS → Gmail** (per request): `docs/feedback.js` sends each vote via EmailJS;
  email body carries a `BIRDVOTE {json}` line. Votes visible directly in the Gmail inbox.
- `scripts/feedback_refresh.py` reads **UNSEEN** vote emails over Gmail IMAP (idempotent),
  or a CSV for testing. Blocklist source id + replace that image — tested: downvote → different. ✅
- `scripts/rejects.py` + reject-aware `fetch_images.py`. `.github/workflows/refresh-images.yml`
  (weekly cron + manual; secrets GMAIL_USER / GMAIL_APP_PASSWORD). `.gitignore` added.
- `feedback/README.md` = EmailJS + Gmail IMAP app-password setup. (Old Apps Script `Code.gs` is
  superseded; can be deleted.)
- TODO: repoint refresh to **regenerate via FLUX** instead of refetch a photo (generation pivot).

## Review — Phase 1 first pass (2026-06-25)
Built & ran end-to-end on 12 Nordic species, 4/pose:
- `scripts/species.py` — loads 10,206 model birds (labels.txt ∩ taxonomy aves). Note: label keys
  are mixed eBird/GBIF codes, so adapters query by **scientific name**, not code.
- `scripts/sources/{base,wikimedia,inat}.py` + `fetch_images.py` — fetched 66 raw images.
- `scripts/cutout.py` (rembg/U2Net) — 59/66 cutouts kept; `scripts/build_manifest.py` — manifest OK.
- Engine data copied: `docs/labels.txt`, `docs/taxonomy.csv`.

Findings (see contact sheet):
- ✅ Cutout/alpha quality is good on clean single-bird photos; full sitting coverage.
- ⚠️ Wrong subjects slip in (tin can for crane, jars/bottles, human figures, multi-bird plates) —
  from loose iNat photos + broad Wikimedia search.
- ⚠️ Flying coverage thin (6/12); sitting-only: barswa, eurbla, eurgol1, eurrob1, eursta, houspa.

Next (precision + poses):
- [ ] iNat: resolve `taxon_id`, skip non-organism photos.
- [ ] Wikimedia: prefer category members; reject multi-subject/plate images by heuristic.
- [ ] Add Flickr-CC adapter for flying poses; more flight-search variants.
- [ ] Lightweight manual review/cull tool (browser grid keep/reject) for a truly clean final set.

## Iterative review loop + prioritized generation queue (2026-06-28)
Confirmed with user:
- **No auto-selected default** in review; choose deliberately.
- **No feedback** → keep current, nothing queued.
- **Pick one of three** (not Satisfied) → keep it as champion (live), queue the
  **other two** as fresh suggestions; species stays for another round.
- **Satisfied** → finalize (mark reviewed, drop off, no regen).
- **None good enough** → keep current, queue regeneration of all 3.
- **choices_5**: apply the new loop — every pick keeps that image + queues 2 new.
- **Continuous queue worker**, feedback jobs (priority 0) before coverage (10).

Tasks:
- [x] Review UI: remove auto-select default (no pre-highlight, drop "(auto)").
- [ ] `gen_queue.py`: load/save/enqueue/pop-highest-priority (FIFO within level).
- [ ] `gen_worker.py`: import regen_flagged internals; job kinds `challengers`
      (keep champion + regen N) and `coverage` (full best-of-3); push every N.
- [ ] `apply_choices.py`: new semantics (pick→keep+enqueue 2; satisfied→
      finalize; noneGood→enqueue 3; no-feedback→nothing); keep badRef/notes/id.
- [ ] Seed coverage backlog as priority-10 queue entries (replaces --all).
- [ ] Update `post_batch_apply.sh`: apply_choices (enqueues) → run worker.
- [ ] Gitignore `gen_queue.json`.

## Feet visibility (family-level feet descriptions)
- [x] Add `scripts/feet_features.json`: family (sci) -> characteristic legs/feet
      morphology (webbed ducks/gulls/loons; talons raptors/owls; long wading
      legs sandpipers/herons; zygodactyl woodpeckers/parrots/cuckoos; lobed
      toes grebes/coots; fine perching feet warblers/finches; `_default`).
- [x] `improved_prompt()` injects the family feet clause after id_features, so
      feet render plausibly even when the reference photo hides them.
- [x] Restart worker (kill uv child + launcher, relaunch) so the 82 queued
      feedback jobs regenerate with the feet clause. Draining continuously.
- [x] Also: review page can now unselect a pick (toggle) + "Clear picks" button.

Review: feet are now anchored per family in every prompt; per-species id_features
still layer on specifics. No CLIP feet-detection added (reliability uncertain) —
the description-based approach the user chose is deterministic and editable.

## Clean new entries + hide resolved cards
- [x] Write a generation stamp (`gen`) on every (re)generated review entry
      (gen_worker.py + regen_flagged.py). apply_choices edits in place, so it
      preserves `gen`.
- [x] review.js reconcileGen(): when an entry's `gen` advances, wipe stored
      picks/toggles for that code -> new candidates show clean. Legacy entries
      (no `gen`) left untouched (don't nuke in-progress feedback).
- [x] Resolved (Satisfied / None good) cards drop off the list immediately;
      return clean when new candidates arrive. "Show resolved (N)" bar toggle
      reveals them to undo. Picks stay visible (can still mark Satisfied).
- [x] Restarted worker so the remaining batch writes `gen` and uses the clause.

## von Wright — Svenska fåglar plates (Wikimedia Commons)
Source: Category:Svenska_fåglar_(von_Wright) — 411 files: 340 hi-res rawpixel
scans of the 1929 folio (tagged CC BY-SA 4.0; species in the description) and
71 PD files of the original lithographs, pre-cropped (binomial in filename).
- [x] `scripts/fetch_vonwright.py`: list category via API, parse species from
      description/filename (strip male/female/young markers -> `priority`),
      download 1200px renditions to `book_plates/vonwright/`, write `index.csv`
      in the Gould/Dresser schema (+ `priority`, `license`).
- [x] `match_plates.py`: generic BOOKS list; sci-only alias fallback for old
      binomials; local-file regen path (clean -> transparent -> strip caption
      footer -> autocrop -> face -> vignette); prefer adult/male plate.
- [x] `plate_aliases.json` / `plate_multi.json`: von Wright's 1920s names
      (Nyroca, Oidemia, Harelda, Colymbus, Cypselus...) and multi-species plates.
- [x] App: third "von Wright" source button, BOOK_INFO, detail-view variant,
      help text + attribution (CC BY-SA for the rawpixel scans).
- [x] Re-emit `docs/plates/`, verify coverage numbers and spot-check images.

Review: 410 of the 411 Commons files parsed (1 hybrid skipped); 248 app species
now have a von Wright plate (15 of them had no plate before) -> plate coverage
413 -> 428 of 522. Gould/Dresser manifest entries and PNGs untouched
(`--books vonwright`). Known limits: white birds (swans, Ivory/Iceland Gull)
go ghostly on dark grounds in all three books (paper->transparent); a caption
survives when it touches the bird (magpie). Re-run:
`python fetch_vonwright.py && python match_plates.py --emit --books vonwright`.

## Lilford — Coloured Figures of the Birds of the British Islands (Thorburn / Keulemans)
Source: Internet Archive, 7 volumes (colouredfigureso01lilf .. 07lilf), 1885-97.
- [x] Register `lilford` in `extract_book_plates.py` BOOKS; test-extract one
      volume, then run all 7 (plate detection + Tesseract caption OCR).
- [x] `match_plates.py`: add to BOOKS; aliases for 1890s names as needed.
- [x] App: "Lilford" source button, BOOK_INFO, help text.
- [x] Emit with `--books lilford`, verify coverage, spot-check plates.

Review: 470 plates extracted from IA (415 with OCR'd captions); 92 alias
entries for 1885-97 names; 325 app species get a Lilford plate (10 new) ->
plate coverage 428 -> 438 of 522. Existing books untouched (`--books lilford`).
`plate_skip.json` culls hand-rejected plates (one so far). Menu now fits five
source buttons (compact #src padding, 400px cap; wraps on phones).
WebP was measured against the 256-colour PNGs: no gain at 600px, kept PNG.

## Field-ID descriptions (Western Palearctic), sourced + cross-referenced
- [x] `scripts/fetch_field_id.py`: for each app species resolve the Wikidata
      item by scientific name; pull the Description/Identification section from
      en-Wikipedia and a second edition (de, else sv) + Wikidata measurements.
- [x] Cross-reference: taxon identity (article binomial == ours), and
      length / wingspan / mass extracted from each source must agree (overlap).
- [x] Store `scripts/field_id.json` (text verbatim, CC BY-SA attribution, rev
      ids, measurements, check status) + conflict report; run for all species.

Review: `scripts/field_id.json` (3.4 MB) holds the identification text for all
522 species from three independent Wikipedia editions (en 521, de 504, sv 519;
58 species fall back to lead paragraphs), each with article url + revision id,
CC BY-SA 4.0. Cross-check of body length / wingspan / mass parsed from each
edition: 357 ok, 62 conflict, 103 unverified (only one edition quotes a
figure). 5 taxon mismatches are eBird-vs-Wikidata renames (Thinornis dubius /
Charadrius dubius etc.), not wrong birds. Re-run checks offline with
`python fetch_field_id.py --recheck`; conflicts list with `--report`.
Parsing rules that mattered: cue words before the figure win over ones after;
a window never crosses a neighbouring figure; body-part words (wing chord,
Flügellänge, stjärt) disqualify a figure; imperial conversions are stripped.

## Field-ID text in the app + in the generation prompts
- [x] `build_field_id_web.py` -> `docs/field_id.json` (622 kB: best text per
      species + source url/revid + check status + agreed measurements).
- [x] Detail view: "How to identify it" textarea under the large image, with
      cross-check state, measurements, source link. Fetched lazily on first
      open. Edits are per-species in localStorage (`birdcal.desc.edits`),
      with Revert and "Export all edits" -> field_id_edits.json.
- [x] `apply_field_id_edits.py`: folds exported edits into `field_id.json` as
      `edited_text` (sources kept underneath), then rebuilds the web file and
      the distilled clauses. `--revert code,code` undoes one.
- [x] `distill_field_id.py` -> `id_features_sourced.json`: whole-sentence
      plumage clause (<=55 words) per species; a hand edit is used verbatim.
- [x] `regen_flagged.improved_prompt()` appends " Described in the literature
      as: ..." after the curated field marks; loader re-reads on mtime change
      so a running worker picks up edits.

Review: 515 clauses distilled, 0 with parse artefacts. Curated id_features.json
is untouched — the sourced clause is additive, so hand-tuned prompts still lead.
Trap hit: an earlier heredoc wrote a literal 0x08 byte where `` was intended,
so a filter silently never matched (see tasks/lessons.md).

## Professional field notes + confusion species (2026-08-22)
The stored descriptions are encyclopaedic Wikipedia prose. Rewrite them as
field notes in a consistent house style, and state the confusion species and
how to separate them.

Format per species (plain text, still editable in the app):
  Jizz — size/shape/structure, with the verified measurements.
  Plumage — adult key marks; sexes when dimorphic; juvenile when distinct.
  Bare parts — bill / legs / eye.
  In the field — flight, gait, habitat, behaviour worth using for ID.
  Similar species — one line per confusion species: what actually separates it.

- [x] `scripts/prep_field_notes.py`: confusion candidates per species (same
      genus among app species; species named near "similar/confused/
      distinguished" in the sources; same family within +-25% body length),
      capped at 4; write per-batch source packs for the drafting pass.
- [x] Draft the notes from the stored en/de/sv sources only (no outside
      knowledge), in parallel batches; strict JSON out.
- [x] Validate mechanically: schema, confusion codes exist, measurements match
      the cross-checked figures, no numbers absent from the sources.
- [x] Merge to `scripts/field_notes.json`, keep CC BY-SA attribution (derived
      work), rebuild `docs/field_id.json` + distilled prompt clauses.

Review: `scripts/field_notes.json` — all 522 species now carry field notes in a
fixed house style (Jizz / Plumage / Bare parts / In the field / Similar
species), written only from the stored en/de/sv sources. 503 species name
confusion species, 1827 separations in all; median note 1441 characters; 1127
gaps recorded per species in `unsupported` rather than filled from memory.
Every draft passed `merge_field_notes.py` (schema, candidate list, word limits,
and no number absent from the sources at any unit scale).
Two fixes the drafting surfaced: `prep_field_notes.sane()` drops mislabelled
measurements (wingspan stored as length), and Anatidae candidates now stay
inside their guild, so a sea duck is compared with scoters and eiders rather
than with geese (28 species re-drafted).
Notes feed both the app (docs/field_id.json, still editable) and the image
prompts (`distill_field_id.py` now takes the plumage + bare-parts lines: 522
clauses, all from notes).

## Placeholder for species with no image + request path (2026-08-25)
- [x] `scripts/build_missing.py` -> `docs/missing.json`: app species with
      neither a plate nor an AI cutout (today: Chukar), with localized names.
- [x] App: those species now appear as a dashed placeholder card (feather glyph,
      name, "+ add images"); the card opens the detail view (field notes still
      shown), the button opens the image tool.
- [x] The placeholder is keyed on `missing.json` alone — a species that merely
      lacks an image for the chosen source or stance has pictures elsewhere and
      is skipped as before. (First cut also let a plate-only species through.)
- [x] review.html: `?add=<code>&name=&sci=` shows a "Species with no images yet"
      panel with Request images / remove; requests live in localStorage and are
      included in the exported choices.json as `{"request": true}`.
- [x] `apply_choices.py`: a request queues a first-time `coverage` job at
      feedback priority; added `--dry-run` (writes nothing, no git) — the script
      commits and pushes docs/, which is a trap when testing.

Review: verified end to end in a headless browser — placeholder renders in the
layout, opens the species (notes intact), the button deep-links to the tool, the
request survives a reload, the export carries it, and a dry-run apply queues the
coverage job. Re-run `build_missing.py` after cutout/match_plates/apply_choices
so the placeholder disappears once images land.

## Photos source + placeholders everywhere (2026-08-25)
- [x] `build_aves.py` -> `docs/aves.txt`: labels.txt filtered to class aves
      (10,206 of 12,012). Any bird the model predicts here without an image now
      gets a placeholder, named from labels.txt — Cape Town 0 -> 152, Singapore
      186, Stockholm still 0.
- [x] Placeholders load a real photograph and hand it to the review tool as the
      generation seed. Macaulay assets when `missing.json` carries them (resolved
      offline), else a live iNaturalist lookup — Macaulay's search API allows
      neither cross-origin reads nor scripted clients (it answers with an
      anti-bot challenge), so it cannot be queried from the browser.
- [x] New **Photos** source: `build_photos.py` -> `docs/photos.json` (449
      species; 247 Macaulay CDN links, the rest thumbnails already published
      under docs/review_imgs/, 64 pruned ones skipped). `#stage.grid` renders an
      even gallery instead of the scatter, credited per tile, click opens the
      species. A tile whose stored thumbnail 404s re-looks-up live.

## Copyright split: CC photos on screen, Macaulay only as a seed (2026-08-25)
- [x] The app displays **openly licensed photos only**. `build_photos.py` keeps
      just the Wikimedia / iNaturalist / GBIF references (with licence and
      photographer); everything else is looked up live from iNaturalist's
      observations endpoint with an explicit `photo_license` filter, so an
      all-rights-reserved photo is never returned (a taxon's default photo can
      be one — that is why it isn't used).
- [x] Macaulay Library is never rendered: `build_ml_assets.py` writes
      docs/ml_assets.json (6,378 codes -> asset id) purely so "+ add images" can
      hand the asset to the local pipeline and the app can link to the
      catalogue. Verified in the browser: no request ever reaches Cornell.
- [x] "+ add images" is now one click — the app records the request (same
      localStorage the review tool reads) with the seed, opens the tool with the
      species already ticked, and the export carries `seed_asset`.
- [x] `apply_choices.py` `fetch_seeds()`: downloads the curated whoBIRD asset
      (plus a couple more from the Macaulay search when it is reachable) into
      scripts/ml_seeds/<code>/ (gitignored), pins the best as the img2img
      reference, and queues the coverage job — the normal generation/feedback
      flow.

**Outstanding — pre-existing:** `docs/review_imgs/*/ref.jpg` contains **178
Macaulay-sourced thumbnails that are committed and published**, though
scripts/sources/whobird.py states they must never be. They are not shown by the
app any more, but they are still in the repo and on Pages. Purging them (and
having the review page hotlink the CDN instead, as 247 species already do) is
the fix; it deletes tracked files, so it needs a decision.

## Photos grid tidy-up (2026-08-25)
- [x] Switching source starts the grid at the top (it kept the scatter's scroll
      position, so the grid opened mid-page).
- [x] One-line caption: "Great Tit" over "© Name · CC BY NC"; the full
      attribution moved to the tile's tooltip. Long iNaturalist attribution
      strings were wrapping to three lines and swamping the tile.
- [x] Photos are looked up only as a tile scrolls into view (IntersectionObserver,
      400px margin), paced to iNaturalist's ~1/sec guidance (2 in flight, 900ms
      apart). Asking for all 285-448 species at once was tripping the rate limit,
      which showed up as "no photo" on most tiles.
- [x] A failed or throttled lookup is retried (2x, backing off) instead of being
      cached as "no photo"; only a real empty answer marks the tile. Results are
      cached in localStorage, so a revisited grid fills instantly.
- [x] Kept the plain research-grade sample rather than `order_by=votes`: the
      most-faved photos are aberrant or arty (a leucistic Mallard, a feather
      macro, a murmuration) — the same bias scripts/sources/inat.py warns about.

## Redraw the stack from Commons photos, field-sketch style (2026-09-17)
- [x] Base photo: `_gather` puts Wikimedia Commons first and `best_ref` prefers
      the best Commons candidate that CLIP accepts as a real bird in a usable
      pose; iNaturalist / GBIF / the curated Macaulay shot remain the fallback,
      so coverage never suffers. Openly licensed base = publishable derivative.
- [x] Attribution travels: each downloaded candidate gets a sidecar with author,
      licence and source page; `setup_reference` carries it to the reference,
      `gen_best` writes it into the drawing's sidecar as `reference_credit`,
      `build_manifest` copies it into the manifest as `drawn_from`, and the app's
      detail view reads "AI drawing, after a photo by X (CC BY-SA) — wikimedia",
      linking to the source page.
- [x] Base prompt: new `fieldsketch` style (colour field sketch: pencil
      underdrawing, watercolour washes, sharp where ID lives, looser at the tail)
      plus two clauses used by every style — NO_BACKGROUND (leave out habitat,
      feeders, hands, wires; plain white, no cast shadow) and RESEMBLANCE (keep
      this bird's proportions, bill and head shape, posture, pattern, tones).
- [x] Prompts are assembled to a budget: T5 reads ~512 tokens and silently drops
      the rest, and the first draft ran to ~690. The sourced description is now
      trimmed a sentence at a time; style, species, field marks, likeness and the
      background rule always survive. Measured across 400 species: 1,669-1,850
      chars (~420-460 tokens).
- [x] Model is configuration: `BIRD_MODEL` (default `ostris/Flex.2-preview`,
      local paths fine), `BIRD_STYLE`, `BIRD_CLIP`. `check_model.py` reports the
      GPU, the configured checkpoint and what is cached, and `--load` proves it
      builds. No HuggingFace account or network is needed once weights are local.
- [x] `RECIPE` bumped to `v5-commons-fieldsketch`, so every existing image counts
      as stale; `queue_restyle.py` queues them through the ordinary queue, which
      means the existing review/feedback loop (pick a variant, more iterations,
      edit field marks, export, apply) drives the redraw. Dry run: 522 species to
      queue, 8 of them never drawn.

**Not verified here:** the generation itself. This sandbox is denied access to
the HuggingFace cache (`~/.cache/huggingface`), so CLIP could not load and no
image was drawn. `check_model.py --load` is the check to run on the box, and the
first few species out of `gen_worker.py` should be eyeballed before queueing all
522.

## Curated photo list, the open-licence twin of whoBIRD's (2026-09-17)
The Photos grid sampled research-grade observations at random, which is why it
showed distant birds on wires and cluttered feeders. whoBIRD works from one
editor-picked photo per species; iNaturalist has the same thing in the open —
`taxon_photos`, a community-curated ordered set per taxon.
- [x] `build_photos.py` rewritten: resolve the taxon by exact scientific name,
      take the first `taxon_photos` entry carrying a CC licence, keep url,
      licence, photographer and photo page. Paced at ~1 request/second,
      resumable, `--codes` / `--refresh` for single species.
- [x] The app's live fallback (species outside the curated set) now walks the
      same curated list instead of the observation sample.
- [x] Checked in the browser: every visible tile is a portrait of the species —
      Great Tit, Blue Tit, Hooded Crow, Chaffinch, White-tailed Eagle — with the
      photographer and licence on each tile.

Checked BirdsWhere (pcmoan70/BirdsWhere, b5a6a1b3) for a photo pipeline to copy:
it has none. No species photo is fetched, stored or displayed — 0 bundled jpgs,
no per-species asset mapping. It only *links out*: Macaulay's catalogue search
(`search.macaulaylibrary.org/catalog?taxonCode=...`), iNaturalist's taxon search,
Kew and ADW, all opened in a tab; the sole remote image is a Wikipedia range map
scraped per view. One idea worth borrowing: its Macaulay link narrows to +-1
month around the observation date so the plumage matches the season.

## Photos grid: strays and resolution (2026-09-17)
- [x] Bird cutouts appeared on top of the photos. The scatter's incremental
      builder keeps a pending layout in `SCROLL.items`; switching source cleared
      the DOM but not that list, so scrolling the grid mounted leftover birds
      into it. The grid now resets `SCROLL`, and `buildUpTo` returns early while
      the stage is in grid mode. Checked: 0 strays before and after scrolling.
- [x] Photos were soft. iNaturalist serves square/small/medium/large; the list
      stored `medium` (~500 px), which is blurry on a retina tile once cropped to
      a square. photos.json now holds the `large` rendition (~1024 px, 140-450 kB)
      — rewritten in place, no refetch — `build_photos.py` prefers `large_url`,
      and the small placeholder cards in the scatter downscale to `medium` so
      they cost no more than before. Tiles also widened 190px -> 230px.
