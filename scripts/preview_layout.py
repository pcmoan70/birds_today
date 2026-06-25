"""Render a static mockup of the artistic layout (no browser needed).

Uses the real model probabilities + the produced cutouts to composite a preview
of what the page would look like, so we can iterate on the layout design. The
placement algorithm here is the reference for docs/layout.js.

Usage:
  python preview_layout.py                       # Mode A, Stockholm, this week
  python preview_layout.py --mode B --lat 55.6 --lon 13.0 --week 18
"""
import argparse
import math
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
import onnxruntime as ort  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

from build_manifest import detect_facing  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(os.path.dirname(HERE), "docs")
BIRDS = os.path.join(DOCS, "birds")


def model_values(lat, lon, week, codes):
    sess = ort.InferenceSession(os.path.join(DOCS, "geomodel_fp16.onnx"),
                                providers=["CPUExecutionProvider"])
    labels = [l.split("\t")[0] for l in
              open(os.path.join(DOCS, "labels.txt"), encoding="utf-8").read().strip().split("\n")]
    idx = {c: i for i, c in enumerate(labels)}
    inp = np.zeros((48, 3), dtype=np.float32)
    for w in range(48):
        inp[w] = [lat, lon, w + 1]
    out = sess.run(None, {"input": inp})[0]  # 48 x n
    wi = week - 1
    vals = {}
    for c in codes:
        if c not in idx:
            continue
        col = out[:, idx[c]]
        peak = float(col.max())
        cur = float(col[wi])
        arr = 0.0 if peak < 1e-6 else (float(col[(wi + 1) % 48]) - float(col[(wi - 1) % 48])) / peak
        vals[c] = {"cur": cur, "arrival": arr, "peak": peak}
    return vals


def background(W, H):
    """Radial cream gradient like the site CSS."""
    cx, cy = W * 0.5, H * 0.45
    maxd = math.hypot(max(cx, W - cx), max(cy, H - cy))
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / maxd
    c0 = np.array([247, 244, 236]); c1 = np.array([231, 224, 207])
    img = (c0 * (1 - d[..., None]) + c1 * d[..., None]).astype(np.uint8)
    return Image.fromarray(img, "RGB").convert("RGBA")


def place(items, W, H, top=70):
    """Radial scatter around an empty centre; size ∝ value (relative spread)."""
    minside = min(W, H)
    cx, cy = W / 2, (H + top) / 2
    inner_x, inner_y = W * 0.10, H * 0.09          # small empty-centre ellipse
    vals = [it["value"] for it in items]
    vmax = max(vals) or 1.0
    # Subtle, data-true sizing: size tracks the value directly (no min-max
    # exaggeration), gentle range.
    minpx, maxpx = max(46, minside * 0.06), max(90, minside * 0.15)

    gap = 8  # extra px between birds
    placed = []
    for it in sorted(items, key=lambda d: -d["value"]):
        base = minpx + (maxpx - minpx) * (it["value"] / vmax)
        spot = None
        # Try full size, then shrink a little; never allow any overlap.
        for sf in (1.0, 0.85, 0.72):
            size = base * sf
            for _ in range(200):
                x = random.uniform(size / 2, W - size / 2)
                y = random.uniform(top + size / 2, H - size / 2)
                ex = (x - cx) / (inner_x + size * 0.5)
                ey = (y - cy) / (inner_y + size * 0.5)
                if ex * ex + ey * ey < 1.0:           # keep the empty centre clear
                    continue
                if all(math.hypot(x - q[0], y - q[1]) >= (size + q[2]) * 0.5 + gap
                       for q in placed):
                    spot = (x, y, size)
                    break
            if spot:
                break
        if spot:                                       # else: doesn't fit -> skip
            placed.append(spot)
            it["_pos"] = spot
    return [it for it in items if "_pos" in it]


def place_spiral(items, W, H, top=70):
    """Residents: most probable in the centre, spiralling outward (phyllotaxis)
    as probability drops. Size ∝ value; strict no overlap (off-screen birds are
    skipped)."""
    minside = min(W, H)
    cx, cy = W / 2, (H + top) / 2
    vmax = max(it["value"] for it in items) or 1.0
    minpx, maxpx = max(46, minside * 0.06), max(90, minside * 0.16)
    gap = 8
    golden = math.pi * (3 - math.sqrt(5))          # ~2.39996 rad
    C = (minpx + maxpx) * 0.32                      # spiral tightness
    xs, ys = 1.15, 0.82                             # stretch to fill widescreen

    placed = []
    ordered = sorted(items, key=lambda d: -d["value"])
    for i, it in enumerate(ordered):
        size = minpx + (maxpx - minpx) * (it["value"] / vmax)
        if i == 0:
            placed.append((cx, cy, size)); it["_pos"] = (cx, cy, size); continue
        spot, k = None, i
        while k < i + 6000:
            ang = k * golden
            rad = C * math.sqrt(k)
            x = cx + rad * math.cos(ang) * xs
            y = cy + rad * math.sin(ang) * ys
            if (size / 2 <= x <= W - size / 2 and
                    top + size / 2 <= y <= H - size / 2 and
                    all(math.hypot(x - q[0], y - q[1]) >= (size + q[2]) * 0.5 + gap
                        for q in placed)):
                spot = (x, y, size); break
            k += 1
        if spot:
            placed.append(spot); it["_pos"] = spot
    return [it for it in items if "_pos" in it]


def paste_with_shadow(canvas, cut, x, y, size):
    w = size
    h = int(cut.height * (size / cut.width))
    im = cut.resize((int(w), max(1, h)), Image.LANCZOS)
    # soft drop shadow from the alpha
    alpha = im.split()[3]
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.point(lambda a: int(a * 0.35)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, size * 0.02)))
    off = int(size * 0.04)
    px, py = int(x - w / 2), int(y - h / 2)
    canvas.alpha_composite(shadow, (px + off, py + off))
    canvas.alpha_composite(im, (px, py))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["A", "B"], default="A")
    ap.add_argument("--lat", type=float, default=59.33)
    ap.add_argument("--lon", type=float, default=18.07)
    ap.add_argument("--week", type=int, default=24)
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=os.path.join(HERE, "layout_preview.png"))
    args = ap.parse_args()
    random.seed(args.seed)

    stance = "sitting" if args.mode == "A" else "flying"
    # species that have a cutout for this stance
    codes = {}
    for code in sorted(os.listdir(BIRDS)):
        d = os.path.join(BIRDS, code)
        if not os.path.isdir(d):
            continue
        imgs = [f for f in os.listdir(d) if f.startswith(stance + "_") and f.endswith(".png")]
        if imgs:
            codes[code] = random.choice(imgs)
    vals = model_values(args.lat, args.lon, args.week, list(codes))

    items = []
    for code, img in codes.items():
        v = vals.get(code)
        if not v:
            continue
        value = v["cur"] if args.mode == "A" else max(0.0, v["arrival"])
        if value <= 0:
            continue
        items.append({"code": code, "img": os.path.join(BIRDS, code, img), "value": value})
    print(f"Mode {args.mode}: {len(items)} species with {stance} plates, week {args.week}")

    canvas = background(args.width, args.height)
    cx = args.width / 2
    # Mode A (residents): probability spiral. Mode B (migration): scatter.
    layout = place_spiral if args.mode == "A" else place
    for it in layout(items, args.width, args.height):
        cut = Image.open(it["img"]).convert("RGBA")
        x, y, size = it["_pos"]
        # Flip so the bird faces the centre: right-side birds should face left
        # and vice-versa.
        faces = detect_facing(it["img"])
        if (x > cx and faces == "right") or (x < cx and faces == "left"):
            cut = cut.transpose(Image.FLIP_LEFT_RIGHT)
        paste_with_shadow(canvas, cut, x, y, size)
    canvas.convert("RGB").save(args.out, quality=92)
    print("saved", args.out)


if __name__ == "__main__":
    main()
