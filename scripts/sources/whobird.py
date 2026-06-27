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
CDN = "https://cdn.download.ams.birds.cornell.edu/api/v2/asset/{aid}/" + str(WIDTH)
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


def search(sci, common, pose, limit):
    # Curated Macaulay stills are perched/standing birds — serve "sitting" only.
    if pose != "sitting":
        return []
    m = _load()
    aid = m["sci"].get((sci or "").lower()) or m["common"].get((common or "").lower())
    if not aid:
        return []
    return [Candidate(
        url=CDN.format(aid=aid), pose=pose, source="whobird",
        license="Macaulay © (reference only, not redistributed)",
        author="Macaulay Library / whoBIRD curation", src_id=str(aid),
        page_url=PAGE.format(aid=aid),
    )]
