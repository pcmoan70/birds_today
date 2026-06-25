"""Generate field-guide-style bird plates with FLUX.1-dev, grounded on a reference.

Reference-grounded image-to-image: a fetched reference photo (scripts/raw/) sets
the bird's true shape and field marks; the prompt + img2img restyle it into a
consistent modern field-guide illustration (the watercolour/gouache style of
Lars Jonsson, Killian Mullarney, Dan Zetterström, Hampus Lejon and Axel
Thorenfeldt, as in the Collins Bird Guide). FLUX.1-dev runs in fp8 (optimum-quanto)
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
  python generate.py --test --lora path/to/fieldguide_flux_lora.safetensors
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

# Switchable art styles. Each has a short `tag` for the CLIP encoder (77-token
# limit) and a full `prompt` for T5 (prompt_2) so nothing is truncated. The
# active style is recorded in every plate's sidecar, and the whole live set can
# be swapped between styles with style_switch.py — so adding a style here is all
# it takes to make it available.
# IMPORTANT: ask for the BIRD ONLY on a plain background — NOT a "plate on paper"
# (that renders a paper sheet with caption/border that the matting then keeps).
STYLES = {
    "fieldguide": {
        "tag": ("highly detailed lifelike field-guide bird illustration, realistic "
                "watercolour and gouache, Lars Jonsson style, photorealistic "
                "feather detail, plain white background, no text, no border"),
        "prompt": ("a highly detailed, lifelike modern ornithological field-guide "
                   "illustration of a single bird in the naturalistic watercolour-"
                   "and-gouache style of Lars Jonsson, Killian Mullarney, Dan "
                   "Zetterström, Hampus Lejon and Axel Thorenfeldt (as in the "
                   "Collins Bird Guide), rendered with near-photographic realism: "
                   "true-to-life proportions and colours, realistic feather "
                   "textures and barring, subtle three-dimensional form and depth, "
                   "soft natural light with gentle shadow, crisp sharp focus, "
                   "scientifically accurate field marks, fine feather detail, full "
                   "body, the bird only, isolated on a plain solid white "
                   "background, no paper texture, no border, no frame, no caption, "
                   "no text"),
    },
    "audubon": {
        "tag": ("Audubon-style watercolour painting of a single bird, plain white "
                "background, no text, no border"),
        "prompt": ("a detailed hand-coloured watercolour and ink painting of a "
                   "single bird in the style of John James Audubon, fine feather "
                   "detail, scientifically accurate, full body, the bird only, "
                   "isolated on a plain solid white background, no paper texture, "
                   "no border, no frame, no caption, no text"),
    },
}
DEFAULT_STYLE = "fieldguide"
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


def build_prompt(common, sci, marks, stance, style=DEFAULT_STYLE):
    """Full prompt for the T5 encoder (prompt_2). Field marks are optional —
    when absent, the reference photo + species name carry the appearance."""
    feat = f" Distinctive features: {marks}." if marks else ""
    return (f"{STYLES[style]['prompt']}. A {common} ({sci}), "
            f"{STANCES[stance]['desc']}.{feat} {ANATOMY}.")


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


def render_one(pipe, rembg_session, common, sci, marks, pose, ref_path, idx,
               out_dir, model="black-forest-labs/FLUX.1-dev", steps=28,
               guidance=3.5, size=1024, strength=None, max_edge=640,
               style=DEFAULT_STYLE):
    """Generate and matte ONE plate (pose_<idx>.png) from a single reference.

    Returns the output path on success, or None if the cutout failed. Shared by
    the batch CLI and the downvote-driven regeneration (refetch_downvoted.py).
    The exact style name + prompt are recorded in the sidecar so a plate is
    self-describing and styles stay switchable.
    """
    if strength is None:
        strength = STANCES[pose]["strength"]
    prompt = build_prompt(common, sci, marks, pose, style)
    init = Image.open(ref_path).convert("RGB").resize((size, size))
    gen = torch.Generator("cpu").manual_seed(1000 + idx)
    out = pipe(prompt=STYLES[style]["tag"], prompt_2=prompt, image=init,
               strength=strength, num_inference_steps=steps,
               guidance_scale=guidance, generator=gen).images[0]
    cutimg = cut.cut_pil(out, rembg_session, max_edge)
    if cutimg is None:
        return None
    os.makedirs(out_dir, exist_ok=True)
    png = os.path.join(out_dir, f"{pose}_{idx}.png")
    cutimg.save(png)
    with open(png + ".json", "w", encoding="utf-8") as jf:
        json.dump({"source": "generated", "model": model, "style": style,
                   "prompt": prompt, "pose": pose,
                   "reference": os.path.basename(ref_path)},
                  jf, ensure_ascii=False, indent=2)
    return png


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--test", action="store_true")
    g.add_argument("--codes", help="comma-separated species codes")
    g.add_argument("--codes-file", help="file with a species code in the first "
                                        "tab-separated column per line")
    ap.add_argument("--model", default="black-forest-labs/FLUX.1-dev")
    ap.add_argument("--style", default=DEFAULT_STYLE, choices=list(STYLES),
                    help="art style (recorded per plate; swap sets with "
                         "style_switch.py)")
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
    rembg_session = new_session("birefnet-general")  # high-quality matting

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
            for i, ref in enumerate(refs):
                png = render_one(pipe, rembg_session, common, sci, fm, pose, ref,
                                 i, dst, model=args.model, steps=args.steps,
                                 guidance=args.guidance, size=args.size,
                                 strength=args.strength, max_edge=args.max_edge,
                                 style=args.style)
                print(f"  saved {pose}_{i}.png" if png
                      else f"  {pose}_{i}: cutout failed")
    print("\nDone. Now run: python build_manifest.py")


if __name__ == "__main__":
    main()
