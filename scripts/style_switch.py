"""Set aside / swap the live bird-plate art style.

The plates the website serves live in docs/birds/. Other rendered styles are
archived under scripts/styles/<name>/ (a local cache, gitignored, so it never
bloats the deployed Pages site). This lets you keep the old Audubon set, switch
the site to the new field-guide style, and switch back later — without
regenerating. The prompts themselves live in generate.STYLES and in each
plate's sidecar, so an archived set is fully self-describing.

  python style_switch.py save audubon      # copy current docs/birds -> archive
  python style_switch.py use fieldguide     # archive -> docs/birds + manifest
  python style_switch.py list               # archived styles + live style mix
"""
import argparse
import json
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8")

import build_manifest  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(os.path.dirname(HERE), "docs", "birds")
ARCHIVE = os.path.join(HERE, "styles")


def _species_dirs(root):
    if not os.path.isdir(root):
        return []
    return sorted(d for d in os.listdir(root)
                  if os.path.isdir(os.path.join(root, d)))


def _copy_species(src_root, dst_root):
    """Copy every species dir from src to dst (overwriting), skipping loose
    files like manifest.json. Returns the number of species copied."""
    os.makedirs(dst_root, exist_ok=True)
    n = 0
    for code in _species_dirs(src_root):
        shutil.copytree(os.path.join(src_root, code),
                        os.path.join(dst_root, code), dirs_exist_ok=True)
        n += 1
    return n


def _style_mix(root):
    """Count plates per style name from sidecars under a tree."""
    mix = {}
    for code in _species_dirs(root):
        d = os.path.join(root, code)
        for f in os.listdir(d):
            if f.endswith(".png.json"):
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as jf:
                        s = json.load(jf).get("style", "?")
                    mix[s] = mix.get(s, 0) + 1
                except (OSError, json.JSONDecodeError):
                    pass
    return mix


def save(name):
    n = _copy_species(LIVE, os.path.join(ARCHIVE, name))
    print(f"Archived {n} species from docs/birds -> styles/{name}")
    mix = _style_mix(os.path.join(ARCHIVE, name))
    print("  styles in archive:", mix or "none")


def use(name):
    src = os.path.join(ARCHIVE, name)
    if not os.path.isdir(src):
        sys.exit(f"no archived style '{name}' (have: {_species_dirs(ARCHIVE)})")
    n = _copy_species(src, LIVE)
    print(f"Restored {n} species from styles/{name} -> docs/birds")
    print("Rebuilding manifest...")
    build_manifest.main()


def show_list():
    print("Archived styles (scripts/styles/):")
    for name in _species_dirs(ARCHIVE):
        mix = _style_mix(os.path.join(ARCHIVE, name))
        total = sum(mix.values())
        print(f"  {name}: {len(_species_dirs(os.path.join(ARCHIVE, name)))} "
              f"species, {total} plates {mix}")
    print("\nLive (docs/birds/) style mix:", _style_mix(LIVE) or "none")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("save").add_argument("name")
    sub.add_parser("use").add_argument("name")
    sub.add_parser("list")
    args = ap.parse_args()
    if args.cmd == "save":
        save(args.name)
    elif args.cmd == "use":
        use(args.name)
    else:
        show_list()


if __name__ == "__main__":
    main()
