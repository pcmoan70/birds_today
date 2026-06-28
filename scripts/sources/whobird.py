"""whoBIRD / Macaulay Library adapter — one hand-curated photo per species.

The whoBIRD Android app (woheller69/whoBIRD, GPL-3.0) ships
app/src/main/assets/assets.txt: a single curated Macaulay Library asset id
per BirdNET species, line-aligned with its English labels. We vendored that
mapping into whobird_assets.json (scientific + common name -> asset id).

These are editor-picked, whole-bird, in-focus photos — far better references
than scraped search hits — so this is the highest-quality reference source we
have. The image is fetched from Cornell's media CDN at a modest width.

LICENSING: Macaulay Library photos are copyright their photographers (all
rights reserved), NOT openly licensed. They are therefore used ONLY as
transient img2img references (the published artwork is a generated
illustration); the raw photo is never committed or redistributed. The caller
(regen_flagged.py) must not save a whoBIRD reference into the public review
folder — it keys off Candidate.source == "whobird".
"""
import json
import os

from .base import Candidate

_HERE = os.path.dirname(os.path.abspath(__file__))
_MAP_PATH = os.path.join(_HERE, "whobird_assets.json")
# Modest width: ample for a 1024px img2img init, keeps the download small.
WIDTH = 900
CDN_TMPL = "https://cdn.download.ams.birds.cornell.edu/api/v2/asset/{aid}/{w}"
PAGE = "https://macaulaylibrary.org/asset/{aid}"
_map = None


def _load():
    global _map
    if _map is None:
        try:
            _map = json.load(open(_MAP_PATH, encoding="utf-8"))
        except Exception:
            _map = {"sci": {}, "common": {}}
    return _map


# Recent taxonomic splits whose new scientific names postdate whoBIRD's curated
# asset list — map them to the (still curated) parent/sister taxon so the right
# Macaulay reference is found. Key and value are lowercase scientific names.
SCI_ALIAS = {
    "cecropis rufula": "cecropis daurica",  # European Red-rumped Swallow split
}


def _asset_id(sci, common):
    m = _load()
    s = (sci or "").lower()
    s = SCI_ALIAS.get(s, s)
    return m["sci"].get(s) or m["common"].get((common or "").lower())


def asset_url(sci, common, width=320):
    """Direct Macaulay CDN image URL for hotlinking (e.g. a review thumbnail).
    Returns None if this species has no curated asset. The photo is displayed
    by reference only — never downloaded/redistributed by us."""
    aid = _asset_id(sci, common)
    return CDN_TMPL.format(aid=aid, w=width) if aid else None


def search(sci, common, pose, limit):
    # Curated Macaulay stills are perched/standing birds — serve "sitting" only.
    if pose != "sitting":
        return []
    aid = _asset_id(sci, common)
    if not aid:
        return []
    return [Candidate(
        url=CDN_TMPL.format(aid=aid, w=WIDTH), pose=pose, source="whobird",
        license="Macaulay © (reference only, not redistributed)",
        author="Macaulay Library / whoBIRD curation", src_id=str(aid),
        page_url=PAGE.format(aid=aid),
    )]
