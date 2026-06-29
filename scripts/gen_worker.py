"""Continuous image-generation worker.

Drains gen_queue.json highest-priority-first (feedback before coverage), loading
the FLUX pipeline once. Job kinds (see gen_queue.py):
  - challengers: keep the live champion, generate n_new fresh variant slots.
  - regen:       regenerate all 3 variant slots ("none good enough"); live kept.
  - coverage:    first-time best-of-3 for a never-generated species; sets live.

Each finished job updates docs/review/manifest.json with a fresh, unreviewed
entry (chosen = null — nothing is auto-selected) and the variants are pushed in
batches. Runs continuously, polling for new feedback; pass --drain to exit once
the queue is empty.

Usage:
  python gen_worker.py            # continuous (stays up for new feedback)
  python gen_worker.py --drain    # process what's queued, then exit
"""
import argparse
import json
import os
import sys
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8")

from rembg import new_session  # noqa: E402

import generate as G  # noqa: E402
import gen_queue as Q  # noqa: E402
import regen_flagged as R  # noqa: E402
from species import load_species  # noqa: E402

POLL = 30  # seconds between queue checks when idle (continuous mode)
# Fresh seed band for challenger suggestions (distinct from the coverage band).
CHALLENGER_SEEDS = [(3000, 0.62), (3001, 0.70), (3002, 0.66)]


def _seeds_for(job):
    kind = job["kind"]
    if kind == "challengers":
        n = max(1, min(int(job.get("n_new", 2)), len(CHALLENGER_SEEDS)))
        return CHALLENGER_SEEDS[:n], False          # variants only, live kept
    if kind == "regen":
        return R.VARIANTS, False                     # all 3 fresh, live kept
    return R.VARIANTS, True                           # coverage: 3 + set live


def process(job, pipe, sess, by_code, fams, ids):
    code = job["code"]
    pose = job.get("pose", "sitting")
    sp = by_code.get(code)
    if not sp:
        print(f"  {code}: unknown code, skip"); return False
    kind = job["kind"]
    print(f"\n[{kind}] {code}  {sp['common']}  ({job.get('reason','')})")

    vdir = os.path.join(R.REVIEW_IMGS, code); os.makedirs(vdir, exist_ok=True)
    before_rel = R.snapshot_before(code, vdir)

    # Reuse the existing local reference for feedback re-gens (gentle on the
    # Macaulay CDN); only re-fetch on coverage or an explicit bad-ref flag.
    review = json.load(open(R.REVIEW_MAN, encoding="utf-8")) if os.path.exists(R.REVIEW_MAN) else {"species": {}}
    review.setdefault("species", {})
    prev = review["species"].get(code, {})
    raw_input = os.path.join(R.RAW, code, "sitting_0.jpg")
    can_reuse = (kind != "coverage" and not job.get("refetch")
                 and os.path.exists(raw_input) and prev)
    if can_reuse:
        ref_input, refsrc = raw_input, prev.get("ref_source")
        print(f"    ref[reuse {refsrc}]: existing local reference")
    else:
        ref_input, refsrc = R.setup_reference(code, sp, sess)
        if not ref_input:
            return False

    seeds, set_live = _seeds_for(job)
    res = R.gen_best(pipe, sess, code, sp, pose, ref_input, fams, ids,
                     seed_off=int(job.get("seed_off", 0)), set_live=set_live,
                     seeds=seeds)
    if not res:
        return False

    fam = fams.get(code) or [None, None]
    # Re-read just before writing to fold in any concurrent apply_choices edits.
    review = json.load(open(R.REVIEW_MAN, encoding="utf-8")) if os.path.exists(R.REVIEW_MAN) else {"species": {}}
    review.setdefault("species", {})
    review["species"][code] = {
        "name": sp["common"], "sci": sp["sci"], "family": fam[1],
        "reason": job.get("reason", ""), "before": before_rel,
        "ref": res.get("ref"), "ref_source": refsrc, "recipe": R.RECIPE,
        "id": ids.get(code, ""),
        "chosen": None, "variants": res["variants"],
        # gen = generation stamp: when this advances, the review page knows the
        # entry is a fresh round and clears any stale picks/toggles for it.
        "gen": int(time.time()),
        "reviewed": False, "pending": False}
    json.dump(review, open(R.REVIEW_MAN, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--drain", action="store_true",
                    help="exit when the queue is empty (else poll continuously)")
    args = ap.parse_args()

    by_code = {s["code"]: s for s in load_species()}
    fams = R.load_families()
    ids = R.load_id_features()
    os.makedirs(R.BA, exist_ok=True)
    os.makedirs(os.path.dirname(R.REVIEW_MAN), exist_ok=True)

    print("Loading FLUX pipeline...")
    pipe = G.load_pipeline("black-forest-labs/FLUX.1-dev", None, fp8=True)
    sess = new_session("birefnet-general")

    done = 0
    idle = False
    while True:
        ids = R.load_id_features()   # pick up prompt edits between jobs
        job, rest = Q.pop(Q.load())
        if not job:
            if args.drain:
                print("queue empty; draining done."); break
            if not idle:
                print("queue empty; waiting for feedback..."); idle = True
            time.sleep(POLL); continue
        idle = False
        Q.save(rest)   # claim the job (remove before running)
        try:
            ok = process(job, pipe, sess, by_code, fams, ids)
        except Exception:  # noqa: BLE001
            traceback.print_exc(); ok = False
        if ok:
            done += 1
            if done % R.PUSH_EVERY == 0:
                R.push_batch(done)
    if done:
        R.push_batch(done, final=True)
    print(f"\nworker stopped; {done} jobs generated")


if __name__ == "__main__":
    main()
