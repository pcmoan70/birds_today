"""Build docs/birds/manifest.json from the produced cutouts.

manifest schema (one list per stance present — sitting/takeoff/landing/flying):
  { "<species_code>": {
        "sci": str, "common": str,
        "stances": { "sitting": ["<code>/sitting_0.png", ...], "flying": [...] },
        "credits": { "<code>/sitting_0.png": {source, author, license, page_url} }
  } }
Stance is the filename prefix before "_<idx>". Only species with >=1 cutout
appear; the app flags species missing a stance rather than faking one.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from species import load_taxonomy  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BIRDS_DIR = os.path.join(os.path.dirname(HERE), "docs", "birds")
MANIFEST = os.path.join(BIRDS_DIR, "manifest.json")


def main():
    tax = load_taxonomy()
    manifest = {}
    no_flying = []
    for code in sorted(os.listdir(BIRDS_DIR)):
        d = os.path.join(BIRDS_DIR, code)
        if not os.path.isdir(d):
            continue
        pngs = sorted(f for f in os.listdir(d) if f.endswith(".png"))
        if not pngs:
            continue
        entry = {
            "sci": tax.get(code, {}).get("sci", ""),
            "common": tax.get(code, {}).get("common", ""),
            "stances": {}, "credits": {},
        }
        for f in pngs:
            rel = f"{code}/{f}"
            stance = f.rsplit("_", 1)[0] if "_" in f else "sitting"
            entry["stances"].setdefault(stance, []).append(rel)
            sc = os.path.join(d, f + ".json")
            if os.path.exists(sc):
                with open(sc, encoding="utf-8") as jf:
                    m = json.load(jf)
                entry["credits"][rel] = {
                    "source": m.get("source", ""), "author": m.get("author", ""),
                    "license": m.get("license", ""), "page_url": m.get("page_url", ""),
                }
        if "flying" not in entry["stances"]:
            no_flying.append(code)
        manifest[code] = entry

    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    # Tally stances across the set.
    counts = {}
    for e in manifest.values():
        for st in e["stances"]:
            counts[st] = counts.get(st, 0) + 1
    print(f"{len(manifest)} species -> {MANIFEST}")
    print(f"  stances: {counts}")
    if no_flying:
        print(f"  no flying: {', '.join(no_flying)}")


if __name__ == "__main__":
    main()
