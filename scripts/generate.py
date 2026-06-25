"""Generate Audubon-style bird plates with FLUX.1-dev, grounded on a reference.

Reference-grounded image-to-image: a fetched reference photo (scripts/raw/) sets
the bird's true shape and field marks; the prompt + img2img restyle it into a
consistent hand-coloured naturalist plate. FLUX.1-dev runs in fp8 (optimum-quanto)
so it fits an RTX 3090 (24 GB). Output is background-removed and written to
docs/birds/ exactly like the photo cutouts, so the manifest step is unchanged.

Prereqs (one-time):
  pip install -r requirements.txt diffusers transformers accelerate \
      optimum-quanto peft sentencepiece protobuf
  # FLUX.1-dev is gated: accept the license at
  #   https://huggingface.co/black-forest-labs/FLUX.1-dev
  # then:  huggingface-cli login

Usage:
  python generate.py --test                      # the 12 test species
  python generate.py --codes gretit1,eurgol1 --num 2
  python generate.py --test --lora path/to/audubon_flux_lora.safetensors
  python generate.py --test --model black-forest-labs/FLUX.1-schnell  # ungated
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import torch  # noqa: E402
from PIL import Image  # noqa: E402

import cutout as cut  # noqa: E402
from rembg import new_session  # noqa: E402
from species import load_species  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw")
OUT_DIR = os.path.join(os.path.dirname(HERE), "docs", "birds")
PROMPTS = os.path.join(HERE, "species_prompts.json")

TEST_CODES = ["gretit1", "blutit", "eurrob1", "eurbla", "comcha", "eurmag1",
              "eursta", "houspa", "eurgol1", "barswa", "comcra", "whoswa"]

# Short style tag for the CLIP encoder (77-token limit); the full prompt goes
# to T5 (prompt_2) so nothing is truncated.
STYLE_TAG = ("Audubon-style hand-coloured naturalist bird plate, watercolour and "
             "ink, cream paper, no text")
STYLE = ("a hand-coloured naturalist plate in the style of John James Audubon, "
         "watercolour and ink, fine feather detail, scientifically accurate, "
         "single bird, full body, on aged cream paper background, "
         "no text, no border")
# Reinforce correct avian anatomy — keeps wings natural rather than warped.
ANATOMY = ("anatomically correct, exactly two wings in a natural realistic "
           "position with properly layered flight feathers, correct wing "
           "orientation, natural posture")

# Each stance: prompt fragment, which fetched reference pose to ground on, and a
# default img2img strength. Lower strength stays closer to the real reference
# photo (more natural wings); landing/takeoff have no exact reference so they
# lean a little harder on the prompt.
STANCES = {
    "sitting": {"desc": "perched on a small branch, wings folded neatly against "
                        "the body, side profile",
                "ref": "sitting", "strength": 0.85},
    "takeoff": {"desc": "taking off, crouched and springing upward, wings raised "
                        "and beginning to open, tail fanned",
                "ref": "sitting", "strength": 0.9},
    "landing": {"desc": "landing, wings cupped and swept forward to brake, legs "
                        "extended toward a perch, tail spread",
                "ref": "flying", "strength": 0.88},
    "flying": {"desc": "in level flight, wings symmetrically spread in a natural "
                       "gliding position",
               "ref": "flying", "strength": 0.8},
}


def build_prompt(common, sci, marks, stance):
    """Full prompt for the T5 encoder (prompt_2). Field marks are optional —
    when absent, the reference photo + species name carry the appearance."""
    feat = f" Distinctive features: {marks}." if marks else ""
    return (f"{STYLE}. A {common} ({sci}), {STANCES[stance]['desc']}.{feat} "
            f"{ANATOMY}.")


def load_pipeline(model_id, lora=None, fp8=True):
    """FLUX img2img pipeline, fp8-quantized to fit 24 GB."""
    from diffusers import FluxImg2ImgPipeline
    pipe = FluxImg2ImgPipeline.from_pretrained(model_id, torch_dtype=torch.bfloat16)
    if fp8:
        # Quantize the two big modules to 8-bit so weights fit comfortably.
        from optimum.quanto import freeze, qfloat8, quantize
        for mod in (pipe.transformer, pipe.text_encoder_2):
            quantize(mod, weights=qfloat8)
            freeze(mod)
    if lora:
        pipe.load_lora_weights(lora)
    pipe.enable_model_cpu_offload()  # safe on 24 GB; keeps headroom
    return pipe


def ref_images(code, pose, want):
    """Pick up to `want` reference photos for a species+pose from raw/."""
    d = os.path.join(RAW_DIR, code)
    if not os.path.isdir(d):
        return []
    files = sorted(f for f in os.listdir(d)
                   if f.startswith(pose + "_") and not f.endswith(".json"))
    return [os.path.join(d, f) for f in files[:want]]


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--test", action="store_true")
    g.add_argument("--codes", help="comma-separated species codes")
    g.add_argument("--codes-file", help="file with a species code in the first "
                                        "tab-separated column per line")
    ap.add_argument("--model", default="black-forest-labs/FLUX.1-dev")
    ap.add_argument("--lora", help="optional style LoRA .safetensors")
    ap.add_argument("--num", type=int, default=2, help="plates per stance")
    ap.add_argument("--poses", default="sitting,flying",
                    help="stances: sitting,takeoff,landing,flying")
    ap.add_argument("--strength", type=float, default=None,
                    help="img2img denoise (default: per-stance; higher = more "
                         "stylised, less faithful to the reference)")
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--guidance", type=float, default=3.5)
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--max-edge", type=int, default=640)
    ap.add_argument("--no-fp8", action="store_true")
    args = ap.parse_args()

    if args.test:
        codes = TEST_CODES
    elif args.codes_file:
        codes = [ln.split("\t")[0].strip() for ln in
                 open(args.codes_file, encoding="utf-8") if ln.strip()]
    else:
        codes = [c.strip() for c in args.codes.split(",")]
    poses = [p for p in args.poses.split(",") if p]
    bad = [p for p in poses if p not in STANCES]
    if bad:
        sys.exit(f"unknown stance(s): {bad}; choose from {list(STANCES)}")
    marks = json.load(open(PROMPTS, encoding="utf-8"))
    by_code = {s["code"]: s for s in load_species()}

    print(f"Loading {args.model} (fp8={not args.no_fp8})...")
    pipe = load_pipeline(args.model, args.lora, fp8=not args.no_fp8)
    rembg_session = new_session("u2net")

    for code in codes:
        sp = by_code.get(code, {})
        common = sp.get("common", code)
        sci = sp.get("sci", "")
        fm = marks.get(code, "")
        dst = os.path.join(OUT_DIR, code)
        os.makedirs(dst, exist_ok=True)
        print(f"\n{code}  {common}")
        for pose in poses:
            refs = ref_images(code, STANCES[pose]["ref"], args.num)
            if not refs:
                print(f"  {pose}: no '{STANCES[pose]['ref']}' reference photo, skip")
                continue
            strength = args.strength if args.strength is not None else STANCES[pose]["strength"]
            prompt = build_prompt(common, sci, fm, pose)
            for i, ref in enumerate(refs):
                init = Image.open(ref).convert("RGB").resize((args.size, args.size))
                gen = torch.Generator("cpu").manual_seed(1000 + i)
                out = pipe(prompt=STYLE_TAG, prompt_2=prompt, image=init,
                           strength=strength, num_inference_steps=args.steps,
                           guidance_scale=args.guidance, generator=gen).images[0]
                cutimg = cut.cut_pil(out, rembg_session, args.max_edge)
                if cutimg is None:
                    print(f"  {pose}_{i}: cutout failed")
                    continue
                png = os.path.join(dst, f"{pose}_{i}.png")
                cutimg.save(png)
                with open(png + ".json", "w", encoding="utf-8") as jf:
                    json.dump({"source": "generated", "model": args.model,
                               "style": "audubon", "pose": pose,
                               "reference": os.path.basename(ref)},
                              jf, ensure_ascii=False, indent=2)
                print(f"  saved {pose}_{i}.png")
    print("\nDone. Now run: python build_manifest.py")


if __name__ == "__main__":
    main()
