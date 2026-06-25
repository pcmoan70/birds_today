"""Extract & clean hand-coloured bird plates from out-of-copyright books.

Sources (Internet Archive, public domain) — from
docs/european_birding_books_out_of_copyright.md:
  - gould    John Gould,   The Birds of Europe                  (5 vols)
  - dresser  H. E. Dresser, A History of the Birds of Europe    (9 vols)
  - naumann  J. A. Naumann, Naturgeschichte der Vögel Deutschlands (13 parts)

For each leaf of each volume the script downloads the page scan, detects
hand-coloured plates by colourfulness (Hasler-Süsstrunk: aged paper is a
uniform tan and scores low; a coloured plate's varied hues score high),
cleans the keepers (auto-crops the paper margin and neutralises the yellow
cast), and writes the plate plus full provenance — book, volume, Internet
Archive identifier, leaf index and the canonical IA page URL — to a sidecar
JSON and a shared CSV index.

Runs entirely locally. Needs:  pip install requests pillow numpy

Usage:
  python extract_book_plates.py --book gould --limit 5      # quick sample
  python extract_book_plates.py --book dresser --volume 1
  python extract_book_plates.py --book naumann --scan       # only score leaves
  python extract_book_plates.py --book all                  # everything

Tuning: plate detection is governed by --min-colorfulness (default 16). Use
--scan first on a new book to see the score distribution and pick a threshold;
Gould's subtly-coloured plates sit ~17-19, text pages ~12-15, while the vivid
Keulemans/Naumann plates score much higher.
"""
import argparse
import csv
import io
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import requests
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "book_plates")          # local; gitignored
INDEX_CSV = os.path.join(OUT_DIR, "index.csv")

# Confirmed Internet Archive identifiers (resolved via the IA search API).
BOOKS = {
    "gould": {
        "title": "The Birds of Europe", "author": "John Gould",
        "volumes": [("v1", "birdsEuropeIGoul"), ("v2", "birdsEuropeIIGoul"),
                    ("v3", "birdsEuropeIIIGoul"), ("v4", "birdsEuropeIVGoul"),
                    ("v5", "birdsEuropeVGoul")],
    },
    "dresser": {
        "title": "A History of the Birds of Europe", "author": "H. E. Dresser",
        "volumes": [("v1", "historyofbirdsof1111dres"),
                    ("v2", "historyofbirdsof12dres"),
                    ("v3", "historyofbirdsof13dres"),
                    ("v4", "historyofbirdsof14dres"),
                    ("v5", "historyofbirdsof15dres"),
                    ("v6", "historyofbirdsof16dres"),
                    ("v7", "historyofbirdsof17dres"),
                    ("v8", "historyofbirdsof18dres"),
                    ("v9", "historyofbirdsof19dres")],
    },
    "naumann": {
        "title": "Naturgeschichte der Vögel Deutschlands",
        "author": "Johann Andreas Naumann",
        "volumes": [(f"T{i:02d}", f"johannandreasnau{i:02d}naum")
                    for i in range(1, 14)],
    },
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "BirdCalendar/0.1 (non-commercial; "
                                       "public-domain plate extraction)"})


def _get(url, timeout=40, retries=4):
    """GET with polite retry/backoff. Returns Response or None."""
    for attempt in range(retries):
        try:
            time.sleep(0.3)
            r = SESSION.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(2.0 * (attempt + 1))
        except requests.RequestException:
            pass
        time.sleep(1.0 * (attempt + 1))
    return None


def ia_metadata(identifier):
    """Leaf count + bibliographic fields for an IA item."""
    r = _get(f"https://archive.org/metadata/{identifier}")
    md = (r.json().get("metadata", {}) if r else {}) or {}
    try:
        leaves = int(md.get("imagecount") or 0)
    except (TypeError, ValueError):
        leaves = 0
    return {
        "leaves": leaves,
        "title": md.get("title", ""),
        "creator": md.get("creator", ""),
        "year": str(md.get("year", "")),
        "volume": md.get("volume", ""),
    }


def page_image_url(identifier, leaf, width):
    return f"https://archive.org/download/{identifier}/page/n{leaf}_w{width}.jpg"


def page_url(identifier, leaf):
    """Canonical, human-facing IA page reference (provenance)."""
    return f"https://archive.org/details/{identifier}/page/n{leaf}"


def fetch_image(identifier, leaf, width):
    r = _get(page_image_url(identifier, leaf, width))
    if not r or not r.content:
        return None
    try:
        return Image.open(io.BytesIO(r.content)).convert("RGB")
    except OSError:
        return None


def colorfulness(im):
    """Hasler-Süsstrunk colourfulness — high for varied hues, low for tan paper."""
    a = np.asarray(im.resize((160, 200))).astype(np.float32)
    rg = a[..., 0] - a[..., 1]
    yb = 0.5 * (a[..., 0] + a[..., 1]) - a[..., 2]
    return float(np.hypot(rg.std(), yb.std()) + 0.3 * np.hypot(rg.mean(), yb.mean()))


def clean_plate(im, margin=0.012):
    """Auto-crop the paper margin and neutralise the yellow cast.

    Paper colour is the median of a thin border frame; content is everything
    far enough from it. We crop to the content bounding box (+ a small margin)
    and white-balance so the paper reads near-white."""
    arr = np.asarray(im).astype(np.int16)
    h, w, _ = arr.shape
    b = max(6, min(h, w) // 100)
    frame = np.concatenate([arr[:b].reshape(-1, 3), arr[-b:].reshape(-1, 3),
                            arr[:, :b].reshape(-1, 3), arr[:, -b:].reshape(-1, 3)])
    paper = np.median(frame, axis=0)
    dist = np.abs(arr - paper).sum(axis=2)
    ys, xs = np.where(dist > 55)
    crop = im
    if len(xs) > 100:
        m = int(min(h, w) * margin)
        crop = im.crop((max(0, xs.min() - m), max(0, ys.min() - m),
                        min(w, xs.max() + m), min(h, ys.max() + m)))
    scale = 244.0 / np.clip(paper, 1, 255)
    carr = np.clip(np.asarray(crop).astype(np.float32) * scale, 0, 255).astype(np.uint8)
    return Image.fromarray(carr)


def _append_index(row):
    new = not os.path.exists(INDEX_CSV)
    with open(INDEX_CSV, "a", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(row))
        if new:
            wr.writeheader()
        wr.writerow(row)


def process_volume(book, vol_label, identifier, args):
    info = ia_metadata(identifier)
    leaves = info["leaves"]
    if not leaves:
        print(f"  {identifier}: no leaf count from IA, skipping")
        return 0, 0
    title = info["title"] or BOOKS[book]["title"]
    year = info["year"]
    volume = info["volume"] or vol_label
    out = os.path.join(OUT_DIR, book, identifier)
    os.makedirs(out, exist_ok=True)
    start = args.start or 0
    end = min(args.end, leaves) if args.end else leaves
    print(f"  {identifier} ({volume}) {title} — leaves {start}..{end - 1} "
          f"of {leaves}")

    saved = skipped = 0
    for leaf in range(start, end):
        if args.limit and saved >= args.limit:
            break
        dst = os.path.join(out, f"leaf_{leaf:04d}.jpg")
        if not args.scan and os.path.exists(dst):
            saved += 1  # resume: already extracted
            continue
        thumb = fetch_image(identifier, leaf, args.scan_width)
        if thumb is None:
            continue
        score = colorfulness(thumb)
        if args.scan:
            print(f"    n{leaf:>4}  colourfulness={score:5.1f}"
                  f"  {'PLATE' if score >= args.min_colorfulness else ''}")
            continue
        if score < args.min_colorfulness:
            skipped += 1
            continue
        full = fetch_image(identifier, leaf, args.width) or thumb
        clean_plate(full).save(dst, quality=90)
        prov = {
            "book": book, "title": title, "author": BOOKS[book]["author"],
            "year": year, "source": "Internet Archive", "identifier": identifier,
            "volume": volume, "leaf": leaf, "page_url": page_url(identifier, leaf),
            "image_url": page_image_url(identifier, leaf, args.width),
            "colorfulness": round(score, 1),
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        import json
        with open(dst + ".json", "w", encoding="utf-8") as jf:
            json.dump(prov, jf, ensure_ascii=False, indent=2)
        _append_index({**prov, "file": os.path.relpath(dst, OUT_DIR)})
        saved += 1
        print(f"    n{leaf:>4}  plate ({score:.1f}) -> {os.path.basename(dst)}")
    if not args.scan:
        print(f"    {identifier}: {saved} plate(s), {skipped} non-plate leaves skipped")
    return saved, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", choices=list(BOOKS) + ["all"], default="all")
    ap.add_argument("--volume", help="only this volume label (e.g. v1, T03)")
    ap.add_argument("--start", type=int, default=0, help="first leaf index")
    ap.add_argument("--end", type=int, default=0, help="last leaf index (exclusive)")
    ap.add_argument("--limit", type=int, default=0, help="max plates per volume")
    ap.add_argument("--min-colorfulness", type=float, default=16.0,
                    help="plate threshold (use --scan to calibrate)")
    ap.add_argument("--width", type=int, default=1800, help="saved plate width px")
    ap.add_argument("--scan-width", type=int, default=480,
                    help="low-res width used only for scoring")
    ap.add_argument("--scan", action="store_true",
                    help="only print colourfulness per leaf; save nothing")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    books = list(BOOKS) if args.book == "all" else [args.book]
    total_saved = total_skip = 0
    for book in books:
        print(f"\n=== {book}: {BOOKS[book]['title']} ===")
        for vol_label, identifier in BOOKS[book]["volumes"]:
            if args.volume and args.volume != vol_label:
                continue
            s, k = process_volume(book, vol_label, identifier, args)
            total_saved += s
            total_skip += k
    if not args.scan:
        print(f"\nDone. {total_saved} plate(s) saved under {OUT_DIR}; "
              f"{total_skip} non-plate leaves skipped. Index: {INDEX_CSV}")


if __name__ == "__main__":
    main()
