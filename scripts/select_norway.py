"""The birds you can see in Norway, from the model itself.

Same method as select_species.py, with points spanning mainland Norway: run the
BirdNET geomodel for all 48 weeks at each point and keep the bird species whose
peak weekly probability clears a threshold anywhere in the country.

Writes scripts/norway_species.txt (code, sci, common, peak) — the drawing set
for the AI images. Svalbard is left out: its avifauna is a different set, and
the mainland list is what "birds seen in Norway" means here.

  python select_norway.py                  # threshold 0.05
  python select_norway.py --target 400     # pick the threshold that lands there
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from select_species import peak_by_code  # noqa: E402
from species import load_species  # noqa: E402

OUT = os.path.join(HERE, "norway_species.txt")

# Mainland Norway, south to north: coast, inland valleys, mountains and
# Finnmark, so a species that only occurs in one corner still shows up.
POINTS = {
    "Kristiansand": (58.15, 8.00), "Jaeren": (58.80, 5.55),
    "Stavanger": (58.97, 5.73), "Bergen": (60.39, 5.32),
    "Halden": (59.12, 11.39), "Oslo": (59.91, 10.75),
    "Lillehammer": (61.11, 10.47), "Aalesund": (62.47, 6.15),
    "Dovre": (62.24, 9.32), "Roeros": (62.57, 11.38),
    "Trondheim": (63.43, 10.39), "Mo i Rana": (66.31, 14.14),
    "Bodoe": (67.28, 14.40), "Lofoten": (68.23, 14.57),
    "Vesteraalen": (68.70, 15.40), "Tromsoe": (69.65, 18.96),
    "Karasjok": (69.47, 25.51), "Alta": (69.97, 23.27),
    "Nordkapp": (71.00, 25.78), "Kirkenes": (69.73, 30.05),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.05,
                    help="min peak weekly probability at any point")
    ap.add_argument("--target", type=int, default=0,
                    help="instead pick the threshold giving about N species")
    args = ap.parse_args()

    birds = {s["code"]: s for s in load_species()}  # aves only
    codes, peak = peak_by_code(POINTS)
    scored = sorted(((float(peak[i]), c) for i, c in enumerate(codes) if c in birds),
                    reverse=True)

    if args.target:
        n = min(args.target, len(scored))
        thr = scored[n - 1][0]
        print(f"threshold for ~{args.target} species: {thr:.4f}")
    else:
        thr = args.threshold
    keep = [(p, c) for p, c in scored if p >= thr]

    with open(OUT, "w", encoding="utf-8") as f:
        for p, code in keep:
            b = birds[code]
            f.write(f"{code}\t{b['sci']}\t{b['common']}\t{p:.3f}\n")
    print(f"{len(keep)} bird species (peak >= {thr:.4f}) -> {OUT}")
    for p, code in keep[:10]:
        print(f"  {p:.2f}  {code:9s} {birds[code]['common']}")
    print("  ...")
    for p, code in keep[-5:]:
        print(f"  {p:.2f}  {code:9s} {birds[code]['common']}")


if __name__ == "__main__":
    main()
