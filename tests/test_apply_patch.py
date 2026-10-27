"""Tests for the patchers in patch/.

The tests run each patcher as a separate process, the way a user runs it, from
a temporary folder that holds a copy of the patcher and a patches.json. This
lets the same tests run against every implementation (see IMPLEMENTATIONS).

* Fake-EXE tests always run. They use a file of the right size (494,910 bytes)
  that holds only the expected bytes at each patch site. No game code needed.
* Real-EXE tests run only when COL1_PRISTINE_EXE points at an unmodified
  VICEROY.EXE (md5 0f5d5b0063721fbc6aca314e5a43ddaf).

Run:  python3 -m unittest discover -s tests -v
"""
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATCH_DIR = ROOT / "patch"
REAL_MANIFEST = json.loads((PATCH_DIR / "patches.json").read_text(encoding="utf-8"))
SIZE = REAL_MANIFEST["target"]["size"]
PRISTINE = os.environ.get("COL1_PRISTINE_EXE")
HAVE_PRISTINE = bool(PRISTINE) and Path(PRISTINE).is_file()
# What --all applies to the real manifest.
RECOMMENDED = {p["id"] for p in REAL_MANIFEST["patches"] if p["status"] == "recommended"}
# The first boycott fix (Aug 2026, withdrawn 2026-10): file offset and bytes.
OLD_BOYCOTT = (0x4121A, b"\x90\x90")


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def fake_exe(manifest: dict, applied=()) -> bytes:
    data = bytearray(SIZE)
    for p in manifest["patches"]:
        hexval = p["patched"] if p["id"] in applied else p["original"]
        b = bytes.fromhex(hexval)
        off = int(p["offset"], 16)
        data[off:off + len(b)] = b
    return bytes(data)


# Two made-up patches used to test rules the real manifest cannot show yet
# (experimental status, conflicts). They sit in empty space of the fake EXE.
def by_id(m: dict, pid: str) -> dict:
    return next(p for p in m["patches"] if p["id"] == pid)


def synthetic_manifest() -> dict:
    m = copy.deepcopy(REAL_MANIFEST)
    base = dict(asm_before="x", asm_after="y", md5_alone="0" * 32, summary="test", conflicts=[])
    m["patches"] += [
        dict(base, id="exp-a", title="A", status="experimental",
             offset="0x100", original="1111", patched="2222"),
        dict(base, id="exp-b", title="B", status="experimental",
             offset="0x200", original="3333", patched="4444", conflicts=["exp-a"]),
    ]
    return m


class PythonImpl:
    name = "python"
    script = "apply_patch.py"

    @staticmethod
    def argv(script: Path, exe, ids=(), manifest=None, **flags):
        cmd = [sys.executable, str(script), str(exe), *ids]
        for flag, on in flags.items():
            if on:
                cmd.append("--" + flag.replace("_", "-"))
        if manifest is not None:
            cmd += ["--manifest", str(manifest)]
        return cmd


class PowerShellImpl:
    name = "powershell"
    script = "Apply-Patch.ps1"
    FLAGS = {"all": "-All", "revert": "-Revert", "list": "-List",
             "status": "-Status", "check": "-Check", "force": "-Force"}

    @classmethod
    def argv(cls, script: Path, exe, ids=(), manifest=None, **flags):
        cmd = [PWSH, "-NoProfile", "-NonInteractive", "-File", str(script), str(exe)]
        cmd += [cls.FLAGS[f] for f, on in flags.items() if on]
        if manifest is not None:
            cmd += ["-Manifest", str(manifest)]
        return cmd + list(ids)


# COL1_PWSH picks the shell, e.g. "powershell" for Windows PowerShell 5.1.
PWSH = shutil.which(os.environ.get("COL1_PWSH", "")) or shutil.which("pwsh") or shutil.which("powershell")
IMPLEMENTATIONS = [PythonImpl] + ([PowerShellImpl] if PWSH else [])
if not PWSH:
    print("note: pwsh not found; PowerShell patcher not tested", file=sys.stderr)


class PatcherTests:
    """Mixed into one TestCase per implementation (see bottom of file)."""
    impl = None

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tool = self.tmp / "tool"
        self.tool.mkdir()
        shutil.copy(PATCH_DIR / self.impl.script, self.tool)
        self.use_manifest(REAL_MANIFEST)
        self.game = self.tmp / "game"
        self.game.mkdir()
        self.exe = self.game / "VICEROY.EXE"

    def use_manifest(self, m):
        (self.tool / "patches.json").write_text(json.dumps(m, indent=2), encoding="utf-8")

    def run_tool(self, ids=(), exe=None, cwd=None, manifest=None, **flags):
        cmd = self.impl.argv(self.tool / self.impl.script, exe or self.exe, ids, manifest, **flags)
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd or self.tmp)
        return r.returncode, r.stdout, r.stderr

    def baks(self):
        return sorted(p.name for p in self.game.glob("*.bak"))

    # ---- basic apply / revert -------------------------------------------------

    def test_apply_all_changes_only_patch_bytes(self):
        self.exe.write_bytes(fake_exe(REAL_MANIFEST))
        rc, out, err = self.run_tool(all=True)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.exe.read_bytes(), fake_exe(REAL_MANIFEST, applied=RECOMMENDED))

    def test_apply_by_id_then_revert(self):
        orig = fake_exe(REAL_MANIFEST)
        self.exe.write_bytes(orig)
        self.assertEqual(self.run_tool(["traderoute-topup"])[0], 0)
        self.assertNotEqual(self.exe.read_bytes(), orig)
        self.assertEqual(self.run_tool(["traderoute-topup"], revert=True)[0], 0)
        self.assertEqual(self.exe.read_bytes(), orig)

    def test_second_apply_is_noop_and_makes_no_backup(self):
        self.exe.write_bytes(fake_exe(REAL_MANIFEST, applied=RECOMMENDED))
        rc, out, _ = self.run_tool(all=True)
        self.assertEqual(rc, 0)
        self.assertIn("Nothing to do", out)
        self.assertEqual(self.baks(), [])

    # ---- backups ----------------------------------------------------------------

    def test_backup_is_named_by_md5_of_input(self):
        orig = fake_exe(REAL_MANIFEST)
        self.exe.write_bytes(orig)
        self.run_tool(all=True)
        bak = self.game / f"VICEROY.EXE.{md5(orig)[:8]}.bak"
        self.assertEqual(self.baks(), [bak.name])
        self.assertEqual(bak.read_bytes(), orig)

    def test_each_state_gets_its_own_backup(self):
        orig = fake_exe(REAL_MANIFEST)
        self.exe.write_bytes(orig)
        self.run_tool(all=True)
        patched = self.exe.read_bytes()
        self.run_tool(all=True, revert=True)
        self.assertEqual(sorted(self.baks()),
                         sorted([f"VICEROY.EXE.{md5(orig)[:8]}.bak",
                                 f"VICEROY.EXE.{md5(patched)[:8]}.bak"]))

    def test_existing_backup_is_not_overwritten(self):
        orig = fake_exe(REAL_MANIFEST)
        self.exe.write_bytes(orig)
        bak = self.game / f"VICEROY.EXE.{md5(orig)[:8]}.bak"
        bak.write_bytes(b"keep me")
        self.run_tool(all=True)
        self.assertEqual(bak.read_bytes(), b"keep me")

    # ---- refusals ---------------------------------------------------------------

    def test_unexpected_bytes_refused_and_nothing_written(self):
        data = bytearray(fake_exe(REAL_MANIFEST))
        data[0x41384] = 0x11                    # traderoute-topup site, 74 03
        self.exe.write_bytes(bytes(data))
        rc, _, err = self.run_tool(all=True)
        self.assertEqual(rc, 3)
        self.assertIn("1103", err)
        self.assertEqual(self.exe.read_bytes(), bytes(data))
        self.assertEqual(self.baks(), [])

    def test_unexpected_bytes_message_shows_whole_site(self):
        self.use_manifest(synthetic_manifest())
        data = bytearray(fake_exe(synthetic_manifest()))
        data[0x100:0x102] = b"\xab\xcd"
        self.exe.write_bytes(bytes(data))
        rc, _, err = self.run_tool(["exp-a"])
        self.assertEqual(rc, 3)
        self.assertIn("abcd", err)

    def test_wrong_size_refused(self):
        self.exe.write_bytes(fake_exe(REAL_MANIFEST) + b"\0")
        rc, _, err = self.run_tool(all=True)
        self.assertEqual(rc, 3)
        self.assertIn("494910", err)

    def test_missing_file(self):
        rc, _, err = self.run_tool(all=True, exe=self.game / "nope.exe")
        self.assertEqual(rc, 1)

    def test_unknown_id(self):
        self.exe.write_bytes(fake_exe(REAL_MANIFEST))
        rc, _, err = self.run_tool(["bogus"])
        self.assertEqual(rc, 2)
        self.assertIn("bogus", err)

    def test_nothing_selected(self):
        self.exe.write_bytes(fake_exe(REAL_MANIFEST))
        self.assertEqual(self.run_tool()[0], 2)

    def test_all_plus_ids_refused(self):
        self.exe.write_bytes(fake_exe(REAL_MANIFEST))
        self.assertEqual(self.run_tool(["traderoute-topup"], all=True)[0], 2)

    # ---- status / list ----------------------------------------------------------

    def test_status_and_check_alias_write_nothing(self):
        orig = fake_exe(REAL_MANIFEST)
        self.exe.write_bytes(orig)
        for flag in ("status", "check"):
            rc, out, _ = self.run_tool(**{flag: True})
            self.assertEqual(rc, 0)
            self.assertIn("traderoute-boycott", out)
            self.assertIn("not applied", out)
        self.assertEqual(self.exe.read_bytes(), orig)
        self.assertEqual(self.baks(), [])

    def test_list_needs_no_valid_exe(self):
        rc, out, _ = self.run_tool(list=True, exe=self.game / "nope.exe")
        self.assertEqual(rc, 0)
        self.assertIn("traderoute-boycott", out)

    # ---- experimental and conflicts -----------------------------------------------

    def test_all_skips_experimental(self):
        m = synthetic_manifest()
        self.use_manifest(m)
        self.exe.write_bytes(fake_exe(m))
        rc, out, _ = self.run_tool(all=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.exe.read_bytes(), fake_exe(m, applied=RECOMMENDED))
        self.assertIn("exp-a", out)

    def test_withdrawn_boycott_fix_refused(self):
        # The first boycott fix (0x4121A 75DA -> 9090) is no longer listed.
        # v2's site covers those bytes, so an EXE that has it is refused
        # (exit 3, nothing written) instead of getting v2 on top of it.
        data = bytearray(fake_exe(REAL_MANIFEST))
        data[OLD_BOYCOTT[0]:OLD_BOYCOTT[0] + 2] = OLD_BOYCOTT[1]
        self.exe.write_bytes(bytes(data))
        rc, _, err = self.run_tool(all=True)
        self.assertEqual(rc, 3, err)
        self.assertIn("0x41215", err)
        self.assertEqual(self.exe.read_bytes(), bytes(data))
        self.assertEqual(self.baks(), [])

    def test_experimental_applies_when_named(self):
        m = synthetic_manifest()
        self.use_manifest(m)
        self.exe.write_bytes(fake_exe(m))
        self.assertEqual(self.run_tool(["exp-a"])[0], 0)
        self.assertEqual(self.exe.read_bytes(), fake_exe(m, applied={"exp-a"}))

    def test_conflict_declared_on_new_patch(self):
        m = synthetic_manifest()
        self.use_manifest(m)
        self.exe.write_bytes(fake_exe(m, applied={"exp-a"}))
        rc, _, err = self.run_tool(["exp-b"])
        self.assertEqual(rc, 5)
        self.assertEqual(self.exe.read_bytes(), fake_exe(m, applied={"exp-a"}))

    def test_conflict_declared_on_installed_patch(self):
        m = synthetic_manifest()
        self.use_manifest(m)
        self.exe.write_bytes(fake_exe(m, applied={"exp-b"}))
        self.assertEqual(self.run_tool(["exp-a"])[0], 5)

    def test_conflict_within_one_command(self):
        m = synthetic_manifest()
        self.use_manifest(m)
        self.exe.write_bytes(fake_exe(m))
        self.assertEqual(self.run_tool(["exp-a", "exp-b"])[0], 5)
        self.assertEqual(self.exe.read_bytes(), fake_exe(m))

    def test_revert_is_never_blocked_by_conflicts(self):
        m = synthetic_manifest()
        self.use_manifest(m)
        self.exe.write_bytes(fake_exe(m, applied={"exp-a"}))
        self.assertEqual(self.run_tool(["exp-a"], revert=True)[0], 0)
        self.assertEqual(self.exe.read_bytes(), fake_exe(m))

    def test_revert_all_includes_experimental(self):
        m = synthetic_manifest()
        self.use_manifest(m)
        self.exe.write_bytes(fake_exe(m, applied={"traderoute-topup", "exp-a"}))
        self.assertEqual(self.run_tool(all=True, revert=True)[0], 0)
        self.assertEqual(self.exe.read_bytes(), fake_exe(m))

    # ---- manifest validation (fail closed) ----------------------------------------

    def assert_manifest_rejected(self, m):
        self.use_manifest(m)
        self.exe.write_bytes(fake_exe(REAL_MANIFEST))
        rc, _, err = self.run_tool(all=True)
        self.assertEqual(rc, 6, err)
        self.assertEqual(self.exe.read_bytes(), fake_exe(REAL_MANIFEST))

    def test_manifest_missing_status_rejected(self):
        m = copy.deepcopy(REAL_MANIFEST)
        del by_id(m, "traderoute-topup")["status"]
        self.assert_manifest_rejected(m)

    def test_manifest_unknown_status_rejected(self):
        m = copy.deepcopy(REAL_MANIFEST)
        by_id(m, "traderoute-topup")["status"] = "maybe"
        self.assert_manifest_rejected(m)

    def test_manifest_length_mismatch_rejected(self):
        m = copy.deepcopy(REAL_MANIFEST)
        by_id(m, "traderoute-topup")["patched"] = "90"
        self.assert_manifest_rejected(m)

    def test_manifest_overlap_without_conflict_rejected(self):
        m = synthetic_manifest()
        by_id(m, "exp-b")["offset"] = "0x101"
        by_id(m, "exp-b")["conflicts"] = []
        self.assert_manifest_rejected(m)

    def test_manifest_unknown_conflict_id_rejected(self):
        m = synthetic_manifest()
        by_id(m, "exp-b")["conflicts"] = ["nope"]
        self.assert_manifest_rejected(m)

    def test_manifest_old_schema_rejected(self):
        m = copy.deepcopy(REAL_MANIFEST)
        m["schema"] = 1
        self.assert_manifest_rejected(m)

    # ---- --manifest: a second patch list ------------------------------------------

    def other_manifest(self) -> dict:
        m = copy.deepcopy(REAL_MANIFEST)
        m["patches"] = [dict(id="other-x", title="X", status="recommended", offset="0x300",
                             original="5555", patched="6666", asm_before="x", asm_after="y",
                             md5_alone="0" * 32, summary="test")]
        return m

    def test_manifest_option_uses_the_named_list(self):
        m = self.other_manifest()
        path = self.tmp / "other.json"
        path.write_text(json.dumps(m), encoding="utf-8")
        both = copy.deepcopy(REAL_MANIFEST)
        both["patches"] += m["patches"]
        self.exe.write_bytes(fake_exe(both))
        self.assertEqual(self.run_tool(["other-x"])[0], 2)        # not in patches.json
        rc, _, err = self.run_tool(["other-x"], manifest=path)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.exe.read_bytes(), fake_exe(both, applied={"other-x"}))
        rc, out, _ = self.run_tool(status=True, manifest=path)
        self.assertIn("other-x", out)
        self.assertNotIn("traderoute-boycott", out)

    def test_manifest_option_relative_path_resolves_from_working_directory(self):
        m = self.other_manifest()
        (self.game / "other.json").write_text(json.dumps(m), encoding="utf-8")
        both = copy.deepcopy(REAL_MANIFEST)
        both["patches"] += m["patches"]
        self.exe.write_bytes(fake_exe(both))
        rc, _, err = self.run_tool(["other-x"], manifest=Path("other.json"), cwd=self.game)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.exe.read_bytes(), fake_exe(both, applied={"other-x"}))

    def test_manifest_option_missing_file_is_invalid_manifest(self):
        self.exe.write_bytes(fake_exe(REAL_MANIFEST))
        rc, _, _ = self.run_tool(all=True, manifest=self.tmp / "nope.json")
        self.assertEqual(rc, 6)
        self.assertEqual(self.exe.read_bytes(), fake_exe(REAL_MANIFEST))

    # ---- paths ----------------------------------------------------------------

    def test_relative_path_resolves_from_working_directory(self):
        orig = fake_exe(REAL_MANIFEST)
        self.exe.write_bytes(orig)
        decoy = self.tmp / "VICEROY.EXE"
        decoy.write_bytes(orig)
        rc, _, err = self.run_tool(all=True, exe=Path("VICEROY.EXE"), cwd=self.game)
        self.assertEqual(rc, 0, err)
        self.assertNotEqual(self.exe.read_bytes(), orig)
        self.assertEqual(decoy.read_bytes(), orig)
        self.assertEqual(len(self.baks()), 1)

    def test_path_with_brackets(self):
        d = self.tmp / "C[1]"
        d.mkdir()
        exe = d / "VICEROY.EXE"
        exe.write_bytes(fake_exe(REAL_MANIFEST))
        rc, _, err = self.run_tool(all=True, exe=exe)
        self.assertEqual(rc, 0, err)
        self.assertEqual(exe.read_bytes(), fake_exe(REAL_MANIFEST, applied=RECOMMENDED))

    def test_no_temp_files_left_behind(self):
        self.exe.write_bytes(fake_exe(REAL_MANIFEST))
        self.run_tool(all=True)
        self.assertEqual(sorted(p.name for p in self.game.iterdir()
                                if not p.name.endswith(".bak")), ["VICEROY.EXE"])

    # ---- the real binary ------------------------------------------------------

    @unittest.skipUnless(HAVE_PRISTINE, "set COL1_PRISTINE_EXE to an unmodified VICEROY.EXE")
    def test_real_exe_every_patch_matches_md5_alone(self):
        pristine = Path(PRISTINE).read_bytes()
        self.assertEqual(md5(pristine), REAL_MANIFEST["target"]["md5_pristine"])
        for p in REAL_MANIFEST["patches"]:
            with self.subTest(patch=p["id"]):
                self.exe.write_bytes(pristine)
                rc, _, err = self.run_tool([p["id"]])
                self.assertEqual(rc, 0, err)
                self.assertEqual(md5(self.exe.read_bytes()), p["md5_alone"])
                self.run_tool([p["id"]], revert=True)
                self.assertEqual(md5(self.exe.read_bytes()), REAL_MANIFEST["target"]["md5_pristine"])


@unittest.skipUnless(PWSH, "pwsh not found")
class PowerShellOnlyTests(unittest.TestCase):
    """Traps that exist only in PowerShell."""

    def test_set_location_differs_from_process_folder(self):
        # Old bug: Test-Path used the PowerShell location, [IO.File] used the
        # process folder, so the backup landed in one place and the patch in
        # another file.
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        game, other = tmp / "game", tmp / "other"
        game.mkdir(); other.mkdir()
        orig = fake_exe(REAL_MANIFEST)
        (game / "VICEROY.EXE").write_bytes(orig)
        (other / "VICEROY.EXE").write_bytes(orig)
        script = PATCH_DIR / "Apply-Patch.ps1"
        cmd = [PWSH, "-NoProfile", "-NonInteractive", "-Command",
               f"Set-Location -LiteralPath '{game}'; & '{script}' VICEROY.EXE -All; exit $LASTEXITCODE"]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=other)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotEqual((game / "VICEROY.EXE").read_bytes(), orig)
        self.assertEqual((other / "VICEROY.EXE").read_bytes(), orig)
        self.assertEqual(list(other.glob("*.bak")), [])


class ManifestTests(unittest.TestCase):
    def test_real_manifest_offsets_are_hex_strings(self):
        for p in REAL_MANIFEST["patches"]:
            self.assertTrue(p["offset"].startswith("0x"), p["id"])

    def test_real_manifest_sites_match_pristine_exe(self):
        if not HAVE_PRISTINE:
            self.skipTest("set COL1_PRISTINE_EXE")
        data = Path(PRISTINE).read_bytes()
        for p in REAL_MANIFEST["patches"]:
            off = int(p["offset"], 16)
            orig = bytes.fromhex(p["original"])
            self.assertEqual(data[off:off + len(orig)], orig, p["id"])


@unittest.skipUnless(HAVE_PRISTINE, "set COL1_PRISTINE_EXE to an unmodified VICEROY.EXE")
class RealBinaryStaticTests(unittest.TestCase):
    """Static facts about the patches, checked on the real VICEROY.EXE."""

    @classmethod
    def setUpClass(cls):
        cls.data = Path(PRISTINE).read_bytes()

    def relocated_words(self):
        """File offsets of every word the DOS loader rewrites (MZ relocations)."""
        d = self.data
        count = int.from_bytes(d[6:8], "little")
        table = int.from_bytes(d[0x18:0x1A], "little")
        header = int.from_bytes(d[8:10], "little") * 16
        out = {}
        for i in range(count):
            off = int.from_bytes(d[table + 4 * i:table + 4 * i + 2], "little")
            seg = int.from_bytes(d[table + 4 * i + 2:table + 4 * i + 4], "little")
            out[header + seg * 16 + off] = i
        return out

    def test_no_patch_touches_srand(self):
        # srand() at 0x103C2..0x103D3 zeroes the high state word; that is what
        # makes seeded content (colony layout, village teaching) reproducible.
        for p in REAL_MANIFEST["patches"]:
            off = int(p["offset"], 16)
            end = off + len(bytes.fromhex(p["original"]))
            self.assertTrue(end <= 0x103C2 or off >= 0x103D4, p["id"])

    def test_relocations_inside_patch_sites_are_the_known_ones(self):
        # A relocated word inside a patch site is rewritten by the loader, so
        # it must never be executed. Each one here has been checked by hand;
        # a new entry means a new patch needs the same check.
        known = {("rng-idle-stir", 0xC304): 523}   # jumped over: 0xC301 jmp -> 0xC306
        relocs = self.relocated_words()
        found = {}
        for p in REAL_MANIFEST["patches"]:
            off = int(p["offset"], 16)
            end = off + len(bytes.fromhex(p["original"]))
            for w, idx in relocs.items():
                if w < end and w + 2 > off:
                    found[(p["id"], w)] = idx
        self.assertEqual(found, known)

    def test_boycott_and_idle_stir_match_live_install(self):
        # boycott + idle-stir is what Florin's install ran on 2026-09-23.
        d = bytearray(self.data)
        d[OLD_BOYCOTT[0]:OLD_BOYCOTT[0] + 2] = OLD_BOYCOTT[1]
        for pid in ("rng-idle-stir",):
            p = by_id(REAL_MANIFEST, pid)
            off = int(p["offset"], 16)
            new = bytes.fromhex(p["patched"])
            d[off:off + len(new)] = new
        self.assertEqual(md5(bytes(d)), "7289494e2ba2561792092a5918d075bd")

    def test_recommended_set_matches_play_exe(self):
        # --all applies every recommended patch: the v2 boycott pair, topup and
        # idle-stir since 2026-10-19 (1905cb45), plus the unload cap pair since
        # 2026-10-27 (QEMU-tested as play/unload-cap/GAME-after).
        rec = [p for p in REAL_MANIFEST["patches"] if p["status"] == "recommended"]
        self.assertEqual(sorted(p["id"] for p in rec),
                         ["rng-idle-stir", "traderoute-boycott-v2", "traderoute-boycott-v2-tail",
                          "traderoute-topup", "traderoute-unload-cap", "traderoute-unload-cap-loop"])
        d = bytearray(self.data)
        for p in rec:
            off = int(p["offset"], 16)
            new = bytes.fromhex(p["patched"])
            d[off:off + len(new)] = new
        self.assertEqual(md5(bytes(d)), "40c5827211da71be83722c3676ab8ec4")

    def overlays(self):
        """(header, code start, end, relocated file offsets) for each overlay.

        The overlays follow the MZ image, one after another. Header words:
        w0 = size in paragraphs (header included), w1 = header paragraphs,
        w2 = 0x2BC, w4 = relocation count; (offset, segment) pairs follow.
        """
        d = self.data
        pages, last = int.from_bytes(d[4:6], "little"), int.from_bytes(d[2:4], "little")
        h = (pages * 512 - ((512 - last) if last else 0) + 15) // 16 * 16
        out = []
        while h + 16 <= len(d) and int.from_bytes(d[h + 4:h + 6], "little") == 0x2BC:
            size, hdr, count = (int.from_bytes(d[h + k:h + k + 2], "little") for k in (0, 2, 8))
            code = h + hdr * 16
            relocs = set()
            for i in range(count):
                off = int.from_bytes(d[h + 16 + 4 * i:h + 18 + 4 * i], "little")
                seg = int.from_bytes(d[h + 18 + 4 * i:h + 20 + 4 * i], "little")
                relocs.add(code + seg * 16 + off)
            out.append((h, code, h + size * 16, relocs))
            h += size * 16
        return out

    def test_overlay_patch_sites_avoid_relocations(self):
        # The overlay manager rewrites relocated words when it loads an
        # overlay, like the DOS loader does for the MZ image.
        ovs = self.overlays()
        self.assertEqual(len(ovs), 31)
        for p in REAL_MANIFEST["patches"]:
            off = int(p["offset"], 16)
            end = off + len(bytes.fromhex(p["original"]))
            if off < ovs[0][0]:
                continue                       # resident: see the MZ test above
            ov = [o for o in ovs if o[0] <= off < o[2]]
            self.assertEqual(len(ov), 1, p["id"])
            _, code, ov_end, relocs = ov[0]
            self.assertTrue(code <= off and end <= ov_end, p["id"])
            self.assertEqual([w for w in relocs if w < end and w + 2 > off], [], p["id"])

    def test_boycott_and_topup_match_qemu_test_exe(self):
        # boycott + topup is the EXE measured in QEMU on 2026-09-24 (run X).
        d = bytearray(self.data)
        d[OLD_BOYCOTT[0]:OLD_BOYCOTT[0] + 2] = OLD_BOYCOTT[1]
        for pid in ("traderoute-topup",):
            p = by_id(REAL_MANIFEST, pid)
            off = int(p["offset"], 16)
            new = bytes.fromhex(p["patched"])
            d[off:off + len(new)] = new
        self.assertEqual(md5(bytes(d)), "f827c0bdd89a27de811941315b027c14")


for _impl in IMPLEMENTATIONS:
    globals()[f"Test_{_impl.name}"] = type(f"Test_{_impl.name}",
                                           (PatcherTests, unittest.TestCase), {"impl": _impl})

if __name__ == "__main__":
    unittest.main()
