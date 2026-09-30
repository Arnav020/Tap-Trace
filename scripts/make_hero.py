"""Composite the REAL TapTrace phone UI (from the headless console screenshot) onto the phone in the cover photo.

inputs : submission/deck_assets/hero_raw.png   (photo, generated once with ChatGPT image generation, phone screen blank)
         submission/deck_assets/shots/console.png (headless Edge screenshot of demo/ at 2x)
outputs: submission/deck_assets/hero.jpg (cover), hero_dark.jpg (closing slide)
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

A = Path(__file__).resolve().parents[1] / "submission" / "deck_assets"


def screen_box(photo: Image.Image):
    """Find the dark glass rectangle of the phone inside its rough bbox (right half of the frame)."""
    g = np.asarray(photo.convert("L"), dtype=np.float32)
    x0, x1, y0, y1 = 1000, 1320, 180, 720  # generous search window around the phone
    sub = g[y0:y1, x0:x1]
    dark = sub < 18
    cols = np.where(dark.mean(axis=0) > 0.6)[0]
    rows = np.where(dark.mean(axis=1) > 0.6)[0]
    return x0 + cols.min(), y0 + rows.min(), x0 + cols.max(), y0 + rows.max()


def main():
    photo = Image.open(A / "hero_raw.png").convert("RGB")
    # glass edges measured from brightness scans of hero_raw.png (metal frame at x=1056/1264, y=230/658)
    bx0, by0, bx1, by1 = 1065, 242, 1256, 649
    bw, bh = bx1 - bx0, by1 - by0
    print("screen box", (bx0, by0, bx1, by1), bw, bh)

    shot = Image.open(A / "shots" / "console.png").convert("RGB")
    ui = shot.crop((2020, 254, 2772, 1894))  # inner phone screen of the real console (2x screenshot)
    # cover-fit the UI into the screen box
    s = max(bw / ui.width, bh / ui.height)
    ui = ui.resize((int(ui.width * s) + 1, int(ui.height * s) + 1), Image.LANCZOS)
    ui = ui.crop((0, 0, bw, bh))
    # match the night scene: a phone at ~70% brightness, slightly warm
    ui = ImageEnhance.Brightness(ui).enhance(0.86)
    warm = Image.new("RGB", ui.size, (255, 214, 170))
    ui = Image.blend(ui, warm, 0.05)
    # glass reflection: soft diagonal highlight
    refl = Image.new("L", ui.size, 0)
    d = ImageDraw.Draw(refl)
    d.polygon([(0, 0), (int(bw * 0.55), 0), (0, int(bh * 0.35))], fill=26)
    refl = refl.filter(ImageFilter.GaussianBlur(18))
    ui = Image.composite(Image.new("RGB", ui.size, (255, 255, 255)), ui, refl)
    # rounded-corner mask
    mask = Image.new("L", ui.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=int(bw * 0.11), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(0.8))
    out = photo.copy()
    out.paste(ui, (bx0, by0), mask)

    # left-side legibility gradient (text sits on the left 50%)
    W, H = out.size
    grad = np.zeros((H, W), dtype=np.float32)
    xs = np.linspace(0, 1, W)
    grad[:] = np.clip((0.62 - xs) / 0.62, 0, 1) ** 1.4 * 0.55
    dark = Image.fromarray((grad * 255).astype("uint8"))
    out = Image.composite(Image.new("RGB", out.size, (8, 10, 16)), out, dark)
    out = out.resize((1920, int(1920 * H / W)), Image.LANCZOS)
    out.save(A / "hero.jpg", quality=92)
    darker = ImageEnhance.Brightness(out).enhance(0.45).filter(ImageFilter.GaussianBlur(2))
    darker.save(A / "hero_dark.jpg", quality=90)
    print("saved hero.jpg", out.size)


if __name__ == "__main__":
    main()
