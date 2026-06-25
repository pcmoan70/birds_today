"""Apply a vanishing (elliptical vignette) gradient to the web plates so any
residual scan-edge or label artefacts near the borders fade out cleanly.

Per image:
  1. Fit an axis-aligned ellipse, centred on the alpha-weighted content centroid,
     whose semi-axes match the content spread and which contains 95% of the image
     "information" (alpha mass).
  2. Inside that ellipse the image is preserved at 100%.
  3. From the ellipse outwards, alpha ramps linearly to 0 exactly at the image
     border (along every direction), so the very edge is fully transparent.

  python vignette_plates.py            # apply in place to docs/plates/**.png
"""
import glob
import os
import sys

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
PLATES = os.path.join(os.path.dirname(HERE), "docs", "plates")
KEEP = 0.95   # fraction of content (alpha mass) kept fully opaque (inside ellipse)


def apply_vignette(im):
    """Return a new RGBA image with the vanishing gradient applied."""
    arr = np.asarray(im.convert("RGBA")).astype(np.float32)
    h, w, _ = arr.shape
    a = arr[:, :, 3]
    tot = float(a.sum())
    if tot <= 0:
        return im

    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    cx = float((a * xs).sum() / tot)
    cy = float((a * ys).sum() / tot)
    dx, dy = xs - cx, ys - cy
    sx = max(1.0, float(np.sqrt((a * dx * dx).sum() / tot)))
    sy = max(1.0, float(np.sqrt((a * dy * dy).sum() / tot)))

    # 95th alpha-mass percentile of the normalised elliptical radius
    rho = np.sqrt((dx / sx) ** 2 + (dy / sy) ** 2)
    order = np.argsort(rho, axis=None)
    cum = np.cumsum(a.ravel()[order])
    r95 = float(rho.ravel()[order][np.searchsorted(cum, KEEP * tot)])
    ae, be = max(1.0, r95 * sx), max(1.0, r95 * sy)   # ellipse semi-axes (px)

    # Along each pixel's ray from the centroid: distance to the image border and
    # to the 95% ellipse; alpha factor ramps 1 (at/inside ellipse) -> 0 (border).
    s_pix = np.sqrt(dx * dx + dy * dy)
    safe = np.where(s_pix == 0, 1.0, s_pix)
    ux, uy = dx / safe, dy / safe
    with np.errstate(divide="ignore", invalid="ignore"):
        tx = np.where(ux > 0, (w - 1 - cx) / ux,
                      np.where(ux < 0, (-cx) / ux, np.inf))
        ty = np.where(uy > 0, (h - 1 - cy) / uy,
                      np.where(uy < 0, (-cy) / uy, np.inf))
        s_border = np.minimum(tx, ty)
        s_ell = 1.0 / np.sqrt((ux / ae) ** 2 + (uy / be) ** 2)
    frac = s_pix / s_border
    frac_e = s_ell / s_border
    denom = 1.0 - frac_e
    factor = np.where(denom > 1e-6,
                      np.clip((1.0 - frac) / np.maximum(denom, 1e-6), 0.0, 1.0),
                      1.0)            # ellipse reaches/exceeds the border: no fade
    factor[s_pix == 0] = 1.0

    arr[:, :, 3] = a * factor
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def main():
    paths = sorted(glob.glob(os.path.join(PLATES, "**", "*.png"), recursive=True))
    for i, p in enumerate(paths, 1):
        out = apply_vignette(Image.open(p))
        out = out.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
        out.save(p, optimize=True)
        if i % 100 == 0:
            print(f"  {i}/{len(paths)}")
    print(f"vignetted {len(paths)} plate(s)")


if __name__ == "__main__":
    main()
