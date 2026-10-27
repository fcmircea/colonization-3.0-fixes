"""Render sample text with the built fonts to a PNG (needs Pillow).

Usage: python preview.py FONT_DIR OUT.PNG ["text" ...]

Colours are a guess at the game's: wood background, green main colour,
darker green edge, black shadow. Good enough to judge shapes.
"""
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fontedit  # noqa: E402
import madspack  # noqa: E402

PALETTE = {0: (92, 56, 30), 1: (140, 214, 90), 2: (20, 16, 10), 3: (70, 150, 50)}
SAMPLES = ["ăâîșț ĂÂÎȘȚ", "Țesătorie, Întărește, Mlaștină împădurită",
           "Craiul din Anglia. Luminăția Voastră, vistieria e goală!",
           "ÎN NUMELE CRAIULUI: ȚARA ROMÂNEASCĂ"]


def to_codes(text):
    return [fontedit.SLOTS.get(c, ord(c)) for c in text]


def render(font, text, scale=4):
    codes = [c for c in to_codes(text) if 0 < c <= 128 and font.widths[c - 1]]
    width = sum(font.widths[c - 1] + 1 for c in codes) + 2
    img = Image.new("RGB", (width, font.height + 2), PALETTE[0])
    x = 1
    for c in codes:
        for y, row in enumerate(fontedit.glyph_rows(font, c)):
            for dx, p in enumerate(row):
                if p != ".":
                    img.putpixel((x + dx, y + 1), PALETTE[int(p)])
        x += font.widths[c - 1] + 1
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST)


def main():
    font_dir, out = sys.argv[1], sys.argv[2]
    texts = sys.argv[3:] or SAMPLES
    lines = []
    for name in fontedit.FONTS:
        font = madspack.load_font(os.path.join(font_dir, name))
        for t in texts:
            lines.append(render(font, t.upper() if name == "FONT-NP.FF" else t))
    sheet = Image.new("RGB", (max(i.width for i in lines), sum(i.height + 8 for i in lines)), (40, 30, 20))
    y = 0
    for img in lines:
        sheet.paste(img, (0, y))
        y += img.height + 8
    sheet.save(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
