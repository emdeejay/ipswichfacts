"""Regenerate assets/og-image.png (the social share card). One-off; run when
the headline or branding changes. Needs Pillow (not a build dependency —
the PNG is committed):  pip install pillow && python assets/make_og_image.py
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

W, H = 1200, 630
ACCENT, FG, MUTED, BG = (0, 82, 56), (26, 26, 26), (102, 102, 102), (253, 253, 253)


def font(sz, bold=False):
    for p in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(p, sz)
        except OSError:
            continue
    return ImageFont.load_default()


img = Image.new("RGB", (W, H), BG)
d = ImageDraw.Draw(img)
d.rectangle([0, 0, 16, H], fill=ACCENT)
d.text((72, 72), "Ipswich Facts", font=font(46, bold=True), fill=ACCENT)
y = 190
for line in ("What your Council is doing,", "who decided it,", "and what it costs."):
    d.text((72, y), line, font=font(66, bold=True), fill=FG)
    y += 84
d.text((72, y + 20), "Projects, road closures, meeting decisions, budgets —", font=font(30), fill=MUTED)
d.text((72, y + 62), "Ipswich City Council data, joined up and searchable.", font=font(30), fill=MUTED)
d.text((72, H - 76), "ipswichfacts.au", font=font(30, bold=True), fill=ACCENT)
d.text((72, H - 40), "Unofficial · CC BY 4.0", font=font(22), fill=MUTED)
img.save(Path(__file__).parent / "og-image.png", optimize=True)
print("wrote assets/og-image.png")
