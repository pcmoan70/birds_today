"""Assemble images into a single labelled contact-sheet PNG for quick review.

Usage:
  python make_montage.py <out.png> <cols> <thumb> <img_or_glob> [more...]
Each input image is drawn in a cell with its filename (sans extension) as a
caption, on a light background. Transparent PNGs are flattened onto the cell.
"""
import glob
import os
import sys

from PIL import Image, ImageDraw, ImageFont

BG = (243, 239, 230)
CELL = (255, 255, 255)
INK = (43, 42, 38)


def expand(args):
    out = []
    for a in args:
        m = sorted(glob.glob(a))
        out.extend(m if m else [a])
    return [p for p in out if os.path.isfile(p)]


def font(sz):
    for f in ("arial.ttf", "DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(f, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def main():
    out_path = sys.argv[1]
    cols = int(sys.argv[2])
    thumb = int(sys.argv[3])
    paths = expand(sys.argv[4:])
    if not paths:
        print("no images"); return
    pad, cap = 10, 20
    cw, ch = thumb + 2 * pad, thumb + 2 * pad + cap
    rows = (len(paths) + cols - 1) // cols
    W, H = cols * cw, rows * ch
    sheet = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(sheet)
    ft = font(13)
    for i, p in enumerate(paths):
        r, c = divmod(i, cols)
        x0, y0 = c * cw, r * ch
        d.rectangle([x0 + pad // 2, y0 + pad // 2, x0 + cw - pad // 2,
                     y0 + ch - pad // 2], fill=CELL)
        try:
            im = Image.open(p).convert("RGBA")
            bgc = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bgc, im).convert("RGB")
            im.thumbnail((thumb, thumb))
            ox = x0 + (cw - im.width) // 2
            oy = y0 + pad + (thumb - im.height) // 2
            sheet.paste(im, (ox, oy))
        except Exception as e:
            d.text((x0 + pad, y0 + pad), "ERR", fill=INK, font=ft)
        name = os.path.splitext(os.path.basename(p))[0]
        d.text((x0 + pad, y0 + ch - cap), name[:28], fill=INK, font=ft)
    sheet.save(out_path)
    print("wrote", out_path, f"({len(paths)} imgs, {W}x{H})")


if __name__ == "__main__":
    main()
