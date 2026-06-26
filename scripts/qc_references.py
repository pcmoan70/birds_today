"""QC the img2img reference photos: flag the ones that are not a clean, sharp
photo of a single real bird (sculptures, maps, illustrations, decorative
objects, blurry or distant shots) so they can be re-fetched.

Signals per reference (the sitting_0 init that generate.py would pick):
  - clip_bird : CLIP zero-shot P(real wild-bird photo) vs sculpture/map/art/...
  - sharp     : variance of the Laplacian (low => blurry/soft)
  - subjfrac  : rembg subject-mask area fraction (low => bird tiny in frame)

Writes scripts/qc_refs.csv (sorted worst-first) and, with --montage, copies the
N worst references into scripts/qc_out/refs_worst/ for review.

Usage:
  python qc_references.py --sample comchi1,wlwwar,wiltit1,eurrob1
  python qc_references.py --all [--montage 40]
"""
import argparse
import csv
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from scipy.ndimage import laplace  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
OUT = os.path.join(HERE, "qc_out")

# CLIP judges CATEGORY only (is it a real bird, or a non-bird object). Framing
# quality (bird too small / blurry) is handled separately by subjfrac+sharp, so
# these negatives are object types, NOT "tiny/blurry/no bird" — otherwise CLIP
# penalises perfectly good photos where the bird isn't frame-filling.
POS = "a photograph of a real living bird"
NEG = [
    "a sculpture, statue or metal figurine of a bird",
    "a geographic distribution range map",
    "a drawing, painting or engraving of a bird",
    "a clock, ornament or gold decorative object",
    "a postage stamp, coin or banknote",
]
_clip = {}


def _ensure_clip():
    if not _clip:
        import torch
        from transformers import CLIPModel, CLIPProcessor
        name = "openai/clip-vit-base-patch32"
        _clip["m"] = CLIPModel.from_pretrained(name).eval()
        _clip["p"] = CLIPProcessor.from_pretrained(name)
        _clip["t"] = torch


def clip_probs(images, labels):
    """Softmax probabilities (n_img, n_label) for arbitrary text labels."""
    _ensure_clip()
    import torch
    m, p = _clip["m"], _clip["p"]
    with torch.no_grad():
        inp = p(text=labels, images=images, return_tensors="pt", padding=True)
        return m(**inp).logits_per_image.softmax(dim=1)


def clip_image_features(images):
    """L2-normalised CLIP image embeddings (n_img, d) for image-image similarity."""
    _ensure_clip()
    import torch
    m, p = _clip["m"], _clip["p"]
    with torch.no_grad():
        inp = p(images=images, return_tensors="pt")
        f = m.get_image_features(**inp)
        if not torch.is_tensor(f):  # some builds return a model-output object
            f = getattr(f, "pooler_output", None)
            if f is None:
                f = getattr(f, "last_hidden_state").mean(dim=1)
        return f / f.norm(dim=-1, keepdim=True)


def clip_scores(images):
    """Return P(POS) for each PIL image via CLIP zero-shot over [POS]+NEG."""
    return clip_probs(images, [POS] + NEG)[:, 0].tolist()


def sharpness(im):
    g = np.asarray(im.convert("L").resize((256, 256)), dtype=np.float32)
    return float(laplace(g).var())


def subj_frac(im, sess):
    import cutout as cut
    try:
        cutimg = cut.cut_pil(im.convert("RGB"), sess, 256)
        if cutimg is None:
            return 0.0
        a = np.asarray(cutimg.convert("RGBA"))[..., 3]
        return float((a > 40).mean())
    except Exception:
        return -1.0


def ref_path(code):
    d = os.path.join(RAW, code)
    if not os.path.isdir(d):
        return None
    files = sorted(f for f in os.listdir(d)
                   if f.startswith("sitting_") and not f.endswith(".json"))
    return os.path.join(d, files[0]) if files else None


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--sample", help="comma-separated codes")
    ap.add_argument("--montage", type=int, default=0, help="copy N worst refs")
    ap.add_argument("--fast", action="store_true",
                    help="skip rembg subject-size (CLIP + sharpness only; ~50x faster)")
    args = ap.parse_args()

    if args.sample:
        codes = [c.strip() for c in args.sample.split(",")]
    else:
        codes = sorted(d for d in os.listdir(RAW)
                       if os.path.isdir(os.path.join(RAW, d)))

    sess = None
    if not args.fast:
        from rembg import new_session
        sess = new_session("birefnet-general")

    rows = []
    paths = [(c, ref_path(c)) for c in codes]
    paths = [(c, p) for c, p in paths if p]
    print(f"scoring {len(paths)} references...")
    # CLIP in batches
    B = 16
    for i in range(0, len(paths), B):
        chunk = paths[i:i + B]
        imgs = [Image.open(p).convert("RGB") for _, p in chunk]
        cb = clip_scores(imgs)
        for (code, p), im, c in zip(chunk, imgs, cb):
            rows.append({"code": code, "ref": os.path.basename(p),
                         "clip_bird": round(c, 3),
                         "sharp": round(sharpness(im), 1),
                         "subjfrac": (-1.0 if sess is None
                                      else round(subj_frac(im, sess), 3)),
                         "path": p})
        print(f"  {min(i+B,len(paths))}/{len(paths)}")

    # Two independent failure modes:
    #   object  - CLIP says it's not a real bird (sculpture/map/art/...) -> hard fail
    #   quality - real bird but tiny in frame or very blurry -> soft fail (re-fetch)
    for r in rows:
        obj = r["clip_bird"] < 0.45
        qual = (0 <= r["subjfrac"] < 0.07) or r["sharp"] < 60
        r["reason"] = ("object" if obj else "") + (";quality" if qual else "")
        r["reason"] = r["reason"].strip(";") or "ok"
        r["bad"] = round((1 - r["clip_bird"]) + (0.4 if qual else 0), 3)
    rows.sort(key=lambda r: -r["bad"])

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "qc_refs.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["code", "ref", "clip_bird", "sharp",
                                          "subjfrac", "bad", "reason", "path"])
        w.writeheader()
        w.writerows(rows)
    flagged = [r for r in rows if r["reason"] != "ok"]
    obj = sum(1 for r in flagged if "object" in r["reason"])
    qual = sum(1 for r in flagged if "quality" in r["reason"])
    print(f"\nflagged {len(flagged)}/{len(rows)} references "
          f"(object={obj}, quality={qual})")
    for r in rows[:25]:
        print(f"  {r['code']:9s} clip={r['clip_bird']:.2f} sharp={r['sharp']:7.1f} "
              f"subj={r['subjfrac']:.2f} bad={r['bad']:.2f} [{r['reason']}]  {r['ref']}")

    if args.montage:
        d = os.path.join(OUT, "refs_worst")
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d)
        for rank, r in enumerate(rows[:args.montage]):
            ext = os.path.splitext(r["path"])[1]
            shutil.copy(r["path"], os.path.join(
                d, f"{rank:02d}_{r['code']}_clip{int(r['clip_bird']*100):02d}{ext}"))
        print(f"copied {min(args.montage,len(rows))} worst -> {d}")


if __name__ == "__main__":
    main()
