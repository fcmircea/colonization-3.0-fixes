#!/usr/bin/env python3
"""Build the CD shim as a ?SOUND.COL: assemble cdshim.s, add the MZ header.

    python3 cdshim/build.py -o play/cdshim/PSOUND.COL [--fm PSOUND.FM] [--log]
    python3 cdshim/build.py --shimtest play/cdshim/SHIMTEST.COM     (test program)

--fm  the 8.3 name the original driver gets next to the shim (default PSOUND.FM)
--log write CDSHIM.LOG (counts, the last 256 commands, init arguments) after
      every command and at shutdown; for tests

Needs GNU as and ld (binutils). Exit codes: 0 ok, 2 usage, 3 build failed.
The MZ header carries two relocations, body 28h (code segment) and 2Ah (data
segment): DOS adds the load segment to both, which is what VICEROY reads.
"""
import argparse
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "cdshim.s"

# fixed body offsets, the same as the .org lines in cdshim.s
CODE_SEG, DATA_SEG, IMAGE_SIZE, MAGIC_64, COUNT, ENTRIES = 0x28, 0x2A, 0x2C, 0x2E, 0x30, 0x32
FM_NAME, FM_NAME_LEN, LOG_ON = 0x48, 13, 0x55
TRACK_TAB = 0x8D                    # 32 bytes: CD track for music command 20h + i, 0 = FM

# Music command -> CD track: KB game-cd-track-map (the Windows game's own table).
# Decided 2026-10-24: 2D = 14 (Florin's ear); a command without its own track
# falls back to FM: 28, 2A (York Fusiliers), 3A (Hole In The Wall).
# The jingles 3C, 3E, 3F (Windows: GOLDCITY, MEETKING, 1812.WAV) are tracks
# 28-30, appended by cue2audio.py --jingles; on the 27-track disc they are FM.
TRACKS = {
    0x20: 9, 0x21: 6, 0x22: 13, 0x23: 10, 0x24: 22, 0x25: 3, 0x26: 25, 0x27: 19,
    0x29: 7, 0x2B: 20, 0x2C: 23, 0x2D: 14, 0x2E: 26, 0x2F: 11,
    0x30: 16, 0x31: 21, 0x32: 8, 0x33: 5, 0x34: 2, 0x35: 12, 0x36: 17, 0x37: 15,
    0x38: 4, 0x39: 24, 0x3B: 18, 0x3D: 27,
    0x3C: 28, 0x3E: 29, 0x3F: 30,
}
N_ENTRIES = 11
HEADER_PARAS = 3                    # 28-byte header + 2 relocations, padded to 48 bytes


def assemble(source=SOURCE, origin=0):
    """Return the flat binary of a 16-bit source (cdshim.s: origin 0; a .COM: 100h)."""
    with tempfile.TemporaryDirectory() as tmp:
        obj, out = Path(tmp) / "x.o", Path(tmp) / "x.bin"
        subprocess.run(["as", "--32", "-o", str(obj), str(source)], check=True)
        subprocess.run(["ld", "-m", "elf_i386", "-Ttext", hex(origin), "-e", "start",
                        "--oformat", "binary", "-o", str(out), str(obj)], check=True)
        return out.read_bytes()


def valid_83(name):
    return re.fullmatch(r"[A-Z0-9_-]{1,8}(\.[A-Z0-9_-]{1,3})?", name) is not None


def make_driver(body, fm_name="PSOUND.FM", log=False):
    """Fill the body fields and wrap it in an MZ header. Returns the file bytes."""
    fm_name = fm_name.upper()
    if not valid_83(fm_name):
        raise ValueError(f"not a DOS 8.3 name: {fm_name!r}")
    body = bytearray(body)
    struct.pack_into("<H", body, IMAGE_SIZE, len(body))
    body[FM_NAME:FM_NAME + FM_NAME_LEN] = fm_name.encode("ascii").ljust(FM_NAME_LEN, b"\0")
    body[LOG_ON] = 1 if log else 0
    body[TRACK_TAB:TRACK_TAB + 32] = bytes(TRACKS.get(0x20 + i, 0) for i in range(32))

    header = bytearray(HEADER_PARAS * 16)
    size = len(header) + len(body)
    struct.pack_into("<2s13H", header, 0,
                     b"MZ",
                     size % 512,                  # bytes in the last page
                     (size + 511) // 512,         # pages
                     2,                           # relocations
                     HEADER_PARAS,
                     0, 0xFFFF,                   # min, max extra paragraphs
                     0, 0,                        # SS, SP (unused by 4B03h)
                     0,                           # checksum
                     0, 0,                        # IP, CS (unused by 4B03h)
                     0x1C,                        # relocation table offset
                     0)                           # overlay number
    struct.pack_into("<4H", header, 0x1C, CODE_SEG, 0, DATA_SEG, 0)
    return bytes(header + body)


def fields(data):
    """The fields VICEROY reads, from a built (or original) ?SOUND.COL."""
    hdr = struct.unpack_from("<H", data, 8)[0] * 16
    nrel, reloff = struct.unpack_from("<H", data, 6)[0], struct.unpack_from("<H", data, 0x18)[0]
    body = data[hdr:]
    w = lambda o: struct.unpack_from("<H", body, o)[0]
    return {
        "header_bytes": hdr,
        "relocations": [struct.unpack_from("<2H", data, reloff + 4 * i) for i in range(nrel)],
        "code_seg": w(CODE_SEG), "data_seg": w(DATA_SEG), "image_size": w(IMAGE_SIZE),
        "magic_64": w(MAGIC_64), "count": w(COUNT),
        "entries": [w(ENTRIES + 2 * i) for i in range(w(COUNT))],
        "tracks": {0x20 + i: t for i, t in enumerate(body[TRACK_TAB:TRACK_TAB + 32]) if t},
        "body_len": len(body),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--shimtest", type=Path, help="also build SHIMTEST.COM (shimtest.s) here")
    ap.add_argument("--fm", default="PSOUND.FM")
    ap.add_argument("--log", action="store_true")
    a = ap.parse_args(argv)
    if not valid_83(a.fm.upper()):
        print(f"--fm: not a DOS 8.3 name: {a.fm}", file=sys.stderr)
        return 2
    if not a.out and not a.shimtest:
        ap.error("give -o and/or --shimtest")
    try:
        if a.shimtest:
            a.shimtest.parent.mkdir(parents=True, exist_ok=True)
            a.shimtest.write_bytes(assemble(HERE / "shimtest.s", 0x100))
            print(f"{a.shimtest}: {a.shimtest.stat().st_size} bytes")
        if not a.out:
            return 0
        data = make_driver(assemble(), a.fm, a.log)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"build failed: {e}", file=sys.stderr)
        return 3
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(data)
    f = fields(data)
    print(f"{a.out}: {len(data)} bytes, body {f['body_len']}, keep {f['image_size']} bytes, "
          f"fm {a.fm.upper()}, log {'on' if a.log else 'off'}")
    print("relocations", [(hex(o), s) for o, s in f["relocations"]],
          "entries", [hex(e) for e in f["entries"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
