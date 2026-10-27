"""Extract every translatable string of the game into one JSON file.

Usage: python extract.py GAME_DIR [OUT_JSON]      (default ro/work/en.json)

Each entry: id, file, section, line (0-based line in the file), field
(NAMES.TXT only), kind, block/block_kind (dialog files), text, keep, vars,
bytes, px (pixel width in FONTINTR and FONTTINY).

block_kind for dialog files:
  prose - consecutive lines the game re-wraps; the line count may change.
  list  - option lines (after @options/@checkbox or after the first blank
          line of a section); the line count must stay the same.
  caret - a line starting with '^' (centred / hard break); stays one line.
The output quotes game text, so it goes to ro/work/ (git-ignored).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import madspack  # noqa: E402
from textfiles import (DIALOG_FILES, LIST_FILES, TABLE_FILES, classify, heap_proxy,  # noqa: E402
                       is_translatable_field, placeholders, read_lines, table_fields)


def text_px(font, text):
    return sum(font.width(c) for c in text)


def dialog_entries(name, lines):
    out, section, blank_seen, list_mode, block = [], None, False, False, 0
    prev_text = False
    for i, line in enumerate(lines):
        kind = classify(line)
        if kind == "section":
            section, blank_seen, list_mode, prev_text = line.strip()[1:], False, False, False
            continue
        if section is None or kind == "comment":
            continue
        if kind == "directive":
            if line.startswith(("@options", "@checkbox")):
                list_mode = True
            prev_text = False
            continue
        if kind in ("blank", "disabled"):
            if kind == "blank":
                blank_seen = True
            prev_text = False
            continue
        stripped = line.strip()
        if set(stripped) <= set("^_"):
            continue                                   # spacer or input field
        if stripped.startswith("^"):
            block, bkind, prev_text = block + 1, "caret", False
        else:
            bkind = "list" if (list_mode or blank_seen) else "prose"
            if not (prev_text and bkind == "prose"):
                block += 1
            prev_text = bkind == "prose"
        out.append({"file": name, "section": section, "line": i, "field": None,
                    "kind": "text", "block": block, "block_kind": bkind, "text": line})
    return out


def table_entries(name, lines):
    out, section = [], None
    for i, line in enumerate(lines):
        kind = classify(line)
        if kind == "section":
            section = line.strip()[1:]
            continue
        if section is None or kind != "text":
            continue
        for idx, _, _, value in table_fields(line):
            if is_translatable_field(section, idx, value) or (value and not value[0].isdigit()
                                                               and value[0] not in "-+"):
                out.append({"file": name, "section": section, "line": i, "field": idx,
                            "kind": "field", "text": value,
                            "keep": not is_translatable_field(section, idx, value)})
    return out


def list_entries(name, lines):
    out, section = [], None
    for i, line in enumerate(lines):
        kind = classify(line)
        if kind == "section":
            section = line.strip()[1:]
            continue
        if section is None or kind in ("comment", "directive", "disabled"):
            continue
        # every line is an entry, blank ones too (entries are positional)
        out.append({"file": name, "section": section, "line": i, "field": None,
                    "kind": "entry", "text": line})
    return out


def extract(game_dir):
    fonts = {k: madspack.load_font(os.path.join(game_dir, f))
             for k, f in (("intr", "FONTINTR.FF"), ("tiny", "FONTTINY.FF"))}
    entries = []
    for name in DIALOG_FILES + TABLE_FILES + LIST_FILES:
        path = os.path.join(game_dir, name)
        if not os.path.exists(path):
            continue
        lines = read_lines(path)
        if name in DIALOG_FILES:
            entries += dialog_entries(name, lines)
        elif name in TABLE_FILES:
            entries += table_entries(name, lines)
        else:
            entries += list_entries(name, lines)
    for e in entries:
        t = e["text"]
        e.setdefault("keep", False)
        e["id"] = "%s/%s/%d%s" % (e["file"], e["section"], e["line"],
                                  "" if e["field"] is None else "/%d" % e["field"])
        e["vars"] = placeholders(t)
        e["bytes"] = len(t.strip())
        e["px"] = {k: text_px(f, t.strip()) for k, f in fonts.items()}
    return entries


def main():
    game_dir = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "ro/work/en.json"
    entries = extract(game_dir)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(entries, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    by_file = {}
    for e in entries:
        f = by_file.setdefault(e["file"], [0, 0, 0])
        f[0] += 1
        f[1] += 0 if e["keep"] else len(e["text"].split())
        f[2] += 1 if e["keep"] else 0
    for name, (n, words, kept) in by_file.items():
        print("%-12s entries %5d  words %6d  kept as-is %4d" % (name, n, words, kept))
    print("heap proxy (NAMES string fields + LABELS entries, +1 each): %d bytes" % heap_proxy(
        read_lines(os.path.join(game_dir, "NAMES.TXT")), read_lines(os.path.join(game_dir, "LABELS.TXT"))))
    print("->", out)


if __name__ == "__main__":
    main()
