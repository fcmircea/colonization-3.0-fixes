"""Parse and write the Colonization text files without losing a byte.

Three layouts:
  - dialog files (GAME.TXT, PEDIA.TXT, MAPEDIT.TXT): @SECTIONS of directives,
    text lines, blank lines. The game re-wraps prose to @width.
  - table files (NAMES.TXT): @SECTIONS of comma-separated fields.
  - list files (LABELS.TXT, MENU.TXT, MAPMENU.TXT, WOODCUT.TXT, CLOSING.TXT):
    @SECTIONS of lines. Every line is an entry, blank lines too.

Everything is read as latin-1 bytes with CRLF kept, so that
write(parse(file)) == file, byte for byte.
"""
import re

SECTION = re.compile(r"@([A-Z0-9_]+)\s*$")
DIRECTIVE = re.compile(r"@([a-z]+)(=.*)?\s*$")
PLACEHOLDER = re.compile(r"%(?:STRING\d|string\d|NUMBER\d|COUNTRY|YEAR|Fs)")
# %string<n>: slot n with its first letter in lower case (EXE patch
# ro-lowerstr-expander). It counts as %STRING<n> when placeholders are compared.
LOWER_SLOT = re.compile(r"%string(\d)")
NUMERIC = re.compile(r"[-+]?\d+(\.\d+)?$|[01]{8}$")

DIALOG_FILES = ("GAME.TXT", "PEDIA.TXT", "MAPEDIT.TXT")
TABLE_FILES = ("NAMES.TXT",)
LIST_FILES = ("LABELS.TXT", "MENU.TXT", "MAPMENU.TXT", "WOODCUT.TXT", "CLOSING.TXT")

# NAMES.TXT: which fields stay as they are (file names, colours, key letters,
# numbers). Leader and Founding Father names may take their Romanian form
# (Florin, 2026-09-27: "Cristofor Columb").
KEEP_SECTIONS = {"COLORS", "SCENARIO"}
KEEP_FIELDS = {("ORDERS", 1)}
KEEP_VALUES = {"<Any>"}


def read_lines(path):
    with open(path, "rb") as f:
        return f.read().decode("latin-1").split("\r\n")


def write_lines(path, lines):
    with open(path, "wb") as f:
        f.write("\r\n".join(lines).encode("latin-1"))


def classify(line):
    """Kind of one raw line in any file."""
    if line.startswith(";"):
        return "comment"
    if SECTION.match(line):
        return "section"
    if line.startswith("@"):
        return "directive" if DIRECTIVE.match(line) else "disabled"
    if not line.strip():
        return "blank"
    return "text"


def placeholders(text):
    return sorted(p.replace("%string", "%STRING") for p in PLACEHOLDER.findall(text))


def lower_slots(text):
    """Slot numbers written as %string<n>: '%string1 %STRING0' -> [1]."""
    return sorted(int(n) for n in LOWER_SLOT.findall(text))


def split_comment(line):
    """'Food,  1, 3 ; note' -> ('Food,  1, 3', ' ; note')"""
    k = line.find(";")
    return (line, "") if k < 0 else (line[:k], line[k:])


def table_fields(line):
    """Fields of one NAMES.TXT line as (index, start, end, value) spans."""
    body, _ = split_comment(line)
    out, pos = [], 0
    for idx, raw in enumerate(body.split(",")):
        lead = len(raw) - len(raw.lstrip())
        value = raw.strip()
        out.append((idx, pos + lead, pos + lead + len(value), value))
        pos += len(raw) + 1
    return out


def is_string_field(value):
    """A NAMES.TXT field the game keeps as a string (not a number)."""
    return bool(value) and not re.match(r"[-+]?\d", value)


def heap_proxy(names_lines, labels_lines):
    """Bytes NAMES + LABELS put in the <Text> heap, as far as we can tell:
    every string field of NAMES and every LABELS entry, each + 1 NUL.
    Only differences matter: tests N100/L100/NL100 padded exactly these."""
    total, section = 0, None
    for line in names_lines:
        kind = classify(line)
        if kind == "section":
            section = line
        elif section and kind == "text":
            total += sum(len(v) + 1 for _, _, _, v in table_fields(line) if is_string_field(v))
    section = None
    for line in labels_lines:
        kind = classify(line)
        if kind == "section":
            section = line
        elif section and kind not in ("comment", "directive", "disabled"):
            total += len(line.strip()) + 1
    return total


def is_translatable_field(section, idx, value):
    if not value or section in KEEP_SECTIONS or (section, idx) in KEEP_FIELDS:
        return False
    if value in KEEP_VALUES or NUMERIC.match(value):
        return False
    return bool(re.search(r"[A-Za-z]", value))
