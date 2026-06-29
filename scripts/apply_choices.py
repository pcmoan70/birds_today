"""Apply choices.json from the review page to the live images + generation queue.

choices.json maps each species code to an object:
  {"choice": "live"|"input"|"v0"|…, "verdict": "satisfied"|"notgood",
   "badRef": true, "id": "...", "note": "..."}
where `choice` is the reviewer's preferred image (the Current live image, the
Model input — a regeneration seed only, never published — or an AI alternative).

Binary-verdict semantics (no image is auto-selected):
  - satisfied -> finalize: an alternative pick is published as the new live
                 image; "live"/"input"/none keep the current image. Mark
                 reviewed and drop the species off the review page.
  - not good enough -> regenerate. A picked alternative (or "live") is kept as
                 the live champion and 2 fresh challengers are queued next to it;
                 with no usable pick (none, or "input") all 3 are regenerated
                 from the reference. Stays on the page (hidden until ready).
  - bad ref / prompt edit only -> queue a re-gen (re-fetch the reference for a
                 bad ref; use the edited prompt). Stays on the page.
  - bare pick / note only / nothing -> recorded; current image kept.

An edited "id" prompt is persisted to id_features.json (the img2img prompt
source of truth) and the review manifest. badRef / satisfied / notgood / notes
are also collected into scripts/review_feedback.json for the record.

Usage:
  python apply_choices.py path/to/choices.json
"""
import glob
import json
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

import gen_queue as Q  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REVIEW_IMGS = os.path.join(ROOT, "docs", "review_imgs")
BIRDS = os.path.join(ROOT, "docs", "birds")
REVIEW_MAN = os.path.join(ROOT, "docs", "review", "manifest.json")
FEEDBACK = os.path.join(HERE, "review_feedback.json")
RETRY = os.path.join(HERE, "retry_rounds.json")
IDFEATURES = os.path.join(HERE, "id_features.json")
APPLIED = os.path.join(HERE, "applied_choices.json")  # {code: last applied pick}
#   so a re-export with the same pick doesn't re-queue a bird you're happy with.


def git(*a):
    return subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True)


def set_live(code, vid):
    """Copy a chosen review variant over the live image. Returns True on success."""
    src = os.path.join(REVIEW_IMGS, code, f"{vid}.png")
    if not os.path.exists(src):
        print(f"  {code}: variant {vid} missing, skip copy"); return False
    dst = os.path.join(BIRDS, code, "sitting_0.png")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy(src, dst)
    return True


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: apply_choices.py choices.json")
    choices = json.load(open(sys.argv[1], encoding="utf-8"))
    review = json.load(open(REVIEW_MAN, encoding="utf-8")) if os.path.exists(REVIEW_MAN) else {"species": {}}
    review.setdefault("species", {})
    retry = json.load(open(RETRY, encoding="utf-8")) if os.path.exists(RETRY) else {}
    idfeat = json.load(open(IDFEATURES, encoding="utf-8")) if os.path.exists(IDFEATURES) else {}
    applied = json.load(open(APPLIED, encoding="utf-8")) if os.path.exists(APPLIED) else {}
    jobs = Q.load()

    finalized = kept = queued = unchanged = 0
    id_changed = []
    feedback = {"badRef": [], "satisfied": [], "notgood": [], "notes": {}}

    for code, val in choices.items():
        # choices.json values are objects: {choice?, verdict?, badRef?, id?, note?}
        # (older plain-string picks are treated as a bare choice).
        is_obj = isinstance(val, dict)
        choice = val.get("choice") if is_obj else val          # "live"/"input"/"vN"/None
        verdict = val.get("verdict") if is_obj else None        # "satisfied"/"notgood"/None
        badref = bool(is_obj and val.get("badRef"))
        new_id = (val.get("id") or "").strip() if is_obj else ""
        note = val.get("note") if is_obj else None
        is_var = bool(choice) and choice not in ("live", "input")  # a "vN" alternative

        if badref: feedback["badRef"].append(code)
        if verdict == "satisfied": feedback["satisfied"].append(code)
        if verdict == "notgood": feedback["notgood"].append(code)
        if note: feedback["notes"][code] = note

        # Edited prompt becomes the new img2img source of truth.
        if new_id and new_id != (idfeat.get(code) or "").strip():
            idfeat[code] = new_id
            id_changed.append(code)
            if code in review["species"]:
                review["species"][code]["id"] = new_id
        id_edited = code in id_changed

        def enqueue(kind, n_new=2, reason=""):
            nonlocal jobs, queued
            retry[code] = retry.get(code, 0) + 1
            jobs = Q.enqueue(jobs, code, kind, n_new=n_new,
                             seed_off=retry[code] * 5, refetch=badref,
                             priority=Q.FEEDBACK, reason=reason)
            # Hide from the review list until the worker produces the new images.
            if code in review["species"]:
                review["species"][code]["pending"] = True
            queued += 1

        if verdict == "satisfied":
            # Finalize with the picked image. An alternative is published as the
            # new live image; "live"/"input"/none keep the current image (the
            # model input is never published — it's all-rights-reserved).
            kept_what = "current image"
            if is_var and set_live(code, choice):
                kept_what = f"alternative {choice}"
            elif choice == "input":
                kept_what = "current image (model input not published)"
            if code in review["species"]:
                review["species"][code]["reviewed"] = True
                review["species"][code]["pending"] = False
            jobs = [j for j in jobs if j["code"] != code]
            applied[code] = f"satisfied|{choice or ''}"
            finalized += 1
            print(f"  {code}: satisfied -> finalized ({kept_what})")
            continue

        if verdict == "notgood":
            sig = f"notgood|{choice or ''}"
            # Skip a duplicate re-export of the same verdict+pick (unless a new
            # bad-ref/prompt edit makes it actionable again).
            if sig == applied.get(code) and not badref and not id_edited:
                unchanged += 1
                continue
            applied[code] = sig
            if is_var:
                # Keep the chosen alternative as the live champion + 2 challengers.
                if set_live(code, choice):
                    kept += 1
                enqueue("challengers", n_new=2, reason="notgood-keep-pick+2")
                print(f"  {code}: not good, keep {choice} -> 2 challengers (round {retry[code]})")
            elif choice == "live":
                # Champion is already the live image; just add 2 challengers.
                enqueue("challengers", n_new=2, reason="notgood-keep-live+2")
                print(f"  {code}: not good, keep live -> 2 challengers (round {retry[code]})")
            else:
                # No usable pick (or "input"): regenerate all 3 from the reference.
                enqueue("regen", reason="notgood-regen3")
                print(f"  {code}: not good -> queued full re-gen (round {retry[code]})")
            continue

        if badref or id_edited:
            enqueue("regen", reason="badref" if badref else "prompt-edit")
            print(f"  {code}: {'bad ref' if badref else 'prompt edit'} -> queued re-gen")
            continue
        # bare pick / note-only / nothing actionable: recorded; current kept.

    # Publish the queued codes so the review page hides anything awaiting (re)gen.
    review["queued"] = Q.job_codes(jobs)
    json.dump(review, open(REVIEW_MAN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(retry, open(RETRY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(applied, open(APPLIED, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    Q.save(jobs)
    if id_changed:
        json.dump(idfeat, open(IDFEATURES, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"updated id_features.json for {len(id_changed)}: " + ", ".join(id_changed))
    print(f"\nfinalized {finalized} · kept {kept} champions live · queued {queued} gen "
          f"jobs · {unchanged} unchanged (left alone)")

    # Drop review_imgs only for finalized (reviewed) species; queued ones keep
    # their folder (champion/before tiles) until the worker refreshes them.
    keep_dirs = {c for c, e in review["species"].items() if not e.get("reviewed")}
    pruned = 0
    for d in glob.glob(os.path.join(REVIEW_IMGS, "*")):
        if os.path.isdir(d) and os.path.basename(d) not in keep_dirs:
            shutil.rmtree(d, ignore_errors=True); pruned += 1
    if pruned:
        print(f"pruned {pruned} finalized/stale review_imgs dirs")

    if any(feedback[k] for k in ("badRef", "satisfied", "notgood", "notes")):
        json.dump(feedback, open(FEEDBACK, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\nfeedback -> {FEEDBACK}")
        for k in ("badRef", "satisfied", "notgood"):
            if feedback[k]:
                print(f"  {k} ({len(feedback[k])}): " + ", ".join(feedback[k]))
        for code, n in feedback["notes"].items():
            print(f"  note {code}: {n}")

    subprocess.run([sys.executable, os.path.join(HERE, "build_manifest.py")],
                   capture_output=True)
    git("add", "docs")
    if id_changed:
        git("add", "scripts/id_features.json")
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("nothing to push"); return
    git("commit", "-m", "Apply review feedback (keep champions; queue re-gens)\n\n"
        "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_01QE9YmeK2n7PbSUUJKRUAzz")
    p = git("push", "origin", "main")
    print("push:", "ok" if p.returncode == 0 else p.stderr[-200:])


if __name__ == "__main__":
    main()
