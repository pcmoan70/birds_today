"""Select regionally-relevant bird species from the model itself.

Runs the BirdNET geomodel at several points across the region and keeps bird
species whose peak weekly probability clears a threshold anywhere — i.e. the
species you could realistically see there across the year. Writes their codes
to scripts/selected_species.txt for the fetch + generate steps.

Usage:
  python select_species.py                 # default: Sweden, threshold 0.15
  python select_species.py --threshold 0.1 --top 200
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402

from species import load_species  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(os.path.dirname(HERE), "docs", "geomodel_fp16.onnx")
LABELS = os.path.join(os.path.dirname(HERE), "docs", "labels.txt")
OUT = os.path.join(HERE, "selected_species.txt")

# Representative points spanning the region (Sweden + nearby), south to north.
POINTS = {
    "Malmo": (55.60, 13.00), "Goteborg": (57.71, 11.97),
    "Stockholm": (59.33, 18.07), "Sundsvall": (62.39, 17.31),
    "Umea": (63.83, 20.26), "Kiruna": (67.86, 20.23),
}


def peak_by_code():
    sess = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
    codes = [l.split("\t")[0] for l in
             open(LABELS, encoding="utf-8").read().strip().split("\n")]
    n = len(codes)
    peak = np.zeros(n, dtype=np.float32)
    for name, (lat, lon) in POINTS.items():
        inp = np.zeros((48, 3), dtype=np.float32)
        for w in range(48):
            inp[w] = [lat, lon, w + 1]
        out = sess.run(None, {"input": inp})[0]  # 48 x n
        peak = np.maximum(peak, out.max(axis=0))
        print(f"  ran {name}")
    return codes, peak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.15,
                    help="min peak weekly probability at any point")
    ap.add_argument("--top", type=int, default=0, help="cap to top-N (0 = no cap)")
    args = ap.parse_args()

    birds = {s["code"]: s for s in load_species()}  # aves only
    codes, peak = peak_by_code()
    scored = []
    for i, code in enumerate(codes):
        if code in birds and peak[i] >= args.threshold:
            scored.append((float(peak[i]), code))
    scored.sort(reverse=True)
    if args.top:
        scored = scored[: args.top]

    with open(OUT, "w", encoding="utf-8") as f:
        for p, code in scored:
            b = birds[code]
            f.write(f"{code}\t{b['sci']}\t{b['common']}\t{p:.3f}\n")
    print(f"\n{len(scored)} bird species (peak >= {args.threshold}"
          f"{', top ' + str(args.top) if args.top else ''}) -> {OUT}")
    for p, code in scored[:12]:
        print(f"  {p:.2f}  {code:9s} {birds[code]['common']}")


if __name__ == "__main__":
    main()
