"""Wait for the main FLUX generation to finish, then regenerate the species
whose AI image was a distribution map (norhar1, batgod) from the real bird
references, rebuild the manifest and push.

The map source files were already quarantined out of scripts/raw, so generate.py
now picks the bird photos. This watcher just avoids competing with the running
generation for GPU memory: it polls until no other generate.py is running, then
does a quick targeted regen. Run in the background.
"""
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CODES = ["norhar1", "batgod"]
COMMIT_MSG = (
    "Regenerate norhar1/batgod AI images from real bird references\n\n"
    "These two had rendered a distribution map / globe; their map source files\n"
    "were quarantined, so they now regenerate from the actual bird photos.\n\n"
    "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>\n"
    "Claude-Session: https://claude.ai/code/session_01QE9YmeK2n7PbSUUJKRUAzz"
)


def others_running():
    """True while another generate.py process (the main job) is alive."""
    me = os.getpid()
    out = subprocess.run(
        ["wmic", "process", "where", "name='python.exe'", "get",
         "ProcessId,CommandLine", "/format:csv"],
        capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "generate.py" in line and str(me) not in line:
            return True
    return False


def git(*a):
    return subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True)


def main():
    print("waiting for main generation to finish...")
    while others_running():
        time.sleep(120)
    print("GPU free; regenerating", CODES)
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "generate.py"),
         "--codes", ",".join(CODES), "--num", "1"],
        cwd=HERE)
    if r.returncode != 0:
        print("generate.py failed"); return
    subprocess.run([sys.executable, os.path.join(HERE, "build_manifest.py")])
    git("add", "docs/birds")
    if git("diff", "--cached", "--quiet").returncode != 0:
        git("commit", "-m", COMMIT_MSG)
        p = git("push", "origin", "main")
        print("push:", "ok" if p.returncode == 0 else p.stderr[-300:])
    print("done")


if __name__ == "__main__":
    main()
