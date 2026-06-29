"""Apply choices.json from the review page to the live images + generation queue.

choices.json maps each species code to either:
  - a variant id string, e.g. {"comchi1": "v2"}, or
  - an object {"choice": "v2", "badRef": true, "noneGood": true,
               "satisfied": true, "id": "...", "note": "..."}.

Iterative-review semantics (no variant is auto-selected):
  - satisfied        -> finalize: keep the chosen variant live, mark reviewed,
                        drop the species off the review page. No regeneration.
  - none good enough -> keep the current live image; queue a full re-gen (3 fresh
                        variants). Stays on the page.
  - pick a variant   -> keep it as the new live "champion"; queue 2 fresh
                        challenger suggestions next to it. Stays on the page.
  - bad ref / prompt edit only -> queue a re-gen (re-fetch the reference for a
                        bad ref; use the edited prompt). Stays on the page.
  - note only / nothing -> recorded; current image kept, stays on the page.

An edited "id" prompt is persisted to id_features.json (the img2img prompt
source of truth) and the review manifest. badRef / noneGood / satisfied / notes
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
    feedback = {"badRef": [], "noneGood": [], "satisfied": [], "notes": {}}

    for code, val in choices.items():
        is_obj = isinstance(val, dict)
        choice = val.get("choice") if is_obj else val          # may be None
        none_good = bool(is_obj and val.get("noneGood"))
        satisfied = bool(is_obj and val.get("satisfied"))
        badref = bool(is_obj and val.get("badRef"))
        new_id = (val.get("id") or "").strip() if is_obj else ""
        note = val.get("note") if is_obj else None

        if badref: feedback["badRef"].append(code)
        if none_good: feedback["noneGood"].append(code)
        if satisfied: feedback["satisfied"].append(code)
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
            queued += 1

        if satisfied:
            # Finalize. If an alternative was selected, it becomes the live image;
            # otherwise the current/live image is the chosen one (kept as-is).
            # Drop off the page and cancel any pending generation job.
            kept_what = "current image"
            if choice and set_live(code, choice):
                kept_what = f"alternative {choice}"
            if code in review["species"]:
                review["species"][code]["reviewed"] = True
            jobs = [j for j in jobs if j["code"] != code]
            applied[code] = choice
            finalized += 1
            print(f"  {code}: satisfied -> finalized ({kept_what})")
            continue

        if none_good:
            enqueue("regen", reason="none-good")
            applied.pop(code, None)   # no champion; next pick always counts as new
            print(f"  {code}: none good -> queued full re-gen (round {retry[code]})")
            continue

        if choice:
            # Only re-queue when the decision actually changed from last time —
            # a re-export with the same pick (and no new bad-ref/prompt edit)
            # leaves the bird alone instead of generating more challengers.
            if choice == applied.get(code) and not badref and not id_edited:
                unchanged += 1
                continue
            if set_live(code, choice):
                kept += 1
            applied[code] = choice
            enqueue("challengers", n_new=2, reason="pick-keep-regen2")
            print(f"  {code}: keep {choice} -> queued 2 challengers (round {retry[code]})")
            continue

        if badref or id_edited:
            enqueue("regen", reason="badref" if badref else "prompt-edit")
            print(f"  {code}: {'bad ref' if badref else 'prompt edit'} -> queued re-gen")
            continue
        # note-only / nothing actionable: recorded; current image kept on page.

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

    if any(feedback[k] for k in ("badRef", "noneGood", "satisfied", "notes")):
        json.dump(feedback, open(FEEDBACK, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\nfeedback -> {FEEDBACK}")
        for k in ("badRef", "noneGood", "satisfied"):
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
