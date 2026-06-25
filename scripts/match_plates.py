"""Match extracted book plates to eBird species codes used by the app.

The plates carry 1837-era names (old Latin synonyms + OCR noise), so we match on
both the scientific name and the English common name, with light normalisation
and a fuzzy fall-back. Produces, per eBird code, the best Gould and Dresser
plate, and reports coverage of the app's selected species.

  python match_plates.py                 # report coverage
  python match_plates.py --emit --max-edge 600   # also write web images+manifest
"""
import argparse
import csv
import difflib
import json
import os
import re
import sys
import unicodedata

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PLATES = os.path.join(ROOT, "book_plates")
TAX = os.path.join(ROOT, "docs", "taxonomy.csv")
SELECTED = os.path.join(HERE, "selected_species.txt")
OUT_DIR = os.path.join(ROOT, "docs", "plates")
ALIASES = os.path.join(HERE, "plate_aliases.json")


# eBird common-name qualifiers; stripping them lets a plate's old short name
# ("Kestrel", "Blackbird") reach the modern eBird name ("Eurasian Kestrel").
_QUALIFIERS = ("eurasian", "common", "european", "great", "northern", "western",
               "greater", "spotted")


def norm(s):
    s = (s or "").lower().replace("œ", "oe").replace("æ", "ae")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\bgrey\b", "gray", s)   # eBird spelling


def _compact(s):
    """Collapse word boundaries so 'Sparrow Hawk' == 'Sparrowhawk'."""
    return s.replace(" ", "")


def load_taxonomy(allowed=None):
    """Build name->code indexes. If `allowed` (set of codes) is given, only those
    species are considered — this restricts matching to the app's species and
    removes false positives from unrelated taxa (e.g. Red Wolf, Red Warbler)."""
    sci2code, com2code = {}, {}
    stripped = {}            # qualifier-stripped common -> set(codes)
    compact = {}             # space-collapsed common -> set(codes)
    with open(TAX, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            code = r["species_code"]
            if allowed is not None and code not in allowed:
                continue
            sci, com = norm(r["sci_name"]), norm(r["com_name"])
            if sci:
                sci2code.setdefault(sci, code)
                g2 = " ".join(sci.split()[:2])
                sci2code.setdefault(g2, code)
            if com:
                com2code.setdefault(com, code)
                compact.setdefault(_compact(com), set()).add(code)
                toks = com.split()
                if len(toks) > 1 and toks[0] in _QUALIFIERS:
                    stripped.setdefault(" ".join(toks[1:]), set()).add(code)
                    compact.setdefault(_compact(" ".join(toks[1:])), set()).add(code)
    # only keep stripped/compact keys that map to exactly one species
    strip2code = {k: next(iter(v)) for k, v in stripped.items()
                  if len(v) == 1 and k not in com2code}
    compact2code = {k: next(iter(v)) for k, v in compact.items() if len(v) == 1}
    return sci2code, com2code, strip2code, compact2code


def _clean_common(com):
    """Drop OCR junk tokens, keep alphabetic words length>=2."""
    return " ".join(t for t in com.split() if len(t) >= 2)


def load_aliases(allowed=None):
    """Hand/web-verified map of OCR'd plate captions -> eBird code, for old
    scientific synonyms and archaic English names that fuzzy matching can't
    resolve (built by scripts/match_plates research, see plate_aliases.json).
    Keyed by (normalised caption_sci, cleaned caption_common)."""
    if not os.path.exists(ALIASES):
        return {}
    out = {}
    for r in json.load(open(ALIASES, encoding="utf-8")):
        if allowed is not None and r["code"] not in allowed:
            continue
        out[(r.get("sci", ""), r.get("common", ""))] = r["code"]
    return out


def match(plate, sci2code, com2code, strip2code, compact2code, sci_keys,
          com_keys, aliases=None):
    sci, com = norm(plate.get("species_sci")), norm(plate.get("species_common"))
    com = _clean_common(com)
    g2 = " ".join(sci.split()[:2]) if sci else ""
    # Verified synonym/archaic-name overrides win outright.
    if aliases:
        a = aliases.get((sci, com))
        if a:
            return a, "alias"
    # Exact matches first (most reliable), then fuzzy. A confident exact common
    # name must beat a fuzzy Latin guess: "Phalaropus hyperboreus" shares an
    # epithet with "Larus hyperboreus" (Glaucous Gull), but the caption clearly
    # reads "Red-necked Phalarope".
    if sci and sci in sci2code:
        return sci2code[sci], "sci"
    if g2 and g2 in sci2code:
        return sci2code[g2], "sci2"
    if com and com in com2code:
        return com2code[com], "common"
    if com and com in strip2code:
        return strip2code[com], "common-strip"
    if com and _compact(com) in compact2code:   # 'Sparrow Hawk' -> 'sparrowhawk'
        return compact2code[_compact(com)], "common-compact"
    # absorb OCR noise on the binomial (Faleo->Falco, Gypadtus->Gypaetus)
    if len(g2) >= 8:
        near = difflib.get_close_matches(g2, sci_keys, n=1, cutoff=0.86)
        if near:
            return sci2code[near[0]], "sci~"
    if com:
        near = difflib.get_close_matches(com, com_keys, n=1, cutoff=0.9)
        if near:
            return com2code[near[0]], "common~"
    return None, None


def read_plates(book):
    path = os.path.join(PLATES, book, "index.csv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f)
                if r.get("species_common") or r.get("species_sci")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", action="store_true",
                    help="write web-sized images + docs/plates/manifest.json")
    ap.add_argument("--max-edge", type=int, default=600,
                    help="downscale longer edge for web images")
    args = ap.parse_args()

    selected = {ln.split("\t")[0].strip() for ln in open(SELECTED, encoding="utf-8")
                if ln.strip()}
    sci2code, com2code, strip2code, compact2code = load_taxonomy(allowed=selected)
    aliases = load_aliases(allowed=selected)
    com_keys = list(com2code)
    sci_keys = [k for k in sci2code if " " in k]

    # code -> {gould: [rows], dresser: [rows]}
    by_code = {}
    stats = {}
    for book in ("gould", "dresser"):
        rows = read_plates(book)
        hit = 0
        for r in rows:
            code, how = match(r, sci2code, com2code, strip2code, compact2code,
                              sci_keys, com_keys, aliases)
            if code:
                hit += 1
                by_code.setdefault(code, {}).setdefault(book, []).append(r)
        stats[book] = (len(rows), hit)

    gould_codes = {c for c, v in by_code.items() if v.get("gould")}
    dress_codes = {c for c, v in by_code.items() if v.get("dresser")}
    either = gould_codes | dress_codes
    print("Matching plates -> eBird codes:")
    for book, (n, hit) in stats.items():
        print(f"  {book}: {hit}/{n} plates matched to a code")
    print(f"\nDistinct codes covered: gould={len(gould_codes)}, "
          f"dresser={len(dress_codes)}, either={len(either)}")
    print(f"Of the {len(selected)} app species (selected_species.txt):")
    print(f"  with a GOULD plate:   {len(gould_codes & selected)}")
    print(f"  with a DRESSER plate: {len(dress_codes & selected)}")
    print(f"  with EITHER:          {len(either & selected)}")

    if not args.emit:
        return

    from PIL import Image
    os.makedirs(OUT_DIR, exist_ok=True)
    manifest = {}
    for code, books in by_code.items():
        entry = {}
        for book in ("gould", "dresser"):
            rows = books.get(book)
            if not rows:
                continue
            # pick the plate with the highest colourfulness (most vivid/complete)
            best = max(rows, key=lambda r: float(r.get("colorfulness") or 0))
            src = os.path.join(PLATES, best["file"])
            if not os.path.exists(src):
                continue
            dst_dir = os.path.join(OUT_DIR, book)
            os.makedirs(dst_dir, exist_ok=True)
            im = Image.open(src).convert("RGBA")
            im.thumbnail((args.max_edge, args.max_edge))
            # palette-quantize (keeps alpha) to keep web images near AI-image size
            im = im.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
            im.save(os.path.join(dst_dir, f"{code}.png"), optimize=True)
            entry[book] = {
                "img": f"plates/{book}/{code}.png",
                "leaf": best["leaf"], "volume": best["volume"],
                "page_url": best["page_url"],
                "sci": best.get("species_sci", ""),
                "common": best.get("species_common", ""),
            }
        if entry:
            manifest[code] = entry
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\nEmitted {len(manifest)} species images under {OUT_DIR}")


if __name__ == "__main__":
    main()
