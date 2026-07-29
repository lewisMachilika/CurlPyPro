"""Generate the Inno Setup wizard images from assets/icon-master.png.

Inno Setup 6.5+ accepts .png for WizardImageFile / WizardSmallImageFile and picks
the variant closest to the user's DPI scaling, so one file per scaling is emitted.

Usage (from project root):  .venv/Scripts/python.exe scripts/make_wizard_images.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "assets" / "icon-master.png"
OUT_DIR = ROOT / "assets" / "installer"

# Base geometry at 100% DPI, straight from the Inno Setup docs.
LARGE_W, LARGE_H = 164, 314
SMALL = 58
SCALES = (1.0, 1.25, 1.5, 2.0)

# Sampled from the icon itself.
NAVY_TOP = (10, 46, 69)
NAVY_BOTTOM = (2, 20, 37)
TEAL = (2, 216, 170)
MUTED = (127, 168, 190)

SS = 4  # supersampling factor for the artwork; text is drawn at native size

FONT_BOLD = "C:/Windows/Fonts/segoeuib.ttf"
FONT_REG = "C:/Windows/Fonts/segoeui.ttf"


def vertical_gradient(size, top, bottom):
    w, h = size
    grad = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return grad.resize((w, h), Image.Resampling.BILINEAR)


def teal_glow(size, centre, radius):
    """Soft radial teal wash behind the icon."""
    w, h = size
    glow = Image.new("L", (w, h), 0)
    px = glow.load()
    cx, cy = centre
    for y in range(h):
        for x in range(w):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            if d < radius:
                t = 1.0 - d / radius
                px[x, y] = int(70 * t * t)
    return glow


def draw_centred(draw, y, text, font, fill, width):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text((round((width - (right - left)) / 2) - left, y - top), text, font=font, fill=fill)
    return bottom - top


def make_large(icon, scale):
    w, h = round(LARGE_W * scale), round(LARGE_H * scale)
    canvas = vertical_gradient((w * SS, h * SS), NAVY_TOP, NAVY_BOTTOM)

    icon_px = round(96 * scale) * SS
    icon_top = round(58 * scale) * SS
    centre = (w * SS // 2, icon_top + icon_px // 2)
    canvas.paste(Image.new("RGB", (w * SS, h * SS), TEAL), (0, 0), teal_glow((w * SS, h * SS), centre, icon_px))

    art = icon.resize((icon_px, icon_px), Image.Resampling.LANCZOS)
    canvas.paste(art, (centre[0] - icon_px // 2, icon_top), art)

    canvas = canvas.resize((w, h), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(canvas)

    title_y = round(170 * scale)
    draw_centred(draw, title_y, "CurlPyPro", ImageFont.truetype(FONT_BOLD, round(17 * scale)), "white", w)

    rule_w, rule_h = round(30 * scale), max(1, round(2 * scale))
    rule_y = round(198 * scale)
    draw.rectangle([(w - rule_w) // 2, rule_y, (w + rule_w) // 2, rule_y + rule_h - 1], fill=TEAL)

    draw_centred(draw, round(212 * scale), "API request client",
                 ImageFont.truetype(FONT_REG, round(9.5 * scale)), MUTED, w)
    return canvas


def make_small(icon, scale):
    side = round(SMALL * scale)
    return icon.resize((side, side), Image.Resampling.LANCZOS)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    icon = Image.open(MASTER).convert("RGBA")
    icon = icon.crop(icon.getchannel("A").getbbox())  # trim the transparent margin

    for scale in SCALES:
        large = make_large(icon, scale)
        large_path = OUT_DIR / f"wizard-large-{large.width}x{large.height}.png"
        large.save(large_path)

        small = make_small(icon, scale)
        small_path = OUT_DIR / f"wizard-small-{small.width}x{small.height}.png"
        small.save(small_path)

        print(f"{scale:>5.0%}  {large_path.name}  {small_path.name}")


if __name__ == "__main__":
    main()
