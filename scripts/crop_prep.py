"""Prepare the manual photo-crop tool (docs/crop.html).

For each "bad photo"-flagged species, pool candidate reference photos across all
sources (whoBIRD/Macaulay, iNaturalist, Wikimedia, GBIF), download a moderate-
resolution copy to docs/crop/<code>/candN.jpg, and write docs/crop/manifest.json
listing them. The static crop tool then lets you pick the best candidate and
draw a crop box; apply_crops.py turns that into a pinned reference + re-gen.

Macaulay assets are cached on disk and rate-limited (via regen_flagged), so this
never hammers Cornell's CDN.

Usage:
  python crop_prep.py                 # all current badRef species (review_feedback.json)
  python crop_prep.py --codes a,b,c   # specific species
  python crop_prep.py --per 12        # candidates per species (default 12)
"""
import argparse
import json
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image  # noqa: E402

import regen_flagged as R  # noqa: E402
from species import load_species  # noqa: E402

ROOT = R.ROOT
CROP_DIR = os.path.join(ROOT, "docs", "crop")
CROP_MAN = os.path.join(CROP_DIR, "manifest.json")
DISP = 1024  # hosted candidate size (crop boxes are normalised, so this is ample)


def flagged_codes():
    fb = os.path.join(R.HERE, "review_feedback.json")
    if os.path.exists(fb):
        return list(dict.fromkeys(json.load(open(fb, encoding="utf-8")).get("badRef", [])))
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes")
    ap.add_argument("--per", type=int, default=12)
    args = ap.parse_args()

    by_code = {s["code"]: s for s in load_species()}
    codes = ([c.strip() for c in args.codes.split(",")] if args.codes
             else flagged_codes())
    if not codes:
        print("no bad-photo species to prepare"); return
    print(f"{len(codes)} species to prepare")

    man = {"species": {}}
    if os.path.exists(CROP_MAN):
        try:
            man = json.load(open(CROP_MAN, encoding="utf-8")); man.setdefault("species", {})
        except Exception:
            man = {"species": {}}

    for i, code in enumerate(codes, 1):
        sp = by_code.get(code)
        if not sp:
            print(f"  {code}: unknown, skip"); continue
        vdir = os.path.join(CROP_DIR, code)
        shutil.rmtree(vdir, ignore_errors=True)
        os.makedirs(vdir, exist_ok=True)
        print(f"\n[{i}/{len(codes)}] {code}  {sp['common']}")
        cands = R._gather(sp, code, max(args.per + 4, 12))
        saved = []
        for c in cands:
            if len(saved) >= args.per:
                break
            tmp = os.path.join(vdir, "_dl.jpg")
            try:
                if not R._fetch_candidate(c, tmp):
                    continue
                im = Image.open(tmp).convert("RGB")
                if min(im.size) < 200:        # too small to be useful
                    continue
                im.thumbnail((DISP, DISP), Image.LANCZOS)
                idx = len(saved)
                im.save(os.path.join(vdir, f"cand{idx}.jpg"), "JPEG",
                        quality=85, optimize=True)
                saved.append({"img": f"crop/{code}/cand{idx}.jpg",
                              "source": getattr(c, "source", "?"),
                              "src_id": getattr(c, "src_id", "") or "",
                              "url": getattr(c, "url", "") or "",       # original full-res
                              "author": getattr(c, "author", "") or "",
                              "license": getattr(c, "license", "") or "",
                              "page_url": getattr(c, "page_url", "") or ""})
            except Exception as e:  # noqa: BLE001
                print(f"    cand failed: {e}")
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        if not saved:
            print(f"  {code}: no candidates found"); shutil.rmtree(vdir, ignore_errors=True); continue
        man["species"][code] = {"name": sp["common"], "sci": sp["sci"], "cands": saved}
        print(f"  {len(saved)} candidates")
        json.dump(man, open(CROP_MAN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    json.dump(man, open(CROP_MAN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nprepared {len(man['species'])} species -> docs/crop/  (open crop.html)")


if __name__ == "__main__":
    main()
