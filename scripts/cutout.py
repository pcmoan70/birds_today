"""Turn raw photos/plates into transparent bird cutouts.

rembg (U2Net) removes the background; we crop to the subject's alpha bounding
box, normalize size, and save PNG with transparency to docs/birds/. A coverage
sanity check culls failures (no subject found, or background not removed).

Usage:
  python cutout.py                 # process scripts/raw -> docs/birds
  python cutout.py --model u2netp  # lighter/faster model
  python cutout.py --max-edge 640
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
from rembg import new_session, remove  # noqa: E402
from scipy import ndimage  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw")
OUT_DIR = os.path.join(os.path.dirname(HERE), "docs", "birds")

MIN_COVER = 0.008  # < 0.8% opaque -> subject not found (low, to keep small flyers)
MAX_COVER = 0.97   # > 97% opaque -> background not removed
ALPHA_THRESH = 16  # alpha above this counts as "subject" for bbox/coverage
MIN_DOMINANCE = 0.55  # largest blob must be this share of all opaque pixels
                      # (else it's a multi-bird plate / cluttered scene)
MIN_SUBJECT_PX = 256  # the isolated bird must be >= this on both sides (in source
                      # pixels) — reject too-small/low-detail birds rather than upscale


def cut_pil(img, session, max_edge):
    """Background-remove + crop + size-normalise a PIL image.

    Returns an RGBA cutout, or None if it fails the quality check. Shared by
    the photo pipeline (process_image) and the illustration generator.
    """
    cut = remove(img.convert("RGBA"), session=session)
    arr = np.array(cut)
    mask = arr[:, :, 3] > ALPHA_THRESH
    cover = mask.mean()
    if cover < MIN_COVER or cover > MAX_COVER:
        print(f"      cull (coverage {cover:.1%})")
        return None

    # Keep the main subject, but group components by a dilated mask first so
    # thin parts the matting often severs from the body — legs, feet, tail tips,
    # bill — stay attached instead of being erased as separate blobs. Captions,
    # frame edges and well-separated secondary birds are still dropped.
    labels, n = ndimage.label(mask)
    if n > 1:
        r = min(14, max(2, round(0.012 * max(mask.shape))))  # gap-bridging radius
        grouped, gn = ndimage.label(ndimage.binary_dilation(mask, iterations=r))
        sizes = ndimage.sum(mask, grouped, range(1, gn + 1))  # weight by real px
        biggest = int(sizes.argmax()) + 1
        dominance = sizes.max() / sizes.sum()
        if dominance < MIN_DOMINANCE:
            print(f"      cull (multi-subject, largest {dominance:.0%})")
            return None
        keep = (grouped == biggest) & mask   # real pixels within the main group
        arr[~keep, 3] = 0          # erase everything but the main subject
        cut = Image.fromarray(arr)
        mask = keep

    ys, xs = np.where(mask)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    bw, bh = x1 - x0, y1 - y0
    # Reject birds that occupy too few source pixels — upscaling these looks bad.
    if min(bw, bh) < MIN_SUBJECT_PX:
        print(f"      cull (subject {bw}x{bh} < {MIN_SUBJECT_PX}px)")
        return None
    # Crop tight to the bird (it now fills the frame) with a small margin so
    # edges aren't clipped.
    pad = round(0.03 * max(bw, bh))
    box = (max(0, x0 - pad), max(0, y0 - pad),
           min(cut.width, x1 + pad), min(cut.height, y1 + pad))
    cut = cut.crop(box)
    w, h = cut.size
    scale = max_edge / max(w, h)
    if scale < 1:
        cut = cut.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                         Image.LANCZOS)
    return cut


def process_image(path, session, max_edge):
    """Cut out a bird from an image file. Returns RGBA cutout or None."""
    return cut_pil(Image.open(path), session, max_edge)


def run(model="birefnet-general", max_edge=640, raw=RAW_DIR, out=OUT_DIR, codes=None):
    """Cut out raw images -> transparent PNGs. `codes` limits to those species."""
    session = new_session(model)
    if not os.path.isdir(raw):
        print(f"no raw dir: {raw}")
        return
    all_codes = sorted(os.listdir(raw))
    codes = sorted(codes) if codes else all_codes
    total_in = total_out = 0
    for code in codes:
        src_dir = os.path.join(raw, code)
        if not os.path.isdir(src_dir):
            continue
        dst_dir = os.path.join(out, code)
        os.makedirs(dst_dir, exist_ok=True)
        raws = [f for f in sorted(os.listdir(src_dir))
                if not f.endswith(".json")]
        kept = 0
        for f in raws:
            total_in += 1
            base = os.path.splitext(f)[0]
            out_png = os.path.join(dst_dir, base + ".png")
            if os.path.exists(out_png):
                kept += 1
                total_out += 1
                continue
            print(f"  {code}/{f}")
            cut = process_image(os.path.join(src_dir, f), session, max_edge)
            if cut is None:
                continue
            cut.save(out_png)
            # Carry attribution sidecar forward next to the cutout.
            sidecar = os.path.join(src_dir, f + ".json")
            if os.path.exists(sidecar):
                with open(sidecar, encoding="utf-8") as jf:
                    meta = json.load(jf)
                with open(out_png + ".json", "w", encoding="utf-8") as jf:
                    json.dump(meta, jf, ensure_ascii=False, indent=2)
            kept += 1
            total_out += 1
        print(f"{code}: kept {kept}/{len(raws)}")
    print(f"\nDone. {total_out}/{total_in} cutouts -> {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="birefnet-general", help="rembg model name")
    ap.add_argument("--max-edge", type=int, default=640)
    ap.add_argument("--raw", default=RAW_DIR)
    ap.add_argument("--out", default=OUT_DIR)
    ap.add_argument("--codes", help="comma-separated species codes (default all)")
    args = ap.parse_args()
    codes = [c for c in args.codes.split(",")] if args.codes else None
    run(args.model, args.max_edge, args.raw, args.out, codes)


if __name__ == "__main__":
    main()
