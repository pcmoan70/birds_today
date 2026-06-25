"""Push a clean checkpoint every N newly-generated species.

Watches docs/birds for species whose cutouts were (re)generated after this
watcher started (by mtime). Each time the count crosses a multiple of N, it
rebuilds the manifest and commits + pushes docs/birds. Run in the background
alongside the generation job; stop it when generation finishes (a final push
is then done normally).

Usage:
  python checkpoint_push.py            # every 25 species
  python checkpoint_push.py --n 25 --interval 90
"""
import argparse
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIRDS = os.path.join(ROOT, "docs", "birds")

COMMIT_MSG = (
    "Checkpoint: {n} regenerated species (clean transparent plates)\n\n"
    "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\n"
    "Claude-Session: https://claude.ai/code/session_01QE9YmeK2n7PbSUUJKRUAzz"
)


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)


def fresh_count(since):
    """Species dirs with at least one PNG modified after `since`."""
    n = 0
    for code in os.listdir(BIRDS):
        d = os.path.join(BIRDS, code)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.endswith(".png"):
                try:
                    if os.path.getmtime(os.path.join(d, f)) > since:
                        n += 1
                        break
                except OSError:
                    pass
    return n


def checkpoint(n):
    print(f"  building manifest + pushing checkpoint ({n} species)...")
    r = subprocess.run([sys.executable, os.path.join(HERE, "build_manifest.py")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("  build_manifest failed:", r.stderr[-300:]); return False
    git("add", "docs/birds")
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("  nothing staged, skip"); return False
    git("commit", "-m", COMMIT_MSG.format(n=n))
    p = git("push", "origin", "main")
    print("  push:", "ok" if p.returncode == 0 else p.stderr[-300:])
    return p.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25, help="species per checkpoint")
    ap.add_argument("--interval", type=int, default=90, help="poll seconds")
    args = ap.parse_args()
    since = time.time()
    pushed = 0
    print(f"watching {BIRDS}; checkpoint every {args.n} fresh species")
    while True:
        time.sleep(args.interval)
        n = fresh_count(since)
        if n - pushed >= args.n:
            if checkpoint(n):
                pushed = n
        else:
            print(f"  {n} fresh species ({n - pushed} since last push)")


if __name__ == "__main__":
    main()
