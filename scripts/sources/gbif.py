"""GBIF adapter — federates StillImage media from many providers (iNaturalist,
Observation.org, naturgucker, museum live-bird datasets, ...) through one
keyless API, greatly widening the candidate pool for hard species.

We resolve the scientific name to a backbone taxonKey, then pull
HUMAN_OBSERVATION occurrences carrying openly-licensed photos. Restricting to
HUMAN_OBSERVATION drops PRESERVED/FOSSIL specimens, so we never feed a dead
museum skin to img2img. GBIF has no in-flight tag, so this serves "sitting".
"""
from .base import Candidate, get_json

MATCH = "https://api.gbif.org/v1/species/match"
OCC = "https://api.gbif.org/v1/occurrence/search"
# Occurrence-level license pre-filter (GBIF enum). The per-photo license can
# still differ, so we re-check each image with _norm_lic below.
LICENSES = ["CC0_1_0", "CC_BY_4_0", "CC_BY_NC_4_0"]
_key_cache = {}


def _taxon_key(sci):
    if sci in _key_cache:
        return _key_cache[sci]
    d = get_json(MATCH, {"name": sci, "strict": "true"})
    k = d.get("usageKey") if d.get("matchType") not in (None, "NONE") else None
    _key_cache[sci] = k
    return k


def _norm_lic(s):
    """Short label for licenses that permit derivatives; None to REJECT
    (No-Derivatives, all-rights-reserved, or anything unrecognised)."""
    if not s:
        return None
    t = s.lower()
    if "-nd" in t or "noderiv" in t:          # ND forbids the AI derivative
        return None
    if "publicdomain/zero" in t or t == "cc0_1_0":
        return "CC0"
    if "/by-nc-sa/" in t or "cc_by_nc_sa" in t:
        return "CC BY-NC-SA"
    if "/by-nc/" in t or "cc_by_nc_4" in t:
        return "CC BY-NC"
    if "/by-sa/" in t or "cc_by_sa" in t:
        return "CC BY-SA"
    if "/by/" in t or "cc_by_4" in t:
        return "CC BY"
    return None                                # copyright / unknown -> reject


def search(sci, common, pose, limit):
    if pose != "sitting":
        return []
    key = _taxon_key(sci)
    if not key:
        return []
    data = get_json(OCC, {
        "taxonKey": key, "mediaType": "StillImage",
        "basisOfRecord": "HUMAN_OBSERVATION", "license": LICENSES,
        "limit": min(limit * 4, 60),
    })
    out = []
    for occ in data.get("results", []):
        for m in occ.get("media", []):
            if m.get("type") and m.get("type") != "StillImage":
                continue
            url = m.get("identifier", "")
            lic = _norm_lic(m.get("license") or occ.get("license"))
            if not url or not lic:        # skip missing/ND/non-CC photos
                continue
            out.append(Candidate(
                url=url, pose=pose, source="gbif", license=lic,
                author=(m.get("rightsHolder") or m.get("creator")
                        or occ.get("recordedBy") or ""),
                src_id=str(occ.get("key", "")),
                page_url=f"https://www.gbif.org/occurrence/{occ.get('key', '')}",
            ))
            break  # one photo per occurrence
        if len(out) >= limit:
            break
    return out
