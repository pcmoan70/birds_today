"""Audit review coverage: confirm every AI species eventually passes through the
review page, and surface any that haven't yet (or that slipped).

For the full AI species set (docs/birds/manifest.json) it reports, by state:
  - reviewed       : feedback applied (correctly dropped from the review page)
  - pending review : has a v4 image, shown on the review page now
  - awaiting v4    : still on an older image; will enter review once regenerated
  - no image / culled : no live image at all (needs (re)generation) — these are
                        the only ones that could miss review, so they're listed.

Run anytime (does not touch the generation run):
  python review_coverage.py
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIRDS = os.path.join(ROOT, "docs", "birds")
REVIEW_MAN = os.path.join(ROOT, "docs", "review", "manifest.json")
RECIPE = "v4-macaulay-id"


def recipe_of(code):
    j = os.path.join(BIRDS, code, "sitting_0.png.json")
    try:
        return json.load(open(j, encoding="utf-8")).get("recipe", "")
    except Exception:
        return None


def has_image(code):
    return os.path.exists(os.path.join(BIRDS, code, "sitting_0.png"))


def main():
    man = json.load(open(os.path.join(BIRDS, "manifest.json"), encoding="utf-8"))
    codes = list(man["species"]) if isinstance(man, dict) and "species" in man else list(man)
    review = (json.load(open(REVIEW_MAN, encoding="utf-8")).get("species", {})
              if os.path.exists(REVIEW_MAN) else {})

    reviewed = pending = awaiting = noimg = 0
    missing = []
    for c in codes:
        is_v4 = recipe_of(c) == RECIPE
        if not has_image(c):
            noimg += 1
            missing.append(c)
        elif not is_v4:
            awaiting += 1
        elif review.get(c, {}).get("reviewed"):
            reviewed += 1
        elif c in review:
            pending += 1
        else:
            # v4 image but absent from review manifest = bypassed review
            missing.append(c + " (v4, not in review!)")
            noimg += 0

    total = len(codes)
    done = reviewed + pending
    print(f"AI species: {total}")
    print(f"  reviewed (vetted, off page):   {reviewed}")
    print(f"  pending review (on page now):  {pending}")
    print(f"  awaiting v4 (will enter soon): {awaiting}")
    print(f"  no image / culled:             {noimg}")
    print(f"-> {done}/{total} have passed into review so far; "
          f"{awaiting + noimg} still to come.")
    if missing:
        print("\nNeed attention (no image / bypassed):")
        for m in missing[:60]:
            print("  ", m)


if __name__ == "__main__":
    main()
