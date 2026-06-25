"""Shared types + HTTP helper for image-source adapters.

Each adapter exposes:  search(sci, common, pose, limit) -> [Candidate]
where pose is "sitting" or "flying". An adapter that can't serve a pose
returns [].
"""
import re
import time
from dataclasses import dataclass, asdict

import requests

# Reject images whose title/filename signals an atypical individual (colour
# aberration / hybrid), an unhealthy or non-living subject, or a non-bird
# artefact. We want standard-plumage, healthy, living birds.
_BLOCK = [
    # colour / plumage aberrations and hybrids
    "leucis", "leucistic", "albino", "albinis", "melanis", "xanthochro",
    "erythris", "flavis", "aberrant", "aberration", "partial albino",
    "isabelline", "hybrid", "intergrade", "morph", "variant", "abnormal",
    "colou?r aberration",
    # unhealthy / non-living / not a free bird
    "dead", "carcass", "roadkill", "road kill", "injured", "sick", "disease",
    "deformed", "deformity", "skeleton", "skull", "bones", "specimen",
    "mounted", "taxidermy", "museum", "stuffed", "captive", "cage", "aviary",
    "zoo", "falconry", "ringed", "in hand", "in-hand",
    # life stages / parts that aren't a standing/flying adult
    "chick", "nestling", "fledgling", "juvenile", "egg", "eggs", "nest",
    "feather", "plumage detail", "footprint", "pellet", "droppings",
    # non-photo artefacts (NB: keep PD plates allowed — don't block
    # "illustration"/"drawing"; those are a wanted artistic source later)
    "diagram", "distribution map", "stamp", "coin", "logo", "sculpture",
    "statue", "road sign", "label",
]
_BLOCK_RE = re.compile("|".join(_BLOCK), re.IGNORECASE)


def is_blocked(text):
    """True if a title/filename signals an atypical/unhealthy/non-bird image."""
    return bool(text) and bool(_BLOCK_RE.search(text))

# Wikimedia and iNaturalist both require a descriptive User-Agent.
UA = "BirdCalendar/0.1 (non-commercial; https://github.com/; bird image fetch)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})


@dataclass
class Candidate:
    url: str          # direct image URL to download
    pose: str         # "sitting" | "flying"
    source: str       # "wikimedia" | "inaturalist" | ...
    license: str      # short license string, e.g. "CC0", "CC BY-NC", "PD"
    author: str = ""  # attribution name (may be empty/PD)
    src_id: str = ""  # source-native id (asset/file/obs id)
    page_url: str = ""  # human-facing page for the asset

    def meta(self):
        return asdict(self)


def get_json(url, params=None, timeout=20, retries=4, backoff=1.5):
    """GET JSON with polite retry/backoff. Returns dict, or {} on failure."""
    err = None
    for attempt in range(retries):
        try:
            time.sleep(0.34)  # ~3 req/s: polite, avoids bulk-run throttling
            r = SESSION.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:  # rate limited -> wait longer
                time.sleep(2.0 * (attempt + 1))
            err = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001 - log and retry
            err = str(e)
        time.sleep(backoff * (attempt + 1))
    print(f"    get_json failed ({err}): {url}")
    return {}
