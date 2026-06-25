"""Extract & clean hand-coloured bird plates from out-of-copyright books.

Sources (Internet Archive, public domain) — from
docs/european_birding_books_out_of_copyright.md:
  - gould    John Gould,   The Birds of Europe                  (5 vols)
  - dresser  H. E. Dresser, A History of the Birds of Europe    (9 vols)
  - naumann  J. A. Naumann, Naturgeschichte der Vögel Deutschlands (13 parts)

For each leaf of each volume the script downloads the page scan, detects
hand-coloured plates by colourfulness (Hasler-Süsstrunk: aged paper is a
uniform tan and scores low; a coloured plate's varied hues score high), then
for each plate:
  - cleans it (auto-crops the paper margin, neutralises the yellow cast),
  - makes the paper TRANSPARENT (saved as PNG), keeping the caption text on a
    rounded white panel so it stays legible on any background,
  - saves the caption crop and OCRs it for the SPECIES (common + scientific),
  - records full provenance — book, volume, Internet Archive identifier, leaf
    index, canonical IA page URL, species and raw caption — in a sidecar JSON
    and a per-book CSV (book_plates/<book>/index.csv).

Work is spread across CPU cores (ProcessPoolExecutor). Runs entirely locally.
  pip install requests pillow numpy
  # species OCR (optional but recommended): install Tesseract + `pip install
  # pytesseract`, OR `pip install easyocr`. Without it, plates + caption crops
  # are still saved; run --ocr-only later to backfill species (no re-download).

Usage:
  python extract_book_plates.py --book gould --limit 5      # quick sample
  python extract_book_plates.py --book dresser --volume v1
  python extract_book_plates.py --book naumann --scan       # only score leaves
  python extract_book_plates.py --book all --workers 6      # everything
  python extract_book_plates.py --book all --ocr-only       # backfill species

Tuning: plate detection is governed by --min-colorfulness (default 16). Use
--scan first on a new book to see the score distribution and pick a threshold;
Gould's subtly-coloured plates sit ~17-19, text pages ~12-15, while the vivid
Keulemans/Naumann plates score much higher.
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from functools import partial

import numpy as np
import requests
from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, "book_plates")          # local; gitignored


def book_csv(book):
    """One references CSV per book (book_plates/<book>/index.csv)."""
    return os.path.join(OUT_DIR, book, "index.csv")


CSV_FIELDS = ["book", "title", "author", "year", "source", "identifier",
              "volume", "leaf", "species_common", "species_sci", "caption_text",
              "page_url", "image_url", "colorfulness", "file", "label_file",
              "saved_at"]

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


def _rounded_mask(size, box, radius):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle(box, radius=radius, fill=255)
    return np.asarray(m) > 0


def rounded_label(rect_rgb, pad=12, radius_frac=0.18):
    """The caption as a standalone label graphic: white rounded panel with the
    caption text, transparent outside the rounded corners (RGBA PNG)."""
    w, h = rect_rgb.size
    bw, bh = w + 2 * pad, h + 2 * pad
    base = Image.new("RGB", (bw, bh), (255, 255, 255))
    base.paste(rect_rgb, (pad, pad))
    out = base.convert("RGBA")
    mask = Image.new("L", (bw, bh), 0)
    rad = max(8, int(min(bw, bh) * radius_frac))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=rad, fill=255)
    out.putalpha(mask)
    return out


def detect_caption_box(rgb, caption_zone=0.22):
    """Bounding box of the black, low-saturation caption text in the bottom
    band, or None. Coloured illustration (which has saturation) is ignored."""
    arr = np.asarray(rgb).astype(np.float32)
    h, w, _ = arr.shape
    lum = arr.mean(2)
    mx, mn = arr.max(2), arr.min(2)
    sat = (mx - mn) / np.clip(mx, 1, None)
    text = (lum < 135) & (sat < 0.14)
    text[:int(h * (1 - caption_zone)), :] = False
    ys, xs = np.where(text)
    if len(xs) < 150:
        return None
    pad = max(6, int(h * 0.015))
    return (max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad),
            min(w - 1, int(xs.max()) + pad), min(h - 1, int(ys.max()) + pad))


def to_transparent(rgb, box):
    """Paper -> transparent; the illustration stays opaque; the caption text
    sits on a rounded white panel (box from detect_caption_box) so it stays
    legible on any background."""
    arr = np.asarray(rgb).astype(np.float32)
    h, w, _ = arr.shape
    lum = arr.mean(2)
    mx, mn = arr.max(2), arr.min(2)
    sat = (mx - mn) / np.clip(mx, 1, None)
    # near-white paper -> transparent, ink/dark -> opaque ...
    alpha = np.clip((236.0 - lum) / 30.0, 0, 1)
    # ... but keep coloured washes opaque even when fairly light
    alpha = np.maximum(alpha, np.clip((sat - 0.12) / 0.18, 0, 1))
    a8 = (alpha * 255).astype(np.uint8)
    if box:
        rad = max(8, int((box[3] - box[1]) * 0.25))
        panel = _rounded_mask((w, h), box, rad)
        a8[panel] = 255                       # opaque rounded label panel
        arr[panel & (lum > 195)] = 255        # clean white behind the text
    return Image.fromarray(np.dstack([arr.astype(np.uint8), a8]), "RGBA")


# ---- OCR: read the species off the caption panel --------------------------
_OCR = None  # cached engine: ("tess", module) | ("easy", reader) | False


def _ocr_engine():
    """Pick an available OCR engine once: pytesseract (+ Tesseract binary) if
    present, else easyocr (CPU), else False (caption crops are still saved so
    species can be backfilled later with --ocr-only)."""
    global _OCR
    if _OCR is not None:
        return _OCR
    try:
        import pytesseract
        from shutil import which
        if not which("tesseract"):
            for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                      r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                      os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR"
                                         r"\tesseract.exe")):
                if os.path.exists(p):
                    pytesseract.pytesseract.tesseract_cmd = p
                    break
        pytesseract.get_tesseract_version()
        _OCR = ("tess", pytesseract)
    except Exception:  # noqa: BLE001
        try:
            import easyocr
            _OCR = ("easy", easyocr.Reader(["en"], gpu=False, verbose=False))
        except Exception:  # noqa: BLE001
            _OCR = False
    return _OCR


def ocr_text(img):
    eng = _ocr_engine()
    if not eng:
        return ""
    if img.mode == "RGBA":  # flatten the rounded label onto white for OCR
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        img = bg
    if img.width < 900:
        img = img.resize((900, round(img.height * 900 / img.width)))
    kind, obj = eng
    try:
        if kind == "tess":
            return obj.image_to_string(img)
        return "\n".join(obj.readtext(np.asarray(img.convert("RGB")),
                                      detail=0, paragraph=True))
    except Exception:  # noqa: BLE001
        return ""


# Engraver/printer credit lines to ignore when looking for the species name.
_CREDIT = re.compile(r"drawn|nature|on stone|printed|hullmandel|lith|pinx|"
                     r"\bimp\b|\bdel\b|\bsc\b|sculp", re.I)
# Genus + epithet; the epithet may be capitalised in 19th-c. patronyms
# (e.g. "Aquila Bonelli"), so allow either case for the second word.
_BINOMIAL = re.compile(r"\b([A-Z][a-zëïäöüæ]{2,})\s+([A-Za-zëïäöüæ]{3,})\b")


def parse_species(text):
    """Best-effort (common, scientific) from caption OCR, using line order:
    an ALL-CAPS common name, then the binomial on a following line. Credit
    lines are skipped. Raw caption text is always kept too, so a mis-parse is
    never lossy."""
    lines = [l.strip(" .,•·") for l in text.splitlines() if l.strip()]
    lines = [l for l in lines if not _CREDIT.search(l)]
    common = sci = ""
    ci = -1
    for i, ln in enumerate(lines):
        letters = [c for c in ln if c.isalpha()]
        if len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
            common, ci = ln.title(), i
            break
    for ln in (lines[ci + 1:ci + 4] if ci >= 0 else lines):
        m = _BINOMIAL.search(ln)
        if m:
            sci = f"{m.group(1)} {m.group(2)}"
            break
    return common, sci


def _leaf_png(out, leaf):
    return os.path.join(out, f"leaf_{leaf:04d}.png")


def process_leaf(cfg, leaf):
    """Worker (runs in a separate process): download, score, and — for a
    plate — clean, make transparent, save the PNG + a caption-panel crop.
    Returns the provenance dict (species filled later in the main process),
    a scan dict, or None."""
    ident = cfg["identifier"]
    thumb = fetch_image(ident, leaf, cfg["scan_width"])
    if thumb is None:
        return None
    score = colorfulness(thumb)
    if cfg["scan"]:
        return {"scan": True, "identifier": ident, "leaf": leaf,
                "colorfulness": round(score, 1),
                "plate": score >= cfg["min_colorfulness"]}
    if score < cfg["min_colorfulness"]:
        return None
    full = fetch_image(ident, leaf, cfg["width"]) or thumb
    plate = clean_plate(full)
    if plate.width > cfg["width"]:
        plate = plate.resize((cfg["width"],
                              round(plate.height * cfg["width"] / plate.width)))
    base = _leaf_png(cfg["out"], leaf)[:-4]
    box = detect_caption_box(plate)
    to_transparent(plate, box).save(base + ".png", optimize=True)
    label_file = ""
    if box:
        rounded_label(plate.crop(box)).save(base + "_label.png")
        label_file = os.path.relpath(base + "_label.png", OUT_DIR)
    return {
        "book": cfg["book"], "title": cfg["title"], "author": cfg["author"],
        "year": cfg["year"], "source": "Internet Archive", "identifier": ident,
        "volume": cfg["volume"], "leaf": leaf,
        "species_common": "", "species_sci": "", "caption_text": "",
        "page_url": page_url(ident, leaf),
        "image_url": page_image_url(ident, leaf, cfg["width"]),
        "colorfulness": round(score, 1),
        "file": os.path.relpath(base + ".png", OUT_DIR),
        "label_file": label_file,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def fill_species(prov):
    """OCR the saved caption crop -> species_common / species_sci / caption_text."""
    cap = prov.get("label_file")
    if cap and os.path.exists(os.path.join(OUT_DIR, cap)):
        txt = ocr_text(Image.open(os.path.join(OUT_DIR, cap)))
        if txt.strip():
            prov["species_common"], prov["species_sci"] = parse_species(txt)
            prov["caption_text"] = " ".join(txt.split())
    return prov


def write_book_csv(book, rows, mode="a"):
    path = book_csv(book)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    header = mode == "w" or not os.path.exists(path)
    with open(path, mode, newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if header:
            wr.writeheader()
        for r in sorted(rows, key=lambda r: (r.get("identifier", ""), r.get("leaf", 0))):
            wr.writerow({k: r.get(k, "") for k in CSV_FIELDS})
    return path


def process_volume(book, vol_label, identifier, args, pool):
    info = ia_metadata(identifier)
    leaves = info["leaves"]
    if not leaves:
        print(f"  {identifier}: no leaf count from IA, skipping")
        return []
    out = os.path.join(OUT_DIR, book, identifier)
    os.makedirs(out, exist_ok=True)
    cfg = {"identifier": identifier, "book": book,
           "title": info["title"] or BOOKS[book]["title"],
           "author": BOOKS[book]["author"], "year": info["year"],
           "volume": info["volume"] or vol_label, "out": out,
           "scan_width": args.scan_width, "width": args.width,
           "min_colorfulness": args.min_colorfulness, "scan": args.scan}
    start = args.start or 0
    end = min(args.end, leaves) if args.end else leaves
    todo = [n for n in range(start, end)
            if args.scan or not os.path.exists(_leaf_png(out, n))]
    if args.limit:
        todo = todo[:args.limit]
    print(f"  {identifier} ({cfg['volume']}) — {len(todo)} leaf(s) to do of {leaves}")
    results = []
    futs = [pool.submit(process_leaf, cfg, n) for n in todo]
    for fu in as_completed(futs):
        r = fu.result()
        if r:
            results.append(r)
    return results


def ocr_backfill(book):
    """OCR caption crops of already-extracted plates and (re)write the CSV.
    Lets the heavy download/clean run without an OCR engine, then fill species
    once one is installed — no re-downloading."""
    base = os.path.join(OUT_DIR, book)
    rows = []
    for root, _, files in os.walk(base):
        for fn in files:
            if not fn.endswith(".png.json"):
                continue
            p = os.path.join(root, fn)
            with open(p, encoding="utf-8") as jf:
                prov = json.load(jf)
            fill_species(prov)
            with open(p, "w", encoding="utf-8") as jf:
                json.dump(prov, jf, ensure_ascii=False, indent=2)
            rows.append(prov)
    path = write_book_csv(book, rows, mode="w")
    got = sum(1 for r in rows if r.get("species_sci") or r.get("species_common"))
    print(f"  {book}: OCR-backfilled {len(rows)} plate(s), species on {got} "
          f"-> {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", choices=list(BOOKS) + ["all"], default="all")
    ap.add_argument("--volume", help="only this volume label (e.g. v1, T03)")
    ap.add_argument("--start", type=int, default=0, help="first leaf index")
    ap.add_argument("--end", type=int, default=0, help="last leaf index (exclusive)")
    ap.add_argument("--limit", type=int, default=0, help="max leaves per volume (testing)")
    ap.add_argument("--min-colorfulness", type=float, default=16.0,
                    help="plate threshold (use --scan to calibrate)")
    ap.add_argument("--width", type=int, default=1000, help="saved plate width px")
    ap.add_argument("--scan-width", type=int, default=480,
                    help="low-res width used only for scoring")
    ap.add_argument("--workers", type=int, default=min(6, (os.cpu_count() or 4)),
                    help="parallel worker processes")
    ap.add_argument("--scan", action="store_true",
                    help="only print colourfulness per leaf; save nothing")
    ap.add_argument("--ocr-only", action="store_true",
                    help="skip downloads; OCR existing caption crops -> CSV")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    books = list(BOOKS) if args.book == "all" else [args.book]

    if args.ocr_only:
        if not _ocr_engine():
            print("No OCR engine found (install Tesseract+pytesseract, or "
                  "`pip install easyocr`).")
        for book in books:
            ocr_backfill(book)
        return

    if not args.scan and not _ocr_engine():
        print("NOTE: no OCR engine found — plates + caption crops will be saved, "
              "but species columns stay blank. Install Tesseract+pytesseract or "
              "`pip install easyocr`, then run with --ocr-only to backfill species.\n")

    total = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for book in books:
            print(f"\n=== {book}: {BOOKS[book]['title']} (workers={args.workers}) ===")
            results = []
            for vol_label, identifier in BOOKS[book]["volumes"]:
                if args.volume and args.volume != vol_label:
                    continue
                results += process_volume(book, vol_label, identifier, args, pool)
            if args.scan:
                for r in sorted(results, key=lambda r: (r["identifier"], r["leaf"])):
                    print(f"    {r['identifier']} n{r['leaf']:>4}  "
                          f"{r['colorfulness']:5.1f}  {'PLATE' if r['plate'] else ''}")
                continue
            plates = [fill_species(r) for r in results]
            for prov in plates:  # write each plate's provenance sidecar
                with open(os.path.join(OUT_DIR, prov["file"]) + ".json", "w",
                          encoding="utf-8") as jf:
                    json.dump(prov, jf, ensure_ascii=False, indent=2)
            path = write_book_csv(book, plates)
            named = sum(1 for r in plates if r.get("species_sci") or r.get("species_common"))
            print(f"  {book}: {len(plates)} new plate(s), species on {named} "
                  f"-> {path}")
            total += len(plates)
    if not args.scan:
        print(f"\nDone. {total} new plate(s) under {OUT_DIR}.")


if __name__ == "__main__":
    main()
