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
