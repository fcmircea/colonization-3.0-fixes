"""Build the Romanian game files into a game folder copy.

Usage: python build.py EN_GAME_DIR RO_SRC_DIR FONT_DIR TARGET_GAME_DIR [--plain]

1. Runs the checker on RO_SRC_DIR against EN_GAME_DIR; stops on any error.
2. Encodes each translated source (UTF-8) into the game's bytes: Romanian
   letters -> font slots (fontedit.SLOTS), everything else latin-1, CRLF.
   --plain writes the letters without diacritics instead (no font change needed).
3. Copies the Romanian fonts from FONT_DIR (not with --plain).
Files missing from RO_SRC_DIR stay English. TARGET_GAME_DIR must already be a
game folder (it holds VICEROY.EXE); this script never touches anything else.
"""
import os
import shutil
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check  # noqa: E402
import fontedit  # noqa: E402
from textfiles import DIALOG_FILES, LIST_FILES, TABLE_FILES  # noqa: E402

LIVE_GAME = os.path.normcase(os.path.abspath(r"D:\Games\Games\MPS\COLONIZE"))


def encode(text, plain=False):
    out = bytearray()
    for ch in text:
        if ch in fontedit.SLOTS:
            if plain:
                out += unicodedata.normalize("NFD", ch)[0].encode("latin-1")
            else:
                out.append(fontedit.SLOTS[ch])
        else:
            out += ch.encode("latin-1")         # raises on anything the game cannot hold
    return bytes(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    plain = "--plain" in sys.argv
    en_dir, src_dir, font_dir, target = args
    if os.path.normcase(os.path.abspath(target)) == LIVE_GAME:
        raise SystemExit("refusing to write into the live game folder")
    if not os.path.exists(os.path.join(target, "VICEROY.EXE")):
        raise SystemExit("%s is not a game folder" % target)
    errors, delta, n = check.run_check(en_dir, src_dir)
    if errors:
        for e in errors:
            print("ERROR", e)
        raise SystemExit("check failed: %d errors, nothing written" % len(errors))
    print("check OK: %d files, heap %+d bytes vs English" % (n, delta))
    for name in DIALOG_FILES + TABLE_FILES + LIST_FILES:
        src = os.path.join(src_dir, name)
        if not os.path.exists(src):
            continue
        text = open(src, encoding="utf-8").read().replace("\r\n", "\n").replace("\n", "\r\n")
        data = encode(text, plain)
        with open(os.path.join(target, name), "wb") as f:
            f.write(data)
        print("  %-12s %6d bytes" % (name, len(data)))
    if not plain:
        for name in fontedit.FONTS:
            shutil.copyfile(os.path.join(font_dir, name), os.path.join(target, name))
            print("  %-12s (font)" % name)
    print("built into", target)


if __name__ == "__main__":
    main()
