"""Tests for the Romanian translation tools in ro/tools/.

* Synthetic tests always run. They use tiny hand-written text files in the
  game's formats (no MicroProse text), so they also run in CI.
* Game tests run only when COL1_GAME_DIR points at the English game folder.
* Budget calibration runs only when COL1_LANG_TESTS points at the folder that
  holds the N100/N200/L100/L200/NL100 test sets
  (col_debug/lang in the repo folder). It proves
  the checker's budget agrees with what the game did on 2026-09-27:
  N100 and L100 ran, N200, L200 and NL100 crashed with NoMoreHeap.

Run:  python -m unittest discover -s tests -v
"""
import csv
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY = (ROOT / "ro" / "glossary" / "names.tsv").is_file()   # private
PRIVATE = "needs the private Romanian sources"
sys.path.insert(0, str(ROOT / "ro" / "tools"))
import check  # noqa: E402
import madspack  # noqa: E402
import textfiles  # noqa: E402

GAME = os.environ.get("COL1_GAME_DIR")
HAVE_GAME = bool(GAME) and (Path(GAME) / "GAME.TXT").is_file()
LANG = os.environ.get("COL1_LANG_TESTS")
HAVE_LANG = bool(LANG) and (Path(LANG) / "NL100" / "NAMES.TXT").is_file()

EN_FILES = {
    "GAME.TXT": [
        ";  test file", "", "@HELLO", "@width=190",
        "Hello {%STRING0}, this line", "wraps in the game.", "", "Yes", "No", "",
        "@TITLE", "^^Year of Our Lord", "^", "Text.", "",
        "@TEAPARTY", "@width=220", "{%STRING3 Party}!  %STRING0 into the sea at %STRING1!", "",
        "@PRICEUP", "@width=190", "In %STRING1, the price of {%STRING0} has risen to {%NUMBER0$}.", "",
    ],
    "NAMES.TXT": [
        "@UNIT", "Colonists,    101, 1, 0", "Soldiers,     103, 1, 2", "",
        "@ORDERS", "Sentry,           S", "No Orders,        -", "",
    ],
    "LABELS.TXT": ["@MISC", "a", "", "End of Turn", "Deny %Fs", "Of %STRING0", ""],
    "MENU.TXT": ["@GAME", "~GAME", "  Game ~Options", "  Zoom In#   ~Z", "  No Orders (~s~p~a~c~e~ bar)", "",
                 "@TRADE", "~TRADE", ""],
}


def write(folder, files, encoding):
    for name, lines in files.items():
        (Path(folder) / name).write_bytes("\r\n".join(lines).encode(encoding))


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.en = os.path.join(self.tmp, "en")
        self.ro = os.path.join(self.tmp, "ro")
        os.mkdir(self.en)
        os.mkdir(self.ro)
        write(self.en, EN_FILES, "latin-1")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_ro(self, changes, budget=100):
        files = {k: list(v) for k, v in EN_FILES.items()}
        for name, (old, new) in changes:
            lines = files[name]
            if old is None:
                lines[:] = new
            else:
                k = lines.index(old)
                lines[k:k + 1] = new
        write(self.ro, files, "utf-8")
        errors, delta, _ = check.run_check(self.en, self.ro, budget)
        return errors, delta

    def assertError(self, errors, text):
        self.assertTrue(any(text in e for e in errors), "no error with %r in %s" % (text, errors))

    def test_identity_passes(self):
        errors, delta = self.run_ro([])
        self.assertEqual(errors, [])
        self.assertEqual(delta, 0)

    def test_prose_may_rewrap_and_diacritics_pass(self):
        errors, _ = self.run_ro([("GAME.TXT", ("Hello {%STRING0}, this line",
                                                ["Salut {%STRING0}, acest rând", "se",
                                                 "rearanjează în joc."])),
                                 ("GAME.TXT", ("wraps in the game.", []))])
        self.assertEqual(errors, [])

    def test_option_count_change_fails(self):
        errors, _ = self.run_ro([("GAME.TXT", ("No", []))])
        self.assertError(errors, "structure changed")

    def test_caret_level_change_fails(self):
        errors, _ = self.run_ro([("GAME.TXT", ("^^Year of Our Lord", ["^Anul Domnului"]))])
        self.assertError(errors, "structure changed")

    def test_highlight_may_span_lines_but_must_close(self):
        errors, _ = self.run_ro([("GAME.TXT", ("Hello {%STRING0}, this line", ["Salut {%STRING0}, {acest"])),
                                 ("GAME.TXT", ("wraps in the game.", ["rând} se rearanjează."]))])
        self.assertEqual(errors, [])
        errors, _ = self.run_ro([("GAME.TXT", ("Hello {%STRING0}, this line", ["Salut {%STRING0}, {acest"]))])
        self.assertError(errors, "unbalanced")

    def test_placeholder_lost_fails(self):
        errors, _ = self.run_ro([("GAME.TXT", ("Hello {%STRING0}, this line", ["Salut, acest rând"]))])
        self.assertError(errors, "placeholders")

    def test_teaparty_may_reorder_placeholders(self):
        line = "{%STRING3 Party}!  %STRING0 into the sea at %STRING1!"
        errors, _ = self.run_ro([("GAME.TXT", (line, ["{Partida de %STRING0 de la %STRING1}!  %STRING0 în mare!"]))])
        self.assertEqual(errors, [])
        errors, _ = self.run_ro([("GAME.TXT", (line, ["{Partida de %STRING0 de la %STRING4}!"]))])
        self.assertError(errors, "placeholders")

    def test_lower_case_goods_slot(self):
        # ro-lowerstr-expander: %string<n> only on a goods slot (check.LOWER_OK)
        line = "In %STRING1, the price of {%STRING0} has risen to {%NUMBER0$}."
        errors, _ = self.run_ro([("GAME.TXT", (line, ["În %STRING1, prețul pentru {%string0} a urcat la {%NUMBER0$}."]))])
        self.assertEqual(errors, [])
        errors, _ = self.run_ro([("GAME.TXT", (line, ["În %string1, prețul pentru {%STRING0} a urcat la {%NUMBER0$}."]))])
        self.assertError(errors, "not a goods slot")
        errors, _ = self.run_ro([("GAME.TXT", ("Hello {%STRING0}, this line", ["Salut {%string0}, acest rând"]))])
        self.assertError(errors, "not a goods slot")

    def test_other_spellings_of_string_fail(self):
        line = "In %STRING1, the price of {%STRING0} has risen to {%NUMBER0$}."
        for bad in ("%String0", "%sTRING0", "%STRINg0"):
            errors, _ = self.run_ro([("GAME.TXT", (line, ["În %STRING1, prețul pentru {" + bad + "} a urcat."]))])
            self.assertError(errors, "placeholders")

    def test_lower_case_slot_only_in_game_txt(self):
        errors, _ = self.run_ro([("LABELS.TXT", ("Of %STRING0", ["Al %STRING0"]))])
        self.assertEqual(errors, [])
        errors, _ = self.run_ro([("LABELS.TXT", ("Of %STRING0", ["Al %string0"]))])
        self.assertError(errors, "only in GAME.TXT")

    def test_labels_blank_entry_removed_fails(self):
        errors, _ = self.run_ro([("LABELS.TXT", ("", []))])
        self.assertError(errors, "line count")

    def test_labels_entry_may_become_blank(self):
        errors, _ = self.run_ro([("LABELS.TXT", ("a", [""]))])
        self.assertEqual(errors, [])

    def test_kept_key_letter_change_fails(self):
        errors, _ = self.run_ro([("NAMES.TXT", ("Sentry,           S", ["Santinelă,        P"]))])
        self.assertError(errors, "kept field 1")

    def test_number_change_fails(self):
        errors, _ = self.run_ro([("NAMES.TXT", ("Soldiers,     103, 1, 2", ["Soldați,     103, 1, 3"]))])
        self.assertError(errors, "kept field 3")

    def test_reserved_glyph_byte_fails(self):
        errors, _ = self.run_ro([("LABELS.TXT", ("End of Turn", ["Sfâr[it"]))])
        self.assertError(errors, "cannot draw")

    def test_hotkey_marker_change_fails(self):
        errors, _ = self.run_ro([("MENU.TXT", ("  Game ~Options", ["  Opțiuni joc"]))])
        self.assertError(errors, "'~' count")

    def test_menu_column_separator_kept(self):
        errors, _ = self.run_ro([("MENU.TXT", ("  Zoom In#   ~Z", ["  Mărire   ~Z"]))])
        self.assertError(errors, "'#' count")

    def test_menu_key_name_kept(self):
        line = "  No Orders (~s~p~a~c~e~ bar)"
        errors, _ = self.run_ro([("MENU.TXT", (line, ["  Fără ordine (tasta~ ~s~p~a~c~e)"]))])
        self.assertError(errors, "key names changed")
        errors, _ = self.run_ro([("MENU.TXT", (line, ["  Fără ordine (tasta ~s~p~a~c~e~ )"]))])
        self.assertEqual(errors, [])

    def test_menu_hotkey_ascii_or_folded(self):
        # ro-menu-key-fold makes Ț answer to T; a letter it does not fold stays refused
        for ro in ("NEGO~T", "NEGO~Ț", "NEGO~ț"):
            errors, _ = self.run_ro([("MENU.TXT", ("~TRADE", [ro]))])
            self.assertEqual(errors, [], ro)
        errors, _ = self.run_ro([("MENU.TXT", ("~TRADE", ["NEGO~é"]))])
        self.assertError(errors, "not ASCII")

    def test_folded_keys_are_the_romanian_letters(self):
        import check
        self.assertEqual(check.FOLDED_KEYS, set("ăâîșțĂÂÎȘȚ"))

    def test_font_slots_avoid_layout_codes(self):
        import fontedit
        # 0x7C '|' is a dialog layout code (build 2, 2026-09-27: text after it
        # jumped right or vanished). CR, LF, EOF (0x1A) and ESC (0x1B) are unsafe too.
        for letter, byte in fontedit.SLOTS.items():
            self.assertNotIn(byte, (0x0A, 0x0D, 0x1A, 0x1B, 0x7C), letter)
        self.assertEqual(len(set(fontedit.SLOTS.values())), len(fontedit.SLOTS))

    def test_budget(self):
        pad = "x" * 100
        errors, delta = self.run_ro([("LABELS.TXT", ("End of Turn", ["End of Turn" + pad]))])
        self.assertEqual((errors, delta), ([], 100))
        errors, delta = self.run_ro([("LABELS.TXT", ("End of Turn", ["End of Turn" + pad + "x"]))])
        self.assertEqual(delta, 101)
        self.assertError(errors, "heap proxy")


def make_font(height=5, width=4, space=2, missing=()):
    """A synthetic .FF font item: every code 1..128 has a glyph `width` wide
    (a filled first column), space is `space` wide; codes in `missing` have none."""
    import struct
    import fontedit
    widths, offsets, data = [0] * 128, [0] * 128, bytearray()
    for code in range(1, 129):
        if code in missing:
            continue
        w = space if code == 0x20 else width
        rows = ["." * w] * height if code == 0x20 else ["1" + "." * (w - 1)] * height
        widths[code - 1] = w
        offsets[code - 1] = 2 + 128 + 256 + len(data)
        data += fontedit.pack_rows(rows)
    return bytes([height, width]) + bytes(widths) + struct.pack("<128H", *offsets) + bytes(data)


def write_font_file(path, item):
    import fontedit
    head = bytearray(madspack.MAGIC + b"\0" * (0xB0 - len(madspack.MAGIC)))
    head[0x0E] = 1
    Path(path).write_bytes(fontedit.write_madspack(bytes(head), item))


class RenderSynthetic(unittest.TestCase):
    """ro/tools/render.py on hand-made text and fonts: glyphs 4 px, space 2 px."""

    def setUp(self):
        import render
        self.r = render
        self.fonts = {render.FONT_NORMAL: madspack.Font(make_font()),
                      render.FONT_SMALL: madspack.Font(make_font(height=3)),
                      render.FONT_KING: madspack.Font(make_font(height=4))}
        smp = {"STRING": "Name", "COUNTRY": "Land", "why": ""}
        self.smp = {k: smp for k in self.fonts}

    def lay(self, lines, ro=False):
        enc = self.r.ro_encode if ro else self.r.en_encode
        return self.r.layout(lines, self.fonts, enc, self.smp)

    def texts(self, lay):
        return ["".join(chr(c) if k == "g" else " " for c, _, k in ln.cells) for ln in lay.lines]

    def test_prose_joins_and_wraps_at_width(self):
        # "aa bb" = 4+4+2+4+4 = 18 px; adding " cc" makes 28 > 20
        self.assertEqual(self.texts(self.lay(["@width=20", "aa bb", "cc dd"])), ["aa bb", "cc dd"])
        self.assertEqual(self.texts(self.lay(["@width=40", "aa bb", "cc dd"])), ["aa bb cc dd"])
        self.assertEqual(self.texts(self.lay(["@width=10", "aa bb cc"])), ["aa", "bb", "cc"])
        # a word wider than @width stays whole on its own line
        self.assertEqual(self.texts(self.lay(["@width=10", "aaaa b"])), ["aaaa", "b"])

    def test_options_and_lists_do_not_wrap(self):
        lay = self.lay(["@width=20", "aa", "", "aaaa bbbb cccc", "dd"])
        self.assertEqual(self.texts(lay), ["aa", "", "aaaa bbbb cccc", "dd"])
        self.assertEqual(lay.fixed_src, 2)
        lay = self.lay(["@width=20", "@checkbox", "Title here", "@options", "aa bb cc", "dd"])
        self.assertEqual(len(lay.lines), 3)
        # no @width: sized to the text, nothing wraps
        lay = self.lay(["aaaa bbbb cccc dddd eeee"])
        self.assertEqual(len(lay.lines), 1)
        self.assertEqual(lay.box()[2], 20 * 4 + 4 * 2 + 2 * self.r.MARGIN)

    def test_caret_lines(self):
        lay = self.lay(["@width=40", "aa", "^bb", "^", "^^cc", "dd ee"])
        self.assertEqual(self.texts(lay), ["aa", "bb", "", "cc", "dd ee"])
        self.assertEqual([ln.kind for ln in lay.lines], ["prose", "prose", "spacer", "centre", "prose"])
        self.assertEqual(self.r.line_offset(lay, lay.lines[3]), (40 - 8) // 2)
        self.assertEqual(self.r.line_offset(lay, lay.lines[0]), 0)
        wide = self.lay(["@width=10", "^^aaaa"])
        self.assertEqual(self.r.line_offset(wide, wide.lines[0]), 0)

    def test_markup_takes_no_space_and_highlight_spans_lines(self):
        lay = self.lay(["@width=100", "{aa", "b}b ~cc"])
        ln = lay.lines[0]
        # "aa b" + "b cc": 6 glyphs, 2 spaces (one from the joined line end)
        self.assertEqual(ln.px(lay.font), 4 * 6 + 2 * 2)
        self.assertEqual([hl for _, hl, k in ln.cells if k == "g"],
                         [True, True, True, False, False, False])

    def test_underscore_is_a_hard_space(self):
        self.assertEqual(self.texts(self.lay(["@width=20", "aa_bb_cc dd"])), ["aa bb cc", "dd"])
        lay = self.lay(["@width=20", "^^_", "____"])
        self.assertEqual([ln.kind for ln in lay.lines], ["spacer", "field"])

    def test_placeholders_use_samples(self):
        self.assertEqual(self.r.substitute("%STRING1 %NUMBER0%% %COUNTRY %YEAR", self.smp["FONTINTR.FF"]),
                         "Name 10000% Land 1492")
        self.assertEqual(self.r.substitute("%string1 %STRING1", self.smp["FONTINTR.FF"]), "name Name")

    def test_samples_pick_the_widest_name(self):
        f = madspack.Font(make_font(missing={ord("i")}))
        pool = [("UNIT", "iiiiiiii"), ("UNIT", "Wagon"), ("COUNTRY", "Aa"), ("COUNTRY", "Bbbb")]
        smp = self.r.samples(pool, {"F": f}, self.r.en_encode)["F"]
        self.assertEqual((smp["STRING"], smp["COUNTRY"]), ("Wagon", "Bbbb"))

    def flags(self, en_lines, ro_lines):
        return dict(self.r.flags_for(self.lay(en_lines), self.lay(ro_lines, ro=True)))

    def test_same_text_has_no_flags(self):
        lines = ["@width=40", "aa bb {cc} %STRING0", "", "Yes", "No"]
        self.assertEqual(self.flags(lines, lines), {})

    def test_taller_by_more_than_one_line(self):
        en = ["@width=20", "aa bb"]
        self.assertNotIn("taller", self.flags(en, ["@width=20", "aa bb cc"]))
        self.assertIn("taller", self.flags(en, ["@width=20", "aa bb cc dd ee"]))

    def test_options_dialog_taller_by_one_line(self):
        en = ["@width=20", "aa bb", "", "Da", "Nu"]
        self.assertNotIn("options_taller", self.flags(en, en))
        self.assertIn("options_taller", self.flags(en, ["@width=20", "aa bb cc", "", "Da", "Nu"]))

    def test_screen_overflow(self):
        # 5 px font + 1 px gap = 6 px a line; 200 - 16 px frame = 184 px = 30 lines
        en = ["@width=20", "aa"]
        self.assertNotIn("screen", self.flags(en, ["@width=20"] + ["aa bb"] * 30))
        self.assertIn("screen", self.flags(en, ["@width=20"] + ["aa bb"] * 31))
        self.assertIn("screen", self.flags(["@width=20", "@y=150", "aa"],
                                           ["@width=20", "@y=150"] + ["aa bb"] * 6))

    def test_king_scroll_may_not_grow(self):
        en = ["@width=60", "@x=232", "aa bb cc"]
        self.assertEqual(self.lay(en).font_name, self.r.FONT_KING)
        self.assertEqual(self.lay(["@width=60", "aa"]).font_name, self.r.FONT_NORMAL)
        self.assertNotIn("scroll", self.flags(en, ["@width=60", "@x=232", "aa bb"]))
        self.assertIn("scroll", self.flags(en, ["@width=60", "@x=232", "aa bb cc dd ee ff gg"]))

    def test_fonts_by_directive(self):
        self.assertEqual(self.lay(["@width=190", "a"]).font_name, self.r.FONT_NORMAL)
        self.assertEqual(self.lay(["@smallfont", "@width=190", "a"]).font_name, self.r.FONT_SMALL)

    def test_structure_flags(self):
        en = ["@width=40", "aa {bb} %STRING0", "", "Yes", "No"]
        self.assertIn("layout_code", self.flags(en, ["@width=40", "aa {bb} %STRING0 |", "", "Da", "Nu"]))
        self.assertIn("braces", self.flags(en, ["@width=40", "aa {bb %STRING0", "", "Da", "Nu"]))
        self.assertIn("placeholders", self.flags(en, ["@width=40", "aa {bb}", "", "Da", "Nu"]))
        self.assertIn("fixed_count", self.flags(en, ["@width=40", "aa {bb} %STRING0", "", "Da"]))

    def test_missing_glyph(self):
        ro_font = madspack.Font(make_font(missing={0x1C}))    # no glyph in the slot of 'ă'
        ro = self.r.layout(["@width=40", "aă"], {**self.fonts, self.r.FONT_NORMAL: ro_font},
                           self.r.ro_encode, self.smp)
        fl = dict(self.r.flags_for(self.lay(["@width=40", "aa"]), ro))
        self.assertIn("0x1C", fl["glyph"])

    def test_fixed_line_width_flags(self):
        en = ["@width=40", "aa", "", "aaaa"]                     # widest English option 16 px
        self.assertEqual(self.flags(en, ["@width=40", "aa", "", "aaaa"]), {})
        self.assertIn("fixed_over_en", self.flags(en, ["@width=40", "aa", "", "aaaaaa"]))
        self.assertIn("fixed_over_width", self.flags(en, ["@width=40", "aa", "", "a" * 11]))
        # English itself past @width: the English maximum is the limit
        en = ["@width=20", "aa", "", "a" * 10]
        self.assertEqual(self.flags(en, ["@width=20", "aa", "", "a" * 10]), {})
        self.assertIn("fixed_over_width", self.flags(en, ["@width=20", "aa", "", "a" * 11]))
        self.assertIn("fixed_over_screen", self.flags(en, ["@width=20", "aa", "", "a" * 80]))

    def test_end_to_end_writes_reports_and_png(self):
        tmp = tempfile.mkdtemp()
        try:
            en, ro, fonts, out = (os.path.join(tmp, d) for d in ("en", "ro", "fonts", "out"))
            for d in (en, ro, fonts):
                os.mkdir(d)
            for folder in (en, fonts):
                for name, h in (("FONTINTR.FF", 5), ("FONTTINY.FF", 3), ("FONTKING.FF", 4)):
                    write_font_file(os.path.join(folder, name), make_font(height=h))
            game = ["; head", "", "@OK", "@width=40", "aa bb", "", "@BAD", "@width=20", "aa %STRING0", "",
                    "@LIST", "one", "two", ""]
            (Path(en) / "GAME.TXT").write_bytes("\r\n".join(game).encode("latin-1"))
            (Path(en) / "PEDIA.TXT").write_bytes("\r\n".join(game).encode("latin-1"))
            (Path(en) / "NAMES.TXT").write_bytes(b"@UNIT\r\nWagon, 1\r\n@COUNTRY\r\nLand, 2\r\n")
            ro_game = list(game)
            ro_game[ro_game.index("aa %STRING0")] = "aă țâ îș %STRING0 bb cc dd ee"
            (Path(ro) / "GAME.TXT").write_bytes("\r\n".join(ro_game).encode("utf-8"))
            self.assertEqual(self.r.main([en, ro, fonts, out]), 0)
            with open(os.path.join(out, "report.csv"), encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["section"], "BAD")
            self.assertIn("taller", rows[0]["flags"])
            self.assertTrue(os.path.isfile(os.path.join(out, rows[0]["png"])))
            self.assertEqual({r["flags"] for r in rows[1:]}, {""})
            with open(os.path.join(out, "report.md"), encoding="utf-8") as f:
                self.assertIn("'Wagon'", f.read())
            # no Romanian PEDIA.TXT: English only
            out2 = os.path.join(tmp, "out2")
            self.assertEqual(self.r.main([en, ro, fonts, out2, "--file", "PEDIA.TXT", "--section", "OK"]), 0)
            with open(os.path.join(out2, "report.csv"), encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([(r["section"], r["ro_lines"]) for r in rows], [("OK", "")])
            self.assertTrue(os.path.isfile(os.path.join(out2, "png", "PEDIA_OK.png")))
        finally:
            shutil.rmtree(tmp)


@unittest.skipUnless(HAVE_GAME, "set COL1_GAME_DIR to the English game folder")
class GameFiles(unittest.TestCase):
    NAMES = ("GAME.TXT", "PEDIA.TXT", "MAPEDIT.TXT", "NAMES.TXT", "LABELS.TXT",
             "MENU.TXT", "MAPMENU.TXT", "WOODCUT.TXT", "CLOSING.TXT")

    def test_read_write_round_trip(self):
        tmp = tempfile.mkdtemp()
        try:
            for name in self.NAMES:
                src = Path(GAME) / name
                out = Path(tmp) / name
                textfiles.write_lines(out, textfiles.read_lines(src))
                self.assertEqual(out.read_bytes(), src.read_bytes(), name)
        finally:
            shutil.rmtree(tmp)

    def test_english_passes_its_own_check(self):
        tmp = tempfile.mkdtemp()
        try:
            for name in self.NAMES:
                data = (Path(GAME) / name).read_bytes().decode("latin-1")
                (Path(tmp) / name).write_bytes(data.encode("utf-8"))
            errors, delta, n = check.run_check(GAME, tmp)
            self.assertEqual(errors, [])
            self.assertEqual((delta, n), (0, len(self.NAMES)))
        finally:
            shutil.rmtree(tmp)

    @unittest.skipUnless(GLOSSARY, PRIVATE)
    def test_names_word_list_applies_and_fits(self):
        import apply_names
        tmp = tempfile.mkdtemp()
        try:
            lines = apply_names.apply(textfiles.read_lines(Path(GAME) / "NAMES.TXT"),
                                      apply_names.load_glossary(ROOT / "ro" / "glossary" / "names.tsv"))
            (Path(tmp) / "NAMES.TXT").write_bytes("\r\n".join(lines).encode("utf-8"))
            errors, delta, _ = check.run_check(GAME, tmp)
            self.assertEqual(errors, [])
            self.assertLessEqual(delta, 100)
        finally:
            shutil.rmtree(tmp)

    def test_font_np_is_space_and_capitals(self):
        font = madspack.load_font(str(Path(GAME) / "FONT-NP.FF"))
        self.assertEqual(font.chars(), " ABCDEFGHIJKLMNOPQRSTUVWXYZ")


@unittest.skipUnless(HAVE_GAME and HAVE_LANG, "set COL1_GAME_DIR and COL1_LANG_TESTS")
class BudgetCalibration(unittest.TestCase):
    """The checker's verdict must match the game's verdict for each test set."""
    GAME_RESULT = {"N100": "ran", "L100": "ran", "N200": "crashed",
                   "L200": "crashed", "NL100": "crashed"}

    def test_matches_game(self):
        for folder, result in self.GAME_RESULT.items():
            tmp = tempfile.mkdtemp()
            try:
                for name in ("NAMES.TXT", "LABELS.TXT"):
                    data = (Path(LANG) / folder / name).read_bytes().decode("latin-1")
                    (Path(tmp) / name).write_bytes(data.encode("utf-8"))
                errors, delta, _ = check.run_check(GAME, tmp)
                heap_errors = [e for e in errors if "heap proxy" in e]
                self.assertEqual(bool(heap_errors), result == "crashed",
                                 "%s: delta %+d, game %s" % (folder, delta, result))
            finally:
                shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
