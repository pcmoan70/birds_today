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
import json
import os
import shutil
import subprocess
import sys

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
from sources import inat, wikimedia, gbif  # noqa: E402
from sources.base import SESSION  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(HERE, "raw")
BADREFS = os.path.join(HERE, "raw_badrefs")
OUT = os.path.join(ROOT, "docs", "birds")
QCDIR = os.path.join(HERE, "qc_out")
BA = os.path.join(QCDIR, "ba")
FAMILIES = os.path.join(HERE, "families.json")
REVIEW_IMGS = os.path.join(ROOT, "docs", "review_imgs")   # variant images (on Pages)
REVIEW_MAN = os.path.join(ROOT, "docs", "review", "manifest.json")
PUSH_EVERY = 5


def load_families():
    return json.load(open(FAMILIES, encoding="utf-8")) if os.path.exists(FAMILIES) else {}


def improved_prompt(common, sci, code, stance, fams):
    fam = fams.get(code) or [None, None]
    fam_clause = ""
    if fam[0]:
        en = f" ({fam[1]})" if fam[1] else ""
        fam_clause = f", a member of the family {fam[0]}{en}"
    return (G.STYLES["fieldguide"]["prompt"] + ". "
            f"A {common} ({sci}){fam_clause}, {G.STANCES[stance]['desc']}. "
            "Depict a typical wild adult in natural, accurate, muted plumage "
            "colours, true to the reference photograph; avoid over-saturated or "
            f"exaggerated colours. {G.ANATOMY}.")


def sharp(im):
    g = np.asarray(im.convert("L").resize((256, 256)), np.float32)
    return float(laplace(g).var())


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


def _gather(sp, code, want):
    """Pool reference candidates across sources: iNaturalist (direct), Wikimedia,
    and GBIF (which federates more iNat + Observation.org + naturgucker + Flickr).
    Macaulay/eBird is IP-blocked (HTTP 403) from here, so it is omitted."""
    from itertools import zip_longest
    lists = []
    for name, fn in (("inat", lambda: inat.search(sp["sci"], sp["common"], "sitting", want)),
                     ("wiki", lambda: wikimedia.search(sp["sci"], sp["common"], "sitting", want)),
                     ("gbif", lambda: gbif.search(sp["sci"], sp["common"], "sitting", want))):
        try:
            lists.append(fn() or [])
        except Exception as e:  # noqa: BLE001
            print(f"    {name} search failed: {e}")
            lists.append([])
    # Round-robin interleave so the first downloaded (capped) candidates span all
    # sources rather than being exhausted by whichever is listed first.
    seen, out = set(), []
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
            r = SESSION.get(c.url, timeout=30)
            if r.status_code != 200 or not r.content:
                continue
            p = os.path.join(BADREFS, f"_cand_{code}_{len(tmp)}.jpg")
            os.makedirs(BADREFS, exist_ok=True)
            open(p, "wb").write(r.content)
            tmp.append(p); imgs.append(Image.open(p).convert("RGB"))
            srcs.append(getattr(c, "source", "?"))
        except Exception:
            continue
    if not imgs:
        return None
    obj = QC.clip_probs(imgs, [QC.POS] + QC.NEG)[:, 0].tolist()
    pose = QC.clip_probs(imgs, [POSE_GOOD] + POSE_BAD)[:, 0].tolist()
    # CLIP is cheap; the whole-bird mask is not. Only mask the most promising
    # candidates (top real-bird + pose) with the fast model.
    pre = sorted(range(len(imgs)), key=lambda i: obj[i] + 0.6 * pose[i], reverse=True)
    fast = _fast_session()
    scored = []
    for i in pre[:8]:
        whole, subj = _wholeness(_mask(imgs[i], fast))
        size_term = max(0.0, 0.4 - abs(subj - 0.35))   # prefer bird ~35% of frame
        ok = obj[i] > 0.5 and whole > 0.55 and subj > 0.05  # real, whole, not tiny
        score = obj[i] * 1.0 + pose[i] * 0.7 + whole * 1.3 + size_term + min(sharp(imgs[i]) / 1500, 0.3)
        scored.append((ok, round(score, 3), obj[i], pose[i], whole, subj, srcs[i], tmp[i]))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    s = scored[0]
    print(f"    ref[{s[6]}]: bird={s[2]:.2f} pose={s[3]:.2f} whole={s[4]:.2f} "
          f"subj={s[5]:.2f} ok={s[0]} (of {len(imgs)})")
    if not s[0]:
        print(f"    {code}: no fully-in-frame candidate; using best available")
    return s[7]


def prep_init(ref_path, sess, size=1024):
    """Isolate the bird and centre it on white so it fills the frame.

    Feeding a tight, background-free bird to img2img makes the generated bird
    large and central (so the final matte never culls it as too small) and
    removes distracting backgrounds that pull colour/shape off. Falls back to a
    centre square crop if the subject can't be isolated."""
    im = Image.open(ref_path).convert("RGB")
    try:
        ci = cut.cut_pil(im, sess, 900)  # RGBA, cropped tight to the bird
        if ci is not None:
            side = int(max(ci.size) * 1.18)
            sq = Image.new("RGBA", (side, side), (255, 255, 255, 255))
            sq.paste(ci, ((side - ci.width) // 2, (side - ci.height) // 2), ci)
            return sq.convert("RGB").resize((size, size), Image.LANCZOS)
    except Exception:
        pass
    w, h = im.size
    s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    return im.resize((size, size), Image.LANCZOS)


VARIANTS = [(1000, 0.60), (1001, 0.68), (1002, 0.74)]
MAX_EDGE = 512   # display is <=220px (~440px retina); 512 is ample, smaller files


def save_small(im, path, colors=200):
    """Save an RGBA cutout compactly: keep the soft alpha edge, but quantise the
    RGB to a palette so PNG compresses far better (no visible loss at this size).
    Matches how the book plates are stored."""
    im = im.convert("RGBA")
    q = im.convert("RGB").quantize(colors=colors, method=Image.FASTOCTREE,
                                   dither=Image.NONE).convert("RGBA")
    q.putalpha(im.getchannel("A"))
    q.save(path, optimize=True)


def gen_best(pipe, sess, code, sp, pose, ref_path, fams):
    """Generate several seed/strength variants and keep the best one (most
    consistent with the reference, clearest perched bird)."""
    prompt = improved_prompt(sp["common"], sp["sci"], code, pose, fams)
    init = prep_init(ref_path, sess)
    variants = []
    for seed, strength in VARIANTS:
        gen = torch.Generator("cpu").manual_seed(seed)
        out = pipe(prompt=G.STYLES["fieldguide"]["tag"], prompt_2=prompt, image=init,
                   strength=strength, num_inference_steps=28, guidance_scale=3.5,
                   generator=gen).images[0]
        ci = cut.cut_pil(out, sess, MAX_EDGE)
        if ci is not None:
            variants.append((ci, seed, strength))
    if not variants:
        print(f"    {code} {pose}: all variants culled"); return None
    outs = [v[0].convert("RGB") for v in variants]
    feats = QC.clip_image_features([init] + outs)
    ref = feats[0]
    sims = [float((ref * feats[i + 1]).sum()) for i in range(len(outs))]
    pose_p = QC.clip_probs(outs, [POSE_GOOD] + POSE_BAD)[:, 0].tolist()
    bird_p = QC.clip_probs(outs, [QC.POS] + QC.NEG)[:, 0].tolist()
    # Rank all variants best-first and save them for the review page so the user
    # can pick a different one; v0 is the auto-chosen best.
    order = sorted(range(len(outs)),
                   key=lambda i: sims[i] + 0.5 * pose_p[i] + 0.5 * bird_p[i],
                   reverse=True)
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
                      "sim": round(sims[i], 3), "pose": round(pose_p[i], 3),
                      "bird": round(bird_p[i], 3)})
    best = variants[order[0]]
    print(f"    chose v0 seed={best[1]} s={best[2]} (sim={sims[order[0]]:.2f} "
          f"pose={pose_p[order[0]]:.2f} bird={bird_p[order[0]]:.2f}) of {len(variants)}")
    dst = os.path.join(OUT, code)
    os.makedirs(dst, exist_ok=True)
    png = os.path.join(dst, f"{pose}_0.png")
    save_small(best[0], png)
    with open(png + ".json", "w", encoding="utf-8") as jf:
        json.dump({"source": "generated", "model": "black-forest-labs/FLUX.1-dev",
                   "style": "fieldguide", "prompt": prompt, "pose": pose,
                   "reference": os.path.basename(ref_path), "strength": best[2],
                   "seed": best[1], "recipe": "v3-pose-bestof"}, jf,
                  ensure_ascii=False, indent=2)
    return {"png": png, "chosen": "v0", "variants": vmeta}


def _is_v3_done(code):
    """True if this species' sitting image already came from the v3 pipeline."""
    j = os.path.join(OUT, code, "sitting_0.png.json")
    try:
        return str(json.load(open(j, encoding="utf-8")).get("recipe", "")).startswith("v3")
    except Exception:
        return False


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

    if args.codes:
        flagged = [{"code": c.strip(), "reason": "manual"} for c in args.codes.split(",")]
    elif args.all:
        man = json.load(open(os.path.join(OUT, "manifest.json"), encoding="utf-8"))
        codes = list(man["species"]) if isinstance(man, dict) and "species" in man else list(man)
        flagged = [{"code": c, "reason": "all"} for c in codes if not _is_v3_done(c)]
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

    print("Loading FLUX pipeline...")
    pipe = G.load_pipeline("black-forest-labs/FLUX.1-dev", None, fp8=True)
    sess = new_session("birefnet-general")

    done = 0
    for i, r in enumerate(flagged, 1):
        code = r["code"]
        sp = by_code.get(code)
        if not sp:
            print(f"  {code}: unknown code, skip"); continue
        print(f"\n[{i}/{len(flagged)}] {code}  {sp['common']}  ({r['reason']})")
        vdir = os.path.join(REVIEW_IMGS, code); os.makedirs(vdir, exist_ok=True)
        # snapshot the current live image as 'before' (for review + montage)
        cur = os.path.join(OUT, code, "sitting_0.png")
        before_rel = None
        if os.path.exists(cur):
            shutil.copy(cur, os.path.join(BA, f"{code}_before.png"))
            shutil.copy(cur, os.path.join(vdir, "before.png"))
            before_rel = f"review_imgs/{code}/before.png"
        # re-fetch a better reference (whole bird, multi-source)
        newref = best_ref(sp, code, sess)
        if not newref:
            print(f"  {code}: no usable iNat reference found, skip"); continue
        # quarantine the old reference, install the new one as sitting_0
        d = os.path.join(RAW, code); os.makedirs(d, exist_ok=True)
        for f in sorted(os.listdir(d)):
            if f.startswith("sitting_0.") and not f.endswith(".json"):
                os.makedirs(os.path.join(BADREFS, code), exist_ok=True)
                shutil.move(os.path.join(d, f), os.path.join(BADREFS, code, f))
                j = os.path.join(d, f + ".json")
                if os.path.exists(j):
                    shutil.move(j, os.path.join(BADREFS, code, f + ".json"))
        shutil.copy(newref, os.path.join(d, "sitting_0.jpg"))
        # also keep the chosen reference visible in the review folder
        shutil.copy(newref, os.path.join(vdir, "ref.jpg"))
        res = gen_best(pipe, sess, code, sp, "sitting",
                       os.path.join(d, "sitting_0.jpg"), fams)
        if not res:
            continue
        shutil.copy(res["png"], os.path.join(BA, f"{code}_after.png"))
        fam = fams.get(code) or [None, None]
        review["species"][code] = {
            "name": sp["common"], "sci": sp["sci"], "family": fam[1],
            "reason": r.get("reason", ""), "before": before_rel,
            "ref": f"review_imgs/{code}/ref.jpg", "chosen": res["chosen"],
            "variants": res["variants"]}
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
    msg = (f"Regenerate flagged AI birds (v3 recipe), {n} done\n\n"
           "Pose-aware iNat references + family-anchored muted prompts + "
           "best-of-N selection. Variants saved for review at docs/review/.\n\n"
           "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\n"
           "Claude-Session: https://claude.ai/code/session_01QE9YmeK2n7PbSUUJKRUAzz")
    git("commit", "-m", msg)
    p = git("push", "origin", "main")
    print("  push:", "ok" if p.returncode == 0 else p.stderr[-200:])


if __name__ == "__main__":
    main()
