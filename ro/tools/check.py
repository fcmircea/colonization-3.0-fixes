"""Check translated text files against the English originals.

Usage: python check.py EN_DIR RO_DIR [--budget N]

RO files are the translation *source*: UTF-8, real diacritics. (build.py later
maps the diacritics to font slots.) Only files present in RO_DIR are checked.

Errors (the game may break or show wrong text):
  - sections missing, extra, or out of order
  - dialog files: directives and disabled lines changed; list/caret blocks
    with a different line count; a line's leading '^' changed; placeholders
    (%STRING0 ...) not the same per section; unbalanced { }; %string<n>
    (lower-case first letter) outside LOWER_OK
  - list files: line count per section changed; placeholders, '~' hotkey
    markers or '#' column separators per line changed
  - NAMES.TXT: field count per line changed; a kept field (numbers, names,
    key letters) changed
  - characters the fonts cannot draw, or the bytes reserved for diacritic
    glyphs used as plain characters
  - NAMES + LABELS heap proxy above English + budget (default 100 bytes, the
    known-safe value from test NL100, 2026-09-27)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fontedit  # noqa: E402
import hotkey_patch  # noqa: E402
from textfiles import (DIALOG_FILES, LIST_FILES, TABLE_FILES, classify, heap_proxy,  # noqa: E402
                       is_translatable_field, lower_slots, placeholders, table_fields)

RO_LETTERS = "ăâîșțĂÂÎȘȚ"
RESERVED = set("[\\]`|")               # become diacritic glyph slots in the build
# 0xF9 is PEDIA.TXT's bullet; one tab occurs in PEDIA.TXT (English original).
ALLOWED = set(chr(c) for c in range(32, 127)) - RESERVED | set(RO_LETTERS) | {"\xf9", "\t"}


def read(path, encoding):
    """Lines of a file; CRLF (game files) and LF (translation sources) both work."""
    with open(path, "rb") as f:
        return f.read().decode(encoding).replace("\r\n", "\n").split("\n")


def sections(lines):
    out, cur = {}, None
    order = []
    for i, line in enumerate(lines):
        if classify(line) == "section":
            cur = line.strip()[1:]
            out[cur] = []
            order.append(cur)
        elif cur is not None:
            out[cur].append(line)
    return order, out


def blocks(lines):
    """Structure of a dialog section: list of (kind, payload)."""
    out, list_mode, blank_seen, prose = [], False, False, None
    for line in lines:
        kind = classify(line)
        if kind == "comment":
            continue
        if kind in ("directive", "disabled"):
            if line.startswith(("@options", "@checkbox")):
                list_mode = True
            out.append(("fixed", line.rstrip()))
            prose = None
            continue
        if kind == "blank":
            blank_seen, prose = True, None
            out.append(("blank", ""))
            continue
        s = line.strip()
        if set(s) <= set("^_"):
            out.append(("fixed", s))
            prose = None
        elif s.startswith("^"):
            out.append(("caret", len(s) - len(s.lstrip("^"))))
            prose = None
        elif list_mode or blank_seen:
            out.append(("list", 1))
            prose = None
        elif prose is None:
            prose = ["prose", 0]
            out.append(prose)
    return [tuple(b) if isinstance(b, list) else b for b in out]


class Report:
    def __init__(self):
        self.errors = []

    def err(self, where, msg):
        self.errors.append("%s: %s" % (where, msg))


def check_chars(rep, name, lines):
    for i, line in enumerate(lines):
        if classify(line) == "comment":
            continue
        bad = sorted(set(c for c in line if c not in ALLOWED))
        if bad:
            rep.err("%s:%d" % (name, i + 1), "characters the fonts cannot draw: %r" % "".join(bad))


# Sections that may drop or repeat placeholders (only English ones allowed).
# TEAPARTY: %STRING3 is "<colony> <goods>" glued in English order; the title
# uses %STRING0/%STRING1 instead ("Partida de blănuri de la Boston").
PLACEHOLDERS_FREE = {"TEAPARTY"}

# Sections whose placeholders an EXE patch changes: the Romanian text must have
# exactly these (an untranslated section keeps the English ones). TAXOPTIONS:
# ro-taxparty-split fills %STRING3 with the goods in lower case and %STRING4
# with the colony ("Partidă de blănuri' la Boston").
PLACEHOLDERS_SET = {"TAXOPTIONS": ["%STRING3", "%STRING4"]}

# GAME.TXT slots that hold a goods name (NAMES @CARGO, pushed from the goods
# table DS:97C0h): only these may be written %string<n>, which EXE patch
# ro-lowerstr-expander shows with a lower-case first letter. On a colony,
# nation or ship name it would lower-case that name too. Found from the slot
# setters, 2026-10-01; CHIEFHOWDY %STRING0 and TRADE0 %STRING0 come from other
# tables and stay out.
LOWER_OK = {
    "CARGOREADY0": {1}, "CARGOREADY1": {1}, "CARGOREADY2": {1}, "SPOIL1": {1}, "SPOIL3": {1},
    "WAREHOUSEFULL": {1}, "HOWMUCH1": {0}, "HOWMUCH2": {0}, "HOWMUCH3": {0}, "HOWMUCH4": {0},
    "HOWMUCH5": {0}, "PRICEUP": {0}, "PRICEDOWN": {0}, "KISSUP": {0}, "TUTORIAL3": {0},
    "TUTORIAL4": {0, 1}, "TUTORIAL6": {0}, "TUTORIAL18": {0}, "BADCARGO": {0, 1, 2, 3},
    "BADHAGGLE0": {1}, "BADHAGGLE1": {0}, "TRADE0": {1}, "TRADE1": {1}, "BRING": {0, 1, 2},
    "BUYWHICH": {0, 1, 2}, "BUY0": {0}, "BUY1": {0}, "CHIEFHOWDY": {1, 2, 3}, "EXTORTSTUFF": {2},
    "INDIANGIVESTUFF": {2}, "INDIANCITY": {2}, "INDIANWAGONS": {2}, "WANTSTUFF": {1},
    "WANTSTUFFUSA": {1}, "TRADENOWANT": {0}, "TRADEWITH": {0, 1}, "CARGOCAPTURE": {1},
    "RAIDSTORES": {2},
}

# NAMES sections with lines added after the English ones. @INDEPENDENT: lines
# 4-7 are the "Coloniile <nation>" texts that EXE patch ro-hof-colonies reads.
EXTRA_LINES = {"INDEPENDENT": 4}


def check_file(rep, name, en, ro):
    en_order, en_secs = sections(en)
    ro_order, ro_secs = sections(ro)
    if en_order != ro_order:
        rep.err(name, "sections differ: missing %s, extra %s" % (
            sorted(set(en_order) - set(ro_order)), sorted(set(ro_order) - set(en_order))))
    check_chars(rep, name, ro)
    for sec in en_order:
        if sec not in ro_secs:
            continue
        e, r, where = en_secs[sec], ro_secs[sec], "%s @%s" % (name, sec)
        if name in DIALOG_FILES:
            be, br = blocks(e), blocks(r)
            if be != br:
                rep.err(where, "structure changed (directives, blank lines, option/caret lines)")
            pe, pr = placeholders(" ".join(e)), placeholders(" ".join(r))
            lower = set(lower_slots(" ".join(r))) - (LOWER_OK.get(sec, set()) if name == "GAME.TXT" else set())
            if lower:
                rep.err(where, "%%string%s: not a goods slot (check.py LOWER_OK)" % min(lower))
            if sec in PLACEHOLDERS_SET and r != e:
                if pr != PLACEHOLDERS_SET[sec]:
                    rep.err(where, "placeholders %s, the EXE patch needs %s" % (pr, PLACEHOLDERS_SET[sec]))
            elif sec in PLACEHOLDERS_FREE and set(pr) <= set(pe):
                pass
            elif pe != pr:
                rep.err(where, "placeholders %s -> %s" % (placeholders(" ".join(e)),
                                                          placeholders(" ".join(r))))
            # a {highlight} may span two lines, so balance is checked per section
            depth = 0
            for line in r:
                for c in line:
                    depth += {"{": 1, "}": -1}.get(c, 0)
                    if depth not in (0, 1):
                        break
                if depth not in (0, 1):
                    break
            if depth != 0:
                rep.err(where, "unbalanced or nested { }")
        elif name in TABLE_FILES:
            te = [l for l in e if classify(l) == "text"]
            tr = [l for l in r if classify(l) == "text"]
            if len(tr) not in (len(te), len(te) + EXTRA_LINES.get(sec, 0)):
                rep.err(where, "line count %d -> %d" % (len(te), len(tr)))
                continue
            for le, lr in zip(te, tr):
                fe, fr = table_fields(le), table_fields(lr)
                if len(fe) != len(fr):
                    rep.err(where, "field count changed: %s" % lr.strip()[:50])
                    continue
                for (idx, _, _, ve), (_, _, _, vr) in zip(fe, fr):
                    if not is_translatable_field(sec, idx, ve) and ve != vr:
                        rep.err(where, "kept field %d changed: %r -> %r" % (idx, ve, vr))
                if placeholders(le) != placeholders(lr):
                    rep.err(where, "placeholders changed: %s" % lr.strip()[:50])
                if lower_slots(lr):
                    rep.err(where, "%%string only in GAME.TXT: %s" % lr.strip()[:50])
        else:
            le = [l for l in e if classify(l) not in ("comment",)]
            lr = [l for l in r if classify(l) not in ("comment",)]
            if len(le) != len(lr):
                rep.err(where, "line count %d -> %d (entries are positional)" % (len(le), len(lr)))
                continue
            for a, b in zip(le, lr):
                for mark in ("~", "#"):
                    if a.count(mark) != b.count(mark):
                        rep.err(where, "%r count changed: %r -> %r" % (mark, a.strip(), b.strip()))
                if placeholders(a) != placeholders(b):
                    rep.err(where, "placeholders changed: %r -> %r" % (a.strip(), b.strip()))
                if lower_slots(b):
                    rep.err(where, "%%string only in GAME.TXT: %r" % b.strip())
                check_hotkeys(rep, where, a, b)


def hotkey_runs(line):
    """Marked runs of a menu line: '~s~p~a~c~e~ bar' -> ['space ']."""
    return ["".join(m[1::2]) for m in re.findall(r"(?:~.)+", line)]


# Romanian letters a hotkey may be marked on: the EXE patches ro-menu-key-fold
# and ro-dialog-key-fold fold them to their base key (NEGO~Ț -> Alt+T).
FOLDED_KEYS = {ch for ch, byte in fontedit.SLOTS.items() if byte in hotkey_patch.FOLD}


def check_hotkeys(rep, where, a, b):
    """The EXE takes the key from the byte after the last '~' of a line, so a
    multi-character run must stay exactly as it is ('space ' with its
    trailing space; ' space' or 'space)' leave the space bar dead, rig test
    2026-09-28). A one-letter hotkey must be ASCII or one of FOLDED_KEYS:
    without the fold patches NEGO~Ț did not open with Alt+T."""
    ea = [r for r in hotkey_runs(a) if len(r) > 1]
    eb = [r for r in hotkey_runs(b) if len(r) > 1]
    if ea != eb:
        rep.err(where, "key names changed: %r -> %r" % (ea, eb))
    for r in hotkey_runs(b):
        if len(r) == 1 and not r.isascii() and r not in FOLDED_KEYS:
            rep.err(where, "hotkey %r is not ASCII: %r" % (r, b.strip()))


def run_check(en_dir, ro_dir, budget=100):
    """Return (errors, heap delta). Used by main() and by the tests."""
    rep, loaded = Report(), {}
    for name in DIALOG_FILES + TABLE_FILES + LIST_FILES:
        rp = os.path.join(ro_dir, name)
        if not os.path.exists(rp):
            continue
        en = read(os.path.join(en_dir, name), "latin-1")
        try:
            ro = read(rp, "utf-8")
        except UnicodeDecodeError:
            rep.err(name, "not valid UTF-8 (translation sources are UTF-8)")
            continue
        loaded[name] = (en, ro)
        check_file(rep, name, en, ro)
    en_heap = heap_proxy(*(read(os.path.join(en_dir, n), "latin-1") for n in ("NAMES.TXT", "LABELS.TXT")))
    ro_names = loaded.get("NAMES.TXT", (None, read(os.path.join(en_dir, "NAMES.TXT"), "latin-1")))[1]
    ro_labels = loaded.get("LABELS.TXT", (None, read(os.path.join(en_dir, "LABELS.TXT"), "latin-1")))[1]
    ro_heap = heap_proxy(ro_names, ro_labels)
    delta = ro_heap - en_heap
    if delta > budget:
        rep.err("NAMES+LABELS", "heap proxy %+d bytes over English, budget is %+d" % (delta, budget))
    return rep.errors, delta, len(loaded)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    budget = 100
    if "--budget" in argv:
        k = argv.index("--budget")
        budget = int(argv[k + 1])
        argv = argv[:k] + argv[k + 2:]
    errors, delta, nfiles = run_check(argv[0], argv[1], budget)
    print("heap proxy difference vs English: %+d bytes (budget %+d)" % (delta, budget))
    for e in errors:
        print("ERROR", e)
    print("%d files checked, %d errors" % (nfiles, len(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
