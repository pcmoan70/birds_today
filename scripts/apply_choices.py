"""Apply choices.json from the review page to the live images.

choices.json maps {species_code: variant_id} (e.g. {"comchi1":"v2"}). For each,
copy docs/review_imgs/<code>/<vid>.png over docs/birds/<code>/sitting_0.png,
update the review manifest's "chosen", rebuild the bird manifest and push.

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


def git(*a):
    return subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: apply_choices.py choices.json")
    choices = json.load(open(sys.argv[1], encoding="utf-8"))
    review = json.load(open(REVIEW_MAN, encoding="utf-8")) if os.path.exists(REVIEW_MAN) else {"species": {}}

    changed = 0
    for code, vid in choices.items():
        src = os.path.join(REVIEW_IMGS, code, f"{vid}.png")
        if not os.path.exists(src):
            print(f"  {code}: variant {vid} missing, skip"); continue
        cur_chosen = review.get("species", {}).get(code, {}).get("chosen", "v0")
        dst = os.path.join(BIRDS, code, "sitting_0.png")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(src, dst)
        if code in review.get("species", {}):
            review["species"][code]["chosen"] = vid
        if vid != cur_chosen:
            changed += 1
            print(f"  {code}: {cur_chosen} -> {vid}")
    json.dump(review, open(REVIEW_MAN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"applied {len(choices)} choices ({changed} changed from auto-pick)")

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
