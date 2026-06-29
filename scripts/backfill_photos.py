"""Backfill the review "Reference photo" tile from each species' local raw
reference, so the review page shows the real photograph (not the isolated
model-input cutout, which legacy entries fell back to).

For every non-reviewed manifest entry that lacks a `photo`:
  - whoBIRD (Macaulay) and pinned references are real photos by construction —
    always publish photo.jpg.
  - Wikimedia / GBIF / iNaturalist can serve artwork, so gate them through CLIP
    (PHOTO_POS vs PHOTO_NEG). Publish only if it scores as a real photograph;
    otherwise leave `photo` unset (the tile is omitted) and log it for refetch.

Usage: python backfill_photos.py
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image  # noqa: E402

import qc_references as QC  # noqa: E402
import regen_flagged as R  # noqa: E402

TRUSTED = {"whobird", "pinned"}
GATE = 0.5   # min P(real photo) for untrusted sources


def main():
    review = json.load(open(R.REVIEW_MAN, encoding="utf-8"))
    sp = review.get("species", {})
    written = skipped = rejected = 0
    reject_codes = []
    for code, s in sp.items():
        if s.get("reviewed"):
            continue
        raw = os.path.join(R.RAW, code, "sitting_0.jpg")
        if not os.path.exists(raw):
            skipped += 1
            continue
        try:
            im = Image.open(raw).convert("RGB")
        except Exception:
            skipped += 1
            continue
        src = s.get("ref_source")
        if src not in TRUSTED:
            p = QC.clip_probs([im], [R.PHOTO_POS, R.PHOTO_NEG])[0, 0].item()
            if p < GATE:
                rejected += 1
                reject_codes.append(f"{code}({src} {p:.2f})")
                continue
        vdir = os.path.join(R.REVIEW_IMGS, code)
        os.makedirs(vdir, exist_ok=True)
        full = im.copy()
        full.thumbnail((512, 512), Image.LANCZOS)  # full photo, aspect preserved
        full.save(os.path.join(vdir, "photo.jpg"), "JPEG", quality=82, optimize=True)
        s["photo"] = f"review_imgs/{code}/photo.jpg"
        written += 1

    json.dump(review, open(R.REVIEW_MAN, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"backfilled photo.jpg: {written} written · {rejected} rejected (not a "
          f"real photo) · {skipped} skipped (no local raw)")
    if reject_codes:
        print("  rejected (left without a reference photo; flag+refetch to fix):")
        print("   " + ", ".join(reject_codes))


if __name__ == "__main__":
    main()
