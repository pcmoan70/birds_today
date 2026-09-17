"""Process user image feedback and replace downvoted cutouts.

The site files each vote into a Google Drive folder as one small JSON file (see
feedback/README.md: docs/feedback.js POSTs to an Apps Script web app). Point
this job at that folder — synced to disk by Google Drive for desktop, so no API
credentials are needed — and for every image whose net downvotes clear a
threshold it:
  1. blocklists that image's source id (rejects.json) so it's never re-pulled,
  2. deletes the cutout + its raw original,
  3. fetches ONE fresh alternative for that species+pose (skipping rejects),
  4. re-cuts only the affected species and rebuilds the manifest.

Processed vote files are moved into a "processed" subfolder, which makes runs
idempotent: a vote is acted on once, so an old vote can't re-retire an image
that has already been replaced.

Usage:
  # the synced Drive folder (or set BIRD_VOTES_DIR)
  python feedback_refresh.py --votes-dir "G:/My Drive/birds_today_feedback"
  # local CSV for testing
  python feedback_refresh.py --votes-file votes.csv --threshold 1 --per-pose 4
"""
import argparse
import csv
import io
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

import build_manifest  # noqa: E402
import cutout  # noqa: E402
import rejects as rejects_mod  # noqa: E402
from fetch_images import fetch_species  # noqa: E402
from sources.base import SESSION  # noqa: E402
from species import load_species  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw")
BIRDS_DIR = os.path.join(os.path.dirname(HERE), "docs", "birds")

_IMG_KEYS = ("image", "image_id", "file", "path", "img")
_VOTE_KEYS = ("vote", "rating", "feedback", "thumb")
_HASH_KEYS = ("hash", "image_hash")
_DOWN = ("down", "downvote", "negative", "neg", "-1", "bad", "thumbsdown", "0")
_UP = ("up", "upvote", "positive", "pos", "1", "good", "thumbsup")


def _pick(row, keys):
    for k in row:
        if k and k.strip().lower() in keys:
            return (str(row[k]) or "").strip()
    return ""


def load_votes_csv(url=None, path=None):
    if url:
        r = SESSION.get(url, timeout=30)
        r.raise_for_status()
        text = r.text
    else:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    return list(csv.DictReader(io.StringIO(text)))


def load_votes_dir(path, keep=False):
    """Read vote JSON files from the synced Drive folder, oldest first.

    Each file holds one vote (docs/feedback.js -> Apps Script). Files are moved
    into <path>/processed afterwards so the next run doesn't see them again —
    the same "act once" property the old unseen-email read had. `keep` leaves
    them in place (for a dry look at what is waiting).
    """
    if not os.path.isdir(path):
        raise SystemExit(f"votes dir not found: {path}")
    files = sorted(f for f in os.listdir(path)
                   if f.lower().endswith(".json") and
                   os.path.isfile(os.path.join(path, f)))
    rows, done = [], []
    for name in files:
        full = os.path.join(path, name)
        try:
            with open(full, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"  skipping {name}: {e}")
            continue
        # One vote per file is what the sink writes; tolerate a list as well.
        rows.extend(rec if isinstance(rec, list) else [rec])
        done.append(full)
    print(f"Drive: {len(rows)} vote(s) in {len(done)} file(s) from {path}")
    if done and not keep:
        pdir = os.path.join(path, "processed")
        os.makedirs(pdir, exist_ok=True)
        for full in done:
            try:
                os.replace(full, os.path.join(pdir, os.path.basename(full)))
            except OSError as e:
                print(f"  could not move {os.path.basename(full)}: {e}")
    return rows


def tally(rows):
    """relative image path -> {"net": downs-ups, "hash": last voted hash}."""
    info = {}
    for row in rows:
        img = _pick(row, _IMG_KEYS).replace("\\", "/").lstrip("/")
        vote = _pick(row, _VOTE_KEYS).lower()
        if not img:
            continue
        rec = info.setdefault(img, {"net": 0, "hash": ""})
        h = _pick(row, _HASH_KEYS)
        if h:
            rec["hash"] = h
        if vote in _DOWN:
            rec["net"] += 1
        elif vote in _UP:
            rec["net"] -= 1
    return info


def _raw_for(code, base):
    d = os.path.join(RAW_DIR, code)
    if os.path.isdir(d):
        for f in os.listdir(d):
            if os.path.splitext(f)[0] == base and not f.endswith(".json"):
                return os.path.join(d, f)
    return None


def _rm(path):
    for p in (path, str(path) + ".json"):
        if p and os.path.exists(p):
            os.remove(p)


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def retire(img, voted_hash=""):
    """Blocklist + delete one downvoted cutout. Returns its species code."""
    parts = img.split("/")
    if len(parts) != 2 or not img.endswith(".png"):
        print(f"  skip malformed image path: {img}")
        return None
    code, fname = parts
    base = os.path.splitext(fname)[0]
    cut_png = os.path.join(BIRDS_DIR, code, fname)
    if not os.path.exists(cut_png):
        print(f"  skip {img}: already gone")
        return None
    # If the voter's hash no longer matches, this image was already replaced —
    # don't retire the newer one.
    if voted_hash and _sha256(cut_png) != voted_hash:
        print(f"  skip {img}: hash changed (already replaced)")
        return None
    sidecar = cut_png + ".json"
    if os.path.exists(sidecar):
        with open(sidecar, encoding="utf-8") as f:
            m = json.load(f)
        if rejects_mod.add(code, m.get("source", ""), m.get("src_id", "")):
            print(f"  reject {code}: {m.get('source')}:{m.get('src_id')}")
    _rm(cut_png)
    _rm(_raw_for(code, base))
    print(f"  retired {img}")
    return code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--votes-dir", default=os.environ.get("BIRD_VOTES_DIR"),
                    help="synced Drive folder of vote JSON files "
                         "(or BIRD_VOTES_DIR env)")
    ap.add_argument("--keep", action="store_true",
                    help="don't move the vote files to processed/ (dry look)")
    ap.add_argument("--votes-url", help="published CSV URL (testing)")
    ap.add_argument("--votes-file", help="local CSV path (testing)")
    ap.add_argument("--threshold", type=int, default=1,
                    help="net downvotes needed to replace an image")
    ap.add_argument("--per-pose", type=int, default=4)
    args = ap.parse_args()

    if args.votes_file or args.votes_url:
        rows = load_votes_csv(args.votes_url, args.votes_file)
    elif args.votes_dir:
        rows = load_votes_dir(args.votes_dir, keep=args.keep)
    else:
        ap.error("provide --votes-dir (or BIRD_VOTES_DIR), or --votes-file/--votes-url")
    info = tally(rows)
    targets = sorted(img for img, rec in info.items() if rec["net"] >= args.threshold)
    print(f"{len(rows)} votes, {len(targets)} images at/over threshold "
          f"{args.threshold}")
    if not targets:
        print("Nothing to do.")
        return

    affected = set()
    for img in targets:
        code = retire(img, info[img].get("hash", ""))
        if code:
            affected.add(code)
    if not affected:
        return

    by_code = {s["code"]: s for s in load_species()}
    print(f"\nRefetching alternatives for {len(affected)} species...")
    for code in sorted(affected):
        sp = by_code.get(code)
        if sp:
            fetch_species(sp, args.per_pose)
        else:
            print(f"  ! {code} not in species list, skipping refetch")

    print("\nRe-cutting affected species...")
    cutout.run(codes=sorted(affected))
    print()
    build_manifest.main()


if __name__ == "__main__":
    main()
