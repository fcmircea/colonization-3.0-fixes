"""Tests for the private Romanian patch set, ro/patches-ro.json.

It is applied with the public patchers and --manifest, so it must pass the
same checks as patch/patches.json, and it must never touch a byte that a
public patch touches (the patcher checks overlaps only inside one manifest).
"""
import json
import subprocess
import sys
import unicodedata
import unittest
from pathlib import Path

from test_apply_patch import HAVE_PRISTINE, PATCH_DIR, PRISTINE, REAL_MANIFEST, ROOT, md5
# Romanian sources (ro/work, ro/glossary) and docs/TRANSLATION-RO.md are private:
# the tests that read them run on the private branch only.
RO_SRC = (ROOT / "ro" / "work" / "ro-src" / "NAMES.TXT").is_file()
GLOSSARY = (ROOT / "ro" / "glossary" / "names.tsv").is_file()
RECIPE = (ROOT / "docs" / "TRANSLATION-RO.md").is_file()
PRIVATE = "needs the private Romanian sources"
from x86mini import CPU

sys.path.insert(0, str(ROOT / "ro" / "tools"))
import build  # noqa: E402
import fontedit  # noqa: E402
import hotkey_patch  # noqa: E402
import sort_patch  # noqa: E402
import upcase_patch  # noqa: E402

RO_MANIFEST_PATH = ROOT / "ro" / "patches-ro.json"
RO_MANIFEST = json.loads(RO_MANIFEST_PATH.read_text(encoding="utf-8"))
DS_FILE_BASE = 0x1D9A0          # data segment start in the file (MS C runtime banner at DS:0x0008)


def span(p):
    off = int(p["offset"], 16)
    return off, off + len(bytes.fromhex(p["original"]))


class RoManifestTests(unittest.TestCase):
    def test_patcher_accepts_it(self):
        r = subprocess.run([sys.executable, str(PATCH_DIR / "apply_patch.py"), "unused.exe",
                            "--manifest", str(RO_MANIFEST_PATH), "--list"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_same_target_as_public_manifest(self):
        self.assertEqual(RO_MANIFEST["target"], REAL_MANIFEST["target"])

    def test_no_site_overlaps_a_public_patch(self):
        for p in RO_MANIFEST["patches"]:
            a0, a1 = span(p)
            for q in REAL_MANIFEST["patches"]:
                b0, b1 = span(q)
                self.assertTrue(a1 <= b0 or b1 <= a0, (p["id"], q["id"]))


@unittest.skipUnless(HAVE_PRISTINE, "set COL1_PRISTINE_EXE to an unmodified VICEROY.EXE")
class RoRealBinaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = Path(PRISTINE).read_bytes()

    def test_sites_match_pristine_and_md5_alone(self):
        for p in RO_MANIFEST["patches"]:
            off, end = span(p)
            self.assertEqual(self.data[off:end].hex(), p["original"], p["id"])
            d = bytearray(self.data)
            d[off:end] = bytes.fromhex(p["patched"])
            self.assertEqual(md5(bytes(d)), p["md5_alone"], p["id"])

    def test_no_changed_byte_on_a_relocation(self):
        d = self.data
        count = int.from_bytes(d[6:8], "little")
        table = int.from_bytes(d[0x18:0x1A], "little")
        header = int.from_bytes(d[8:10], "little") * 16
        relocs = [header + int.from_bytes(d[table + 4 * i + 2:table + 4 * i + 4], "little") * 16
                  + int.from_bytes(d[table + 4 * i:table + 4 * i + 2], "little") for i in range(count)]
        # The loader rewrites relocated words in memory, so a patch must not
        # change one. (ro-upcase's long site holds three, left as they are.)
        for p in RO_MANIFEST["patches"]:
            off, end = span(p)
            orig, new = bytes.fromhex(p["original"]), bytes.fromhex(p["patched"])
            changed = {off + i for i in range(len(orig)) if orig[i] != new[i]}
            hit = [hex(w) for w in relocs if {w, w + 1} & changed]
            self.assertEqual(hit, [], p["id"])

    def test_empty_slot_string_is_nul_terminated_and_used_once(self):
        # The string must stay a C string, and the only code that names it is
        # "push 0x20EE" at 0x72F0C (a strcpy into the slot label). The other
        # "EE 20" in the file is "sub si, 0x20" (83 EE 20), not a pointer.
        self.assertEqual(self.data[DS_FILE_BASE + 8:DS_FILE_BASE + 19], b"MS Run-Time")
        off = 0x1FA8E
        self.assertEqual(self.data[off:off + 8], b"(EMPTY)\0")
        self.assertEqual((off - DS_FILE_BASE).to_bytes(2, "little"), b"\xee\x20")
        hits, i = [], self.data.find(b"\xee\x20")
        while i >= 0:
            hits.append(i)
            i = self.data.find(b"\xee\x20", i + 1)
        self.assertEqual(hits, [0x12DC8, 0x72F0D])
        self.assertEqual(self.data[0x12DC7], 0x83)
        self.assertEqual(self.data[0x72F0C], 0x68)
        p = next(p for p in RO_MANIFEST["patches"] if p["id"] == "ro-empty-slot")
        self.assertEqual(bytes.fromhex(p["patched"]), b"(LIBER)")


# ---- ro-upcase: run the routines themselves ------------------------------------

RUNTIME_CS, RUNTIME_FILE = 0x0D1D, 0xF5D0      # segment of strlwr/strupr/_fstrupr, and its file offset
STRLWR, STRUPR, FSTRUPR = 0x0D46, 0x0D64, 0x1118
RO_UPPER = {0x1C: 0x5C, 0x1D: 0x16, 0x1E: 0x7F, 0x1F: 0x14, 0x60: 0x15}


def exe_with(pid=None):
    d = bytearray(Path(PRISTINE).read_bytes())
    if pid:
        p = next(p for p in RO_MANIFEST["patches"] if p["id"] == pid)
        off, end = span(p)
        d[off:end] = bytes.fromhex(p["patched"])
    return bytes(d)


def run(exe, entry, text: bytes, far=False):
    """Call a routine on text; return (result bytes, cpu)."""
    cpu = CPU()
    cpu.mem[RUNTIME_CS] = bytearray(exe[RUNTIME_FILE:RUNTIME_FILE + 0x10000])
    cpu.r.update(ss=0x2000, sp=0xFFF0, ds=0x2000, bp=0x1234, si=0x5678, di=0x9ABC)
    seg, off = (0x3000, 0x0100) if far else (0x2000, 0x0100)
    cpu.seg(seg)[off:off + len(text) + 1] = text + b"\0"
    cpu.call_far(RUNTIME_CS, entry, [off, seg] if far else [off])
    out = bytes(cpu.seg(seg)[off:off + len(text)])
    return out, cpu, (seg, off)


def expect_upper(c):
    if 0x61 <= c <= 0x7A:
        return c - 0x20
    return RO_UPPER.get(c, 0x15 if c == 0x1B else c)


@unittest.skipUnless(HAVE_PRISTINE, "set COL1_PRISTINE_EXE to an unmodified VICEROY.EXE")
class RoUpcaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = exe_with()
        cls.new = exe_with("ro-upcase")

    def test_manifest_matches_builder(self):
        p = next(p for p in RO_MANIFEST["patches"] if p["id"] == "ro-upcase")
        e = upcase_patch.manifest_entry(self.old)
        for k in ("offset", "original", "patched", "md5_alone"):
            self.assertEqual(p[k], e[k], k)

    def test_only_the_two_regions_change(self):
        diff = [i for i in range(len(self.old)) if self.old[i] != self.new[i]]
        self.assertTrue(all(0x10316 <= i < 0x10352 or 0x106E8 <= i < 0x1070B for i in diff))

    def test_entry_points_are_the_ones_the_game_calls(self):
        # far calls 9A off seg with seg 0D1Dh, as found in the file
        for entry, n in ((STRLWR, 2), (STRUPR, 9), (FSTRUPR, 6)):
            pat = b"\x9a" + entry.to_bytes(2, "little") + RUNTIME_CS.to_bytes(2, "little")
            self.assertEqual(self.old.count(pat), n, hex(entry))

    def test_every_byte_value(self):
        all_bytes = bytes(range(1, 256))
        for far, entry in ((False, STRUPR), (True, FSTRUPR)):
            new, cpu, (seg, off) = run(self.new, entry, all_bytes, far)
            self.assertEqual(new, bytes(expect_upper(c) for c in all_bytes), (far, entry))
            self.assertEqual((cpu.r["ax"], cpu.r["dx"]), (off, seg))
            old, _, _ = run(self.old, entry, all_bytes, far)
            self.assertEqual(old, bytes(c - 0x20 if 0x61 <= c <= 0x7A else c for c in all_bytes))
        lower = bytes(c + 0x20 if 0x41 <= c <= 0x5A else c for c in all_bytes)
        self.assertEqual(run(self.new, STRLWR, all_bytes)[0], lower)
        self.assertEqual(run(self.old, STRLWR, all_bytes)[0], lower)

    def test_labels_from_the_play_tests(self):
        for text, want in ((b"Fran\x60a:", b"FRAN\x15A:"),
                           (b"Ces\x1Ctorie", b"CES\x5CTORIE"),
                           (b"Vam\x1C", b"VAM\x5C"),
                           (b"\x1Env\x1C\x60\x1Fm\x1Dnt", b"\x7FNV\x5C\x15\x14M\x16NT")):
            self.assertEqual(run(self.new, STRUPR, text)[0], want)
            self.assertNotEqual(run(self.old, STRUPR, text)[0], want)

    def test_registers_the_caller_keeps(self):
        for far, entry in ((False, STRLWR), (False, STRUPR), (True, FSTRUPR)):
            _, cpu, _ = run(self.new, entry, b"Ab\x1Cz\x60", far)
            self.assertEqual((cpu.r["ds"], cpu.r["ss"], cpu.r["sp"], cpu.r["bp"], cpu.r["si"], cpu.r["di"]),
                             (0x2000, 0x2000, 0xFFF0, 0x1234, 0x5678, 0x9ABC), entry)

    def test_empty_string(self):
        for far, entry in ((False, STRLWR), (False, STRUPR), (True, FSTRUPR)):
            out, cpu, (seg, off) = run(self.new, entry, b"", far)
            self.assertEqual(cpu.r["ax"], off)


# ---- ro-no-the, ro-adviser-first: run the label builders ---------------------------

LABELS_MISC = 0x2DBA          # DS: 221 near pointers, one per LABELS @MISC line (loader at 0x75221)
THE, ADVISER = LABELS_MISC + 2 * 36, LABELS_MISC + 2 * 103
NATION_NAMES, FOUNDING = 0x8D42, 0x96E8     # DS: NAMES nation names; NAMES @FOUNDING types
FATHER_NAMES = 0x9652         # DS: 6-byte father records, name pointer first
CODE_SEG, DATA_SEG = 0x1000, 0x2000
RESIDENT_END = 0x20665        # end of the resident image in the file; overlays follow
T = 0x60                      # "ț" in the game's code page


def builder_cpu(exe, base, strings):
    """A CPU with the code from file offset `base` at CODE_SEG:0000 and the
    real data segment at DATA_SEG (SS = DS, as in the game). `strings` maps
    DS pointer slots to the text they should point to. The string helpers are
    stubs: strcat, and the ones that append " ", "(" and ")"."""
    cpu = CPU()
    cpu.mem[CODE_SEG] = bytearray(exe[base:base + 0x10000].ljust(0x10000, b"\0"))
    # only the resident image is data; above it (DS:2CC5h on) the file holds
    # overlays, which at run time is zero-filled space
    cpu.mem[DATA_SEG] = bytearray(exe[DS_FILE_BASE:RESIDENT_END].ljust(0x10000, b"\0"))
    cpu.r.update(ds=DATA_SEG, ss=DATA_SEG, sp=0xE000, bp=0xE800)
    ds = cpu.seg(DATA_SEG)
    free = 0xA000
    for slot, text in strings.items():
        ds[free:free + len(text) + 1] = text + b"\0"
        ds[slot:slot + 2] = free.to_bytes(2, "little")
        free += len(text) + 1

    def cstr(o):
        return bytes(ds[o:ds.index(0, o)])

    def cat(dst, text):
        end = ds.index(0, dst)
        ds[end:end + len(text) + 1] = text + b"\0"

    arg = lambda n: cpu.rw(DATA_SEG, cpu.r["sp"] + 2 * n)
    for seg, off, fn in ((0x004B, 0x00E2, lambda: cat(arg(0), cstr(arg(1)))),   # strcat
                         (0x004B, 0x0000, lambda: cat(arg(0), b" ")),
                         (0x181F, 0x016E, lambda: cat(arg(0), cstr(arg(1)))),   # thunk to 4B:00E2
                         (0x181F, 0x0178, lambda: cat(arg(0), b" ")),           # thunk to 4B:0000
                         (0x181F, 0x011E, lambda: cat(arg(0), b"(")),
                         (0x181F, 0x0128, lambda: cat(arg(0), b")"))):
        cpu.far_hooks[(seg, off)] = lambda cpu, fn=fn: fn()
    return cpu, cstr


def nation_name(exe, nation, the, lower=False):
    """Far routine at file 0x8074: append nation `nation`'s name to an empty buffer."""
    cpu, cstr = builder_cpu(exe, 0x8000, {THE: the, NATION_NAMES + 2 * nation: b"Olanda"})
    buf = 0xB000
    cpu.call_far(CODE_SEG, 0x0074, [nation, 0 if lower else 1, buf])
    return cstr(buf)


def father_entry(exe, slot, type_text, adviser):
    """One pass of the father-choice list loop (file 0x3C172-0x3C1E6): the menu line."""
    father = 7
    cpu, cstr = builder_cpu(exe, 0x3C000, {FATHER_NAMES + 6 * father: b"Adam Smith",
                                           FOUNDING + 2 * slot: type_text, ADVISER: adviser})
    bp = cpu.r["bp"]
    cpu.wb(DATA_SEG, bp - 0x68, slot)                         # loop index [bp-68h]
    cpu.wb(DATA_SEG, bp + 2 * slot - 0x74, father)            # father for this slot [bp+si-74h]
    cpu.run_until(CODE_SEG, 0x0172, 0x01E6)
    return cstr(bp - 0x52)


FREE, OF_THE = LABELS_MISC + 2 * 191, LABELS_MISC + 2 * 19
LIBERA = b"liber" + bytes([fontedit.SLOTS["ă"]])


def with_nation_stub(cpu, entry, nation, name):
    """Stub the far routine `entry` (181F:entry) that returns a nation's name in ax."""
    ptr = 0xA800
    cpu.seg(DATA_SEG)[ptr:ptr + len(name) + 1] = name + b"\0"
    seen = []

    def stub(cpu):
        seen.append(cpu.rw(DATA_SEG, cpu.r["sp"]))
        cpu.r["ax"] = ptr
    cpu.far_hooks[(0x181F, entry)] = stub
    return seen


def hof_line(exe, free):
    """Hall of Fame line tail (file 0x3B1B1-0x3B213): "<of the> [<Free> ]<nation>"."""
    cpu, cstr = builder_cpu(exe, 0x3B000, {OF_THE: b"din", FREE: LIBERA})
    ds, bp, row, nation = cpu.seg(DATA_SEG), cpu.r["bp"], 1, 2
    si = row * 0x2A
    cpu.r["si"] = si
    ds[bp - 0x15A:bp - 0x158] = row.to_bytes(2, "little")            # row index, the old code recomputes si
    ds[bp + si - 0xE8:bp + si - 0xE6] = nation.to_bytes(2, "little")
    ds[bp + si - 0xE6:bp + si - 0xE4] = int(free).to_bytes(2, "little")
    ds[bp - 0x150:bp - 0x14D] = b"X \0"                               # "<title> <name> " so far
    seen = with_nation_stub(cpu, 0x065E, nation, b"Fran" + bytes([T]) + b"a")
    sp = cpu.r["sp"]
    cpu.run_until(CODE_SEG, 0x01B1, 0x0213)
    return cstr(bp - 0x150), cpu.r["sp"] - sp, seen


def report_line(exe, free):
    """Report header line (file 0x39D57-0x39DA7): "[<Free> ]<nation>" + one more helper."""
    cpu, cstr = builder_cpu(exe, 0x39000, {FREE: LIBERA})
    ds, bp, nation = cpu.seg(DATA_SEG), cpu.r["bp"], 2
    ds[bp - 0x66:bp - 0x64] = nation.to_bytes(2, "little")
    ds[(nation * 0x13C - 0x77F8) & 0xFFFF] = 4 if free else 0x0B     # nation record flags, bit 2 = free
    seen = with_nation_stub(cpu, 0x09A4, nation, b"Anglia")

    def mark(cpu):
        # 181F:01BE is not resolved; it takes the buffer. The stub marks where it runs.
        end = ds.index(0, cpu.rw(DATA_SEG, cpu.r["sp"]))
        ds[end:end + 2] = b"#\0"
    cpu.far_hooks[(0x181F, 0x01BE)] = mark
    sp = cpu.r["sp"]
    cpu.run_until(CODE_SEG, 0x0D57, 0x0DA7)
    return cstr(bp - 0x50), cpu.r["sp"] - sp, seen


LEADER, GENERAL, COLONIES = LABELS_MISC + 2 * 197, LABELS_MISC + 2 * 196, LABELS_MISC + 2 * 95
NAMES_OUT = 0x833C            # DS: where 181F:0422 leaves the NAMES line it read


def hof_colonies_line(exe, free):
    """Hall of Fame second line, first part (file 0x3AF2A-0x3AF94, and the
    President path's tail 0x3B27E-0x3B299 that the patched Leader path uses):
    "<Leader>, <nationality> <Colonies>" or "<General, Continental Army>"."""
    cpu, cstr = builder_cpu(exe, 0x3A000, {LEADER: b"Conduc" + bytes([fontedit.SLOTS["ă"]]) + b"tor",
                                           GENERAL: b"General", COLONIES: b"Colonii"})
    ds, bp, row, nation = cpu.seg(DATA_SEG), cpu.r["bp"], 1, 2
    si = row * 0x2A
    cpu.r["si"] = si
    ds[bp - 0x15A:bp - 0x158] = row.to_bytes(2, "little")
    ds[bp + si - 0xE8:bp + si - 0xE6] = nation.to_bytes(2, "little")
    ds[bp + si - 0xE6:bp + si - 0xE4] = int(free).to_bytes(2, "little")
    ds[bp - 0x150] = 0
    seen = with_nation_stub(cpu, 0x065E, nation, b"Spania")
    lookups = []

    def names_line(cpu):
        f, sec, idx = (cpu.rw(DATA_SEG, cpu.r["sp"] + 2 * n) for n in range(3))
        lookups.append((cstr(f), cstr(sec), idx))
        text = b"Coloniile Spaniei" if idx == 4 + nation else b"?"
        ds[NAMES_OUT:NAMES_OUT + len(text) + 1] = text + b"\0"

    def comma(cpu):
        end = ds.index(0, cpu.rw(DATA_SEG, cpu.r["sp"]))
        ds[end:end + 3] = b", \0"
    def heap_strcat(cpu):
        # 4B:00E2 takes a text-heap string number, not a DS pointer: DS:833Ch
        # must go through the resident strcat instead (QEMU 2026-09-29 showed
        # the line cut after "Conducător," when it did not)
        if arg(1) == NAMES_OUT:
            raise AssertionError("DS:833Ch passed to 181F:016E")
        cat(arg(0), cstr(arg(1)))

    def ds_strcat(cpu):
        cat(arg(0), cstr(arg(1)))

    def cat(dst, text):
        end = ds.index(0, dst)
        ds[end:end + len(text) + 1] = text + b"\0"
    arg = lambda n: cpu.rw(DATA_SEG, cpu.r["sp"] + 2 * n)
    cpu.far_hooks[(0x181F, 0x016E)] = heap_strcat
    cpu.far_hooks[(0x0D1D, 0x07A4)] = ds_strcat  # resident strcat, called directly at 0x3B294
    cpu.far_hooks[(0x181F, 0x0422)] = names_line
    cpu.far_hooks[(0x181F, 0x01B4)] = comma      # not resolved; the line shows "Conducător, Spania"
    sp = cpu.r["sp"]
    cpu.run_until(CODE_SEG, 0x0F2A, 0x0F94)
    return cstr(bp - 0x150), sp - cpu.r["sp"], seen, lookups


@unittest.skipUnless(HAVE_PRISTINE, "set COL1_PRISTINE_EXE to an unmodified VICEROY.EXE")
class RoLabelBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = exe_with()

    def test_label_slots_come_from_the_loader(self):
        # mov [bx+2DBAh], ax ... cmp word [bp-8], 0DDh: 221 @MISC entries at DS:2DBAh
        self.assertEqual(self.old[0x75230:0x7523C].hex(), "8987ba2dff46f8817ef8dd00")
        # "The" and "Adviser" are each read in one place only
        for slot, site in ((THE, 0x807E), (ADVISER, 0x3C1CA)):
            pat = b"\xff\x36" + slot.to_bytes(2, "little")
            self.assertEqual(self.old.count(pat), 1, hex(slot))
            self.assertEqual(self.old.find(pat), site)

    def test_the_netherlands(self):
        new = exe_with("ro-no-the")
        # Romanian: "The" is empty, so the old code leaves a space ("din  Olanda")
        self.assertEqual(nation_name(self.old, 3, b""), b" Olanda")
        self.assertEqual(nation_name(new, 3, b""), b"Olanda")
        # the English path, run for real: "The" and its lower-case form
        self.assertEqual(nation_name(self.old, 3, b"The"), b"The Olanda")
        self.assertEqual(nation_name(self.old, 3, b"The", lower=True), b"the Olanda")
        # other nations never had the block
        for exe in (self.old, new):
            self.assertEqual(nation_name(exe, 0, b""), b"Olanda")

    def test_father_list(self):
        new = exe_with("ro-adviser-first")
        for slot, typ in enumerate((b"de nego" + bytes([T]), b"de descoperiri", b"militar",
                                    b"politic", b"religios")):
            self.assertEqual(father_entry(self.old, slot, typ, b"Consilier"),
                             b"Adam Smith (" + typ + b" Consilier)")
            self.assertEqual(father_entry(new, slot, typ, b"Consilier"),
                             b"Adam Smith (Consilier " + typ + b")")

    def test_free_is_read_by_two_builders_only(self):
        pat = b"\xff\x36" + FREE.to_bytes(2, "little")
        self.assertEqual([i for i in range(len(self.old)) if self.old.startswith(pat, i)],
                         [0x39D67, 0x3B1D6])

    def test_hall_of_fame_free_after_nation(self):
        new = exe_with("ro-free-after-hof")
        fr = b"Fran" + bytes([T]) + b"a"
        for exe, free, want in ((self.old, True, b"X din " + LIBERA + b" " + fr),
                                (self.old, False, b"X din " + fr),
                                (new, True, b"X din " + fr + b" " + LIBERA),
                                (new, False, b"X din " + fr)):
            line, sp_moved, seen = hof_line(exe, free)
            self.assertEqual(line, want, (exe is new, free))
            self.assertEqual(sp_moved, 0, "stack not balanced")
            self.assertEqual(seen, [2], "nation index passed to 181F:065E")

    def test_report_free_after_nation(self):
        new = exe_with("ro-free-after-report")
        for exe, free, want in ((self.old, True, LIBERA + b" Anglia#"),
                                (self.old, False, b"Anglia#"),
                                (new, True, b"Anglia " + LIBERA + b"#"),
                                (new, False, b"Anglia#")):
            line, sp_moved, seen = report_line(exe, free)
            self.assertEqual(line, want, (exe is new, free))
            self.assertEqual(sp_moved, 0, "stack not balanced")
            self.assertEqual(seen, [2], "nation index passed to 181F:09A4")


    def test_hall_of_fame_colonies(self):
        new = exe_with("ro-hof-colonies")
        lead = b"Conduc" + bytes([fontedit.SLOTS["ă"]]) + b"tor, "
        for exe, free, want, nat, names in (
                (self.old, False, lead + b"Spania Colonii", [2], []),
                (self.old, True, b"General", [], []),
                (new, False, lead + b"Coloniile Spaniei", [], [(b"NAMES", b"INDEPENDENT", 6)]),
                (new, True, b"General", [], [])):
            line, pushed, seen, lookups = hof_colonies_line(exe, free)
            self.assertEqual(line, want, (exe is new, free))
            # both paths reach 0x3AF94 with one strcat's two arguments on the stack,
            # as the President path does (0x3B299)
            self.assertEqual(pushed, 4, "stack at 0x3AF94")
            self.assertEqual(seen, nat, "nationality routine 181F:065E")
            self.assertEqual(lookups, names)

    def test_colonies_readers(self):
        pat = b"\xff\x36" + COLONIES.to_bytes(2, "little")
        find = lambda exe: [i for i in range(len(exe)) if exe.startswith(pat, i)]
        self.assertEqual(find(self.old), [0x39940, 0x3AF86])
        self.assertEqual(find(exe_with("ro-hof-colonies")), [0x39940])   # F8 column only


# ---- report headers in Romanian order: "<label> <adjective>:" -----------------------

ADJECTIVES = LABELS_MISC + 2 * 211      # LABELS @MISC 211-214: "211".."214" in English, unread
ADJ = (b"englez", b"francez", b"spaniol", b"olandez")
NATIONS = (b"Anglia", b"Fran" + bytes([T]) + b"a", b"Spania", b"Olanda")
# id: (label n, Romanian label, code base, run from, run to, nation source, buffer bp-n, tail)
HEADERS = {
    "ro-f3-expedition-adj": (85, b"Oastea Craiului", 0x37000, 0x37DAF, 0x37DF3, "bp+6", 0x50, b": "),
    "ro-f3-intervention-force-adj": (111, b"Oastea de sprijin a Craiului", 0x37000, 0x37E90, 0x37ED5,
                                     "53D4", 0x50, b": "),
    "ro-f3-intervention-adj": (113, b"Interven" + bytes([T]) + b"ia Craiului", 0x37000, 0x37A53, 0x37B08,
                               "53D4", 0x50, b": "),
    "ro-f10-citizens-adj": (115, b"Poporul", 0x3A000, 0x3A205, 0x3A261, "bp-7E", 0x52, b":  +"),
    "ro-f10-congress-adj": (134, b"Congresul Continental", 0x3A000, 0x3A330, 0x3A380, "bp-7E", 0x52, b": +"),
}


def report_header(exe, pid, nation, flags=1):
    """One F3/F10 header line, from an empty buffer: (text, stack moved, nation calls seen)."""
    n, label, base, start, stop, source, buf, _ = HEADERS[pid]
    strings = {LABELS_MISC + 2 * n: label}
    strings.update({ADJECTIVES + 2 * k: a for k, a in enumerate(ADJ)})
    cpu, cstr = builder_cpu(exe, base, strings)
    ds, bp = cpu.seg(DATA_SEG), cpu.r["bp"]
    if source == "bp+6":
        ds[bp + 6:bp + 8] = nation.to_bytes(2, "little")
    elif source == "bp-7E":
        ds[bp - 0x7E:bp - 0x7C] = nation.to_bytes(2, "little")
    else:                                    # the intervening power; F3 flags: bit 0 declared, bit 1 come
        ds[0x53D4:0x53D6] = nation.to_bytes(2, "little")
        ds[0x5382] = flags
    ds[bp - buf] = 0
    seen = (with_nation_stub(cpu, 0x065E, nation, NATIONS[nation]),
            with_nation_stub(cpu, 0x09A4, nation, NATIONS[nation]))

    def cat(text):
        def hook(cpu):
            dst = cpu.rw(DATA_SEG, cpu.r["sp"])
            end = ds.index(0, dst)
            ds[end:end + len(text) + 1] = text + b"\0"
        return hook
    for off, text in ((0x01BE, b": "), (0x0146, b"+")):         # 4B helpers: ": " (DS:55h), "+"
        cpu.far_hooks[(0x181F, off)] = cat(text)
    sp = cpu.r["sp"]
    cpu.run_until(CODE_SEG, start - base, stop - base)
    return cstr(bp - buf), cpu.r["sp"] - sp, seen[0] + seen[1]


def f3_count(exe):
    """F3 "(n in m)" tail (file 0x37B41-0x37BB8) with 4968 of 5000."""
    cpu, cstr = builder_cpu(exe, 0x37000, {LABELS_MISC + 2 * 26: b"\xeen", OF_THE: b"din"})
    ds, bp = cpu.seg(DATA_SEG), cpu.r["bp"]
    ds[0x5383] |= 0x20
    ds[bp - 0x54:bp - 0x52] = (5000).to_bytes(2, "little")
    ds[bp - 0x66:bp - 0x64] = (32).to_bytes(2, "little")
    ds[bp - 0x50] = 0

    def number(cpu):                          # 181F:0182 (buf, ?, n): append n in decimal
        dst, n = cpu.rw(DATA_SEG, cpu.r["sp"]), cpu.rw(DATA_SEG, cpu.r["sp"] + 4)
        end = ds.index(0, dst)
        ds[end:end + len(str(n)) + 1] = str(n).encode() + b"\0"
    cpu.far_hooks[(0x181F, 0x0182)] = number
    sp = cpu.r["sp"]
    cpu.run_until(CODE_SEG, 0x0B41, 0x0BB8)
    return cstr(bp - 0x50), cpu.r["sp"] - sp


TRIBE_WORD = 0x8CFC          # DS: NAMES @TRIBES, 6-byte records, field 2
TINUT = bytes([T]) + b"inut"     # placeholder for "Ținut"; the capital Ț slot does not matter here


def terrain_owner(exe, tribe, wilderness=False):
    """Terrain panel owner line (file 0x437A2/0x437C2-0x437F6) from an empty buffer."""
    cpu, cstr = builder_cpu(exe, 0x43000, {LABELS_MISC + 2 * 18: TINUT, LABELS_MISC + 2 * 17: b"S"})
    ds, bp = cpu.seg(DATA_SEG), cpu.r["bp"]
    cpu.r["ax"] = tribe
    ds[0xA900:0xA908] = b"Irochez\0"
    ds[TRIBE_WORD + 6 * tribe:TRIBE_WORD + 6 * tribe + 2] = (0xA900).to_bytes(2, "little")
    ds[bp - 0x56] = 0
    sp = cpu.r["sp"]
    cpu.run_until(CODE_SEG, 0x07A2 if wilderness else 0x07C2, 0x07F6)
    return cstr(bp - 0x56), cpu.r["sp"] - sp


@unittest.skipUnless(HAVE_PRISTINE, "set COL1_PRISTINE_EXE to an unmodified VICEROY.EXE")
class RoReportOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = exe_with()

    def readers(self, exe, slot):
        b = slot.to_bytes(2, "little")
        return [i for i in range(len(exe) - 3)
                if exe[i + 2:i + 4] == b and exe[i:i + 2] in (b"\xff\x36", b"\xff\xb7", b"\xff\xb4")]

    def test_each_label_has_one_reader(self):
        for pid, (n, *_rest) in HEADERS.items():
            self.assertEqual(len(self.readers(self.old, LABELS_MISC + 2 * n)), 1, pid)
        self.assertEqual(self.readers(self.old, LABELS_MISC + 2 * 18), [0x437E6])      # Land

    def test_adjective_lines_are_unread_in_the_pristine_exe(self):
        # no push/mov of the four slots, and the loader stops at 221 lines (0DDh)
        for k in range(4):
            b = (ADJECTIVES + 2 * k).to_bytes(2, "little")
            self.assertNotIn(b"\xff\x36" + b, self.old)
            self.assertNotIn(b"\xa1" + b, self.old)
        self.assertEqual(self.old[0x75237:0x7523C].hex(), "817ef8dd00")      # cmp word [bp-8], 221
        self.assertLess(ADJECTIVES + 2 * 3, LABELS_MISC + 2 * 221)

    def test_headers(self):
        for pid, (_n, label, *_mid, tail) in HEADERS.items():
            new = exe_with(pid)
            for nation in range(4):
                line, sp_moved, seen = report_header(self.old, pid, nation)
                self.assertEqual(line, NATIONS[nation] + b" " + label + tail, (pid, nation, "old"))
                self.assertEqual(sp_moved, 0, (pid, "old stack"))
                line, sp_moved, seen = report_header(new, pid, nation)
                self.assertEqual(line, label + b" " + ADJ[nation] + tail, (pid, nation))
                self.assertEqual(sp_moved, 0, (pid, nation, "stack not balanced"))
                self.assertEqual(seen, [nation], (pid, "the nation call stays"))

    def test_intervention_other_paths_unchanged(self):
        # bit 0 clear: the Rebel/Tory line, which ends at the shared add sp,2 (0x37B05);
        # bit 1 set: the line is skipped
        new = exe_with("ro-f3-intervention-adj")
        for flags in (0, 2, 3):
            self.assertEqual(report_header(self.old, "ro-f3-intervention-adj", 3, flags)[:2],
                             report_header(new, "ro-f3-intervention-adj", 3, flags)[:2], flags)

    def test_f3_count_reads_of_the(self):
        self.assertEqual(f3_count(self.old), (b" (4968 \xeen 5000)", 0))
        self.assertEqual(f3_count(exe_with("ro-f3-of-not-in")), (b" (4968 din 5000)", 0))

    def test_terrain_land_first(self):
        new = exe_with("ro-land-after")
        self.assertEqual(terrain_owner(self.old, 7), (b"Irochez " + TINUT, 0))
        self.assertEqual(terrain_owner(new, 7), (TINUT + b" Irochez", 0))
        for exe in (self.old, new):
            self.assertEqual(terrain_owner(exe, 7, wilderness=True), (b"S", 0))


# ---- ro-panel-unit-first: the map's unit panel line ------------------------------------

UNITS, UNIT_TYPES = 0x3144, 0x5230   # DS: units (28 bytes: +2 type, +3 owner nibble); types (14 bytes, name first)
NATION_ABBREV = 0x97F0               # DS: NAMES @NATIONABBREV, 4 words (loader 0x74B7B)
CARAVELA = b"Caravel" + bytes([fontedit.SLOTS["ă"]])
ABBREV_EN = (b"Eng.", b"Fr.", b"Span.", b"Dutch")
ABBREV_RO = (b"engl.", b"fran.", b"span.", b"olan.")


def panel_line(exe, nation, abbrev):
    """Unit panel line (file 0x43428-0x43478) from an empty buffer: (text, stack moved, si)."""
    unit, utype = 5, 13
    strings = {UNIT_TYPES + 14 * utype: CARAVELA}
    strings.update({NATION_ABBREV + 2 * k: a for k, a in enumerate(abbrev)})
    cpu, cstr = builder_cpu(exe, 0x43000, strings)
    ds, bp = cpu.seg(DATA_SEG), cpu.r["bp"]
    ds[bp - 0xAE:bp - 0xAC] = unit.to_bytes(2, "little")
    ds[UNITS + 28 * unit + 2] = utype
    ds[UNITS + 28 * unit + 3] = 0x30 | nation                   # high nibble: not the owner
    ds[bp - 0x56] = 0
    sp = cpu.r["sp"]
    cpu.run_until(CODE_SEG, 0x0428, 0x0478)
    return cstr(bp - 0x56), cpu.r["sp"] - sp, cpu.r["si"]


@unittest.skipUnless(HAVE_PRISTINE, "set COL1_PRISTINE_EXE to an unmodified VICEROY.EXE")
class RoPanelOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old, cls.new = exe_with(), exe_with("ro-panel-unit-first")

    def test_abbreviations_have_one_reader(self):
        self.assertEqual(self.old[0x74B97:0x74B9B].hex(), "8987f097")    # the loader's store
        for exe, at in ((self.old, 0x43438), (self.new, 0x4345F)):
            self.assertEqual([i for i in range(len(exe) - 3) if exe[i:i + 4] == b"\xff\xb7\xf0\x97"], [at])

    def test_unit_first(self):
        for nation in range(4):
            self.assertEqual(panel_line(self.old, nation, ABBREV_EN),
                             (ABBREV_EN[nation] + b" " + CARAVELA, 0, 5 * 28), nation)
            self.assertEqual(panel_line(self.new, nation, ABBREV_RO),
                             (CARAVELA + b" " + ABBREV_RO[nation], 0, 5 * 28), nation)

    @unittest.skipUnless(RO_SRC, PRIVATE)
    def test_romanian_abbreviations(self):
        lines = (ROOT / "ro" / "work" / "ro-src" / "NAMES.TXT").read_text(encoding="utf-8").splitlines()
        start = lines.index("@NATIONABBREV") + 1
        self.assertEqual(lines[start:start + 4], [a.decode() for a in ABBREV_RO])


# ---- ro-taxparty-split, ro-teaparty-lower: the Tea Party names -------------------------

STRING_SLOTS = 0x9CD2         # DS: %STRING0..4, 64 bytes each (text expander, file 0x6EF59)
GOODS_NAMES = 0x97C0          # DS: NAMES @CARGO name (text-heap number), one word per goods
COLONIES_REC = 0x5D48         # DS: colony records, 0CAh bytes, name first
NATION_WORD = 0x838C          # DS: word per nation read for TEAPARTY's %STRING2 ([9E12h] = player)
BLANURI = b"Bl" + bytes([fontedit.SLOTS["ă"]]) + b"nuri"


def tax_cpu(exe, goods=4, colony=2):
    """Function 0x34318 (tax raise) with the goods in [bp-2] and the colony in
    [bp-0A6h]; slots 0-4 hold "S0".."S4". The slot setters are stubs."""
    cpu, cstr = builder_cpu(exe, 0x34000, {GOODS_NAMES + 2 * goods: BLANURI, NATION_WORD: b"Anglia"})
    ds, bp = cpu.seg(DATA_SEG), cpu.r["bp"]
    ds[0x9E12:0x9E14] = b"\0\0"                  # player 0
    rec = COLONIES_REC + colony * 0xCA
    ds[rec:rec + 7] = b"Boston\0"
    ds[bp - 2:bp] = goods.to_bytes(2, "little")
    ds[bp - 0xA6:bp - 0xA4] = colony.to_bytes(2, "little")
    ds[bp + 2 * goods - 0xA2:bp + 2 * goods - 0xA0] = colony.to_bytes(2, "little")
    for n in range(5):
        ds[STRING_SLOTS + 64 * n:STRING_SLOTS + 64 * n + 3] = b"S%d\0" % n
    arg = lambda n: cpu.rw(DATA_SEG, cpu.r["sp"] + 2 * n)

    def put(n, text):
        assert 0 <= n <= 4 and len(text) < 64
        ds[STRING_SLOTS + 64 * n:STRING_SLOTS + 64 * n + len(text) + 1] = text + b"\0"

    def strcpy(cpu):
        text = cstr(arg(1))
        ds[arg(0):arg(0) + len(text) + 1] = text + b"\0"
    cpu.far_hooks[(0x181F, 0x0416)] = lambda cpu: put(arg(0), bytes(cpu.seg(arg(2))[arg(1):cpu.seg(arg(2)).index(0, arg(1))]))
    cpu.far_hooks[(0x181F, 0x0438)] = lambda cpu: put(arg(0), cstr(arg(1)))
    cpu.far_hooks[(0x0D1D, 0x07E4)] = strcpy
    for off in (0x09AE, 0x04C0):                  # %NUMBERn, a sound: not modelled
        cpu.far_hooks[(0x181F, off)] = lambda cpu: None
    slots = lambda: [cstr(STRING_SLOTS + 64 * n) for n in range(5)]
    return cpu, slots


def tax_option(exe):
    """Slots when the TAXOPTIONS dialog shows (file 0x3458C-0x345D8), stack moved, si."""
    cpu, slots = tax_cpu(exe)
    sp = cpu.r["sp"]
    cpu.run_until(CODE_SEG, 0x058C, 0x05D8)
    return slots(), cpu.r["sp"] - sp, cpu.r["si"]


def tea_party(exe, goods=4):
    """Slots when TEAPARTY shows (file 0x346A3-0x34708, 181F:0652), stack moved after it."""
    cpu, slots = tax_cpu(exe, goods)
    seen = []
    cpu.far_hooks[(0x181F, 0x0652)] = lambda cpu: seen.append(slots())
    cpu.r["si"] = 2 * goods                       # as set at 0x3458E
    cpu.r["bx"] = 2 * 0xCA                        # colony 2: imul at 0x346A3
    sp = cpu.r["sp"]
    cpu.run_until(CODE_SEG, 0x06B1, 0x070B)
    return seen, cpu.r["sp"] - sp


@unittest.skipUnless(HAVE_PRISTINE, "set COL1_PRISTINE_EXE to an unmodified VICEROY.EXE")
class RoTeaPartyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = exe_with()

    def test_slots_come_from_the_expander(self):
        # "STRING" at DS:1FA4h; n = atoi; slot = n shl 6 + 9CD2h
        self.assertEqual(self.old[0x6EF37:0x6EF3A].hex(), "68a41f")
        self.assertEqual(self.old[0x6EF59:0x6EF5F].hex(), "c1e00605d29c")

    @unittest.skipUnless(RO_SRC, PRIVATE)
    def test_cargo_names_start_with_an_ascii_capital(self):
        # "or 20h" on the first byte is the whole lower-casing
        lines = (ROOT / "ro" / "work" / "ro-src" / "NAMES.TXT").read_text(encoding="utf-8").splitlines()
        start = lines.index("@CARGO") + 1
        names = [l.split(",")[0] for l in lines[start:start + 16]]
        self.assertEqual(len(names), 16)
        for name in names:
            self.assertTrue("A" <= name[0] <= "Z" and " " not in name, name)

    def test_tax_option(self):
        slots, sp_moved, si = tax_option(self.old)
        self.assertEqual(slots, [b"S0", b"S1", b"S2", b"Boston " + BLANURI, b"S4"])
        self.assertEqual(sp_moved, 0)
        slots, sp_moved, si = tax_option(exe_with("ro-taxparty-split"))
        self.assertEqual(slots, [b"S0", b"S1", b"S2", b"b" + BLANURI[1:], b"Boston"])
        self.assertEqual((sp_moved, si), (0, 8), "stack balanced, si = goods*2 kept")

    def test_tea_party(self):
        seen, sp_moved = tea_party(self.old)
        self.assertEqual(seen, [[BLANURI, b"Boston", b"Anglia", b"S3", b"S4"]])
        self.assertEqual(sp_moved, 0)
        seen, sp_moved = tea_party(exe_with("ro-teaparty-lower"))
        self.assertEqual(seen, [[b"b" + BLANURI[1:], b"Boston", b"Anglia", b"S3", b"S4"]])
        self.assertEqual(sp_moved, 0)


# ---- ro-menu-key-fold, ro-dialog-key-fold: run the hotkey readers ----------------------

MENU_KEY, DIALOG_KEY = 0x4477E, 0x6C492     # file offsets: far (MENU.TXT) and near (dialogs)
STRRCHR, STRCHR = (0x0D1D, 0x10EA), (0x0D1D, 0x1010)
TEXT_SEG = 0x3000
KEY_FOLD = {c: ord(k) for c, k in hotkey_patch.FOLD.items()}


def hotkey(exe, entry, text: bytes, at=0x0100):
    """Run a hotkey reader on `text` (a far string at TEXT_SEG:at); return (key, cpu)."""
    cpu = CPU()
    cpu.mem[CODE_SEG] = bytearray(exe[entry - 0x100:entry + 0xFF00].ljust(0x10000, b"\0"))
    cpu.mem[DATA_SEG] = bytearray(exe[DS_FILE_BASE:RESIDENT_END].ljust(0x10000, b"\0"))
    cpu.r.update(ds=DATA_SEG, ss=DATA_SEG, sp=0xE000, bp=0xE800, si=0x5678, di=0x9ABC)
    cpu.seg(TEXT_SEG)[at:at + len(text) + 1] = text + b"\0"

    def find(last):                  # _fstrrchr / _fstrchr(far s, ch): DX:AX, 0:0 if absent
        def stub(cpu):
            off, seg, ch = (cpu.rw(DATA_SEG, cpu.r["sp"] + 2 * i) for i in range(3))
            s, hit = cpu.seg(seg), None
            while s[off]:
                if s[off] == ch & 0xFF:
                    hit = off
                    if not last:
                        break
                off += 1
            cpu.r["ax"], cpu.r["dx"] = (hit, seg) if hit is not None else (0, 0)
        return stub
    cpu.far_hooks[STRRCHR], cpu.far_hooks[STRCHR] = find(True), find(False)
    if entry == MENU_KEY:
        cpu.call_far(CODE_SEG, 0x0100, [at, TEXT_SEG])
    else:
        for w in (TEXT_SEG, at, 0xFFF0):     # far pointer, then the near return address
            cpu.push(w)
        cpu.run_until(CODE_SEG, 0x0100, 0xFFF0)
    return cpu.r["ax"], cpu


@unittest.skipUnless(HAVE_PRISTINE, "set COL1_PRISTINE_EXE to an unmodified VICEROY.EXE")
class RoHotkeyFoldTests(unittest.TestCase):
    PAIRS = ((MENU_KEY, "ro-menu-key-fold"), (DIALOG_KEY, "ro-dialog-key-fold"))

    @classmethod
    def setUpClass(cls):
        cls.old = exe_with()
        d = bytearray(cls.old)
        for _, pid in cls.PAIRS:
            p = next(p for p in RO_MANIFEST["patches"] if p["id"] == pid)
            off, end = span(p)
            d[off:end] = bytes.fromhex(p["patched"])
        cls.new = bytes(d)

    def test_manifest_matches_builder(self):
        for _, pid in self.PAIRS:
            p = next(p for p in RO_MANIFEST["patches"] if p["id"] == pid)
            e = hotkey_patch.manifest_entry(self.old, pid)
            for k in ("offset", "original", "patched", "md5_alone"):
                self.assertEqual(p[k], e[k], (pid, k))

    def test_kept_bytes(self):
        # the two lcalls before each site, and the menu tail that reloads DS
        for a, b in ((0x44791, 0x447AE), (0x4482A, 0x44836), (0x6C4A3, 0x6C4BE)):
            self.assertEqual(self.new[a:b], self.old[a:b], hex(a))
        self.assertEqual(self.old[0x4482C:0x4482F].hex(), "b95a1b")      # mov cx,1B5Ah

    def test_the_old_routines_as_read(self):
        # the key is the byte after the LAST '~'; F keys are 100h + scan code
        menu = {b"NEGO~T": 0x54, b"~GAME": 0x47, b"Begin ~Trade Route": 0x54,
                b"No Orders (~s~p~a~c~e~ bar)": 0x20, b"Disband Unit (~s~h~i~f~t~-~D)": 0x44,
                b"~F~1 Terrain": 0x13B, b"~F~9 Indian": 0x143, b"~F~1~0 Score": 0x144,
                b"~F~0~1 Create Unit": 0x154, b"~F~0~1~0 Test Routine": 0x15D,
                b"NEGO~\x15": 0x15, b"Game Options": 0, b"": 0}
        for text, key in menu.items():
            self.assertEqual(hotkey(self.old, MENU_KEY, text)[0], key, text)
        dialog = {b"~Combat Analysis": 0x43, b"Analiza ~luptei": 0x4C, b"~F~1": 0x13B,
                  b"~F~1~0": 0x13A, b"x~\x60": 0x60, b"none": 0}
        for text, key in dialog.items():
            self.assertEqual(hotkey(self.old, DIALOG_KEY, text)[0], key, text)

    def test_every_byte_value(self):
        # same key as before for every byte and every '~' shape, except that
        # a Romanian slot now gives its base letter
        for entry, _ in self.PAIRS:
            for c in range(1, 256):
                b = bytes([c])
                for text in (b"~" + b, b"Xy~" + b + b"z", b"~a~" + b, b"~F~" + b + b" F",
                             b"~F~1~" + b, b"~F~0~" + b, b"~F~0~0~" + b, b"~s~" + b + b"~ "):
                    for at in (0, 0x100):
                        old, _ = hotkey(self.old, entry, text, at)
                        new, _ = hotkey(self.new, entry, text, at)
                        self.assertEqual(new, KEY_FOLD.get(old, old), (hex(entry), text, at))

    def test_romanian_lines(self):
        for text, key in ((b"NEGO~\x15", "T"), (b"NEGO~T", "T"), (b"\x7Encepe drumul de nego~\x60", "T"),
                          (b"~\x5Cr", "A"), (b"~\x16", "A"), (b"~\x1C", "A"), (b"~\x1D", "A"),
                          (b"~\x7F", "I"), (b"~\x1E", "I"), (b"~\x14", "S"), (b"~\x1F", "S")):
            for entry, _ in self.PAIRS:
                self.assertEqual(hotkey(self.new, entry, text)[0], ord(key), (hex(entry), text))

    def test_registers_the_caller_keeps(self):
        for entry, _ in self.PAIRS:
            for text in (b"NEGO~\x15", b"~F~1~0 x", b"plain"):
                _, cpu = hotkey(self.new, entry, text)
                self.assertEqual((cpu.r["ss"], cpu.r["sp"], cpu.r["bp"], cpu.r["si"], cpu.r["di"]),
                                 (DATA_SEG, 0xE000, 0xE800, 0x5678, 0x9ABC), (hex(entry), text))


# ---- ro-pedia-sort: run the Colonizopedia list sort ----------------------------------

SORT, SWAP = 0x69058, 0x68FDC           # file offsets: the gnome sort and its swap routine
SORT_BASE = 0x68000                     # file offset mapped at CODE_SEG:0000
LIST_COUNT, LIST_IDS, LIST_B1, LIST_B2 = 0xA5AA, 0x1EA6, 0x1EAA, 0x1EAE   # DS: count, far ptrs
LIST_SEG, STR_SEG = 0x4000, 0x3000
HEAP_STRING = (0x181F, 0x0022)          # thunk: string number -> far pointer (DX:AX)
RO_RANK = {"ă": 1, "â": 2, "î": 1, "ș": 1, "ț": 1}


def pedia_sort(exe, names):
    """Fill the sort's three arrays with string numbers 0..n-1 of `names`
    (game bytes) and two side bytes each, run the sort; return (names in
    the new order, cpu). Checks that the side bytes moved with their item."""
    cpu = CPU()
    cpu.mem[CODE_SEG] = bytearray(exe[SORT_BASE:SORT_BASE + 0x10000])
    cpu.mem[RUNTIME_CS] = bytearray(exe[RUNTIME_FILE:RUNTIME_FILE + 0x10000])
    cpu.mem[DATA_SEG] = bytearray(exe[DS_FILE_BASE:RESIDENT_END].ljust(0x10000, b"\0"))
    cpu.r.update(ds=DATA_SEG, ss=DATA_SEG, sp=0xE000, bp=0xE800, si=0x5678, di=0x9ABC)
    ds, lst, strs = cpu.seg(DATA_SEG), cpu.seg(LIST_SEG), cpu.seg(STR_SEG)
    at, where = 0x10, []
    for n, text in enumerate(names):
        strs[at:at + len(text) + 1] = text + b"\0"
        where.append(at)
        lst[2 * n:2 * n + 2] = n.to_bytes(2, "little")
        lst[0x400 + n], lst[0x600 + n] = n & 0xFF, n ^ 0x5A
        at += len(text) + 1
    ds[LIST_COUNT:LIST_COUNT + 2] = len(names).to_bytes(2, "little")
    for ptr, off in ((LIST_IDS, 0), (LIST_B1, 0x400), (LIST_B2, 0x600)):
        ds[ptr:ptr + 4] = off.to_bytes(2, "little") + LIST_SEG.to_bytes(2, "little")

    def heap_string(cpu):
        cpu.r["ax"], cpu.r["dx"] = where[cpu.rw(DATA_SEG, cpu.r["sp"])], STR_SEG
    cpu.far_hooks[HEAP_STRING] = heap_string
    cpu.call_far(CODE_SEG, SORT - SORT_BASE, [], limit=20_000_000)
    ids = [int.from_bytes(lst[2 * i:2 * i + 2], "little") for i in range(len(names))]
    for i, n in enumerate(ids):
        assert (lst[0x400 + i], lst[0x600 + i]) == (n & 0xFF, n ^ 0x5A), (i, n)
    return [names[n] for n in ids], cpu


def names_lists(column):
    """NAMES.TXT lists by section (first field), from ro/glossary/names.tsv."""
    lists = {}
    for line in (ROOT / "ro" / "glossary" / "names.tsv").read_text(encoding="utf-8").splitlines():
        f = line.split("\t")
        if line.startswith("#") or len(f) < 5 or f[0] == "id" or f[2] != "0":
            continue
        text = f[4] if column == "ro" else f[3]
        if text:
            lists.setdefault(f[1], []).append(text)
    return lists


def old_key(b: bytes):
    return bytes(c + 0x20 if 0x41 <= c <= 0x5A else c for c in b)      # _fstricmp


def ro_key(text: str):
    """Romanian dictionary order, from the letters themselves (not from the patch's table)."""
    return [(ord(unicodedata.normalize("NFD", c.lower())[0]), RO_RANK.get(c.lower(), 0)) for c in text]


@unittest.skipUnless(HAVE_PRISTINE, "set COL1_PRISTINE_EXE to an unmodified VICEROY.EXE")
@unittest.skipUnless(GLOSSARY, PRIVATE)
class RoPediaSortTests(unittest.TestCase):
    SECTIONS = ("UNIT", "BUILDING", "JOB", "RESOURCE", "CARGO", "FATHERS", "ORDERS")

    @classmethod
    def setUpClass(cls):
        cls.old = exe_with()
        cls.new = exe_with("ro-pedia-sort")

    def test_manifest_matches_builder(self):
        p = next(p for p in RO_MANIFEST["patches"] if p["id"] == "ro-pedia-sort")
        e = sort_patch.manifest_entry(self.old)
        for k in ("offset", "original", "patched", "md5_alone"):
            self.assertEqual(p[k], e[k], k)

    def test_kept_bytes(self):
        # the lcall to _fstricmp (overlay fixup), the loop tail, the swap routine
        self.assertEqual(self.old[0x690B5:0x690BA].hex(), "9a3e101d0d")
        for a, b in ((0x69058, 0x690B3), (0x690B5, 0x690BA), (0x6913C, 0x69156), (SWAP, SORT)):
            self.assertEqual(self.new[a:b], self.old[a:b], hex(a))

    def test_the_old_sort_as_read(self):
        # byte order after A-Z folding: Ț (15h) first, "Vânător" (â 1Dh) before "Velnicer"
        job = [build.encode(t) for t in names_lists("ro")["JOB"]]
        got, _ = pedia_sort(self.old, job)
        self.assertEqual(got, sorted(job, key=old_key))
        self.assertEqual(got[0], build.encode("Țesător"))
        self.assertLess(got.index(build.encode("Vânător")), got.index(build.encode("Velnicer")))

    def test_romanian_lists(self):
        lists = names_lists("ro")
        for sec in self.SECTIONS:
            want = [build.encode(t) for t in sorted(lists[sec], key=ro_key)]
            got, _ = pedia_sort(self.new, [build.encode(t) for t in lists[sec]])
            self.assertEqual(got, want, sec)
        got, _ = pedia_sort(self.new, [build.encode(t) for t in lists["JOB"]])
        at = [got.index(build.encode(t)) for t in ("Tutungiu", "Țesător", "Vânător", "Velnicer")]
        self.assertEqual(at, sorted(at))

    def test_english_lists_unchanged(self):
        for sec, names in names_lists("en").items():
            if sec in self.SECTIONS:
                names = [t.encode("latin-1") for t in names]
                self.assertEqual(pedia_sort(self.new, names)[0], pedia_sort(self.old, names)[0], sec)

    def test_every_other_byte_keeps_its_place(self):
        # all bytes but the 10 slots, already in the old order: the new sort swaps nothing
        slots = set(sort_patch.RANK)
        for pre, post in ((b"", b""), (b"Xy", b"z")):
            items = sorted((pre + bytes([c]) + post for c in range(1, 256) if c not in slots), key=old_key)
            self.assertEqual(pedia_sort(self.new, items)[0], items, pre)

    def test_romanian_letters(self):
        words = "ta Ța ța tz ua sz Șa șa sa Ta î I Î i j a ă â Ă Â b ab ăa âa".split()
        got, _ = pedia_sort(self.new, [build.encode(w) for w in words])
        self.assertEqual(got, [build.encode(w) for w in sorted(words, key=ro_key)])

    def test_registers_the_caller_keeps(self):
        _, cpu = pedia_sort(self.new, [build.encode(t) for t in ("Țesător", "Băiaș", "Velnicer", "Vânător")])
        self.assertEqual((cpu.r["ss"], cpu.r["sp"], cpu.r["bp"], cpu.r["si"], cpu.r["di"], cpu.r["ds"]),
                         (DATA_SEG, 0xE000, 0xE800, 0x5678, 0x9ABC, DATA_SEG))


# ---- ro-lowerstr-expander + ro-lowerstr-cave: run the text expander --------------

OVL17_FILE, EXPANDER = 0x6BE50, 0x6EEEC      # overlay 0x17 file base; the expander (far)
LOWERSTR = ("ro-lowerstr-expander", "ro-lowerstr-cave")
SLOT_BASE, TEMPLATE, OUTPUT = 0x9CD2, 0xA000, 0xB000


def exe_with_all(*pids):
    d = bytearray(Path(PRISTINE).read_bytes())
    for pid in pids:
        p = next(p for p in RO_MANIFEST["patches"] if p["id"] == pid)
        off, end = span(p)
        d[off:end] = bytes.fromhex(p["patched"])
    return bytes(d)


def expand(exe, template: bytes, slots):
    """Run the expander on template with %STRING0.. = slots; return (output, slots after, regs)."""
    cpu = CPU()
    cpu.mem[CODE_SEG] = bytearray(exe[OVL17_FILE:OVL17_FILE + 0x10000].ljust(0x10000, b"\0"))
    cpu.mem[DATA_SEG] = bytearray(exe[DS_FILE_BASE:RESIDENT_END].ljust(0x10000, b"\0"))
    cpu.r.update(ds=DATA_SEG, ss=DATA_SEG, sp=0xE000, bp=0xE800, si=0x5678, di=0x9ABC)
    ds = cpu.seg(DATA_SEG)
    ds[TEMPLATE:TEMPLATE + len(template) + 1] = template + b"\0"
    ds[OUTPUT:OUTPUT + 0x100] = b"\0" + b"\xEE" * 0xFF
    for n, s in enumerate(slots):
        ds[SLOT_BASE + 64 * n:SLOT_BASE + 64 * n + len(s) + 1] = s + b"\0"

    def cstr(o):
        return bytes(ds[o:ds.index(0, o)])

    def arg(n):
        return cpu.rw(DATA_SEG, cpu.r["sp"] + 2 * n)

    def ret(v):
        cpu.r["ax"] = v & 0xFFFF

    def strchr(cpu):
        p, ch = arg(0), arg(1) & 0xFF
        while ds[p] not in (ch, 0):
            p += 1
        ret(p if ds[p] == ch else 0)

    def strcat(cpu):
        dst, s = arg(0), cstr(arg(1))
        e = ds.index(0, dst)
        ds[e:e + len(s) + 1] = s + b"\0"
        ret(dst)

    def ncmp(fold):
        def f(cpu):
            a, b, n = arg(0), arg(1), arg(2)
            for i in range(n):
                x, y = ds[a + i], ds[b + i]
                if fold:
                    x, y = (c + 32 if 65 <= c <= 90 else c for c in (x, y))
                if x != y or x == 0:
                    return ret(x - y)
            ret(0)
        return f

    def fstrcat(cpu):                # (far dst in DS, far src)
        src = cpu.seg(arg(3))
        s = bytes(src[arg(2):src.index(0, arg(2))])
        e = ds.index(0, arg(0))
        ds[e:e + len(s) + 1] = s + b"\0"
        ret(arg(0))

    def atoi(cpu):
        p, v = arg(0), 0
        while 48 <= ds[p] <= 57:
            v, p = v * 10 + ds[p] - 48, p + 1
        ret(v)
    cpu.far_hooks.update({(0x0D1D, 0x0C56): strchr, (0x0D1D, 0x07A4): strcat,
                          (0x0D1D, 0x0CC2): ncmp(True), (0x0D1D, 0x08BC): ncmp(False),
                          (0x0D1D, 0x08F6): atoi, (0x0D1D, 0x11B4): fstrcat})
    cpu.call_far(CODE_SEG, EXPANDER - OVL17_FILE, [TEMPLATE, OUTPUT])
    regs = tuple(cpu.r[k] for k in ("sp", "bp", "si", "di", "ds", "ss"))
    return cstr(OUTPUT), [cstr(SLOT_BASE + 64 * n) for n in range(len(slots))], regs


@unittest.skipUnless(HAVE_PRISTINE, "set COL1_PRISTINE_EXE to an unmodified VICEROY.EXE")
class RoLowerStrTests(unittest.TestCase):
    SLOTS = [b"Boston", b"Bumbac", b"Anglia"]

    @classmethod
    def setUpClass(cls):
        cls.old, cls.new = exe_with_all(), exe_with_all(*LOWERSTR)

    def check(self, template, slots, want):
        out_old, slots_old, regs_old = expand(self.old, template, slots)
        out_new, slots_new, regs_new = expand(self.new, template, slots)
        self.assertEqual(out_new, want)
        self.assertEqual(slots_new, slots_old, "the slots keep their text")
        self.assertEqual(slots_new, list(slots))
        self.assertEqual(regs_new, regs_old, "sp, bp, si, di, ds, ss as before")
        return out_old

    def test_the_old_expander_as_read(self):
        # strnicmp for "STRING": %string already expanded like %STRING
        self.assertEqual(self.old[0x6EF3B:0x6EF40].hex(), "9ac20c1d0d")
        self.assertEqual(self.old[0x6EFC8:0x6EFCF].hex(), "834606" "07" "e91a01")
        old = self.check(b"tone de %string1 la %STRING0.", self.SLOTS, b"tone de bumbac la Boston.")
        self.assertEqual(old, b"tone de Bumbac la Boston.")

    def test_upper_case_unchanged(self):
        t = b"{%STRING0} de %STRING1, %STRING2 100%%"
        self.check(t, self.SLOTS, b"{Boston} de Bumbac, Anglia 100%")

    def test_lower_and_upper_in_one_line(self):
        self.check(b"%string1 apoi %STRING1 apoi %string1", self.SLOTS, b"bumbac apoi Bumbac apoi bumbac")
        self.check(b"{%STRING1} si %string0 si %string2!", self.SLOTS, b"{Bumbac} si boston si anglia!")

    def test_only_a_to_z_changes(self):
        for name, want in ((b"Zahar", b"zahar"), (b"Arme", b"arme"), (b"bumbac", b"bumbac"), (b"", b""),
                           (b"@A", b"@A"), (b"[Z", b"[Z"), (bytes([0x15]) + b"ara", bytes([0x15]) + b"ara")):
            self.check(b"x %string1 y", [b"", name, b""], b"x " + want + b" y")

    def test_text_without_slots(self):
        self.check(b"100%% sigur", self.SLOTS, b"100% sigur")
        self.check(b"nimic", self.SLOTS, b"nimic")

    @unittest.skipUnless(RECIPE, PRIVATE)
    def test_one_piece_alone_is_not_enough(self):
        # ro-lowerstr-expander calls into ro-lowerstr-cave; the rebuild recipe
        # names both, and nothing applies one without the other.
        recipe = (ROOT / "docs" / "TRANSLATION-RO.md").read_text(encoding="utf-8")
        line = next(l for l in recipe.splitlines() if "apply_patch.py" in l and "--manifest" in l)
        self.assertEqual([pid in line.split() for pid in LOWERSTR], [True, True])
        cave = next(p for p in RO_MANIFEST["patches"] if p["id"] == LOWERSTR[1])
        call = self.new[0x6EF56:0x6EF59]
        self.assertEqual(call[0], 0xE8)
        self.assertEqual(0x6EF59 + int.from_bytes(call[1:], "little"), int(cave["offset"], 16))


if __name__ == "__main__":
    unittest.main()
