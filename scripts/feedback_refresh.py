"""Process user image feedback and replace downvoted cutouts.

Votes are emailed from the site via EmailJS to a Gmail inbox (see
feedback/README.md); each email body carries a "BIRDVOTE {json}" line. This
job reads UNSEEN vote emails over Gmail IMAP (or a CSV for testing), and for
every image whose net downvotes clear a threshold:
  1. blocklists that image's source id (rejects.json) so it's never re-pulled,
  2. deletes the cutout + its raw original,
  3. fetches ONE fresh alternative for that species+pose (skipping rejects),
  4. re-cuts only the affected species and rebuilds the manifest.

Reading UNSEEN emails (and leaving them marked Seen) makes runs idempotent — a
vote is acted on once, so a stale email can't re-retire an already-replaced image.

Designed to run unattended from GitHub Actions on a schedule.

Usage:
  # Gmail IMAP (needs an app password; or set GMAIL_USER / GMAIL_APP_PASSWORD)
  python feedback_refresh.py --gmail-user you@gmail.com --gmail-pass APPPW
  # Local CSV for testing
  python feedback_refresh.py --votes-file votes.csv --threshold 1 --per-pose 4
"""
import argparse
import csv
import email
import imaplib
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


def _email_text(msg):
    """Concatenated text/plain + text/html bodies of an email message."""
    parts = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart":
            continue
        try:
            parts.append(part.get_payload(decode=True).decode(
                part.get_content_charset() or "utf-8", "replace"))
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(parts)


_VOTE_LINE = re.compile(r"BIRDVOTE\s+(\{.*?\})")


def load_votes_imap(user, password, mailbox="INBOX", host="imap.gmail.com"):
    """Read UNSEEN vote emails, parse 'BIRDVOTE {json}' lines, mark them Seen."""
    M = imaplib.IMAP4_SSL(host)
    M.login(user, password)
    M.select(mailbox)
    typ, data = M.search(None, "UNSEEN")  # fetching below sets \Seen -> idempotent
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
    print(f"IMAP: {len(rows)} vote(s) from unseen emails")
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
    ap.add_argument("--gmail-user", default=os.environ.get("GMAIL_USER"),
                    help="Gmail address (or GMAIL_USER env)")
    ap.add_argument("--gmail-pass", default=os.environ.get("GMAIL_APP_PASSWORD"),
                    help="Gmail app password (or GMAIL_APP_PASSWORD env)")
    ap.add_argument("--votes-url", help="published CSV URL (testing)")
    ap.add_argument("--votes-file", help="local CSV path (testing)")
    ap.add_argument("--threshold", type=int, default=1,
                    help="net downvotes needed to replace an image")
    ap.add_argument("--per-pose", type=int, default=4)
    args = ap.parse_args()

    if args.votes_file or args.votes_url:
        rows = load_votes_csv(args.votes_url, args.votes_file)
    elif args.gmail_user and args.gmail_pass:
        rows = load_votes_imap(args.gmail_user, args.gmail_pass)
    else:
        ap.error("provide --gmail-user/--gmail-pass (or env), or --votes-file/--votes-url")
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
