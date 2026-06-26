"""Build a species_code -> taxonomic family map from the iNaturalist taxa API.

The local taxonomy.csv has no family column, but iNat's taxon record carries the
full ancestor chain. We grab the family's scientific name and its English
common name (e.g. Phylloscopidae / "Leaf Warblers"), which the generation prompt
uses to anchor the bird to the right group — countering FLUX's bias from
ambiguous English names ("warbler" -> New-World wood-warblers).

Cached + resume-safe to scripts/families.json: {code: [family_sci, family_en]}.

Usage:
  python fetch_families.py --codes-file selected_species.txt
  python fetch_families.py --codes wlwwar,comchi1
"""
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from species import load_species  # noqa: E402
from sources.base import get_json  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "families.json")
TAXA = "https://api.inaturalist.org/v1/taxa"


def _family_from_ancestors(anc):
    for a in anc or []:
        if a.get("rank") == "family":
            return a.get("name"), a.get("preferred_common_name")
    return None, None


def family(sci):
    d = get_json(TAXA, {"q": sci, "is_active": "true", "per_page": 5})
    for r in d.get("results", []):
        if r.get("name", "").lower() == sci.lower():
            fam = _family_from_ancestors(r.get("ancestors"))
            if fam[0]:
                return fam
            dd = get_json(f"{TAXA}/{r.get('id')}", {})
            res = dd.get("results") or [{}]
            return _family_from_ancestors(res[0].get("ancestors"))
    return None, None


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--codes-file")
    g.add_argument("--codes")
    args = ap.parse_args()

    by_code = {s["code"]: s for s in load_species()}
    if args.codes_file:
        codes = [ln.split("\t")[0].strip() for ln in
                 open(args.codes_file, encoding="utf-8") if ln.strip()]
    else:
        codes = [c.strip() for c in args.codes.split(",")]

    cache = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE, encoding="utf-8"))

    todo = [c for c in codes if c not in cache and c in by_code]
    print(f"{len(cache)} cached; fetching family for {len(todo)} species")
    for i, code in enumerate(todo, 1):
        sci = by_code[code]["sci"]
        try:
            fsci, fen = family(sci)
        except Exception as e:  # noqa: BLE001
            print(f"  {code} {sci}: ERROR {e}"); fsci = fen = None
        cache[code] = [fsci, fen]
        if i % 20 == 0 or i == len(todo):
            json.dump(cache, open(CACHE, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=0)
            print(f"  {i}/{len(todo)}  last: {code} {sci} -> {fsci} / {fen}")
        time.sleep(0.4)  # be polite to the API
    json.dump(cache, open(CACHE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=0)
    miss = sum(1 for v in cache.values() if not v[0])
    print(f"done -> {CACHE} ({len(cache)} total, {miss} without family)")


if __name__ == "__main__":
    main()
