"""Macaulay Library (eBird) reference-photo adapter.

Fetches top-rated photos for an eBird taxon code from the Macaulay Library
search API and yields CDN image URLs to use as img2img generation references.

NOTE on licensing: Macaulay photos are copyrighted. They are used here ONLY as
a private generation reference — the published artwork is a newly generated
field-guide-style plate, and the source photo is never redistributed (it stays
in scripts/raw/, which is gitignored).

NOTE on access: the media API rejects non-browser clients (HTTP 403),
especially from datacenter IPs. We send browser-like headers and prime cookies
via the catalog page; this works from a normal residential connection (your
RTX 3090 box) even when cloud IPs are blocked. If it still 403s, the caller
falls back to the CC sources (iNaturalist / Wikimedia).
"""
import time

import requests

from .base import Candidate

SEARCH = "https://search.macaulaylibrary.org/api/v2/search"
# CDN asset endpoint; size is one of thumbnail/320/480/640/900/1200/1800/2400.
ASSET = "https://cdn.download.ams.birds.cornell.edu/api/v2/asset/{id}/{size}"
CATALOG = "https://search.macaulaylibrary.org/catalog"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_session = None


def _client():
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": _UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": CATALOG,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        })
        try:
            s.get(CATALOG, timeout=20)  # prime anti-bot cookies
        except requests.RequestException:
            pass
        _session = s
    return _session


def _items(data):
    """Yield asset dicts across the response shapes the API has used."""
    if isinstance(data, dict):
        res = data.get("results")
        if isinstance(res, dict) and isinstance(res.get("content"), list):
            return res["content"]
        if isinstance(data.get("content"), list):
            return data["content"]
        if isinstance(res, list):
            return res
    if isinstance(data, list):
        return data
    return []


def _asset_id(it):
    for k in ("assetId", "catalogId", "id", "mlCatalogNumber", "catId"):
        v = it.get(k)
        if v:
            return str(v)
    return ""


def search(taxon_code, limit=4, size=1200):
    """Top-rated Macaulay photos for an eBird taxon code -> [Candidate]."""
    s = _client()
    params = {"taxonCode": taxon_code, "mediaType": "photo",
              "sort": "rating_rank_desc", "count": max(limit * 3, 12)}
    out, seen = [], set()
    for attempt in range(3):
        try:
            r = s.get(SEARCH, params=params, timeout=25)
            if r.status_code == 200:
                for it in _items(r.json()):
                    aid = _asset_id(it)
                    if not aid or aid in seen:
                        continue
                    seen.add(aid)
                    out.append(Candidate(
                        url=ASSET.format(id=aid, size=size), pose="",
                        source="macaulay",
                        license="Macaulay Library (reference only)",
                        author=(it.get("userDisplayName") or it.get("user")
                                or it.get("byUserDisplayName") or ""),
                        src_id=aid,
                        page_url=f"https://macaulaylibrary.org/asset/{aid}",
                    ))
                return out
            if r.status_code in (403, 429):
                print(f"    macaulay HTTP {r.status_code} (attempt {attempt + 1})")
                time.sleep(1.5 * (attempt + 1))
                continue
            print(f"    macaulay HTTP {r.status_code}")
            break
        except requests.RequestException as e:
            print(f"    macaulay error: {e}")
            time.sleep(1.0 * (attempt + 1))
    return out
