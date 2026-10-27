"""Read MicroProse MADSPACK 2.0 files and the .FF bitmap fonts inside them.

Usage:
    python madspack.py FONTINTR.FF [FONTTINY.FF ...]   # print a font summary

Format notes (checked against the five Colonization .FF files, 2026-09-27):
  - Header: b"MADSPACK 2.0\\x1a", item count (u16 at 0x0E), then 16 item slots
    of 10 bytes from 0x10: flags u16, size u32, compressed size u32.
    Item data starts at 0xB0. Bit 0 of flags = FAB compressed.
  - FAB is the LZ scheme ScummVM calls FabDecompressor (engines/mads).
  - Font item: max height u8, max width u8, 128 widths u8, 128 offsets u16,
    then glyph data. Width slot i holds character i+1 (FONT-NP proves it:
    its 27 glyphs are space and A-Z exactly). Nothing above 127 fits.
"""
import struct
import sys

MAGIC = b"MADSPACK 2.0\x1a"


def fab_decompress(src):
    if src[:3] != b"FAB":
        raise ValueError("not a FAB stream")
    shift = src[3]
    if not 10 <= shift <= 13:
        raise ValueError("bad FAB shift %d" % shift)
    ofs_shift = 16 - shift
    ofs_mask = (0xFF << (shift - 8)) & 0xFF
    len_mask = (1 << ofs_shift) - 1
    state = {"left": 16, "buf": struct.unpack_from("<H", src, 4)[0], "p": 6}

    def bit():
        state["left"] -= 1
        if state["left"] == 0:
            word = struct.unpack_from("<H", src, state["p"])[0]
            state["buf"] = (word << 1) | (state["buf"] & 1)
            state["p"] += 2
            state["left"] = 16
        b = state["buf"] & 1
        state["buf"] >>= 1
        return b

    out = bytearray()
    while True:
        if bit():
            out.append(src[state["p"]])
            state["p"] += 1
            continue
        if bit() == 0:
            length = ((bit() << 1) | bit()) + 2
            offset = src[state["p"]] - 256
            state["p"] += 1
        else:
            p = state["p"]  # read after bit(): a refill moves p
            offset = ((((src[p + 1] >> ofs_shift) | ofs_mask) << 8) | src[p]) - 65536
            length = src[p + 1] & len_mask
            state["p"] += 2
            if length == 0:
                length = src[state["p"]]
                state["p"] += 1
                if length == 0:
                    break
                if length == 1:
                    continue
                length += 1
            else:
                length += 2
        for _ in range(length):
            out.append(out[offset])
    return bytes(out)


def read_items(data):
    if not data.startswith(MAGIC):
        raise ValueError("not MADSPACK 2.0")
    count = struct.unpack_from("<H", data, 0x0E)[0]
    items, pos = [], 0xB0
    for i in range(count):
        flags, size, csize = struct.unpack_from("<HII", data, 0x10 + 10 * i)
        raw = data[pos:pos + csize]
        body = fab_decompress(raw) if flags & 1 else raw
        if len(body) != size:
            raise ValueError("item %d: got %d bytes, header says %d" % (i, len(body), size))
        items.append(body)
        pos += csize
    return items


class Font:
    def __init__(self, item):
        self.height, self.max_width = item[0], item[1]
        self.widths = item[2:130]
        self.offsets = struct.unpack_from("<128H", item, 130)
        self.data = item

    def width(self, ch):
        """Pixel width of one character, 0 if the font has no glyph."""
        code = ord(ch) - 1
        return self.widths[code] if 0 <= code < 128 else 0

    def chars(self):
        return "".join(chr(i + 1) for i in range(128) if self.widths[i])


def load_font(path):
    with open(path, "rb") as f:
        return Font(read_items(f.read())[0])


if __name__ == "__main__":
    for path in sys.argv[1:]:
        font = load_font(path)
        have = font.chars()
        missing = "".join(chr(c) for c in range(33, 127) if chr(c) not in have)
        print("%s  height %d  max width %d  glyphs %d" % (path, font.height, font.max_width, len(have)))
        print("  missing printable ASCII: %r" % missing)
