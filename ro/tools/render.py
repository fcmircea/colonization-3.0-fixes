"""Offline dialog renderer: lay out every dialog the way the game would, English
next to Romanian, and flag the ones that will not fit.

Usage:
    python render.py EN_GAME_DIR RO_SRC_DIR RO_FONT_DIR OUT_DIR
                     [--file GAME.TXT|PEDIA.TXT] [--section NAME]

EN_GAME_DIR holds the English text files and fonts. RO_SRC_DIR holds the
Romanian sources (UTF-8); they are encoded with build.encode(), so the
Romanian letters land in the font slots. RO_FONT_DIR holds the Romanian .FF
fonts. When RO_SRC_DIR has no copy of --file, only English is rendered.

Writes OUT_DIR/report.md (flagged sections, worst first), OUT_DIR/report.csv
(every section) and OUT_DIR/png/<FILE>_<SECTION>.png (English left, Romanian
right, scaled x3) for each flagged section, or for --section always.

Layout model (what the tool assumes the game does; see ASSUMPTIONS):
  - prose (text before the first blank line, outside @options/@checkbox) is
    joined into paragraphs and re-wrapped at @width pixels, greedy, at spaces;
  - '^^text' is one centred line; '^text' starts a new paragraph (hard break);
    a line of only '^'/'_' is an empty line; '_' inside text is a hard space;
  - option lines (after @options/@checkbox or after the first blank line) and
    every line of a section without @width are fixed: one source line, one line;
  - '{' '}' toggle the highlight (it may run over a line end), '~' marks a
    hotkey; neither takes space;
  - %STRINGn etc. are replaced by a worst-case sample (see samples());
    %stringn by the same sample with a lower-case first letter.
"""
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build  # noqa: E402
import check  # noqa: E402
import madspack  # noqa: E402
from textfiles import classify, placeholders, table_fields  # noqa: E402

SCREEN_W, SCREEN_H = 320, 200
MARGIN = 8                  # frame + padding per side of a dialog box (estimate)
LINE_GAP = 1                # pixels between text lines (estimate)
KING_MAX_WIDTH = 90         # @width <= this and @x set: the King's scroll, FONTKING
FONT_NORMAL, FONT_SMALL, FONT_KING = "FONTINTR.FF", "FONTTINY.FF", "FONTKING.FF"
FONTS = (FONT_NORMAL, FONT_SMALL, FONT_KING)
NUMBER_SAMPLE = "10000"
YEAR_SAMPLE = "1492"
TOKEN = re.compile(r"%%|%(STRING\d|string\d|NUMBER\d|COUNTRY|YEAR|Fs)")
# NAMES.TXT sections whose first field may stand in for a %STRINGn
STRING_POOLS = ("UNIT", "BUILDING", "TRIBES", "COUNTRY", "NATIONALITY")

ASSUMPTIONS = [
    "Font: @width <= %d with @x set (the King's scroll: VICEROY, VICEROY2, KINGLOSE, KINGWIN) uses "
    "FONTKING; @smallfont uses FONTTINY; every other dialog FONTINTR. Not verified in the game."
    % KING_MAX_WIDTH,
    "@width is the width of the text area in pixels. A glyph's advance is its width in the "
    "font (the width already holds the blank column).",
    "Line height = font height + %d px. Blank lines and '^' lines take one line." % LINE_GAP,
    "Frame: %d px per side around the text (%d px added to the height). Screen %dx%d. "
    "The box top is @y when set, else the box is centred." % (MARGIN, 2 * MARGIN, SCREEN_W, SCREEN_H),
    "The King's scroll has a fixed height, not known: the English text height is used "
    "as the limit.",
    "A section without @width is sized to its text: no re-wrap, every line fixed.",
    "Option lines are drawn with no indent or checkbox space.",
    "Not modelled: an adviser portrait drawn over the box. In game (DECLARE, TUTORIAL14, "
    "re-tests S3/S6 2026-09-27) its musket covers the right end of some lines, English too; "
    "keep lines in such dialogs well under the English width.",
]


# ---------------------------------------------------------------- text to cells

def substitute(text, samples):
    """Replace placeholders with sample values; '%%' becomes '%'."""
    def rep(m):
        if m.group(0) == "%%":
            return "%"
        name = m.group(1)
        if name.startswith("STRING") or name == "Fs":
            return samples["STRING"]
        if name.startswith("string"):                # ro-lowerstr-expander
            v = samples["STRING"]
            return v[:1].lower() + v[1:] if "A" <= v[:1] <= "Z" else v
        if name.startswith("NUMBER"):
            return NUMBER_SAMPLE
        if name == "YEAR":
            return YEAR_SAMPLE
        return samples["COUNTRY"]
    return TOKEN.sub(rep, text)


def to_cells(data, hl):
    """Game bytes -> ([(code, highlight, kind)], highlight state at the end).
    kind: 'g' glyph, 's' breakable space, 'h' hard space ('_')."""
    out = []
    for b in data:
        if b == 0x7B:
            hl = True
        elif b == 0x7D:
            hl = False
        elif b == 0x7E:
            continue
        elif b == 0x20:
            out.append((0x20, hl, "s"))
        elif b == 0x5F:
            out.append((0x20, hl, "h"))
        else:
            out.append((b, hl, "g"))
    return out, hl


def glyph_width(font, code):
    return font.widths[code - 1] if 1 <= code <= 128 else 0


def cells_px(font, cells):
    return sum(glyph_width(font, c) for c, _, _ in cells)


def wrap(font, cells, width):
    """Greedy word wrap at breakable spaces. Returns a list of cell lists."""
    words, pending, cur = [], [], []
    for cell in cells:
        if cell[2] == "s":
            if cur:
                words.append((pending, cur))
                pending, cur = [], []
            pending.append(cell)
        else:
            cur.append(cell)
    if cur:
        words.append((pending, cur))
    lines, line, line_w = [], [], 0
    for sp, word in words:
        ww, sw = cells_px(font, word), cells_px(font, sp)
        if not line:
            line, line_w = list(word), ww      # spaces at a line start are dropped
        elif line_w + sw + ww <= width:
            line += sp + word
            line_w += sw + ww
        else:
            lines.append(line)
            line, line_w = list(word), ww
    if line or not lines:
        lines.append(line)
    return lines


# ---------------------------------------------------------------- layout

class Line:
    def __init__(self, cells, kind, align="left"):
        self.cells, self.kind, self.align = cells, kind, align

    def px(self, font):
        return cells_px(font, self.cells)


class Layout:
    """One section laid out in one language."""

    def __init__(self):
        self.lines = []
        self.width = None           # @width
        self.x = self.y = None
        self.font_name = FONT_NORMAL
        self.font = None
        self.fixed_src = 0          # source lines that must stay one line each
        self.raw = b""              # every text byte after substitution, markup kept
        self.src_text = ""          # text lines before substitution
        self.missing = set()        # codes with no glyph

    @property
    def line_h(self):
        return self.font.height + LINE_GAP

    @property
    def height(self):
        return len(self.lines) * self.line_h

    def text_w(self):
        return max([ln.px(self.font) for ln in self.lines] or [0])

    def box(self):
        """(x, y, w, h) of the dialog box on the screen, frame included."""
        w = max(self.width or 0, self.text_w()) + 2 * MARGIN   # a long option widens the box
        h = self.height + 2 * MARGIN
        x = self.x if self.x is not None else max(0, (SCREEN_W - w) // 2)
        y = self.y if self.y is not None else max(0, (SCREEN_H - h) // 2)
        return x, y, w, h

    def fixed_lines(self):
        return [ln for ln in self.lines if ln.kind in ("fixed", "centre") and ln.cells]

    def wide_prose(self):
        """Prose lines wider than @width (a single word that cannot wrap)."""
        if self.width is None:
            return []
        return [ln for ln in self.lines if ln.kind == "prose" and ln.px(self.font) > self.width]


def directives(lines):
    out = {}
    for line in lines:
        if classify(line) != "directive":
            continue
        key, _, val = line.strip()[1:].partition("=")
        out[key] = val
    return out


def pick_font(d):
    width = int(d["width"]) if d.get("width", "").isdigit() else None
    if width is not None and width <= KING_MAX_WIDTH and "x" in d:
        return FONT_KING
    if "smallfont" in d:
        return FONT_SMALL
    return FONT_NORMAL


def layout(lines, fonts, encode, samples):
    """Lay out one section. lines: the section's raw lines (after the header).
    fonts: {name: Font}; encode: str -> game bytes; samples: {font name: {...}}."""
    lay = Layout()
    d = directives(lines)
    lay.width = int(d["width"]) if d.get("width", "").isdigit() else None
    lay.x = int(d["x"]) if d.get("x", "").isdigit() else None
    lay.y = int(d["y"]) if d.get("y", "").isdigit() else None
    lay.font_name = pick_font(d)
    lay.font = font = fonts[lay.font_name]
    smp = samples[lay.font_name]
    state = {"hl": False, "para": None}
    list_mode = blank_seen = False
    raw, src = [], []

    def cells_of(text):
        data = encode(substitute(text, smp))
        raw.append(data)
        c, state["hl"] = to_cells(data, state["hl"])
        return c

    def flush():
        if state["para"] is not None:
            for c in wrap(font, state["para"], lay.width):
                lay.lines.append(Line(c, "prose"))
            state["para"] = None

    for line in lines:
        kind = classify(line)
        if kind in ("comment", "disabled", "section"):
            continue
        if kind == "directive":
            flush()
            if line.startswith(("@options", "@checkbox")):
                list_mode = True
            continue
        if kind == "blank":
            flush()
            blank_seen = True
            lay.lines.append(Line([], "blank"))
            continue
        s = line.rstrip()
        src.append(s)
        if set(s.strip()) <= set("^_"):
            flush()
            body = s.strip().lstrip("^")
            if len(body) >= 2:                     # input field '______'
                lay.fixed_src += 1
                lay.lines.append(Line([(0x5F, False, "g")] * len(body), "field"))
            else:
                lay.lines.append(Line([], "spacer"))
            continue
        if s.startswith("^^"):
            flush()
            lay.fixed_src += 1
            lay.lines.append(Line(cells_of(s.lstrip("^")), "centre", "centre"))
            continue
        fixed = lay.width is None or list_mode or blank_seen
        if s.startswith("^") and not fixed:
            flush()
            state["para"] = cells_of(s[1:])
            continue
        if fixed:
            flush()
            lay.fixed_src += 1
            lay.lines.append(Line(cells_of(s.lstrip("^")), "fixed"))
            continue
        c = cells_of(s)
        if state["para"] is None:
            state["para"] = c
        else:
            state["para"] += [(0x20, state["hl"], "s")] + c
    flush()
    while lay.lines and lay.lines[-1].kind == "blank":
        lay.lines.pop()
    lay.raw = b"\n".join(raw)
    lay.src_text = "\n".join(src)
    for ln in lay.lines:
        for code, _, k in ln.cells:
            if k == "g" and not glyph_width(font, code):
                lay.missing.add(code)
    return lay


# ---------------------------------------------------------------- samples

def names_pool(names_lines, colony_lines):
    pool, section = [], None
    for line in names_lines:
        kind = classify(line)
        if kind == "section":
            section = line.strip()[1:]
        elif kind == "text" and section in STRING_POOLS:
            value = table_fields(line)[0][3]
            if value:
                pool.append((section, value))
    for line in colony_lines or []:
        if classify(line) == "text":
            value = line.split(",")[0].strip()
            if value:
                pool.append(("COLONY", value))
    return pool


def samples(pool, fonts, encode):
    """{font name: {'STRING': value, 'COUNTRY': value, 'why': text}}: the widest
    name of the pool in that font (worst case), the widest country."""
    out = {}
    countries = [v for s, v in pool if s == "COUNTRY"] or ["England"]
    for name, font in fonts.items():
        def px(v):
            return cells_px(font, to_cells(encode(v), False)[0])
        s_sec, s_val = max(pool, key=lambda sv: px(sv[1])) if pool else ("-", "Name")
        c_val = max(countries, key=px)
        out[name] = {"STRING": s_val, "COUNTRY": c_val,
                     "why": "%%STRINGn = %r (%s, %d px), %%COUNTRY = %r (%d px), %%NUMBERn = %s, "
                            "%%YEAR = %s" % (s_val, s_sec, px(s_val), c_val, px(c_val),
                                             NUMBER_SAMPLE, YEAR_SAMPLE)}
    return out


# ---------------------------------------------------------------- flags

WEIGHT = {"screen": 100, "scroll": 90, "fixed_over_screen": 85, "layout_code": 80, "glyph": 70, "placeholders": 60,
          "braces": 60, "fixed_count": 60, "fixed_over_width": 50, "missing_section": 50,
          "options_taller": 55, "taller": 30, "fixed_over_en": 10}


def brace_balance(text):
    depth = 0
    for c in text:
        depth += {"{": 1, "}": -1}.get(c, 0)
        if depth not in (0, 1):
            return False
    return depth == 0


def screen_over(lay):
    x, y, w, h = lay.box()
    return y + h - SCREEN_H


def widest_fixed(lay):
    return max([ln.px(lay.font) for ln in lay.fixed_lines()] or [0])


def flags_for(en, ro):
    """[(flag, detail)] for one section. ro may be None (English only).

    Width rules: English option lines often pass @width (the box must grow or
    the line wrap; the game copes), so the limit for a fixed line is the larger
    of @width and the widest English fixed line. Past that: fixed_over_width.
    Wider than the English but within @width: fixed_over_en. A box wider than
    the screen: fixed_over_screen. Height and screen flags compare with English
    too, since the worst-case samples can push English itself off the screen."""
    out = []
    main = ro if ro is not None else en
    tag = "RO" if ro is not None else "EN"
    over = screen_over(main)
    if main.font_name != FONT_KING and over > 0 and (ro is None or over > screen_over(en)):
        out.append(("screen", "%s box %d px tall ends %d px below the screen (EN %d px)" % (
            tag, main.box()[3], over, en.box()[3])))
    if ro is not None and en.font_name == FONT_KING and ro.height > en.height:
        out.append(("scroll", "King's scroll: RO text %d px vs EN %d px (fixed height)" % (
            ro.height, en.height)))
    if ro is not None and 0x7C in ro.raw:
        out.append(("layout_code", "byte 0x7C '|' in the text (dialog layout code)"))
    if main.missing:
        also = en.missing if ro is not None else set()
        out.append(("glyph", "no glyph in %s for %s%s" % (
            main.font_name, " ".join("0x%02X" % c for c in sorted(main.missing)),
            " (EN too)" if main.missing <= also and ro is not None else "")))
    if ro is not None:
        pe, pr = placeholders(en.src_text), placeholders(ro.src_text)
        if pe != pr:
            out.append(("placeholders", "%s -> %s" % (" ".join(pe), " ".join(pr))))
        if not brace_balance(ro.src_text):
            out.append(("braces", "unbalanced { }"))
        if en.fixed_src != ro.fixed_src:
            out.append(("fixed_count", "fixed lines %d -> %d" % (en.fixed_src, ro.fixed_src)))
    elif not brace_balance(en.src_text):
        out.append(("braces", "unbalanced { }"))
    en_max = widest_fixed(en)
    main_max = widest_fixed(main)
    bw = main.box()[2]
    if bw > SCREEN_W and (ro is None or bw > en.box()[2]):
        out.append(("fixed_over_screen", "%s box %d px wide, screen %d (widest fixed line %d px, "
                                         "EN %d px)" % (tag, bw, SCREEN_W, main_max, en_max)))
    if main.width is not None:
        limit = main.width if ro is None else max(main.width, en_max)
        wide = [ln.px(main.font) for ln in main.fixed_lines() if ln.px(main.font) > limit]
        wide += [ln.px(main.font) for ln in main.wide_prose()]
        if wide:
            out.append(("fixed_over_width", "%d line(s) wider than @width=%d%s: max %d px" % (
                len(wide), main.width, "" if ro is None else " and the English max %d" % en_max,
                max(wide))))
    if ro is not None and en_max:
        wide = [ln.px(ro.font) for ln in ro.fixed_lines() if ln.px(ro.font) > en_max]
        if wide and (main.width is None or max(wide) <= main.width):
            out.append(("fixed_over_en", "%d fixed line(s) wider than the English max %d px: "
                                         "max %d px" % (len(wide), en_max, max(wide))))
    if (ro is not None and ro.width is not None
            and any(ln.kind == "fixed" for ln in ro.lines)
            and len(ro.lines) > len(en.lines)):
        # WORTHY, 2026-09-28: one extra line pushed "Da"/"Nu" off the screen
        # in the game (a portrait sits over the box); the model did not see it.
        out.append(("options_taller", "RO %d lines vs EN %d in a dialog with options" % (
            len(ro.lines), len(en.lines))))
    elif ro is not None and len(ro.lines) - len(en.lines) > 1:
        out.append(("taller", "RO %d lines vs EN %d (+%d)" % (
            len(ro.lines), len(en.lines), len(ro.lines) - len(en.lines))))
    return out


def score(flags, en, ro):
    s = sum(WEIGHT[f] for f, _ in flags)
    if ro is not None:
        s += max(0, len(ro.lines) - len(en.lines))
    return s + max(0, screen_over(ro if ro is not None else en)) / 10.0


# ---------------------------------------------------------------- drawing

BG_SCREEN = (40, 44, 52)
BG_OFF = (90, 30, 30)
FRAME = (96, 60, 30)
PAPER = (222, 204, 160)
INK = {False: {1: (40, 26, 12), 2: (150, 128, 96), 3: (40, 26, 12)},
       True: {1: (170, 30, 20), 2: (200, 150, 120), 3: (170, 30, 20)}}
RED = (230, 30, 30)


def glyph_pixels(font, code):
    """[(dx, dy, value)] for one glyph."""
    i = code - 1
    if not 0 <= i < 128 or not font.widths[i]:
        return []
    w, off = font.widths[i], font.offsets[i]
    bpr = (w + 3) // 4
    out = []
    for r in range(font.height):
        row = font.data[off + r * bpr: off + (r + 1) * bpr]
        for k in range(w):
            v = (row[k // 4] >> (6 - 2 * (k % 4))) & 3
            if v:
                out.append((k, r, v))
    return out


def line_offset(lay, ln):
    """x of a line inside the text area: '^^' lines are centred in @width
    (in the text width when there is no @width); a wider line starts at 0."""
    if ln.align != "centre":
        return 0
    area = lay.width if lay.width is not None else lay.text_w()
    return max(0, (area - ln.px(lay.font)) // 2)


def draw_panel(lay):
    from PIL import Image
    x, y, w, h = lay.box()
    tw = lay.text_w()
    pw = max(SCREEN_W, x + w, x + MARGIN + tw + MARGIN)
    ph = max(SCREEN_H, y + h)
    img = Image.new("RGB", (pw, ph), BG_SCREEN)
    px = img.load()
    for yy in range(SCREEN_H, ph):
        for xx in range(pw):
            px[xx, yy] = BG_OFF
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            edge = xx < x + 3 or xx >= x + w - 3 or yy < y + 3 or yy >= y + h - 3
            px[xx, yy] = FRAME if edge else PAPER
    font = lay.font
    for i, ln in enumerate(lay.lines):
        lw = ln.px(font)
        cx = x + MARGIN + line_offset(lay, ln)
        cy = y + MARGIN + i * lay.line_h
        for code, hl, kind in ln.cells:
            if kind == "g":
                for dx, dy, v in glyph_pixels(font, code):
                    if 0 <= cx + dx < pw and 0 <= cy + dy < ph:
                        px[cx + dx, cy + dy] = INK[hl][v]
            cx += glyph_width(font, code)
        if lay.width is not None and lw > lay.width:
            for yy in range(cy, min(ph, cy + lay.line_h)):
                px[min(pw - 1, x + MARGIN + lay.width), yy] = RED
    for xx in range(pw):
        if SCREEN_H < ph:
            px[xx, SCREEN_H] = RED
    return img


def draw_pair(en, ro, title, scale=3):
    from PIL import Image, ImageDraw
    panels = [("EN", en)] + ([("RO", ro)] if ro is not None else [])
    imgs = [(t, draw_panel(lay)) for t, lay in panels]
    gap, head = 6, 12
    w = sum(i.width for _, i in imgs) + gap * (len(imgs) - 1)
    h = max(i.height for _, i in imgs) + head
    sheet = Image.new("RGB", (w, h), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    x = 0
    for t, i in imgs:
        d.text((x + 2, 1), "%s  %s" % (t, title), fill=(255, 255, 255))
        sheet.paste(i, (x, head))
        x += i.width + gap
    return sheet.resize((w * scale, h * scale), Image.NEAREST)


# ---------------------------------------------------------------- driver

def load_fonts(folder):
    return {n: madspack.load_font(os.path.join(folder, n)) for n in FONTS}


def en_encode(text):
    return text.encode("latin-1")


def ro_encode(text):
    return build.encode(text)


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def render(en_dir, ro_dir, font_dir, out_dir, fname="GAME.TXT", only=None):
    """Lay out, flag, draw. Returns the list of result rows."""
    en_lines = check.read(os.path.join(en_dir, fname), "latin-1")
    ro_path = os.path.join(ro_dir, fname)
    have_ro = os.path.exists(ro_path)
    ro_lines = check.read(ro_path, "utf-8") if have_ro else None
    en_fonts = load_fonts(en_dir)
    ro_fonts = load_fonts(font_dir) if have_ro else None

    def lines_of(folder, name, enc):
        p = os.path.join(folder, name)
        return check.read(p, enc) if os.path.exists(p) else None
    en_pool = names_pool(lines_of(en_dir, "NAMES.TXT", "latin-1") or [],
                         lines_of(en_dir, "COLONY.TXT", "latin-1"))
    ro_pool = names_pool(lines_of(ro_dir, "NAMES.TXT", "utf-8")
                         or lines_of(en_dir, "NAMES.TXT", "latin-1") or [],
                         lines_of(ro_dir, "COLONY.TXT", "utf-8")
                         or lines_of(en_dir, "COLONY.TXT", "latin-1"))
    en_smp = samples(en_pool, en_fonts, en_encode)
    ro_smp = samples(ro_pool, ro_fonts, ro_encode) if have_ro else None

    en_order, en_secs = check.sections(en_lines)
    ro_order, ro_secs = check.sections(ro_lines) if have_ro else ([], {})
    os.makedirs(os.path.join(out_dir, "png"), exist_ok=True)
    rows = []
    for name in en_order:
        if only and name != only:
            continue
        en = layout(en_secs[name], en_fonts, en_encode, en_smp)
        ro = None
        if have_ro and name in ro_secs:
            ro = layout(ro_secs[name], ro_fonts, ro_encode, ro_smp)
        fl = flags_for(en, ro)
        if have_ro and name not in ro_secs:
            fl.append(("missing_section", "section not in the Romanian file"))
        png = ""
        if fl or only:
            png = os.path.join("png", "%s_%s.png" % (safe_name(fname.split(".")[0]), safe_name(name)))
            draw_pair(en, ro, "@%s" % name).save(os.path.join(out_dir, png))
        rows.append({"file": fname, "section": name, "font": en.font_name,
                     "width": en.width if en.width is not None else "",
                     "en_lines": len(en.lines), "ro_lines": len(ro.lines) if ro else "",
                     "en_px": en.box()[3], "ro_px": ro.box()[3] if ro else "",
                     "score": round(score(fl, en, ro), 1),
                     "flags": ";".join(f for f, _ in fl),
                     "details": " | ".join(d for _, d in fl), "png": png})
    rows.sort(key=lambda r: (-r["score"], r["section"]))
    write_reports(out_dir, fname, rows, en_smp, ro_smp, have_ro)
    return rows


def write_reports(out_dir, fname, rows, en_smp, ro_smp, have_ro):
    cols = ["file", "section", "font", "width", "en_lines", "ro_lines", "en_px", "ro_px",
            "score", "flags", "details", "png"]
    with open(os.path.join(out_dir, "report.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    flagged = [r for r in rows if r["flags"]]
    counts = {}
    for r in flagged:
        for fl in r["flags"].split(";"):
            counts[fl] = counts.get(fl, 0) + 1
    md = ["# Dialog render report: %s" % fname, "",
          "%d sections, %d flagged. %s" % (len(rows), len(flagged),
                                           "English and Romanian." if have_ro else
                                           "No Romanian file: English only."), "",
          "Heights are box heights in pixels (text + %d px frame)." % (2 * MARGIN), "",
          "## Assumptions", ""]
    md += ["- " + a for a in ASSUMPTIONS]
    md += ["", "## Placeholder samples (worst case: the widest name in that font)", ""]
    for font in FONTS:
        md.append("- EN %s: %s" % (font, en_smp[font]["why"]))
        if ro_smp:
            md.append("- RO %s: %s" % (font, ro_smp[font]["why"]))
    md += ["", "## Flags by type", "", "| flag | sections | meaning |", "|---|---|---|"]
    meaning = {"screen": "box runs past the bottom of the 200 px screen",
               "scroll": "King's scroll text taller than English",
               "layout_code": "0x7C in the text",
               "glyph": "a byte with no glyph in the font",
               "placeholders": "placeholder set differs from English",
               "braces": "unbalanced { }",
               "fixed_count": "number of fixed lines (options, ^^, lists) differs",
               "fixed_over_screen": "the box (widened by a fixed line) is wider than the screen",
               "fixed_over_width": "a line that cannot wrap is wider than @width and the English max",
               "missing_section": "section missing in the Romanian file",
               "options_taller": "a dialog with options is taller than English (answers may be cut off)",
               "taller": "Romanian more than one line taller than English",
               "fixed_over_en": "a fixed line wider than the widest English one (fits @width)"}
    for fl in sorted(counts, key=lambda k: -WEIGHT[k]):
        md.append("| %s | %d | %s |" % (fl, counts[fl], meaning[fl]))
    md += ["", "## Flagged sections, worst first", "",
           "| # | section | font | @width | EN px | RO px | lines EN/RO | flags |",
           "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(flagged, 1):
        md.append("| %d | [%s](%s) | %s | %s | %s | %s | %s/%s | %s |" % (
            i, r["section"], r["png"], r["font"].split(".")[0], r["width"], r["en_px"],
            r["ro_px"], r["en_lines"], r["ro_lines"], r["details"].replace("|", "/")))
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = {}
    for key in ("--file", "--section"):
        if key in argv:
            k = argv.index(key)
            opts[key] = argv[k + 1]
            del argv[k:k + 2]
    if len(argv) != 4:
        print(__doc__)
        return 2
    rows = render(*argv, fname=opts.get("--file", "GAME.TXT"), only=opts.get("--section"))
    flagged = [r for r in rows if r["flags"]]
    print("%d sections, %d flagged -> %s" % (len(rows), len(flagged),
                                            os.path.join(argv[3], "report.md")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
