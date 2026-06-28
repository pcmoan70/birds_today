"""Backfill the species-specific prompt text ("id") into the review manifest.

regen_flagged.py started writing each review entry's "id" (the field-mark
clause from id_features.json) so the review page can show/edit it. Entries
written by earlier runs lack the field; this fills them in from
id_features.json. Idempotent — only touches entries missing "id".

Usage:  python backfill_review_ids.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REVIEW_MAN = os.path.join(ROOT, "docs", "review", "manifest.json")
IDFEATURES = os.path.join(HERE, "id_features.json")


def main():
    review = json.load(open(REVIEW_MAN, encoding="utf-8"))
    idfeat = json.load(open(IDFEATURES, encoding="utf-8"))
    n = 0
    for code, e in review.get("species", {}).items():
        if not e.get("id"):
            e["id"] = idfeat.get(code, "")
            if e["id"]:
                n += 1
    json.dump(review, open(REVIEW_MAN, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"backfilled id for {n} review entries")


if __name__ == "__main__":
    main()
