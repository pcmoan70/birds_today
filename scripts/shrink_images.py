"""Shrink existing bird PNGs in place: cap the max edge at 512 and quantise the
RGB to a palette (keeping the soft alpha edge) so files compress far smaller
with no visible loss at the display size. Idempotent — safe to re-run.

Covers docs/birds/<code>/*.png and docs/review_imgs/<code>/*.png.

Usage:  python shrink_images.py
"""
import glob
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TARGET = 448
COLORS = 200


def shrink(path):
    try:
        im = Image.open(path).convert("RGBA")
    except Exception:
        return 0, 0
    before = os.path.getsize(path)
    if max(im.size) > TARGET:
        im.thumbnail((TARGET, TARGET), Image.LANCZOS)
    q = im.convert("RGB").quantize(colors=COLORS, method=Image.FASTOCTREE,
                                   dither=Image.NONE).convert("RGBA")
    q.putalpha(im.getchannel("A"))
    q.save(path, optimize=True)
    return before, os.path.getsize(path)


def main():
    paths = (glob.glob(os.path.join(ROOT, "docs", "birds", "*", "*.png")) +
             glob.glob(os.path.join(ROOT, "docs", "review_imgs", "*", "*.png")))
    b = a = 0
    for i, p in enumerate(paths, 1):
        bb, aa = shrink(p)
        b += bb; a += aa
        if i % 100 == 0:
            print(f"  {i}/{len(paths)}")
    print(f"{len(paths)} files: {b/1e6:.0f} MB -> {a/1e6:.0f} MB "
          f"({100*(1-a/max(b,1)):.0f}% smaller)")


if __name__ == "__main__":
    main()
