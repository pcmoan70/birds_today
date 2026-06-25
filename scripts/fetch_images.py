"""Fetch raw bird images per species and pose from the source adapters.

Pulls up to N images for each pose (sitting / flying), trying sources in
priority order until the quota is filled. Each image lands in
raw/<species_code>/<pose>_<i>.<ext> with a sidecar .json holding attribution.
Resume-safe: already-downloaded slots are skipped.

Usage:
  python fetch_images.py --test                 # ~12 common Nordic species
  python fetch_images.py --sci "Parus major,Grus grus"
  python fetch_images.py --all --limit 200      # first 200 model birds
  python fetch_images.py --test --per-pose 6
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Windows console is cp1252 by default

from species import load_species, resolve_sci  # noqa: E402
from sources import wikimedia, inat, flickr  # noqa: E402
from sources.base import SESSION  # noqa: E402
import rejects as rejects_mod  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw")

# Source priority per pose. PD-rich Wikimedia first; iNat fills sitting gaps.
# Flying is hard: Wikimedia in-flight search first, then Flickr-CC (needs key).
SOURCES = {
    "sitting": [wikimedia, inat, flickr],
    "flying": [wikimedia, flickr],
}

# A dozen common Nordic/Swedish birds for the first quality pass.
TEST_SCI = [
    "Parus major", "Cyanistes caeruleus", "Erithacus rubecula",
    "Turdus merula", "Fringilla coelebs", "Pica pica",
    "Sturnus vulgaris", "Passer domesticus", "Carduelis carduelis",
    "Hirundo rustica", "Grus grus", "Cygnus cygnus",
]


def _ext(url):
    u = url.lower().split("?")[0]
    return ".png" if u.endswith(".png") else ".jpg"


def _download(url, path):
    try:
        r = SESSION.get(url, timeout=30)
        if r.status_code != 200 or not r.content:
            return False
        with open(path, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"      download failed: {e}")
        return False


def _pose_files(out_dir, pose):
    return [f for f in os.listdir(out_dir)
            if f.startswith(pose + "_") and not f.endswith(".json")]


def _next_idx(files):
    """Lowest free index, so refills don't collide with surviving files."""
    used = set()
    for f in files:
        try:
            used.add(int(os.path.splitext(f)[0].split("_")[1]))
        except (IndexError, ValueError):
            pass
    i = 0
    while i in used:
        i += 1
    return i


def fetch_species(sp, per_pose):
    code = sp["code"]
    out_dir = os.path.join(RAW_DIR, code)
    os.makedirs(out_dir, exist_ok=True)
    rejected = rejects_mod.for_species(code)  # source:id keys to never re-pull
    print(f"\n{code}  {sp['sci']}  ({sp['common']})")
    for pose in ("sitting", "flying"):
        have = len(_pose_files(out_dir, pose))
        if have >= per_pose:
            print(f"  {pose}: already have {have}, skip")
            continue
        seen = set()
        got = have
        for src in SOURCES[pose]:
            if got >= per_pose:
                break
            cands = src.search(sp["sci"], sp["common"], pose, per_pose * 3)
            for c in cands:
                if got >= per_pose:
                    break
                if not c.url or c.url in seen:
                    continue
                if rejects_mod.key(c.source, c.src_id) in rejected:
                    continue  # previously downvoted -> get a different image
                seen.add(c.url)
                idx = _next_idx(_pose_files(out_dir, pose))
                fn = f"{pose}_{idx}{_ext(c.url)}"
                if _download(c.url, os.path.join(out_dir, fn)):
                    with open(os.path.join(out_dir, fn + ".json"), "w",
                              encoding="utf-8") as jf:
                        json.dump(c.meta(), jf, ensure_ascii=False, indent=2)
                    got += 1
        print(f"  {pose}: {got}/{per_pose} ({src_summary(out_dir, pose)})")


def src_summary(out_dir, pose):
    srcs = {}
    for f in os.listdir(out_dir):
        if f.startswith(pose + "_") and f.endswith(".json"):
            with open(os.path.join(out_dir, f), encoding="utf-8") as jf:
                s = json.load(jf).get("source", "?")
                srcs[s] = srcs.get(s, 0) + 1
    return ", ".join(f"{k}:{v}" for k, v in srcs.items()) or "none"


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--test", action="store_true", help="dozen Nordic species")
    g.add_argument("--sci", help="comma-separated scientific names")
    g.add_argument("--codes-file", help="file with a species code in the first "
                                        "tab-separated column per line")
    g.add_argument("--all", action="store_true", help="all model birds")
    ap.add_argument("--limit", type=int, default=0, help="cap species count")
    ap.add_argument("--per-pose", type=int, default=6)
    args = ap.parse_args()

    if args.test:
        species = resolve_sci(TEST_SCI)
    elif args.sci:
        species = resolve_sci([s for s in args.sci.split(",") if s.strip()])
    elif args.codes_file:
        by_code = {s["code"]: s for s in load_species()}
        species = []
        for line in open(args.codes_file, encoding="utf-8"):
            code = line.split("\t")[0].strip()
            if code in by_code:
                species.append(by_code[code])
    else:
        species = load_species()
    if args.limit:
        species = species[: args.limit]

    print(f"Fetching {len(species)} species, {args.per_pose}/pose -> {RAW_DIR}")
    for sp in species:
        fetch_species(sp, args.per_pose)
    print("\nDone.")


if __name__ == "__main__":
    main()
