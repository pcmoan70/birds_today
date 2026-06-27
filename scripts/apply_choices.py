"""Apply choices.json from the review page to the live images.

choices.json maps each species code to either:
  - a variant id string, e.g. {"comchi1": "v2"}, or
  - an object {"choice": "v2", "badRef": true, "noneGood": true, "note": "..."}.

For each entry we copy docs/review_imgs/<code>/<choice>.png over
docs/birds/<code>/sitting_0.png and update the review manifest's "chosen" —
EXCEPT when "noneGood" is set, where we keep the current live image untouched.

"badRef", "noneGood" and free-text "note" flags are collected into
scripts/review_feedback.json (and printed) so they can be acted on: bad
references want re-fetching, "none good enough" species want re-generation.

Usage:
  python apply_choices.py path/to/choices.json
"""
import json
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REVIEW_IMGS = os.path.join(ROOT, "docs", "review_imgs")
BIRDS = os.path.join(ROOT, "docs", "birds")
REVIEW_MAN = os.path.join(ROOT, "docs", "review", "manifest.json")
FEEDBACK = os.path.join(HERE, "review_feedback.json")


def git(*a):
    return subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: apply_choices.py choices.json")
    choices = json.load(open(sys.argv[1], encoding="utf-8"))
    review = json.load(open(REVIEW_MAN, encoding="utf-8")) if os.path.exists(REVIEW_MAN) else {"species": {}}

    changed = applied = 0
    feedback = {"badRef": [], "noneGood": [], "notes": {}}
    for code, val in choices.items():
        if isinstance(val, dict):
            vid = val.get("choice", "v0")
            if val.get("badRef"):
                feedback["badRef"].append(code)
            if val.get("noneGood"):
                feedback["noneGood"].append(code)
            if val.get("note"):
                feedback["notes"][code] = val["note"]
            if val.get("noneGood"):       # keep the current live image as-is
                print(f"  {code}: none good enough — left unchanged")
                continue
        else:
            vid = val

        src = os.path.join(REVIEW_IMGS, code, f"{vid}.png")
        if not os.path.exists(src):
            print(f"  {code}: variant {vid} missing, skip"); continue
        cur_chosen = review.get("species", {}).get(code, {}).get("chosen", "v0")
        dst = os.path.join(BIRDS, code, "sitting_0.png")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(src, dst)
        applied += 1
        if code in review.get("species", {}):
            review["species"][code]["chosen"] = vid
        if vid != cur_chosen:
            changed += 1
            print(f"  {code}: {cur_chosen} -> {vid}")
    json.dump(review, open(REVIEW_MAN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"applied {applied} choices ({changed} changed from auto-pick)")

    if feedback["badRef"] or feedback["noneGood"] or feedback["notes"]:
        json.dump(feedback, open(FEEDBACK, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\nfeedback -> {FEEDBACK}")
        if feedback["badRef"]:
            print(f"  bad reference photo ({len(feedback['badRef'])}): "
                  + ", ".join(feedback["badRef"]))
        if feedback["noneGood"]:
            print(f"  none good enough ({len(feedback['noneGood'])}): "
                  + ", ".join(feedback["noneGood"]))
        for code, n in feedback["notes"].items():
            print(f"  note {code}: {n}")

    subprocess.run([sys.executable, os.path.join(HERE, "build_manifest.py")],
                   capture_output=True)
    git("add", "docs")
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("nothing to push"); return
    git("commit", "-m", "Apply reviewed AI image choices\n\n"
        "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_01QE9YmeK2n7PbSUUJKRUAzz")
    p = git("push", "origin", "main")
    print("push:", "ok" if p.returncode == 0 else p.stderr[-200:])


if __name__ == "__main__":
    main()
