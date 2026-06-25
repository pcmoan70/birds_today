"""Flickr adapter — Creative-Commons photos, mainly for the flying pose.

Needs a free Flickr API key in the FLICKR_API_KEY environment variable
(https://www.flickr.com/services/apps/create/apply/). If unset, the adapter
yields nothing (the pipeline just skips it).

License IDs we allow (non-commercial site, but cutouts are *derivatives* so we
exclude No-Derivatives 3 & 6):
  1 CC BY-NC-SA  2 CC BY-NC  4 CC BY  5 CC BY-SA  7 No known copyright
  9 CC0  10 Public Domain Mark
"""
import os

from .base import Candidate, get_json

API = "https://www.flickr.com/services/rest/"
LICENSES = "1,2,4,5,7,9,10"
_LIC_LABEL = {
    "0": "All rights reserved", "1": "CC BY-NC-SA", "2": "CC BY-NC",
    "4": "CC BY", "5": "CC BY-SA", "7": "No known copyright",
    "9": "CC0", "10": "Public Domain",
}


def _key():
    return os.environ.get("FLICKR_API_KEY", "").strip()


def search(sci, common, pose, limit):
    key = _key()
    if not key:
        return []
    terms = f'{sci} in flight' if pose == "flying" else f'{sci} {common}'.strip()
    data = get_json(API, {
        "method": "flickr.photos.search", "api_key": key, "format": "json",
        "nojsoncallback": 1, "text": terms, "license": LICENSES,
        "sort": "relevance", "content_type": 1, "media": "photos",
        "per_page": min(limit * 3, 30), "safe_search": 1,
        "extras": "url_l,url_c,license,owner_name",
    })
    photos = ((data.get("photos") or {}).get("photo")) or []
    out = []
    for p in photos:
        url = p.get("url_l") or p.get("url_c")
        if not url:
            continue
        out.append(Candidate(
            url=url, pose=pose, source="flickr",
            license=_LIC_LABEL.get(str(p.get("license")), ""),
            author=p.get("ownername", ""), src_id=str(p.get("id", "")),
            page_url=f"https://www.flickr.com/photos/{p.get('owner','')}/{p.get('id','')}",
        ))
        if len(out) >= limit:
            break
    return out
