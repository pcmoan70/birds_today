"""Queue the whole image stack for redrawing with the current recipe.

The recipe id in regen_flagged.py (RECIPE) stamps every generated image. When it
changes — a new base-photo source, a new style prompt — every image made by the
old recipe is stale. This enqueues those species for a fresh best-of-3 through
the ordinary queue, so gen_worker.py picks them up and the results land on the
review page exactly like any other generation: pick a variant, mark it good or
ask for more iterations, edit the field marks, export, apply.

Nothing is regenerated here and nothing is deleted; this only writes jobs into
scripts/gen_queue.json (local state).

  python queue_restyle.py                  # what would be queued
  python queue_restyle.py --apply          # actually queue them
  python queue_restyle.py --apply --limit 25
  python queue_restyle.py --apply --codes gretit1,blutit
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)
BIRDS = os.path.join(ROOT, "docs", "birds")
SELECTED = os.path.join(HERE, "selected_species.txt")

import gen_queue as Q  # noqa: E402


def current_recipe():
    """The recipe id without importing regen_flagged (which pulls in torch)."""
    for line in open(os.path.join(HERE, "regen_flagged.py"), encoding="utf-8"):
        if line.startswith("RECIPE ="):
            return line.split("=", 1)[1].split("#")[0].strip().strip('"\'')
    return ""


def recipe_of(code):
    path = os.path.join(BIRDS, code, "sitting_0.png.json")
    try:
        return str(json.load(open(path, encoding="utf-8")).get("recipe", ""))
    except Exception:                                           # noqa: BLE001
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the jobs (else dry run)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--codes", help="comma-separated species codes")
    ap.add_argument("--all", action="store_true",
                    help="queue every species, not just the ones on an old recipe")
    args = ap.parse_args()

    recipe = current_recipe()
    species = []
    for ln in open(SELECTED, encoding="utf-8"):
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            species.append((p[0], p[2]))
    if args.codes:
        want = {c.strip() for c in args.codes.split(",")}
        species = [s for s in species if s[0] in want]

    todo, current, fresh = [], 0, 0
    for code, common in species:
        have = recipe_of(code)
        if not have:
            fresh += 1                    # never drawn: a first-time generation
        elif have == recipe and not args.all:
            current += 1
            continue
        todo.append((code, common, have or "(none yet)"))
    if args.limit:
        todo = todo[:args.limit]

    print(f"recipe now: {recipe}")
    print(f"{len(species)} species — {current} already on it, {len(todo)} to queue "
          f"({fresh} have no image at all)")
    for code, common, had in todo[:10]:
        print(f"  {code:9} {common[:28]:28} was {had}")
    if len(todo) > 10:
        print(f"  ... and {len(todo) - 10} more")
    if not args.apply:
        print("\ndry run — re-run with --apply to queue these.")
        return

    jobs = Q.load()
    for code, _common, _had in todo:
        jobs = Q.enqueue(jobs, code, "coverage", n_new=3, priority=Q.COVERAGE,
                         reason=f"restyle -> {recipe}")
    Q.save(jobs)
    print(f"\nqueued {len(todo)} job(s); {len(jobs)} in gen_queue.json. "
          f"Run gen_worker.py to draw them — results appear on the review page.")


if __name__ == "__main__":
    main()
