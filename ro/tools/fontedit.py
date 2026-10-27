"""Add the Romanian letters to the game fonts.

Usage: python fontedit.py SRC_DIR DEST_DIR [--preview PNG]

Reads the English .FF fonts from SRC_DIR, draws the glyphs from
ro/fonts/ro-glyphs.txt into the slots in SLOTS, writes the new fonts to
DEST_DIR, then decodes them again and checks every glyph survived.

Glyph pixels are 2 bits: '.' = 0 (clear), '1' main colour, '2' shadow,
'3' edge. Rows are packed 4 pixels per byte, high bits first; a row takes
ceil(width / 4) bytes.

MADSPACK is written with a literal-only FAB stream (every byte stored as a
literal). Bigger than MicroProse's compressed files but valid for the same
decoder; fab_decompress() is run on the result as a check.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import madspack  # noqa: E402

# Romanian letter -> byte in the built text files. 0x1C-0x1F, 0x5C, 0x60 and
# 0x7F are proven with our EXE (German GAME.TXT, test TESTA, 2026-09-27).
# NEVER 0x7C '|': in dialogs it is a layout code (text after it jumps right or
# vanishes). Build 2 had Î there; the intro, the Spain text and the main menu broke.
# 0x14-0x16 carry Ș, Ț, Â (the Spanish fonts reserved 0x14-0x18); not yet proven.
SLOTS = {"ă": 0x1C, "â": 0x1D, "î": 0x1E, "ș": 0x1F, "ț": 0x60,
         "Ă": 0x5C, "Î": 0x7F, "Â": 0x16, "Ș": 0x14, "Ț": 0x15,
         # plain t redrawn in FONTTINY and FONTINTR (its foot hung below the
         # baseline and read like the comma of ț; re-tests S6/S7, 2026-09-27)
         "t": 0x74}
FONTS = ("FONTINTR.FF", "FONTTINY.FF", "FONTKING.FF", "FONT-NP.FF")
GLYPH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fonts", "ro-glyphs.txt")


def glyph_rows(font, code):
    i = code - 1
    w, off = font.widths[i], font.offsets[i]
    bpr = (w + 3) // 4
    rows = []
    for r in range(font.height):
        px = []
        for b in font.data[off + r * bpr: off + (r + 1) * bpr]:
            px += [(b >> (6 - 2 * k)) & 3 for k in range(4)]
        rows.append("".join(".123"[p] for p in px[:w]))
    return rows


def pack_rows(rows):
    out = bytearray()
    for row in rows:
        vals = [".123".index(c) for c in row]
        vals += [0] * (-len(vals) % 4)
        for k in range(0, len(vals), 4):
            out.append((vals[k] << 6) | (vals[k + 1] << 4) | (vals[k + 2] << 2) | vals[k + 3])
    return bytes(out)


def build_font(font, new_glyphs):
    """Font item bytes with new_glyphs {code: rows} replacing or adding slots."""
    glyphs = {}
    for code in range(1, 129):
        if font.widths[code - 1]:
            glyphs[code] = glyph_rows(font, code)
    glyphs.update(new_glyphs)
    widths = [0] * 128
    offsets = [0] * 128
    data = bytearray()
    base = 2 + 128 + 256
    for code in range(1, 129):
        rows = glyphs.get(code)
        if not rows:
            continue
        if len(rows) != font.height or len(set(map(len, rows))) != 1:
            raise ValueError("glyph %#x: need %d rows of equal width" % (code, font.height))
        widths[code - 1] = len(rows[0])
        offsets[code - 1] = base + len(data)
        data += pack_rows(rows)
    max_w = max(font.max_width, max(widths))
    return bytes([font.height, max_w]) + bytes(widths) + struct.pack("<128H", *offsets) + bytes(data)


def fab_literal(data, shift=12):
    """A FAB stream that stores every byte as a literal, then the end mark."""
    out = bytearray(b"FAB" + bytes([shift]) + b"\0\0")
    word_pos, nbits = 4, 0

    def put(bit):
        nonlocal word_pos, nbits
        i = nbits % 16
        if i == 15:                      # the reader loads the next word here
            next_pos = len(out)
            out.extend(b"\0\0")
        if bit:
            w = struct.unpack_from("<H", out, word_pos)[0] | (1 << i)
            struct.pack_into("<H", out, word_pos, w)
        if i == 15:
            word_pos = next_pos
        nbits += 1

    for b in data:
        put(1)
        out.append(b)
    put(0)
    put(1)
    out.extend(b"\0\0\0")               # long copy, length 0, then 0 = end
    return bytes(out)


def write_madspack(template, item):
    """Same header as the original file, one item, new sizes."""
    head = bytearray(template[:0xB0])
    flags = struct.unpack_from("<H", head, 0x10)[0] | 1
    stream = fab_literal(item)
    struct.pack_into("<HII", head, 0x10, flags, len(item), len(stream))
    return bytes(head) + stream


def load_glyph_file(path=GLYPH_FILE):
    """{font_name: {letter: rows}} from blocks like '[FONTINTR ă]' followed by rows."""
    out, cur = {}, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#") or not line.strip():
                continue
            if line.startswith("["):
                name, letter = line.strip("[]").split()
                cur = out.setdefault(name + ".FF", {}).setdefault(letter, [])
                continue
            cur.append(line.strip())
    return out


def main():
    src, dest = sys.argv[1], sys.argv[2]
    os.makedirs(dest, exist_ok=True)
    drawn = load_glyph_file()
    for name in FONTS:
        template = open(os.path.join(src, name), "rb").read()
        font = madspack.Font(madspack.read_items(template)[0])
        new = {}
        for letter, rows in drawn.get(name, {}).items():
            new[SLOTS[letter]] = rows
        item = build_font(font, new)
        packed = write_madspack(template, item)
        # check: decode what we wrote
        back = madspack.read_items(packed)[0]
        if back != item:
            raise SystemExit("%s: round trip failed" % name)
        check = madspack.Font(back)
        for code, rows in new.items():
            assert glyph_rows(check, code) == rows, (name, hex(code))
        for code in range(1, 129):
            if font.widths[code - 1] and code not in new:
                assert glyph_rows(check, code) == glyph_rows(font, code), (name, code)
        with open(os.path.join(dest, name), "wb") as f:
            f.write(packed)
        print("%-12s %d glyphs added, %d -> %d bytes" % (name, len(new), len(template), len(packed)))


if __name__ == "__main__":
    main()
