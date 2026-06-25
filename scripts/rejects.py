"""Per-species reject list of source images we must never (re)download.

A reject key is "<source>:<src_id>" (e.g. "wikimedia:12345",
"inaturalist:67890"), read from each cutout's attribution sidecar. The
fetcher skips any candidate whose key is rejected, so downvoted images are
replaced by genuinely different ones rather than the same shot again.

Stored as scripts/rejects.json: { "<species_code>": ["source:id", ...] }
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REJECTS_PATH = os.path.join(HERE, "rejects.json")


def key(source, src_id):
    return f"{source}:{src_id}"


def load(path=REJECTS_PATH):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(data, path=REJECTS_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)


def add(code, source, src_id, path=REJECTS_PATH):
    """Add one reject key for a species; returns True if newly added."""
    data = load(path)
    keys = set(data.get(code, []))
    k = key(source, src_id)
    if k in keys:
        return False
    keys.add(k)
    data[code] = sorted(keys)
    save(data, path)
    return True


def for_species(code, path=REJECTS_PATH):
    return set(load(path).get(code, []))
