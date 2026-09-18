"""Re-fetch better references and regenerate the QC-flagged species.

For each flagged species (from qc_out/qc_refs.csv):
  1. Pull several iNaturalist candidates, QC-score each (CLIP real-bird score,
     sharpness, subject size) and keep the best — replacing the bad reference
     (the old one is quarantined to raw_badrefs/).
  2. Regenerate the affected pose(s) with the improved recipe:
       - strength 0.65 (down from 0.85; keeps the real bird's shape/colour)
       - family-anchored, muted-colour prompt (families.json) to stop the
         English-name drift, e.g. "... a member of the family Phylloscopidae
         (Leaf Warblers) ... natural muted plumage colours, true to the photo".

Writes before/after copies to qc_out/ba/<code>_{before,after}.png for montaging.

Usage:
  python regen_flagged.py --limit 20            # first 20 flagged (object first)
  python regen_flagged.py --codes wlwwar,comchi1,wiltit1
  python regen_flagged.py --object-only
"""
import argparse
import csv
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402
from scipy.ndimage import laplace  # noqa: E402

import generate as G  # noqa: E402
import cutout as cut  # noqa: E402
import qc_references as QC  # noqa: E402 (reuse CLIP scorer)
from rembg import new_session, remove as rembg_remove  # noqa: E402
from species import load_species  # noqa: E402
from sources import inat, wikimedia, gbif, whobird  # noqa: E402
from sources.base import Candidate  # noqa: E402
from sources.base import SESSION  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(HERE, "raw")
BADREFS = os.path.join(HERE, "raw_badrefs")
PINNED = os.path.join(HERE, "pinned_refs")  # manually-curated <code>.jpg refs that
#   override auto-selection (for species with no good scraped/Macaulay photo).
OUT = os.path.join(ROOT, "docs", "birds")
QCDIR = os.path.join(HERE, "qc_out")
BA = os.path.join(QCDIR, "ba")
FAMILIES = os.path.join(HERE, "families.json")
IDFEATURES = os.path.join(HERE, "id_features.json")
# Field marks distilled from the sourced, cross-referenced descriptions
# (scripts/distill_field_id.py) — grounds the prompt on checked plumage wording.
IDSOURCED = os.path.join(HERE, "id_features_sourced.json")
FEETFEATURES = os.path.join(HERE, "feet_features.json")  # family -> legs/feet clause
RETRY = os.path.join(HERE, "retry_rounds.json")  # {code: round} — bumped when a
#   species is marked "none good enough" so its re-gen uses fresh seeds.
RECIPE = "v7-commons-i2i"   # openly licensed Commons base photo +
#   colour-field-sketch prompt. Bumping this marks every image made by the old
#   recipe as stale, so a regeneration pass rebuilds the stack.
PHOTOS = os.path.join(ROOT, "docs", "photos.json")        # the app's curated photo
REVIEW_IMGS = os.path.join(ROOT, "docs", "review_imgs")   # variant images (on Pages)
REVIEW_MAN = os.path.join(ROOT, "docs", "review", "manifest.json")
PUSH_EVERY = 5

# Macaulay Library (whoBIRD) is a courtesy resource, not a bulk API. Be gentle:
# cache every fetched asset on disk so we hit Cornell at most once per asset
# ever, and never request faster than ML_MIN_GAP seconds apart.
MLCACHE = os.path.join(HERE, "ml_cache")
ML_MIN_GAP = 3.0
_ml_last = [0.0]


def _fetch_candidate(c, dest):
    """Download a reference candidate to `dest`. whoBIRD/Macaulay assets are
    cached by id and rate-limited so we never hammer Cornell's CDN; other
    sources download normally. Returns True on success."""
    src = getattr(c, "source", "")
    if src == "whobird":
        os.makedirs(MLCACHE, exist_ok=True)
        cached = os.path.join(MLCACHE, f"{c.src_id}.jpg")
        if os.path.exists(cached) and os.path.getsize(cached) > 0:
            shutil.copy(cached, dest); return True       # served from cache, no network
        wait = ML_MIN_GAP - (time.time() - _ml_last[0])
        if wait > 0:
            time.sleep(wait)
        r = SESSION.get(c.url, timeout=30)
        _ml_last[0] = time.time()
        if r.status_code != 200 or not r.content:
            print(f"    whobird fetch {c.src_id}: HTTP {r.status_code}")
            return False
        open(cached, "wb").write(r.content)
        shutil.copy(cached, dest)
        return True
    r = SESSION.get(c.url, timeout=30)
    if r.status_code != 200 or not r.content:
        return False
    open(dest, "wb").write(r.content)
    return True


def load_families():
    return json.load(open(FAMILIES, encoding="utf-8")) if os.path.exists(FAMILIES) else {}


def load_id_features():
    return json.load(open(IDFEATURES, encoding="utf-8")) if os.path.exists(IDFEATURES) else {}


_SOURCED = [None, 0.0]     # cached (data, mtime)


def load_sourced_ids():
    """Distilled sourced field marks, re-read when the file changes so a
    running worker picks up edits applied from the app."""
    if not os.path.exists(IDSOURCED):
        return {}
    mtime = os.path.getmtime(IDSOURCED)
    if _SOURCED[0] is None or mtime != _SOURCED[1]:
        _SOURCED[0] = json.load(open(IDSOURCED, encoding="utf-8"))
        _SOURCED[1] = mtime
    return _SOURCED[0]


_FEET = [None]


def load_feet():
    """Family -> characteristic legs/feet description (cached). Lets a bird's
    family ground its feet morphology (webbed for ducks, talons for raptors,
    long wading legs for sandpipers, zygodactyl for woodpeckers/parrots, …) so
    the drawing renders plausible feet even when the reference hides them."""
    if _FEET[0] is None:
        _FEET[0] = (json.load(open(FEETFEATURES, encoding="utf-8"))
                    if os.path.exists(FEETFEATURES) else {})
    return _FEET[0]


def load_retry():
    return json.load(open(RETRY, encoding="utf-8")) if os.path.exists(RETRY) else {}


# Style used for the drawings. "fieldsketch" draws a colour field sketch from
# the (openly licensed) reference photo; "fieldguide" is the older plate look.
# scripts/style_switch.py swaps the published images between styles.
STYLE = os.environ.get("BIRD_STYLE", "fieldsketch")

# The reference photo is a real bird in a real place — a feeder, a hand, a
# fence, a lawn. prep_init already cuts the bird out onto white, but the model
# still reads the init image, so the prompt says plainly that none of the
# setting survives into the drawing.
NO_BACKGROUND = (
    " Draw the bird only: leave out everything around it in the photo — habitat, "
    "foliage, branches, grass, water, sky, rocks, snow, fences, wires, feeders, "
    "hands, rings, other birds — on plain white, no cast shadow, no backdrop; "
    "at most the barest neutral perch under its feet."
)

# img2img keeps the shape; this keeps the identity.
RESEMBLANCE = (
    " Keep the likeness of the bird in the reference photo: same proportions, "
    "bill and head shape, posture, plumage pattern and colour tones — a portrait "
    "of this species, not a generic or idealised bird."
)

# The T5 encoder reads about 512 tokens (~2,000 characters) and silently drops
# the rest, so the prompt is assembled in priority order and the long sourced
# description is trimmed — never the style, the species, the likeness or the
# background instruction.
PROMPT_BUDGET = 1900


def _trim_sentences(text, keep):
    out = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(out[:keep]).strip()


def improved_prompt(common, sci, code, stance, fams, ids, sourced=None, style=None):
    fam = fams.get(code) or [None, None]
    fam_clause = ""
    if fam[0]:
        en = f" ({fam[1]})" if fam[1] else ""
        fam_clause = f", a member of the family {fam[0]}{en}"
    id_text = (ids or {}).get(code, "").strip()
    # A second, sourced description of the plumage (cross-referenced between
    # Wikipedia editions, or hand-edited in the app). It follows the curated
    # clause so a hand-tuned prompt still leads.
    src_text = (sourced if sourced is not None else load_sourced_ids()).get(code, "").strip()
    # Family-level legs/feet morphology — anchors the feet even when the
    # reference photo hides them (bird on water, crouched, feet behind a perch).
    feet = load_feet()
    feet_text = (feet.get(fam[0]) if fam[0] else None) or feet.get("_default", "")
    feet_clause = f" Render the legs and feet accurately: {feet_text}." if feet_text else ""
    st = G.STYLES.get(style or STYLE) or G.STYLES["fieldguide"]
    head = (st["prompt"] + ". "
            f"A {common} ({sci}){fam_clause}, {G.STANCES[stance]['desc']}.")
    tail = (RESEMBLANCE + NO_BACKGROUND +
            " A typical wild adult in natural, muted colours, the whole bird in "
            "frame, uncropped, both legs and feet visible. " + G.ANATOMY + ".")

    def build(src_keep):
        mid = f" Identification — emphasise these field marks: {id_text}" if id_text else ""
        if src_text and src_keep:
            mid += (" Described in the literature as: "
                    + _trim_sentences(src_text, src_keep).rstrip(".") + ".")
        return head + mid + feet_clause + tail

    # Shed the sourced description a sentence at a time until it fits; the
    # curated field marks, the likeness and the background rule always survive.
    for keep in (4, 3, 2, 1, 0):
        out = build(keep)
        if len(out) <= PROMPT_BUDGET or keep == 0:
            return out
    return out


def sharp(im):
    g = np.asarray(im.convert("L").resize((256, 256)), np.float32)
    return float(laplace(g).var())


# Reject illustrations / field-guide plates / paintings: the reference MUST be
# a real photograph. (Macaulay/whoBIRD assets are always photos; Wikimedia and
# GBIF sometimes serve artwork.)
PHOTO_POS = "a real photograph of a bird taken with a camera"
PHOTO_NEG = "a painting, drawing, illustration, engraving or digital artwork of a bird"

POSE_GOOD = "a single whole bird perched, clear side profile view"
POSE_BAD = [
    "a bird flying with wings spread wide",
    "a bird seen from behind, rear/back view",
    "a close-up of just a bird's head",
    "a blurry or distant tiny bird",
    "two or more birds together",
]


_FAST = [None]


def _fast_session():
    """Light, fast matting model just for the candidate whole-bird checks
    (birefnet is far too slow to run on every candidate)."""
    if _FAST[0] is None:
        _FAST[0] = new_session("u2netp")
    return _FAST[0]


def _mask(im, sess, size=288):
    """rembg alpha mask of the candidate at small size (for whole-bird checks)."""
    s = im.convert("RGBA").copy()
    s.thumbnail((size, size))
    return np.array(rembg_remove(s, session=sess))[:, :, 3] > 40


def _wholeness(a):
    """(whole, subjfrac). whole≈1 when the bird doesn't touch the top/left/right
    edges (bottom is allowed for legs/perch); lower when it's cut off."""
    if a.sum() < 60:
        return 0.0, 0.0
    edge = max(a[0, :].mean(), a[:, 0].mean(), a[:, -1].mean())
    return max(0.0, 1.0 - edge / 0.12), float(a.mean())


def _whole4(a):
    """whole≈1 when the drawn bird touches none of the FOUR edges.

    _wholeness forgives the bottom, because a photographed bird often stands at
    the foot of the frame with its feet in the grass. A drawing is different:
    the model paints on white, so a mask reaching the bottom means the legs were
    cut off — a crane without its legs is as wrong as a magpie without its tail.
    """
    if a.sum() < 60:
        return 0.0
    edge = max(a[0, :].mean(), a[-1, :].mean(), a[:, 0].mean(), a[:, -1].mean())
    return max(0.0, 1.0 - edge / 0.12)


def curated_candidate(code):
    """The photo the app itself shows for this species, as a reference candidate.

    docs/photos.json holds one openly licensed Commons photo per species — the
    lead image of its Wikipedia article, an editor's pick. That is a better
    starting point than anything a keyword search turns up, so it goes into the
    pool first; best_ref still scores it, so a lead image that happens to be a
    poor drawing reference (a bird in flight, an odd angle) can still lose.
    """
    try:
        rec = json.load(open(PHOTOS, encoding="utf-8")).get(code)
    except Exception:                                           # noqa: BLE001
        return None
    if not rec or not rec.get("url"):
        return None
    return Candidate(url=rec["url"], pose="sitting", source="wikimedia",
                     license=rec.get("license", "") or "Commons",
                     author=rec.get("by", ""), src_id="curated",
                     page_url=rec.get("page", ""))


def _gather(sp, code, want):
    """Pool reference candidates across sources: Wikimedia Commons (openly
    licensed, and therefore the base image we prefer to draw from), iNaturalist,
    GBIF (which federates more iNat + Observation.org + naturgucker + Flickr),
    and whoBIRD's curated Macaulay pick as the fallback. Wikimedia is listed
    first so its candidates are always among the capped downloads; best_ref
    still scores everything."""
    from itertools import zip_longest
    lists = []
    for name, fn in (("wiki", lambda: wikimedia.search(sp["sci"], sp["common"], "sitting", want)),
                     ("inat", lambda: inat.search(sp["sci"], sp["common"], "sitting", want)),
                     ("gbif", lambda: gbif.search(sp["sci"], sp["common"], "sitting", want)),
                     ("whobird", lambda: whobird.search(sp["sci"], sp["common"], "sitting", want))):
        try:
            lists.append(fn() or [])
        except Exception as e:  # noqa: BLE001
            print(f"    {name} search failed: {e}")
            lists.append([])
    # Round-robin interleave so the first downloaded (capped) candidates span all
    # sources rather than being exhausted by whichever is listed first.
    seen, out = set(), []
    first = curated_candidate(code)
    if first:
        seen.add(first.url); out.append(first)
    for group in zip_longest(*lists):
        for c in group:
            if c and getattr(c, "url", None) and c.url not in seen:
                seen.add(c.url); out.append(c)
    return out


def best_ref(sp, code, sess):
    """Pick the best WHOLE-bird reference across sources: a real bird, clean
    perched side profile, fully in frame at a moderate size."""
    cands = _gather(sp, code, 9)
    tmp, imgs, srcs = [], [], []
    for c in cands:
        if len(tmp) >= 16:
            break
        try:
            p = os.path.join(BADREFS, f"_cand_{code}_{len(tmp)}.jpg")
            os.makedirs(BADREFS, exist_ok=True)
            if not _fetch_candidate(c, p):
                continue
            # Keep the candidate's attribution beside it: the drawing made from
            # an openly licensed photo has to credit the photographer, so the
            # licence, author and source page travel with the reference.
            try:
                with open(p + ".json", "w", encoding="utf-8") as jf:
                    json.dump(c.meta(), jf, ensure_ascii=False, indent=1)
            except Exception:                                   # noqa: BLE001
                pass
            tmp.append(p); imgs.append(Image.open(p).convert("RGB"))
            srcs.append(getattr(c, "source", "?"))
        except Exception:
            continue
    if not imgs:
        return None
    obj = QC.clip_probs(imgs, [QC.POS] + QC.NEG)[:, 0].tolist()
    pose = QC.clip_probs(imgs, [POSE_GOOD] + POSE_BAD)[:, 0].tolist()
    photo = QC.clip_probs(imgs, [PHOTO_POS, PHOTO_NEG])[:, 0].tolist()  # P(real photo)
    fast = _fast_session()

    # Wikimedia Commons is the PREFERRED base image: the photos are openly
    # licensed (CC / public domain), so the drawing made from one is a clean
    # derivative we can publish and credit, and the reference itself may be shown
    # on the review page. Take the best Commons candidate that CLIP agrees is a
    # real photo of a real bird in a usable pose; fall through to the scored
    # multi-source pick (which still includes the curated Macaulay shot) when
    # Commons has nothing good, so coverage never suffers for it.
    wikis = [i for i, s in enumerate(srcs) if s == "wikimedia"]
    if wikis:
        # The bird has to be ALL THERE, preference or not. A Wikipedia lead
        # image is often a cropped portrait — the magpie's tail, the heron's
        # legs out of frame — and a drawing made from one inherits the missing
        # part, which is exactly what a field guide must not do. Mask the most
        # promising Commons candidates and take the best whole one; if none is
        # whole, fall through to the scored pick (Commons candidates are in
        # that pool too, so nothing is lost but the shortcut).
        pre_w = sorted(wikis, key=lambda i: obj[i] + 0.6 * pose[i], reverse=True)[:5]
        whole_w = {i: _wholeness(_mask(imgs[i], fast))[0] for i in pre_w}
        usable = [i for i in pre_w
                  if obj[i] > 0.55 and photo[i] > 0.35 and whole_w[i] > 0.55]
        if usable:
            best_wiki = max(usable,
                            key=lambda i: obj[i] + 0.6 * pose[i] + 1.3 * whole_w[i])
            print(f"    ref[wikimedia PREFERRED]: bird={obj[best_wiki]:.2f} "
                  f"pose={pose[best_wiki]:.2f} whole={whole_w[best_wiki]:.2f} "
                  f"photo={photo[best_wiki]:.2f} (openly licensed, of {len(imgs)})")
            return tmp[best_wiki], "wikimedia"
        best_wiki = pre_w[0]
        print(f"    no whole-bird Commons photo (best bird={obj[best_wiki]:.2f} "
              f"whole={whole_w[best_wiki]:.2f} photo={photo[best_wiki]:.2f}); "
              f"scoring all sources")

    # CLIP is cheap; the whole-bird mask is not. Only mask the most promising
    # candidates (top real-bird + pose) with the fast model.
    pre = sorted(range(len(imgs)), key=lambda i: obj[i] + 0.6 * pose[i], reverse=True)
    scored = []
    for i in pre[:8]:
        whole, subj = _wholeness(_mask(imgs[i], fast))
        size_term = max(0.0, 0.4 - abs(subj - 0.35))   # prefer bird ~35% of frame
        # Must be a real photo, a real bird, whole and not tiny.
        ok = obj[i] > 0.5 and photo[i] > 0.5 and whole > 0.55 and subj > 0.05
        score = (obj[i] * 1.0 + pose[i] * 0.7 + whole * 1.3 + size_term
                 + photo[i] * 0.9 + min(sharp(imgs[i]) / 1500, 0.3))
        scored.append((ok, round(score, 3), obj[i], pose[i], whole, subj,
                       photo[i], srcs[i], tmp[i]))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    s = scored[0]
    print(f"    ref[{s[7]}]: bird={s[2]:.2f} pose={s[3]:.2f} whole={s[4]:.2f} "
          f"subj={s[5]:.2f} photo={s[6]:.2f} ok={s[0]} (of {len(imgs)})")
    if not s[0]:
        print(f"    {code}: no fully-in-frame real photo; using best available")
    return s[8], s[7]   # (path, source)


def _fit_square(im, size):
    """Letterbox the WHOLE image onto a white square — never crops, so no tail
    or beak sticking out of a centre square is lost."""
    im = im.convert("RGB").copy()
    im.thumbnail((size, size), Image.LANCZOS)
    sq = Image.new("RGB", (size, size), (255, 255, 255))
    sq.paste(im, ((size - im.width) // 2, (size - im.height) // 2))
    return sq


def prep_init(ref_path, sess, size=1024, frame=0):
    """Isolate the bird and centre it on white so it fills the frame.

    Feeding a tight, background-free bird to img2img makes the generated bird
    large and central (so the final matte never culls it as too small) and
    removes distracting backgrounds that pull colour/shape off.

    The bird is NEVER cropped: the isolate path pastes the full cutout onto a
    larger white square, and the whole-photo path letterboxes (pads, not crops)
    so tails and beaks at the edges are kept.

    `frame` cycles the framing so re-flagging a "bad photo" that is really just a
    bad crop yields a genuinely different model input:
      0 -> isolate the bird, 1.45x margin (default)
      1 -> isolate the bird, looser 1.7x margin (more breathing room)
      2 -> no isolation: the whole photo letterboxed onto a white square
    Frame 2 (and any isolation failure) letterboxes the whole photo."""
    im = Image.open(ref_path).convert("RGB")
    if frame % 3 != 2:
        try:
            ci = cut.cut_pil(im, sess, 900)  # RGBA, cropped tight to the bird
            if ci is not None:
                margin = 1.45 if frame % 3 == 0 else 1.7
                side = int(max(ci.size) * margin)
                sq = Image.new("RGBA", (side, side), (255, 255, 255, 255))
                sq.paste(ci, ((side - ci.width) // 2, (side - ci.height) // 2), ci)
                return sq.convert("RGB").resize((size, size), Image.LANCZOS)
        except Exception:
            pass
    return _fit_square(im, size)


# How much of the reference photo each variant keeps (0 = the photo, 1 = a free
# drawing). The band is deliberately low: the point of these images is that they
# are this species, in this posture, with these field marks. Above ~0.7 the
# model starts inventing plumage that is merely plausible.
VARIANTS = [(1000, 0.45), (1001, 0.55), (1002, 0.65)]
# A generated bird counts as whole when almost nothing of it reaches the top or
# side edges of the frame (the bottom is allowed: legs and perch). Same measure
# as the reference check — see _wholeness.
WHOLE_MIN = 0.55
MAX_EDGE = 448   # display is <=230px (~460px retina); 448 is ample and ~20% smaller


def prune_review_imgs(review):
    """Keep review_imgs/ only for species currently shown on the review page
    (in the manifest and not yet reviewed); delete the rest so the published
    folder doesn't accumulate hundreds of MB of stale variants/references."""
    keep = {c for c, e in review.get("species", {}).items() if not e.get("reviewed")}
    removed = 0
    for d in glob.glob(os.path.join(REVIEW_IMGS, "*")):
        if os.path.isdir(d) and os.path.basename(d) not in keep:
            shutil.rmtree(d, ignore_errors=True); removed += 1
    if removed:
        print(f"pruned {removed} stale/reviewed review_imgs dirs")


def save_small(im, path, colors=200):
    """Save an RGBA cutout compactly: keep the soft alpha edge, but quantise the
    RGB to a palette so PNG compresses far better (no visible loss at this size).
    Matches how the book plates are stored."""
    im = im.convert("RGBA")
    q = im.convert("RGB").quantize(colors=colors, method=Image.FASTOCTREE,
                                   dither=Image.NONE).convert("RGBA")
    q.putalpha(im.getchannel("A"))
    q.save(path, optimize=True)


def gen_best(pipe, sess, code, sp, pose, ref_path, fams, ids, seed_off=0,
             set_live=True, seeds=None, vary_frame=False):
    """Generate seed/strength variants and save them for the review page.

    No variant is auto-selected (chosen is None) — the reviewer picks. When
    set_live is True the top-ranked variant is also written as the live image
    (used for first-time/coverage generation); when False the live image is left
    untouched (used for "none good enough" re-gen and for generating fresh
    challengers next to a kept champion). seeds overrides the default 3
    (base_seed, strength) pairs — e.g. 2 fresh pairs for challenger suggestions.
    seed_off shifts the seeds so each round yields genuinely different variants.

    vary_frame=True (used for a flagged "bad photo") generates each variant from
    a DIFFERENT crop/framing of the reference, so a badly-cropped photo is
    re-cropped — the reviewer then picks the best-framed result."""
    prompt = improved_prompt(sp["common"], sp["sci"], code, pose, fams, ids)
    vdir = os.path.join(REVIEW_IMGS, code); os.makedirs(vdir, exist_ok=True)
    base_frame = (seed_off // 5) % 3
    # Cache prep_init per frame so non-recrop calls compute the (slow) cutout
    # once; a recrop call computes one per distinct frame.
    _icache = {}
    def init_for(frame):
        if frame not in _icache:
            _icache[frame] = prep_init(ref_path, sess, frame=frame)
        return _icache[frame]

    seed_list = seeds or VARIANTS
    ref_rel = None
    variants = []   # (cutout, seed, strength, init_used)
    for i, (base_seed, strength) in enumerate(seed_list):
        frame = (base_frame + i) % 3 if vary_frame else base_frame
        init = init_for(frame)
        if ref_rel is None:
            # Publish the first model input as the review reference tile (what
            # actually grounded generation). With vary_frame the crops differ
            # per variant; this shows a representative one.
            rt = init.copy(); rt.thumbnail((384, 384), Image.LANCZOS)
            rt.save(os.path.join(vdir, "ref.jpg"), "JPEG", quality=82, optimize=True)
            ref_rel = f"review_imgs/{code}/ref.jpg"
        seed = base_seed + seed_off
        gen = torch.Generator("cpu").manual_seed(seed)
        if getattr(pipe, "_bird_control", False):
            # Flex.2 has no img2img mode of its own; control_img2img gives it
            # one, so the drawing starts from the photograph rather than from
            # noise with the photo as a hint. Same `strength` meaning as below.
            out = G.control_img2img(pipe, init, G.STYLES["fieldguide"]["tag"],
                                    prompt, strength, seed)
        else:
            out = pipe(prompt=G.STYLES["fieldguide"]["tag"], prompt_2=prompt, image=init,
                       strength=strength, num_inference_steps=28, guidance_scale=3.5,
                       generator=gen).images[0]
        ci = cut.cut_pil(out, sess, MAX_EDGE)
        if ci is not None:
            # Even from a whole reference the model can draw past the canvas —
            # a long tail or a raised wing running off the edge. Measure the
            # generated frame before it is cut out (the cutout is cropped to
            # the bird, so the truncation is invisible afterwards) and keep the
            # score, so a clipped variant loses to an intact one below.
            whole = _whole4(_mask(out, _fast_session()))
            if whole < WHOLE_MIN:
                print(f"    variant seed={seed} clipped at the frame "
                      f"(whole={whole:.2f})")
            variants.append((ci, seed, strength, init, whole))
    # Also publish the raw reference photo IN FULL (aspect preserved, never
    # cropped — the review tile letterboxes it), so the reviewer sees the whole
    # real photograph (tail, feet and all), not a square crop of it.
    photo_rel = None
    try:
        photo = Image.open(ref_path).convert("RGB")
        photo.thumbnail((512, 512), Image.LANCZOS)
        photo.save(os.path.join(vdir, "photo.jpg"), "JPEG", quality=82, optimize=True)
        photo_rel = f"review_imgs/{code}/photo.jpg"
    except Exception:
        pass
    if not variants:
        print(f"    {code} {pose}: all variants culled"); return None
    outs = [v[0].convert("RGB") for v in variants]
    # Score each output's similarity to its OWN model input (crops may differ).
    n = len(outs)
    feats = QC.clip_image_features([v[3] for v in variants] + outs)
    sims = [float((feats[i] * feats[n + i]).sum()) for i in range(n)]
    pose_p = QC.clip_probs(outs, [POSE_GOOD] + POSE_BAD)[:, 0].tolist()
    bird_p = QC.clip_probs(outs, [QC.POS] + QC.NEG)[:, 0].tolist()
    # Rank all variants best-first and save them for the review page so the user
    # can pick a different one; v0 is the auto-chosen best.
    # An intact bird outranks a clipped one whatever else it scores: a field
    # guide showing a magpie without its tail is wrong, however pretty.
    whole_p = [v[4] for v in variants]
    order = sorted(range(len(outs)),
                   key=lambda i: (whole_p[i] >= WHOLE_MIN,
                                  sims[i] + 0.5 * pose_p[i] + 0.5 * bird_p[i]),
                   reverse=True)
    if whole_p[order[0]] < WHOLE_MIN:
        print(f"    {code}: every variant is clipped at the frame; "
              f"keeping the best (whole={whole_p[order[0]]:.2f})")
    vdir = os.path.join(REVIEW_IMGS, code)
    os.makedirs(vdir, exist_ok=True)
    for f in os.listdir(vdir):
        if f.startswith("v") and f.endswith(".png"):
            os.remove(os.path.join(vdir, f))
    vmeta = []
    for rank, i in enumerate(order):
        vid = f"v{rank}"
        save_small(variants[i][0], os.path.join(vdir, f"{vid}.png"))
        vmeta.append({"id": vid, "img": f"review_imgs/{code}/{vid}.png",
                      "seed": variants[i][1], "strength": variants[i][2],
                      "whole": round(variants[i][4], 3),
                      "sim": round(sims[i], 3), "pose": round(pose_p[i], 3),
                      "bird": round(bird_p[i], 3)})
    best = variants[order[0]]
    print(f"    top v0 seed={best[1]} s={best[2]} (sim={sims[order[0]]:.2f} "
          f"pose={pose_p[order[0]]:.2f} bird={bird_p[order[0]]:.2f}) of {len(variants)}"
          f"{' [live]' if set_live else ' [variants only]'}")
    png = None
    if set_live:
        dst = os.path.join(OUT, code)
        os.makedirs(dst, exist_ok=True)
        png = os.path.join(dst, f"{pose}_0.png")
        save_small(best[0], png)
        meta = {"source": "generated", "model": G.DEFAULT_MODEL,
                "style": STYLE, "prompt": prompt, "pose": pose,
                "reference": os.path.basename(ref_path), "strength": best[2],
                "seed": best[1], "recipe": RECIPE}
        # The drawing is a derivative of the reference photo: record who took it
        # and under what licence, so the app can credit them.
        try:
            rc = json.load(open(ref_path + ".json", encoding="utf-8"))
            meta["reference_credit"] = {
                "source": rc.get("source", ""), "author": rc.get("author", ""),
                "license": rc.get("license", ""), "page_url": rc.get("page_url", ""),
            }
        except Exception:                                       # noqa: BLE001
            pass
        with open(png + ".json", "w", encoding="utf-8") as jf:
            json.dump(meta, jf, ensure_ascii=False, indent=2)
    return {"png": png, "chosen": None, "variants": vmeta,
            "ref": ref_rel, "photo": photo_rel,
            # True when even the best variant ran off the frame: the caller can
            # re-queue the species for a re-draw at a looser framing.
            "clipped": whole_p[order[0]] < WHOLE_MIN}


def _is_done(code):
    """True if this species' sitting image already came from the current recipe."""
    j = os.path.join(OUT, code, "sitting_0.png.json")
    try:
        return str(json.load(open(j, encoding="utf-8")).get("recipe", "")) == RECIPE
    except Exception:
        return False


def snapshot_before(code, vdir):
    """Copy the current live image to the review folder as 'before' (the
    'Current (live)' tile / montage baseline). Returns its relative path."""
    cur = os.path.join(OUT, code, "sitting_0.png")
    if os.path.exists(cur):
        shutil.copy(cur, os.path.join(BA, f"{code}_before.png"))
        shutil.copy(cur, os.path.join(vdir, "before.png"))
        return f"review_imgs/{code}/before.png"
    return None


def setup_reference(code, sp, sess):
    """Pick/install the img2img reference. Uses a manually-pinned ref if present,
    else auto-selects the best whole-bird photo across sources. Quarantines any
    previous reference and installs the new one as the local img2img input. The
    review tile is published later by gen_best (the exact prep_init model input).
    Returns (ref_input_path, refsrc), or (None, None) if none usable."""
    d = os.path.join(RAW, code); os.makedirs(d, exist_ok=True)
    pinned = os.path.join(PINNED, code + ".jpg")
    if os.path.exists(pinned):
        newref, refsrc = pinned, "pinned"
        print("    ref[pinned]: manually-curated reference")
    else:
        newref, refsrc = best_ref(sp, code, sess)
    if not newref:
        print(f"  {code}: no usable reference found, skip"); return None, None
    for f in sorted(os.listdir(d)):
        if f.startswith("sitting_0.") and not f.endswith(".json"):
            os.makedirs(os.path.join(BADREFS, code), exist_ok=True)
            shutil.move(os.path.join(d, f), os.path.join(BADREFS, code, f))
            j = os.path.join(d, f + ".json")
            if os.path.exists(j):
                shutil.move(j, os.path.join(BADREFS, code, f + ".json"))
    ref_input = os.path.join(d, "sitting_0.jpg")
    shutil.copy(newref, ref_input)   # local img2img input (gitignored)
    if os.path.exists(newref + ".json"):          # carry the photo's credit over
        shutil.copy(newref + ".json", ref_input + ".json")
    return ref_input, refsrc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--codes")
    ap.add_argument("--object-only", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="every AI species (skips ones already on the v3 recipe)")
    args = ap.parse_args()

    by_code = {s["code"]: s for s in load_species()}
    fams = load_families()
    ids = load_id_features()
    retry = load_retry()

    if args.codes:
        flagged = [{"code": c.strip(), "reason": "manual"} for c in args.codes.split(",")]
    elif args.all:
        man = json.load(open(os.path.join(OUT, "manifest.json"), encoding="utf-8"))
        codes = list(man["species"]) if isinstance(man, dict) and "species" in man else list(man)
        flagged = [{"code": c, "reason": "all"} for c in codes if not _is_done(c)]
        # fetch any missing families up front (best-effort)
        try:
            miss = [r["code"] for r in flagged if r["code"] not in fams]
            if miss:
                subprocess.run([sys.executable, os.path.join(HERE, "fetch_families.py"),
                                "--codes", ",".join(miss)], capture_output=True)
                fams = load_families()
        except Exception as e:  # noqa: BLE001
            print("family prefetch skipped:", e)
    else:
        rows = list(csv.DictReader(open(os.path.join(QCDIR, "qc_refs.csv"), encoding="utf-8")))
        flagged = [r for r in rows if r["reason"] != "ok"]
        if args.object_only:
            flagged = [r for r in flagged if "object" in r["reason"]]
        # object-fails first (broken refs), then quality
        flagged.sort(key=lambda r: (0 if "object" in r["reason"] else 1))
    if args.limit:
        flagged = flagged[:args.limit]
    print(f"{len(flagged)} species to regenerate")

    os.makedirs(BA, exist_ok=True)
    os.makedirs(os.path.dirname(REVIEW_MAN), exist_ok=True)
    review = {"species": {}}
    if os.path.exists(REVIEW_MAN):
        review = json.load(open(REVIEW_MAN, encoding="utf-8"))
        review.setdefault("species", {})
        # The review page should only show current-recipe images. Drop stale
        # entries from earlier recipes; tag the ones whose live image is v4.
        for c in list(review["species"]):
            if _is_done(c):
                review["species"][c]["recipe"] = RECIPE
            else:
                del review["species"][c]
        print(f"review manifest pruned to {len(review['species'])} {RECIPE} entries")
        prune_review_imgs(review)

    print("Loading FLUX pipeline...")
    pipe = G.load_pipeline(fp8=True)   # BIRD_MODEL picks the checkpoint
    sess = new_session("birefnet-general")

    done = 0
    for i, r in enumerate(flagged, 1):
        code = r["code"]
        sp = by_code.get(code)
        if not sp:
            print(f"  {code}: unknown code, skip"); continue
        print(f"\n[{i}/{len(flagged)}] {code}  {sp['common']}  ({r['reason']})")
        vdir = os.path.join(REVIEW_IMGS, code); os.makedirs(vdir, exist_ok=True)
        before_rel = snapshot_before(code, vdir)
        ref_input, refsrc = setup_reference(code, sp, sess)
        if not ref_input:
            continue
        res = gen_best(pipe, sess, code, sp, "sitting", ref_input, fams, ids,
                       seed_off=retry.get(code, 0) * 5)
        if not res:
            continue
        shutil.copy(res["png"], os.path.join(BA, f"{code}_after.png"))
        fam = fams.get(code) or [None, None]
        review["species"][code] = {
            "name": sp["common"], "sci": sp["sci"], "family": fam[1],
            "reason": r.get("reason", ""), "before": before_rel,
            "ref": res.get("ref"), "photo": res.get("photo"),
            "ref_source": refsrc, "recipe": RECIPE,
            "id": ids.get(code, ""),
            "chosen": res["chosen"], "variants": res["variants"],
            "gen": int(time.time()), "reviewed": False, "pending": False}
        json.dump(review, open(REVIEW_MAN, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        done += 1
        if done % PUSH_EVERY == 0:
            push_batch(done)
    push_batch(done, final=True)
    print(f"\nDone. {done} species regenerated; review at docs/review/")


def git(*a):
    return subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True)


def push_batch(n, final=False):
    """Rebuild the birds manifest and push the new images + review data."""
    print(f"  -- pushing batch ({n} done){' [final]' if final else ''} --")
    subprocess.run([sys.executable, os.path.join(HERE, "build_manifest.py")],
                   capture_output=True)
    git("add", "docs")
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("  nothing to push"); return
    msg = (f"Redraw AI birds ({RECIPE}), {n} done\n\n"
           "Openly licensed Commons base photo, background cut out before "
           "img2img, field-sketch prompt, best-of-N selection. Variants saved "
           "for review at docs/review/.\n\n"
           "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\n"
           "Claude-Session: https://claude.ai/code/session_01QE9YmeK2n7PbSUUJKRUAzz")
    git("commit", "-m", msg)
    p = git("push", "origin", "main")
    print("  push:", "ok" if p.returncode == 0 else p.stderr[-200:])


if __name__ == "__main__":
    main()
