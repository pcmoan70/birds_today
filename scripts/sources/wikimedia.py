"""Wikimedia Commons adapter.

Everything on Commons is freely licensed (PD / CC0 / CC BY / CC BY-SA). We
prefer the species category (precise) and fall back to file search. For the
flying pose we search for in-flight files explicitly.
"""
import re

from .base import Candidate, get_json, is_blocked

API = "https://commons.wikimedia.org/w/api.php"
_TAG = re.compile(r"<[^>]+>")


def _clean(html):
    return _TAG.sub("", html or "").strip()


def _imageinfo(titles):
    """Resolve File: titles -> imageinfo (url + license + artist)."""
    if not titles:
        return []
    data = get_json(API, {
        "action": "query", "format": "json", "titles": "|".join(titles),
        "prop": "imageinfo", "iiprop": "url|extmetadata|mime", "iiurlwidth": 1280,
    })
    pages = (data.get("query") or {}).get("pages") or {}
    out = []
    for p in pages.values():
        info = (p.get("imageinfo") or [None])[0]
        if not info:
            continue
        mime = info.get("mime", "")
        if mime not in ("image/jpeg", "image/png"):
            continue
        em = info.get("extmetadata") or {}
        lic = _clean((em.get("LicenseShortName") or {}).get("value")) or "Commons"
        author = _clean((em.get("Artist") or {}).get("value"))
        out.append(Candidate(
            url=info.get("thumburl") or info.get("url"),
            pose="",  # set by caller
            source="wikimedia",
            license=lic,
            author=author,
            src_id=str(p.get("pageid", "")),
            page_url=info.get("descriptionurl", ""),
        ))
    return out


def _category_files(sci, limit):
    data = get_json(API, {
        "action": "query", "format": "json", "generator": "categorymembers",
        "gcmtitle": f"Category:{sci}", "gcmtype": "file", "gcmlimit": limit,
    })
    pages = (data.get("query") or {}).get("pages") or {}
    return [p["title"] for p in pages.values() if "title" in p]


def _search_files(query, limit):
    data = get_json(API, {
        "action": "query", "format": "json", "list": "search",
        "srsearch": query, "srnamespace": 6, "srlimit": limit,
    })
    hits = (data.get("query") or {}).get("search") or []
    return [h["title"] for h in hits]


def search(sci, common, pose, limit):
    if pose == "flying":
        # Restrict the in-flight search to this species category, so we never
        # grab unrelated "flight" images. (incategory: is a CirrusSearch op.)
        titles = _search_files(f'{sci} flight incategory:"{sci}"', limit * 4)
        if not titles:
            titles = _search_files(f'{sci} in flight', limit * 4)
    else:
        # Direct category files first (precise). Big species categories keep
        # most files in subcategories, so supplement with a search scoped to
        # the category tree — still avoids the wrong-subject problem of an
        # unscoped search (e.g. a tin can for "crane").
        titles = _category_files(sci, limit * 4)
        if len(titles) < limit * 2:
            extra = _search_files(f'{sci} incategory:"{sci}"', limit * 4)
            titles += [t for t in extra if t not in titles]
    # Drop aberrant/unhealthy/non-bird titles before fetching imageinfo.
    titles = [t for t in titles if not is_blocked(t)]
    cands = _imageinfo(titles[: limit * 3])
    for c in cands:
        c.pose = pose
    return cands[:limit]
