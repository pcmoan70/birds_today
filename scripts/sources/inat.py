"""iNaturalist adapter.

Research-grade observations, open licenses only (site is non-commercial, so
CC0 / CC BY / CC BY-NC are all fine). We resolve the species to a taxon_id
first (precise — avoids fuzzy name matches), then pull that taxon's
best-voted observation photos. iNat has no reliable in-flight tag, so this
adapter only serves the "sitting" pose.
"""
from .base import Candidate, get_json

OBS_API = "https://api.inaturalist.org/v1/observations"
TAXA_API = "https://api.inaturalist.org/v1/taxa"
LICENSES = "cc0,cc-by,cc-by-nc"
_LIC_LABEL = {
    "cc0": "CC0", "cc-by": "CC BY", "cc-by-nc": "CC BY-NC",
    "cc-by-sa": "CC BY-SA", "cc-by-nc-sa": "CC BY-NC-SA",
}
_taxon_cache = {}


def _taxon_id(sci):
    if sci in _taxon_cache:
        return _taxon_cache[sci]
    data = get_json(TAXA_API, {"q": sci, "is_active": "true", "per_page": 10})
    tid = None
    want = sci.lower()
    for r in data.get("results") or []:
        if r.get("name", "").lower() == want:  # exact match anywhere in results
            tid = r.get("id")
            break
    _taxon_cache[sci] = tid
    return tid


def search(sci, common, pose, limit):
    if pose != "sitting":
        return []
    tid = _taxon_id(sci)
    if not tid:
        return []
    # NB: no order_by=votes — faves bias hard toward striking/aberrant birds
    # (leucistic, rare morphs). We want typical individuals, so take a plain
    # research-grade sample of wild, living birds in standard plumage.
    # captive=false drops cage/aviary birds. We don't require the "Alive"
    # annotation — most observations are unannotated, so requiring it zeroes
    # out coverage; research-grade wild birds are living in practice anyway.
    data = get_json(OBS_API, {
        "taxon_id": tid, "quality_grade": "research",
        "photo_license": LICENSES, "per_page": min(limit * 4, 40),
        "photos": "true", "captive": "false",
    })
    out = []
    for obs in data.get("results", []):
        for ph in obs.get("photos", []):
            url = ph.get("url", "")
            if not url:
                continue
            big = url.replace("/square.", "/large.").replace("/small.", "/large.")
            lic = _LIC_LABEL.get(ph.get("license_code"), ph.get("license_code") or "")
            out.append(Candidate(
                url=big, pose=pose, source="inaturalist", license=lic,
                author=ph.get("attribution", ""), src_id=str(ph.get("id", "")),
                page_url=f"https://www.inaturalist.org/observations/{obs.get('id','')}",
            ))
            break  # one photo per observation
        if len(out) >= limit:
            break
    return out
