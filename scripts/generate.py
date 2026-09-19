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
import re
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
    # Drawn from an openly licensed Commons photo: a colour field sketch that
    # keeps the individual's likeness, with the photo's setting left out
    # entirely. Kept tighter than "fieldguide" because improved_prompt() adds the
    # species' field marks, feet and background clauses on top, and the T5
    # encoder only reads the first ~512 tokens.
    "fieldsketch": {
        "tag": ("field sketch of one bird, pencil underdrawing under watercolour "
                "washes and coloured pencil, hand-drawn on white paper, "
                "no background, no text, no border"),
        "prompt": ("a field sketch of one bird, worked up by hand from a "
                   "photograph: light graphite underdrawing still showing at the "
                   "edges, thin watercolour washes and coloured pencil over it, "
                   "bare paper for the palest areas and never white paint. "
                   "Tightest at bill, eye, face pattern and the folded wing "
                   "drawn feather group by feather group; looser toward belly, "
                   "flanks and legs. Observed, not stylised: true proportions, "
                   "unsaturated daylight colour, no outline round the bird, "
                   "not a photograph"),
    },
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
# What the model actually gets wrong, said plainly and countably. The old clause
# only covered wings, and the drawings kept coming back with tangled feet: a
# photograph usually hides the toes (gripping a branch, sunk in grass, lost in
# shadow), so the model invents them, and invention means six toes and a bundle
# of claws. Counts are what a diffusion model can be held to.
ANATOMY = ("anatomically correct and countable: one head, one bill, two eyes, "
           "two wings folded symmetrically with layered primaries and one clean "
           "wingtip, one tail of straight parallel feathers; two legs, each foot "
           "three toes forward and one back, each toe one short curved claw — "
           "feet simple and correctly jointed, never a tangle of toes or claws, "
           "never scribbled over")

# Each stance: prompt fragment, which fetched reference pose to ground on, and a
# default img2img strength. Lower strength stays closer to the real reference
# photo (more natural wings); landing/takeoff have no exact reference so they
# lean a little harder on the prompt.
STANCES = {
    "sitting": {"desc": "in a natural perched posture, upright and alert, wings "
                        "folded neatly against the body, side profile, nothing "
                        "to perch on",
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


# Which checkpoint to draw with. Flex.2 (ostris/Flex.2-preview, Apache-2.0) is
# FLUX-architecture but 8B, so it fits a 12 GB card with the fp8 path below;
# FLUX.1-dev is the original 12B. Override per run with BIRD_MODEL or --model.
DEFAULT_MODEL = os.environ.get("BIRD_MODEL", "ostris/Flex.2-preview")


def is_control_model(model_id=None):
    """True for a checkpoint whose transformer takes a control/inpaint input.

    Flex.2's transformer has in_channels 196 — (16 image latents + 16 control +
    16 inpaint + 1 mask) x 4 — and no img2img mode at all: FluxImg2ImgPipeline
    builds 49-channel noise against 16-channel image latents and dies in the
    scheduler. Such a checkpoint is driven through its own pipeline instead,
    with the prepared photo as the control image. Plain FLUX stays at 64.
    """
    from diffusers import FluxTransformer2DModel
    try:
        cfg = FluxTransformer2DModel.load_config(model_id or DEFAULT_MODEL,
                                                 subfolder="transformer")
        return int(cfg.get("in_channels", 64)) > 64
    except Exception:                                           # noqa: BLE001
        return False


def load_pipeline(model_id=None, lora=None, fp8=True):
    """Pipeline for the configured checkpoint, fp8-quantized.

    Quantizing the transformer and the T5 encoder to 8 bit is what makes this
    fit a 12 GB 3060 with Flex.2 (and 24 GB with FLUX.1-dev); CPU offload keeps
    only the module in use resident.

    Plain FLUX checkpoints get the img2img pipeline (reference photo as the init
    image). A control checkpoint (Flex.2) gets its own pipeline from the model
    repo and is marked with `_bird_control`, so callers pass the photo as the
    control image instead — see is_control_model.
    """
    from diffusers import FluxImg2ImgPipeline
    model_id = model_id or DEFAULT_MODEL
    if is_control_model(model_id):
        from diffusers import AutoPipelineForText2Image
        pipe = AutoPipelineForText2Image.from_pretrained(
            model_id, custom_pipeline=model_id, torch_dtype=torch.bfloat16,
            trust_remote_code=True)
        pipe._bird_control = True
        return _finish_pipeline(pipe, lora, fp8)
    try:
        pipe = FluxImg2ImgPipeline.from_pretrained(model_id, torch_dtype=torch.bfloat16)
    except Exception as e:                                      # noqa: BLE001
        # A local folder or a differently-packaged snapshot: say which, plainly,
        # rather than failing three frames deep in diffusers.
        raise RuntimeError(
            f"could not load '{model_id}' as an img2img pipeline ({e}). "
            "Point BIRD_MODEL at a diffusers-format checkpoint (a local path "
            "works: BIRD_MODEL=D:/models/Flex.2-preview), or run "
            "scripts/check_model.py to see what is installed."
        ) from e
    return _finish_pipeline(pipe, lora, fp8)


def control_img2img(pipe, init, prompt, prompt_2, strength, seed,
                    control_strength=0.6, control_stop=0.5, steps=28,
                    guidance=3.5):
    """img2img on a control checkpoint (Flex.2), which has no img2img mode.

    Flex.2's own pipeline only generates from noise, with the photo as a control
    hint — and a hint is all it stays: the bird drifts off the reference, and on
    a harder subject the composition falls apart entirely. But that pipeline
    accepts ready-made `latents` and a custom `sigmas` schedule, and img2img is
    exactly those two things: encode the photo, add the noise the schedule
    expects at the starting sigma, and run only the tail of the steps. The
    drawing then starts from the bird instead of from noise.

    `strength` means what it means everywhere else: 0 keeps the photo, 1 ignores
    it. The control input is kept on as well, at a lower weight, to hold the
    silhouette while the style is applied.
    """
    import numpy as np
    from diffusers.pipelines.flux.pipeline_flux import (calculate_shift,
                                                        retrieve_timesteps)
    device = pipe._execution_device

    x = pipe.image_processor.preprocess(init, height=init.height, width=init.width)
    x = x.to(device=device, dtype=pipe.vae.dtype)
    lat = pipe.vae.encode(x).latent_dist.mode()
    lat = (lat - pipe.vae.config.shift_factor) * pipe.vae.config.scaling_factor
    image_latents = pipe._pack_latents(lat, *lat.shape)

    sigmas = np.linspace(1.0, 1 / steps, steps)[max(int(steps - steps * strength), 0):]
    mu = calculate_shift(
        image_latents.shape[1],
        pipe.scheduler.config.get("base_image_seq_len", 256),
        pipe.scheduler.config.get("max_image_seq_len", 4096),
        pipe.scheduler.config.get("base_shift", 0.5),
        pipe.scheduler.config.get("max_shift", 1.15))
    # The scheduler shifts the schedule by mu, so ask it what the first sigma
    # really is rather than assuming: the photo must carry exactly the noise the
    # first step expects, or the run starts off-distribution and smears.
    retrieve_timesteps(pipe.scheduler, len(sigmas), device, sigmas=sigmas, mu=mu)
    sigma0 = float(pipe.scheduler.sigmas[0])

    noise = torch.randn(image_latents.shape,
                        generator=torch.Generator("cpu").manual_seed(seed),
                        dtype=torch.float32).to(device=device,
                                                dtype=image_latents.dtype)
    latents = sigma0 * noise + (1.0 - sigma0) * image_latents

    return pipe(prompt=prompt, prompt_2=prompt_2,
                latents=latents, sigmas=sigmas, num_inference_steps=len(sigmas),
                control_image=init, control_strength=control_strength,
                control_stop=control_stop,
                height=init.height, width=init.width, guidance_scale=guidance,
                generator=torch.Generator("cpu").manual_seed(seed)).images[0]


def _finish_pipeline(pipe, lora, fp8):
    if fp8:
        from optimum.quanto import freeze, qfloat8, quantize
        for mod in (pipe.transformer, pipe.text_encoder_2):
            quantize(mod, weights=qfloat8)
            freeze(mod)
    if lora:
        pipe.load_lora_weights(lora)
    pipe.enable_model_cpu_offload()
    return pipe


# Distribution/migration maps occasionally slip into the scraped references
# (e.g. "Circus_cyaneus_breeding_area.png"). Fed to img2img they can survive as a
# map instead of a bird, so skip any reference whose source URL looks like a map.
MAP_URL = re.compile(
    r"(distribution|verbreitung|migration|range_map|_range\b|bird_range|"
    r"breeding_area|_area_frei|areale|aire[_-]?de[_-]?r|mapa[_-]|_map\.|"
    r"map\.png|map\.svg|map_|wintering_range|distrib)", re.I)


def _is_map_ref(path):
    """True if the reference's sidecar URL matches a distribution-map pattern."""
    try:
        with open(path + ".json", encoding="utf-8") as f:
            return bool(MAP_URL.search(json.load(f).get("url", "")))
    except Exception:
        return False


def ref_images(code, pose, want):
    """Pick up to `want` reference photos for a species+pose from raw/.

    Map-like references are dropped; they're used only as a last resort if a
    species+pose has nothing else, so we never silently skip a bird."""
    d = os.path.join(RAW_DIR, code)
    if not os.path.isdir(d):
        return []
    files = sorted(os.path.join(d, f) for f in os.listdir(d)
                   if f.startswith(pose + "_") and not f.endswith(".json"))
    birds = [f for f in files if not _is_map_ref(f)]
    return (birds or files)[:want]


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
