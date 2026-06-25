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
import csv
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from species import TAXONOMY_PATH, load_taxonomy  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BIRDS_DIR = os.path.join(os.path.dirname(HERE), "docs", "birds")
MANIFEST = os.path.join(BIRDS_DIR, "manifest.json")


def load_names(codes):
    """Multilingual common names {code: {lang: name}} for the given codes.

    Embedding these in the manifest lets the app skip the 10 MB taxonomy.csv
    download — important for free GitHub Pages bandwidth.
    """
    needed = set(codes)
    out = {}
    with open(TAXONOMY_PATH, encoding="utf-8") as f:
        r = csv.DictReader(f)
        langcol = {}
        for fld in r.fieldnames:
            if fld == "com_name":
                langcol["en"] = fld
            elif fld.startswith("common_name_"):
                langcol[fld[len("common_name_"):]] = fld
        for row in r:
            code = row.get("species_code")
            if code in needed:
                out[code] = {lg: row[col] for lg, col in langcol.items() if row.get(col)}
    return out


def detect_facing(png_path):
    """Which way the bird faces: "left" or "right".

    Heuristic: the head is the highest part of the bird, so the horizontal
    position of the top band of opaque pixels (relative to the body centroid)
    tells us the head side. Good for perched birds, best-effort for flight.
    """
    try:
        a = np.array(Image.open(png_path).convert("RGBA"))[:, :, 3] > 16
    except Exception:  # noqa: BLE001 - e.g. a file still being written
        return "right"
    ys, xs = np.where(a)
    if len(xs) < 10:
        return "right"
    y0, y1 = ys.min(), ys.max()
    band = ys <= y0 + 0.20 * (y1 - y0 + 1)      # top 20% = head region
    if band.sum() < 5:
        band = ys <= y0 + 0.4 * (y1 - y0 + 1)
    head_x = xs[band].mean()
    body_cx = xs.mean()
    return "left" if head_x < body_cx else "right"


def main():
    tax = load_taxonomy()
    codes_present = [c for c in sorted(os.listdir(BIRDS_DIR))
                    if os.path.isdir(os.path.join(BIRDS_DIR, c))]
    names = load_names(codes_present)
    manifest = {}
    no_flying = []
    for code in codes_present:
        d = os.path.join(BIRDS_DIR, code)
        pngs = sorted(f for f in os.listdir(d) if f.endswith(".png"))
        if not pngs:
            continue
        entry = {
            "sci": tax.get(code, {}).get("sci", ""),
            "common": tax.get(code, {}).get("common", ""),
            "names": names.get(code, {}),
            "stances": {}, "faces": {}, "credits": {},
        }
        for f in pngs:
            rel = f"{code}/{f}"
            stance = f.rsplit("_", 1)[0] if "_" in f else "sitting"
            entry["stances"].setdefault(stance, []).append(rel)
            entry["faces"][rel] = detect_facing(os.path.join(d, f))
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
