"""Report what the drawing stack will actually run on this machine.

Prints the GPU and its memory, the checkpoint currently configured
(BIRD_MODEL, default Flex.2), whether it is already downloaded, and — with
--load — actually builds the img2img pipeline so a problem shows up here rather
than in the middle of a generation run.

  python check_model.py                 # what is configured and cached
  python check_model.py --load          # also load it (slow, needs the GPU)
  BIRD_MODEL=D:/models/Flex.2-preview python check_model.py --load
"""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CANDIDATES = [            # checkpoints this pipeline knows how to drive
    "ostris/Flex.2-preview",
    "ostris/Flex.1-alpha",
    "black-forest-labs/FLUX.1-dev",
    "black-forest-labs/FLUX.1-schnell",
]


def hub_dirs():
    """Model snapshots in the HuggingFace cache -> {repo_id: size in GB}."""
    root = os.environ.get("HF_HOME") or os.path.join(os.path.expanduser("~"), ".cache",
                                                     "huggingface")
    hub = os.path.join(root, "hub")
    out = {}
    if not os.path.isdir(hub):
        return hub, out
    for name in os.listdir(hub):
        if not name.startswith("models--"):
            continue
        repo = name[len("models--"):].replace("--", "/", 1).replace("--", "/")
        total = 0
        for dirpath, _dirs, files in os.walk(os.path.join(hub, name)):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass
        out[repo] = round(total / 1e9, 1)
    return hub, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--load", action="store_true",
                    help="actually build the pipeline (slow; needs the GPU)")
    args = ap.parse_args()

    try:
        import torch
        if torch.cuda.is_available():
            i = torch.cuda.current_device()
            p = torch.cuda.get_device_properties(i)
            print(f"GPU: {p.name}, {p.total_memory / 1e9:.1f} GB, "
                  f"CUDA {torch.version.cuda}, torch {torch.__version__}")
        else:
            print(f"GPU: none visible (torch {torch.__version__}) — generation needs one")
    except Exception as e:                                      # noqa: BLE001
        print(f"torch not usable: {e}")

    import generate as G
    print(f"\nconfigured checkpoint (BIRD_MODEL): {G.DEFAULT_MODEL}")
    print(f"style (BIRD_STYLE): {os.environ.get('BIRD_STYLE', 'fieldsketch')}")

    hub, cached = hub_dirs()
    print(f"\nHuggingFace cache: {hub}")
    if not cached:
        print("  (empty or unreadable from here)")
    for repo, gb in sorted(cached.items(), key=lambda kv: -kv[1])[:12]:
        mark = " <- configured" if repo.lower() == G.DEFAULT_MODEL.lower() else ""
        print(f"  {gb:6.1f} GB  {repo}{mark}")
    known = [c for c in CANDIDATES if c in cached]
    print("\ncheckpoints this pipeline knows, already downloaded: "
          + (", ".join(known) if known else "none found in the cache"))
    if os.path.isdir(G.DEFAULT_MODEL):
        print(f"(configured model is a local folder: {G.DEFAULT_MODEL})")

    if not args.load:
        print("\nRun with --load to build the pipeline and prove it fits.")
        return
    print("\nloading (fp8 + CPU offload)...")
    try:
        G.load_pipeline(fp8=True)
        print("pipeline loaded — this checkpoint works here.")
    except Exception as e:                                      # noqa: BLE001
        print(f"FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
