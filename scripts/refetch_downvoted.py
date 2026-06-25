"""Regenerate downvoted bird plates from fresh Macaulay Library references.

Pipeline (run on the RTX 3090 box):
  1. Read the downvote feedback emails (Gmail IMAP, subject contains
     "downvote"); each body carries a "BIRDVOTE {json}" line with the image,
     eBird code, scientific + common name, and pose.
  2. For each downvoted plate, fetch a fresh Macaulay Library reference photo
     for that species (by eBird taxon code), skipping any asset already used
     for that species (so repeated downvotes cycle to a different photo). If
     Macaulay is unavailable, fall back to the CC sources (iNaturalist /
     Wikimedia) so the loop still works.
  3. Save the reference into scripts/raw/ and immediately regenerate that exact
     pose+index with FLUX (the model loads once), then rebuild the manifest.

Reading only UNSEEN emails (and leaving them Seen) makes runs idempotent.

Usage:
  # needs an app password; or set GMAIL_USER / GMAIL_APP_PASSWORD
  python refetch_downvoted.py --gmail-user you@gmail.com --gmail-pass APPPW
  python refetch_downvoted.py --dry-run                 # list targets only
  python refetch_downvoted.py --image hoocro1/flying_0.png  # regen one, no email
"""
import argparse
import email
import imaplib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import build_manifest  # noqa: E402
import rejects as rejects_mod  # noqa: E402
from feedback_refresh import _VOTE_LINE, _email_text  # noqa: E402
from fetch_images import _download, _ext  # noqa: E402
from species import load_species  # noqa: E402
from sources import inat, macaulay, wikimedia  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw")
BIRDS_DIR = os.path.join(os.path.dirname(HERE), "docs", "birds")
PROMPTS = os.path.join(HERE, "species_prompts.json")

# Which reference pose grounds each output stance (mirrors generate.STANCES,
# kept here so listing targets doesn't import torch).
REF_POSE = {"sitting": "sitting", "takeoff": "sitting",
            "landing": "flying", "flying": "flying"}
# CC fallback sources per reference pose, when Macaulay can't be reached.
FALLBACK = {"sitting": [inat, wikimedia], "flying": [wikimedia]}


def read_downvotes(user, password, subject="downvote", mailbox="INBOX",
                   host="imap.gmail.com"):
    """UNSEEN emails whose subject matches `subject`, parsed BIRDVOTE blobs."""
    M = imaplib.IMAP4_SSL(host)
    M.login(user, password)
    M.select(mailbox)
    typ, data = M.search(None, "UNSEEN", "SUBJECT", subject)  # fetch sets \Seen
    rows = []
    for num in (data[0].split() if data and data[0] else []):
        typ, msgdata = M.fetch(num, "(RFC822)")
        if typ != "OK" or not msgdata or not msgdata[0]:
            continue
        msg = email.message_from_bytes(msgdata[0][1])
        for m in _VOTE_LINE.finditer(_email_text(msg)):
            try:
                rows.append(json.loads(m.group(1)))
            except json.JSONDecodeError:
                pass
    M.logout()
    print(f"IMAP: {len(rows)} downvote blob(s) from unseen '{subject}' emails")
    return rows


def parse_target(row):
    """A downvote blob -> {code, pose, idx, sci, common} or None."""
    img = str(row.get("image") or "").replace("\\", "/").lstrip("/")
    if str(row.get("vote", "downvote")).lower() not in ("down", "downvote"):
        return None
    parts = img.split("/")
    if len(parts) != 2 or not img.endswith(".png"):
        print(f"  skip malformed image: {img}")
        return None
    code = row.get("species") or parts[0]
    base = os.path.splitext(parts[1])[0]
    try:
        pose, idx = base.rsplit("_", 1)
        idx = int(idx)
    except ValueError:
        print(f"  skip malformed name: {img}")
        return None
    if pose not in REF_POSE:
        print(f"  skip unknown pose '{pose}': {img}")
        return None
    return {"code": code, "pose": pose, "idx": idx,
            "sci": row.get("sci", ""), "common": row.get("common", "")}


def _clear_ref(code, refpose, idx):
    """Remove any existing raw reference at this index; reject its asset id so
    the next fetch picks a different photo. Returns nothing."""
    d = os.path.join(RAW_DIR, code)
    if not os.path.isdir(d):
        return
    stem = f"{refpose}_{idx}"
    for f in os.listdir(d):
        if os.path.splitext(f)[0] != stem:
            continue
        p = os.path.join(d, f)
        if f.endswith(".json"):
            try:
                with open(p, encoding="utf-8") as jf:
                    m = json.load(jf)
                if m.get("source") and m.get("src_id"):
                    if rejects_mod.add(code, m["source"], m["src_id"]):
                        print(f"    reject {m['source']}:{m['src_id']}")
            except (OSError, json.JSONDecodeError):
                pass
        os.remove(p)


def fetch_reference(code, sci, common, refpose, idx):
    """Download one fresh reference into raw/<code>/<refpose>_<idx>.<ext>.

    Tries Macaulay first (by eBird code), then the CC fallback sources. Skips
    assets already rejected for this species. Returns the saved path or None."""
    out_dir = os.path.join(RAW_DIR, code)
    os.makedirs(out_dir, exist_ok=True)
    _clear_ref(code, refpose, idx)
    rejected = rejects_mod.for_species(code)

    cands = macaulay.search(code, limit=6)
    if not cands:
        print("    macaulay: no results, trying CC fallback")
        for src in FALLBACK[refpose]:
            cands = src.search(sci, common, refpose, 8)
            if cands:
                break

    for c in cands:
        if not c.url or rejects_mod.key(c.source, c.src_id) in rejected:
            continue
        fn = f"{refpose}_{idx}{_ext(c.url)}"
        path = os.path.join(out_dir, fn)
        if _download(c.url, path):
            with open(path + ".json", "w", encoding="utf-8") as jf:
                json.dump(c.meta(), jf, ensure_ascii=False, indent=2)
            print(f"    reference: {c.source}:{c.src_id} -> {fn}")
            return path
    print("    no usable reference found")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gmail-user", default=os.environ.get("GMAIL_USER"))
    ap.add_argument("--gmail-pass", default=os.environ.get("GMAIL_APP_PASSWORD"))
    ap.add_argument("--subject", default="downvote",
                    help="only read emails whose subject contains this")
    ap.add_argument("--image", action="append", default=[],
                    help="regenerate this image directly (e.g. hoocro1/flying_0.png); "
                         "repeatable, skips email")
    ap.add_argument("--size", type=int, default=1200, help="Macaulay asset size")
    ap.add_argument("--dry-run", action="store_true",
                    help="list targets and exit (no fetch, no generation)")
    ap.add_argument("--model", default="black-forest-labs/FLUX.1-dev")
    ap.add_argument("--style", default="fieldguide",
                    help="art style to regenerate in (see generate.STYLES)")
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--guidance", type=float, default=3.5)
    ap.add_argument("--no-fp8", action="store_true")
    args = ap.parse_args()

    if args.image:
        rows = [{"image": img, "vote": "downvote"} for img in args.image]
    elif args.gmail_user and args.gmail_pass:
        rows = read_downvotes(args.gmail_user, args.gmail_pass, args.subject)
    else:
        ap.error("provide --gmail-user/--gmail-pass (or env), or --image")

    # De-dupe to one regeneration per (code, pose, idx).
    targets, seen = [], set()
    for row in rows:
        t = parse_target(row)
        if not t:
            continue
        k = (t["code"], t["pose"], t["idx"])
        if k not in seen:
            seen.add(k)
            targets.append(t)

    print(f"{len(targets)} plate(s) to regenerate:")
    for t in targets:
        print(f"  {t['code']}/{t['pose']}_{t['idx']}  {t['common']}")
    if not targets or args.dry_run:
        return

    # Names/field marks for the prompt (prefer the species list, fall back to
    # the values the browser sent in the vote blob).
    by_code = {s["code"]: s for s in load_species()}
    marks = json.load(open(PROMPTS, encoding="utf-8"))

    import generate  # heavy (torch); only import once we have work to do
    print(f"\nLoading {args.model} (fp8={not args.no_fp8})...")
    pipe = generate.load_pipeline(args.model, fp8=not args.no_fp8)
    from rembg import new_session
    rembg_session = new_session("birefnet-general")

    done = 0
    for t in targets:
        code, pose, idx = t["code"], t["pose"], t["idx"]
        sp = by_code.get(code, {})
        common = sp.get("common") or t["common"] or code
        sci = sp.get("sci") or t["sci"]
        refpose = REF_POSE[pose]
        print(f"\n{code}/{pose}_{idx}  {common}")
        ref = fetch_reference(code, sci, common, refpose, idx)
        if not ref:
            continue
        png = generate.render_one(pipe, rembg_session, common, sci,
                                  marks.get(code, ""), pose, ref, idx,
                                  os.path.join(BIRDS_DIR, code), model=args.model,
                                  steps=args.steps, guidance=args.guidance,
                                  style=args.style)
        if png:
            print(f"  regenerated {pose}_{idx}.png")
            done += 1
        else:
            print(f"  {pose}_{idx}: cutout failed")

    if done:
        print(f"\nRegenerated {done} plate(s); rebuilding manifest...")
        build_manifest.main()
    print("\nDone.")


if __name__ == "__main__":
    main()
