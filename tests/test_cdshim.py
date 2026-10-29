"""Tests for the CD shim driver, cdshim/ (plan .claude/plans/2026-10-24-cd-audio-shim.md).

VICEROY loads ?SOUND.COL with INT 21h AX=4B03h and reads body 28h/2Ah
(relocated segments), 2Ch (bytes to keep) and the entry offsets at 32h. These
tests check that the built shim has that shape, and that the original drivers
have it too (when COL1_GAME_DIR is set). Behaviour is proven in DOS (plan
steps 3-5), not here.

* Build tests need GNU as and ld (binutils).
"""
import importlib.util
import os
import re
import shutil
import struct
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# loaded under its own name: ro/tools/build.py is "build" for the other tests
_spec = importlib.util.spec_from_file_location("cdshim_build", ROOT / "cdshim" / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

def _have_binutils():
    """GNU as and an ld that links elf_i386 (MinGW's ld on Windows does not)."""
    if not (shutil.which("as") and shutil.which("ld")):
        return False
    try:
        return "elf_i386" in subprocess.run(["ld", "-V"], capture_output=True, text=True).stdout
    except OSError:
        return False


HAVE_BINUTILS = _have_binutils()
GAME = os.environ.get("COL1_GAME_DIR")
HAVE_GAME = bool(GAME) and (Path(GAME) / "PSOUND.COL").is_file()
LOADED = 0x56                       # body offset of the "FM driver loaded" byte


@unittest.skipUnless(HAVE_BINUTILS, "needs GNU as and ld")
class ShimShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = build.assemble()
        cls.data = build.make_driver(cls.body, "PSOUND.FM", log=False)
        cls.f = build.fields(cls.data)

    def test_mz_header(self):
        d, f = self.data, self.f
        self.assertEqual(d[:2], b"MZ")
        last, pages = struct.unpack_from("<2H", d, 2)
        self.assertEqual(pages, (len(d) + 511) // 512)
        self.assertEqual(last, len(d) % 512)
        self.assertEqual(f["header_bytes"], 48)
        self.assertEqual(f["relocations"], [(0x28, 0), (0x2A, 0)])

    def test_fields_viceroy_reads(self):
        f = self.f
        self.assertEqual((f["code_seg"], f["data_seg"]), (0, 0))   # DOS adds the load segment
        self.assertEqual(f["image_size"], f["body_len"])           # VICEROY keeps the whole image
        self.assertEqual(f["magic_64"], 0x64)
        self.assertEqual(f["count"], 11)
        self.assertEqual(len(set(f["entries"])), 11)
        for e in f["entries"]:
            self.assertTrue(0x56 < e < f["body_len"], hex(e))

    def test_timer_entries_only_jump(self):
        """Entries 3 and 4 run in the timer interrupt: check 'loaded', then a far jump."""
        for n in (3, 4):
            e = self.f["entries"][n]
            code = self.body[e:e + 13]
            self.assertEqual(code[:6], bytes([0x2E, 0x80, 0x3E, LOADED, 0, 0]), n)   # cmp cs:[loaded], 0
            self.assertEqual(code[6], 0x74, n)                                        # je none
            self.assertEqual(code[8:11], bytes([0x2E, 0xFF, 0x2E]), n)                # jmp far cs:[...]
            self.assertEqual(struct.unpack_from("<H", code, 11)[0], 0x59 + 4 * n, n)  # fm_ent[n]

    def test_every_entry_but_init_checks_loaded(self):
        for n, e in enumerate(self.f["entries"][1:], 1):
            self.assertEqual(self.body[e:e + 6], bytes([0x2E, 0x80, 0x3E, LOADED, 0, 0]), n)

    def test_fm_name_and_log_flag(self):
        body = self.data[48:]
        self.assertEqual(body[0x48:0x55], b"PSOUND.FM\0\0\0\0")
        self.assertEqual(body[0x55], 0)
        logged = build.make_driver(self.body, "gsound.fm", log=True)[48:]
        self.assertEqual(logged[0x48:0x55], b"GSOUND.FM\0\0\0\0")
        self.assertEqual(logged[0x55], 1)

    def test_bad_fm_name_refused(self):
        for name in ("TOOLONGNAME.FM", "A.FMXX", "A B.FM", "..\\X.FM", ""):
            with self.assertRaises(ValueError, msg=name):
                build.make_driver(self.body, name)
        self.assertEqual(build.main(["-o", os.devnull, "--fm", "TOOLONGNAME.FM"]), 2)

    def test_track_table_matches_kb(self):
        """The table in the binary is KB game-cd-track-map: a number there is the
        track; 'none' or 'open' (28, decided 2026-10-24) means FM fallback."""
        entry = ROOT / "kb" / "entries" / "game-cd-track-map.md"
        if not entry.is_file():
            self.skipTest("the knowledge base is private (kb/)")
        kb = entry.read_text(encoding="utf-8")
        want = {}
        for line in kb.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != 6 or not re.fullmatch(r"[0-9A-F]{2}( [0-9A-F]{2})*", cells[0]):
                continue
            for cmds, track in zip(cells[0::2], cells[1::2]):
                for c in cmds.split():
                    want[int(c, 16)] = int(track) if track.isdigit() else 0
        self.assertEqual(sorted(want), list(range(0x20, 0x40)))
        got = self.data[48 + build.TRACK_TAB:48 + build.TRACK_TAB + 32]
        self.assertEqual({0x20 + i: t for i, t in enumerate(got)}, want)
        self.assertEqual(build.fields(self.data)["tracks"], {c: t for c, t in want.items() if t})

    def test_cd_track_sets_the_music_flag(self):
        """Before a CD track the shim sets the FM driver's song byte to FFh, as
        the driver's dispatcher does before every song: the scan for
        "mov byte ptr [X], 0FFh" + "call cs:[bx + table]" in entry 1, and the
        write "mov byte ptr es:[bx], 0FFh". Without it PSOUND kept its effect
        samples off after York (2A) on FM (SHIMTEST /E, 2026-10-24)."""
        code = self.body[self.f["entries"][0]:]
        for word in (0x06C6, 0x2EFF, 0x97FF):                  # cmp word ptr es:[si(+n)], imm16
            self.assertIn(struct.pack("<H", word), code, hex(word))
        self.assertIn(bytes([0x26, 0xC6, 0x07, 0xFF]), code)

    def test_cdvol_channel_control(self):
        """CDVOL=n in the environment -> MSCDEX audio channel control (IOCTL
        output, code 3: input channel and volume for outputs 0-3). Measured in
        DOSBox-Daum 2026-10-25: CDVOL=50 halves the CD track's level."""
        self.assertIn(b"CDVOL=", self.body)
        self.assertIn(bytes([3, 0, 0xFF, 1, 0xFF, 2, 0, 3, 0]), self.body)

    def test_no_386_long_jumps(self):
        """.arch i286: no 0F 8x long conditional jumps in the code."""
        code = self.body[self.f["entries"][0]:]
        for i in range(len(code) - 1):
            if code[i] == 0x0F:
                self.assertFalse(0x80 <= code[i + 1] <= 0x8F, hex(i))


_spec2 = importlib.util.spec_from_file_location("cue2audio", ROOT / "cdshim" / "cue2audio.py")
cue2audio = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(cue2audio)


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class CueToAudio(unittest.TestCase):
    """cue2audio.py on a tiny disc: data track, then two audio tracks."""

    def test_split_keeps_layout_and_samples(self):
        import subprocess
        import tempfile
        S = cue2audio.SECTOR
        data = bytes([1]) * 10 * S
        a2 = (bytes(range(256)) * 80)[:8 * S]                # track 2: 8 sectors
        gap3 = bytes(2 * S)                                 # INDEX 00 of track 3: 2 sectors
        a3 = (bytes(range(255, -1, -1)) * 60)[:6 * S]       # track 3: 6 sectors
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "D.bin").write_bytes(data + a2 + gap3 + a3)
            (t / "D.cue").write_text('FILE "D.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
                                     "  TRACK 02 AUDIO\n    PREGAP 00:02:00\n    INDEX 01 00:00:10\n"
                                     "  TRACK 03 AUDIO\n    INDEX 00 00:00:18\n    INDEX 01 00:00:20\n")
            self.assertEqual(cue2audio.main([str(t / "D.cue"), str(t / "out"), "--format", "flac"]), 0)
            cue = (t / "out" / "D.cue").read_text().split()
            self.assertEqual(" ".join(cue), 'FILE "D-01.bin" BINARY TRACK 01 MODE1/2352 INDEX 01 00:00:00 '
                             'FILE "D-02.flac" WAVE TRACK 02 AUDIO PREGAP 00:02:00 INDEX 01 00:00:00 '
                             'FILE "D-03.flac" WAVE TRACK 03 AUDIO PREGAP 00:00:02 INDEX 01 00:00:00')
            self.assertEqual((t / "out" / "D-01.bin").read_bytes(), data)
            for name, want in (("D-02.flac", a2), ("D-03.flac", a3)):
                raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(t / "out" / name),
                                      "-f", "s16le", "-"], capture_output=True, check=True).stdout
                self.assertEqual(raw, want, name)
            self.assertEqual(cue2audio.main([str(t / "D.cue"), str(t / "o2"), "--format", "ogg"]), 2)

    def test_jingles_follow_the_last_track(self):
        """--jingles DIR: GOLDCITY, MEETKING, 1812 (any case) become tracks 4, 5, 6
        of the tiny disc (28-30 of Colonization), 2-second PREGAP, 44.1 kHz stereo
        MP3 as long as the WAV (8-bit mono 11025 Hz, like the Windows files)."""
        import json
        import subprocess
        import tempfile
        import wave
        S = cue2audio.SECTOR
        secs = {"goldcity.wav": 0.75, "MEETKING.WAV": 0.5, "1812.wav": 0.65}
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "D.bin").write_bytes(bytes(10 * S) + (bytes(range(256)) * 160)[:16 * S])
            (t / "D.cue").write_text('FILE "D.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n'
                                     "  TRACK 02 AUDIO\n    INDEX 01 00:00:10\n"
                                     "  TRACK 03 AUDIO\n    INDEX 01 00:00:18\n")
            (t / "win").mkdir()
            for name, s in secs.items():
                with wave.open(str(t / "win" / name), "wb") as w:
                    w.setparams((1, 1, 11025, 0, "NONE", ""))
                    w.writeframes(bytes((128 + (i % 50) * 2) & 255 for i in range(int(s * 11025))))
            self.assertEqual(cue2audio.main([str(t / "D.cue"), str(t / "out"), "--jingles", str(t / "win")]), 0)
            cue = " ".join((t / "out" / "D.cue").read_text().split())
            self.assertTrue(cue.endswith('FILE "D-03.mp3" MP3 TRACK 03 AUDIO INDEX 01 00:00:00 '
                                         'FILE "D-04.mp3" MP3 TRACK 04 AUDIO PREGAP 00:02:00 INDEX 01 00:00:00 '
                                         'FILE "D-05.mp3" MP3 TRACK 05 AUDIO PREGAP 00:02:00 INDEX 01 00:00:00 '
                                         'FILE "D-06.mp3" MP3 TRACK 06 AUDIO PREGAP 00:02:00 INDEX 01 00:00:00'), cue)
            for n, s in zip((4, 5, 6), secs.values()):
                probe = json.loads(subprocess.run(
                    ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                     str(t / "out" / f"D-{n:02d}.mp3")], capture_output=True, check=True).stdout)
                st = probe["streams"][0]
                self.assertEqual((st["codec_name"], st["sample_rate"], st["channels"]), ("mp3", "44100", 2))
                self.assertAlmostEqual(float(probe["format"]["duration"]), s, delta=0.1)
            (t / "win" / "1812.wav").unlink()
            self.assertEqual(cue2audio.main([str(t / "D.cue"), str(t / "o2"), "--jingles", str(t / "win")]), 1)
            self.assertEqual(cue2audio.main([str(t / "D.cue"), str(t / "o3"), "--jingles"]), 2)


@unittest.skipUnless(HAVE_GAME, "set COL1_GAME_DIR to the English game folder")
class OriginalDrivers(unittest.TestCase):
    """The original drivers have the shape the shim copies."""

    def test_relocated_segments_and_entries(self):
        for letter in "AGPR":
            data = (Path(GAME) / f"{letter}SOUND.COL").read_bytes()
            f = build.fields(data)
            rel = {o for o, s in f["relocations"] if s == 0}
            self.assertIn(0x28, rel, letter)          # code segment word
            self.assertIn(0x2A, rel, letter)          # data segment word
            self.assertEqual(f["magic_64"], 0x64, letter)
            self.assertEqual(f["count"], 11, letter)
            body = data[f["header_bytes"]:]
            self.assertEqual(body[:16], bytes(16), letter)
            self.assertEqual(body[0x24:0x26], b"NO", letter)

    def test_music_flag_in_dispatcher(self):
        """The byte the dispatcher sets to FFh before every song, found as the
        shim finds it (entry 1, first 80h bytes). PSOUND plays an effect's
        digitised sample only while it is FFh (body 1CD3h: cmp [1CAh], FFh)."""
        want = {"A": 0x24D, "P": 0x1CA, "G": None, "R": None}
        for letter in "AGPR":
            data = (Path(GAME) / f"{letter}SOUND.COL").read_bytes()
            body = data[build.fields(data)["header_bytes"]:]
            e1 = struct.unpack_from("<H", body, 0x34)[0]
            m = re.search(rb"\xC6\x06(..)\xFF\x2E\xFF\x97", body[e1:e1 + 0x80], re.S)
            got = struct.unpack("<H", m.group(1))[0] if m else None
            self.assertEqual(got, want[letter], letter)
            if letter == "P":
                self.assertEqual(body[0x1CD3:0x1CD8], bytes([0x80, 0x3E, 0xCA, 0x01, 0xFF]))


if __name__ == "__main__":
    unittest.main()
