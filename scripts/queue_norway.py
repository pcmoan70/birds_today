"""Queue every Norwegian bird for a redraw with the current recipe.

scripts/norway_species.txt (select_norway.py) is the drawing set: the birds the
geomodel puts in Norway. This enqueues each of them as an ordinary coverage job,
so gen_worker.py draws a best-of-3 from the app's curated Commons photo, cuts
the background out before img2img, publishes the top-ranked variant as the live
AI image and posts all three to the review page.

Species already drawn with the current recipe are skipped unless --all.

  python queue_norway.py                   # what would be queued
  python queue_norway.py --apply
  python queue_norway.py --apply --limit 5 # a taste first
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
LIST = os.path.join(HERE, "norway_species.txt")

import gen_queue as Q  # noqa: E402
from queue_restyle import current_recipe, recipe_of  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the jobs (else dry run)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true",
                    help="queue every species, not just the ones on an old recipe")
    args = ap.parse_args()

    recipe = current_recipe()
    species = []
    for ln in open(LIST, encoding="utf-8"):
        p = ln.rstrip("\n").split("\t")
        if len(p) >= 3:
            species.append((p[0], p[2]))

    todo, current, fresh = [], 0, 0
    for code, common in species:
        have = recipe_of(code)
        if not have:
            fresh += 1
        elif have == recipe and not args.all:
            current += 1
            continue
        todo.append((code, common, have or "(never drawn)"))
    if args.limit:
        todo = todo[:args.limit]

    print(f"recipe now: {recipe}")
    print(f"{len(species)} Norwegian species — {current} already on it, "
          f"{len(todo)} to queue ({fresh} have no image at all)")
    for code, common, had in todo[:8]:
        print(f"  {code:9} {common[:28]:28} was {had}")
    if len(todo) > 8:
        print(f"  ... and {len(todo) - 8} more")
    if not args.apply:
        print("\ndry run — re-run with --apply to queue these.")
        return

    jobs = Q.load()
    queued = set(Q.job_codes(jobs))
    added = 0
    for code, _common, _had in todo:
        if code in queued:
            continue
        jobs = Q.enqueue(jobs, code, "coverage", n_new=3, priority=Q.COVERAGE,
                         reason=f"Norway redraw -> {recipe}")
        added += 1
    Q.save(jobs)
    print(f"\nqueued {added} job(s); {len(jobs)} in gen_queue.json. "
          f"Run gen_worker.py to draw them.")


if __name__ == "__main__":
    main()
