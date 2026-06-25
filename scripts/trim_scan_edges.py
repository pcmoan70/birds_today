"""Remove the straight scan-edge band some plates have (a bent page leaves a
near-full-length, light-grey, low-saturation strip along one border that the
paper->transparent step keeps slightly opaque, e.g. barn swallow, blackbird).

Operates in place on the web plates under docs/plates/. For each of the four
borders it peels inward, turning band lines transparent, and stops at the first
real (coloured/dark) content line so the bird itself is never touched.

  python trim_scan_edges.py --dry-run     # report what would be trimmed
  python trim_scan_edges.py               # apply
"""
import argparse
import glob
import os
import sys

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
PLATES = os.path.join(os.path.dirname(HERE), "docs", "plates")

FRAC = 0.60        # a band line spans most of the edge
LUM_HI, LUM_LO = 238, 150   # light-grey shadow band (paper that stayed opaque)
SAT_MAX = 0.16     # neutral (the coloured bird is more saturated)
TRANSP = 0.12      # a line this empty is just paper -> skip over it
MAX_DEPTH = 0.12   # never eat more than this fraction of the dimension


def _line_stats(alpha, rgb_lum, rgb_sat, idx, axis):
    """(opaque fraction, mean luminance, mean saturation) of one row/column."""
    if axis == "row":
        op = alpha[idx, :] > 16
        lum, sat = rgb_lum[idx, :], rgb_sat[idx, :]
    else:
        op = alpha[:, idx] > 16
        lum, sat = rgb_lum[:, idx], rgb_sat[:, idx]
    frac = float(op.mean())
    if not op.any():
        return frac, 255.0, 0.0
    return frac, float(lum[op].mean()), float(sat[op].mean())


def _peel(alpha, lum, sat, axis, side, length):
    """Return how many lines to clear from `side` (0..n)."""
    maxd = int(length * MAX_DEPTH)
    order = range(length) if side in ("T", "L") else range(length - 1, length - 1 - maxd, -1)
    remove = []
    seen = 0
    for idx in order:
        if seen >= maxd:
            break
        seen += 1
        frac, ml, ms = _line_stats(alpha, lum, sat, idx, axis)
        is_band = frac >= FRAC and LUM_LO <= ml <= LUM_HI and ms <= SAT_MAX
        is_paper = frac < TRANSP
        if is_band:
            remove.append(idx)
        elif is_paper:
            continue          # skip paper between the border and the band
        else:
            break             # real content -> stop
    # only keep a contiguous run anchored at the border (drop stray paper-only)
    return [i for i in remove]


def trim(path, apply):
    im = Image.open(path).convert("RGBA")
    arr = np.asarray(im).astype(np.float32)
    h, w, _ = arr.shape
    alpha = arr[:, :, 3]
    rgb = arr[:, :, :3]
    lum = rgb.mean(2)
    mx, mn = rgb.max(2), rgb.min(2)
    sat = (mx - mn) / np.clip(mx, 1, None)

    rows_clear, cols_clear = set(), set()
    rows_clear |= set(_peel(alpha, lum, sat, "row", "T", h))
    rows_clear |= set(_peel(alpha, lum, sat, "row", "B", h))
    cols_clear |= set(_peel(alpha, lum, sat, "col", "L", w))
    cols_clear |= set(_peel(alpha, lum, sat, "col", "R", w))
    if not (rows_clear or cols_clear):
        return None

    if apply:
        a = arr[:, :, 3]
        for y in rows_clear:
            a[y, :] = 0
        for x in cols_clear:
            a[:, x] = 0
        out = Image.fromarray(arr.astype(np.uint8), "RGBA")
        out = out.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
        out.save(path, optimize=True)
    sides = []
    if rows_clear:
        sides.append(f"rows={len(rows_clear)}")
    if cols_clear:
        sides.append(f"cols={len(cols_clear)}")
    return ", ".join(sides)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    paths = sorted(glob.glob(os.path.join(PLATES, "**", "*.png"), recursive=True))
    trimmed = 0
    for p in paths:
        r = trim(p, apply=not args.dry_run)
        if r:
            trimmed += 1
            if trimmed <= 40:
                print(f"  {os.path.relpath(p, PLATES)}: {r}")
    verb = "would trim" if args.dry_run else "trimmed"
    print(f"\n{verb} {trimmed} of {len(paths)} plate(s)")


if __name__ == "__main__":
    main()
