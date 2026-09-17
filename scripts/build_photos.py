"""Write docs/photos.json — one curated photograph per species.

The drawings are grounded on whoBIRD's curated list: one editor-picked Macaulay
photo per species. The app needs the same thing for the pictures it *shows*, but
Macaulay photos are all rights reserved, so this builds the open-licence
equivalent, preferring the best-curated source first:

  1. the **lead photograph of the species' English Wikipedia article** — chosen
     by editors, almost always a Commons quality image (typically 3-8 Mpx, by a
     named photographer under CC BY or CC BY-SA);
  2. failing that, iNaturalist's community-curated `taxon_photos`, first entry
     with a Creative Commons licence.

Both beat sampling observations at random, which is what the app did first and
why the grid was full of phone snapshots of distant birds.

Each entry keeps the photographer, the licence and a link to the photo, which is
what CC attribution requires.

  python build_photos.py                 # curate every app species (resumable)
  python build_photos.py --limit 25      # a taste first
  python build_photos.py --codes gretit1,blutit --refresh
"""
import argparse
import json
import os
import re
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SELECTED = os.path.join(HERE, "selected_species.txt")
OUT = os.path.join(ROOT, "docs", "photos.json")

API = "https://api.inaturalist.org/v1"
WIKI = "https://en.wikipedia.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
THUMB_W = 1024        # what a grid tile needs on a retina screen
S = requests.Session()
S.headers["User-Agent"] = ("BirdCalendar/0.1 (https://github.com/pcmoan70/birds_today; "
                           "non-commercial)")
PAUSE = 1.1          # iNaturalist asks for about one request a second
# Licences we may display. Anything without one is all rights reserved.
OPEN = {"cc0", "cc-by", "cc-by-sa", "cc-by-nc", "cc-by-nd",
        "cc-by-nc-sa", "cc-by-nc-nd"}


def _get(path, **params):
    for attempt in range(4):
        try:
            r = S.get(f"{API}/{path}", params=params, timeout=45)
            time.sleep(PAUSE)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 503):
                time.sleep(10 * (attempt + 1))
                continue
            return None
        except requests.RequestException:
            time.sleep(3 * (attempt + 1))
    return None


def _strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _api(url, **params):
    for attempt in range(3):
        try:
            r = S.get(url, params=dict(format="json", **params), timeout=45)
            time.sleep(0.35)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 503):
                time.sleep(5 * (attempt + 1))
                continue
            return None
        except requests.RequestException:
            time.sleep(2 * (attempt + 1))
    return None


def wikipedia_lead(sci, common):
    """The species article's lead photograph, with its Commons licence.

    Wikipedia's editors put the best available portrait at the top of the
    article, so this is a curated pick in the same spirit as whoBIRD's — and the
    file lives on Commons, openly licensed with a named author.
    """
    for title in (sci, common):
        if not title:
            continue
        d = _api(WIKI, action="query", prop="pageimages", piprop="name",
                 titles=title, redirects=1)
        pages = ((d or {}).get("query") or {}).get("pages") or {}
        name = next((p.get("pageimage") for p in pages.values() if p.get("pageimage")), None)
        if not name:
            continue
        c = _api(COMMONS, action="query", titles="File:" + name, prop="imageinfo",
                 iiprop="extmetadata|url|size|user", iiurlwidth=THUMB_W)
        cpages = ((c or {}).get("query") or {}).get("pages") or {}
        info = next((p.get("imageinfo") for p in cpages.values() if p.get("imageinfo")), None)
        if not info:
            continue
        ii = info[0]
        em = ii.get("extmetadata") or {}
        lic = (em.get("LicenseShortName", {}).get("value") or "").strip()
        # Only openly licensed files; Commons marks the rare non-free one.
        if not lic or "fair use" in lic.lower() or "non-free" in lic.lower():
            continue
        url = ii.get("thumburl") or ii.get("url")
        if not url or not re.search(r"[.](jpe?g|png)$", url.split("?")[0], re.I):
            continue
        return {
            # Commons hands back thumb.wikimedia.org for a generated
            # thumbnail; the canonical host serves the same bytes.
            "url": url.split("?")[0].replace("https://thumb.wikimedia.org/",
                                             "https://upload.wikimedia.org/"),
            "credit": "Wikimedia Commons",
            "license": lic,
            # A few files carry no Artist field though Commons still requires
            # attribution; the uploader is the credit in that case.
            "by": (_strip_html(em.get("Artist", {}).get("value"))
                   or ii.get("user") or "unknown"),
            "page": ii.get("descriptionurl") or "",
        }
    return None


def taxon_id(sci):
    """iNaturalist taxon id for an exact scientific name."""
    d = _get("taxa", q=sci, rank="species", per_page=5)
    want = sci.lower()
    for t in (d or {}).get("results", []):
        if (t.get("name") or "").lower() == want:
            return t["id"]
    return None


def curated_photo(tid):
    """The first openly licensed photo from the taxon's curated set."""
    d = _get(f"taxa/{tid}")
    res = (d or {}).get("results") or [{}]
    for entry in res[0].get("taxon_photos") or []:
        ph = entry.get("photo") or {}
        lic = (ph.get("license_code") or "").lower()
        # The "large" rendition (~1024 px) is what a grid tile needs on a
        # retina screen; medium (~500 px) reads soft once cropped to a square.
        url = ph.get("large_url") or ph.get("medium_url") or ph.get("url") or ""
        if lic in OPEN and url:
            return {
                "url": url.replace("/square.", "/large.").replace("/small.", "/large.")
                          .replace("/medium.", "/large."),
                "credit": "iNaturalist",
                "license": lic.upper(),
                "by": ph.get("attribution", ""),
                "page": f"https://www.inaturalist.org/photos/{ph.get('id', '')}",
            }
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--codes", help="comma-separated species codes")
    ap.add_argument("--refresh", action="store_true", help="redo species already stored")
    args = ap.parse_args()

    out = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    species = []
    for ln in open(SELECTED, encoding="utf-8"):
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            species.append((p[0], p[1], p[2]))
    if args.codes:
        want = {c.strip() for c in args.codes.split(",")}
        species = [s for s in species if s[0] in want]
    stored = sum(1 for s in species if s[0] in out)
    todo = [s for s in species if args.refresh or s[0] not in out]
    if args.limit:
        todo = todo[:args.limit]

    print(f"{len(todo)} species to curate ({stored} already stored)", flush=True)
    found = 0
    for n, (code, sci, common) in enumerate(todo, 1):
        rec = wikipedia_lead(sci, common)
        if not rec:                      # no article photo: iNaturalist's pick
            tid = taxon_id(sci)
            rec = curated_photo(tid) if tid else None
        if rec:
            out[code] = rec
            found += 1
        else:
            out.pop(code, None)
        print(f"  {n:3}/{len(todo)} {code:9} {common[:26]:26} "
              f"{(rec['credit'] if rec else 'none'):18} "
              f"{rec['license'] if rec else 'no CC photo':14}", flush=True)
        if n % 10 == 0 or n == len(todo):
            json.dump(out, open(OUT, "w", encoding="utf-8"),
                      ensure_ascii=False, separators=(",", ":"))

    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False,
              separators=(",", ":"))
    print(f"\n{len(out)} curated photos in {OUT} "
          f"({os.path.getsize(OUT) // 1024} kB); {found} added this run")


if __name__ == "__main__":
    main()
