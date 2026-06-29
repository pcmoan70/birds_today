"""Apply crop_choices.json from the manual crop tool (docs/crop.html).

crop_choices.json maps a species code to the chosen candidate + crop box:
  {"comeid": {"cand": 3, "box": [0.10, 0.05, 0.80, 0.85]}}      # x,y,w,h in 0..1
  {"jacsni": {"cand": 1, "full": true}}                          # whole photo

For each, crop the hosted candidate (docs/crop/<code>/cand<idx>.jpg), install it
as the pinned reference (pinned_refs/<code>.jpg, which overrides auto-selection),
and queue a regeneration. The running worker then regenerates from your hand-
picked, hand-cropped photo. Applied species are dropped from docs/crop.

Usage:  python apply_crops.py crop_choices.json
"""
import glob
import json
import os
import shutil
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image  # noqa: E402

import gen_queue as Q  # noqa: E402
import regen_flagged as R  # noqa: E402

ROOT = R.ROOT
CROP_DIR = os.path.join(ROOT, "docs", "crop")
CROP_MAN = os.path.join(CROP_DIR, "manifest.json")
RETRY = os.path.join(R.HERE, "retry_rounds.json")
FEEDBACK = os.path.join(R.HERE, "review_feedback.json")


def git(*a):
    return subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True)


class _Cand:
    """Minimal stand-in so regen_flagged._fetch_candidate can re-download the
    chosen photo at full resolution (whoBIRD assets come from the disk cache)."""
    def __init__(self, source, url, src_id):
        self.source, self.url, self.src_id = source, url, src_id


def source_image(code, v):
    """The chosen photo to crop: the original full-res (re-fetched from its URL)
    if available, else the hosted candidate thumbnail. Returns a PIL RGB image."""
    url, source, src_id = v.get("url"), v.get("source"), v.get("src_id")
    if url:
        tmp = os.path.join(CROP_DIR, f"_dl_{code}.jpg")
        try:
            if R._fetch_candidate(_Cand(source or "", url, src_id or ""), tmp):
                im = Image.open(tmp).convert("RGB")
                os.remove(tmp)
                return im
        except Exception:
            pass
        if os.path.exists(tmp):
            os.remove(tmp)
    rel = v.get("img") or f"crop/{code}/cand{v.get('cand', 0)}.jpg"
    p = os.path.join(ROOT, "docs", rel)
    return Image.open(p).convert("RGB") if os.path.exists(p) else None


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: apply_crops.py crop_choices.json")
    choices = json.load(open(sys.argv[1], encoding="utf-8"))
    review = json.load(open(R.REVIEW_MAN, encoding="utf-8")) if os.path.exists(R.REVIEW_MAN) else {"species": {}}
    review.setdefault("species", {})
    retry = json.load(open(RETRY, encoding="utf-8")) if os.path.exists(RETRY) else {}
    fb = json.load(open(FEEDBACK, encoding="utf-8")) if os.path.exists(FEEDBACK) else {}
    jobs = Q.load()
    os.makedirs(R.PINNED, exist_ok=True)

    done = 0
    for code, v in choices.items():
        im = source_image(code, v)
        if im is None:
            print(f"  {code}: chosen photo unavailable, skip"); continue
        # Crop the recorded region from the (full-res) original.
        if not v.get("full") and v.get("box"):
            x, y, w, h = v["box"]
            W, H = im.size
            l, t = max(0, int(x * W)), max(0, int(y * H))
            r, b = min(W, int((x + w) * W)), min(H, int((y + h) * H))
            if r - l > 20 and b - t > 20:
                im = im.crop((l, t, r, b))
        im.save(os.path.join(R.PINNED, f"{code}.jpg"), "JPEG", quality=92, optimize=True)

        retry[code] = retry.get(code, 0) + 1
        jobs = Q.enqueue(jobs, code, "regen", seed_off=retry[code] * 5,
                         refetch=False, priority=Q.FEEDBACK, reason="manual-crop")
        if code in review["species"]:
            review["species"][code]["pending"] = True
        if isinstance(fb.get("badRef"), list) and code in fb["badRef"]:
            fb["badRef"].remove(code)
        shutil.rmtree(os.path.join(CROP_DIR, code), ignore_errors=True)
        done += 1
        print(f"  {code}: pinned {'full' if v.get('full') else 'cropped'} "
              f"{v.get('source','?')} cand{v.get('cand',0)} -> queued re-gen")

    # Drop applied species from the crop manifest.
    if os.path.exists(CROP_MAN):
        man = json.load(open(CROP_MAN, encoding="utf-8")); man.setdefault("species", {})
        for code in choices:
            man["species"].pop(code, None)
        json.dump(man, open(CROP_MAN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    review["queued"] = Q.job_codes(jobs)
    json.dump(review, open(R.REVIEW_MAN, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(retry, open(RETRY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(fb, open(FEEDBACK, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    Q.save(jobs)
    print(f"\npinned {done} references; queued {done} re-gens (worker will use pinned_refs).")

    git("add", "docs")
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("nothing to push"); return
    git("commit", "-m", "Apply manual photo crops (pinned refs; queue re-gen)\n\n"
        "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_01QE9YmeK2n7PbSUUJKRUAzz")
    p = git("push", "origin", "main")
    print("push:", "ok" if p.returncode == 0 else p.stderr[-200:])


if __name__ == "__main__":
    main()
