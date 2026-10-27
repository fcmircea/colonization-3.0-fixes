"""Tests for the installers, install/install.py and install/Install.ps1
(plan .claude/plans/2026-10-27-release-v1.md, steps 2 and 3).

Each test installs into a temporary play folder from a temporary copy of the
game, non-interactively (--yes), and checks the result by md5. The player's
folder must come out byte for byte as it went in.

* Needs COL1_GAME_DIR (the English game) and COL1_PRISTINE_EXE (the original EXE).
* The Romanian test builds the text and fonts with ro/tools/build.py, as the
  release build does.
* The CD tests build the shim: GNU as and ld (binutils).
* Every test runs against Install.ps1 too when pwsh is found (or COL1_PWSH).
"""
import contextlib
import hashlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RO_SOURCES = (ROOT / "ro" / "work" / "ro-src").is_dir()            # private
PRIVATE = "needs the private Romanian sources"
_spec = importlib.util.spec_from_file_location("installer", ROOT / "install" / "install.py")
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)

GAME = os.environ.get("COL1_GAME_DIR")
PRISTINE = os.environ.get("COL1_PRISTINE_EXE")
HAVE_KIT = bool(GAME and PRISTINE) and (Path(GAME) / "VICEROY.EXE").is_file() and Path(PRISTINE).is_file()
HAVE_BINUTILS = bool(shutil.which("as") and shutil.which("ld"))
PWSH = os.environ.get("COL1_PWSH") or shutil.which("pwsh")

FIXED_MD5 = "40c5827211da71be83722c3676ab8ec4"      # pristine + --all (cap recommended, 2026-10-27)
ROMANIAN_MD5 = "f470861f59eece13f03ed7dfd2cba312"   # that + ro/patches-ro.json --all


def md5(p: Path) -> str:
    return hashlib.md5(p.read_bytes()).hexdigest()


def tree(d: Path) -> dict:
    return {str(p.relative_to(d)): md5(p) for p in sorted(d.rglob("*")) if p.is_file()}


def run_py(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = installer.main(argv + ["--yes", "--no-setup"])
    return code, out.getvalue()


def ps_name(flag):                     # --ro-files -> -RoFiles
    return "-" + "".join(w.capitalize() for w in flag[2:].split("-"))


def run_ps(argv, shim):
    args = [ps_name(a) if a.startswith("--") else a for a in argv] + ["-Yes", "-NoSetup"]
    if "-Cd" in args and shim:
        args += ["-ShimFile", str(shim)]
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(ROOT / "install" / "Install.ps1"), *args],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout + r.stderr


class Base:
    """Shared by both installers; impl picks which one runs."""
    impl = "py"
    shim = None

    @classmethod
    def setUpClass(cls):
        if cls.impl == "ps" and HAVE_BINUTILS:     # Install.ps1 takes a built shim
            cls.shim_dir = Path(tempfile.mkdtemp(prefix="col1-shim-"))
            cls.shim = cls.shim_dir / "PSOUND.COL"
            subprocess.run([sys.executable, str(ROOT / "cdshim/build.py"), "-o", str(cls.shim),
                            "--fm", "PSOUND.FM"], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        if cls.shim:
            shutil.rmtree(cls.shim_dir)

    def run_installer(self, *argv):
        argv = [str(a) for a in argv]
        return run_ps(argv, self.shim) if self.impl == "ps" else run_py(argv)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="col1-install-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        # the player's folder: the English game with the original EXE
        self.game = self.tmp / "GAMES" / "COLONIZE"
        shutil.copytree(GAME, self.game)
        shutil.copy2(PRISTINE, self.game / "VICEROY.EXE")
        self.before = tree(self.game)
        self.dest = self.tmp / "Colonization"

    def tearDown(self):
        self.assertEqual(tree(self.game), self.before, "the player's game folder changed")

    def settings(self):
        text = (self.dest / "settings-linux.txt").read_text()
        return dict(l.split("=", 1) for l in text.splitlines() if "=" in l)


@unittest.skipUnless(HAVE_KIT, "set COL1_GAME_DIR and COL1_PRISTINE_EXE")
class Install(Base, unittest.TestCase):
    def test_fixes_only(self):
        code, out = self.run_installer("--game", self.game, "--dest", self.dest, "--no-cd", "--no-ro")
        self.assertEqual(code, 0, out)
        en = self.dest / "MPS" / "COLONIZE"
        self.assertEqual(md5(en / "VICEROY.EXE"), FIXED_MD5)
        self.assertFalse((self.dest / "MPS" / "ROMANA").exists())
        for name in installer.LAUNCHERS:
            self.assertTrue((self.dest / name).is_file(), name)
        self.assertEqual(self.settings(), {"language": "COLONIZE", "cdmusic": "off"})
        win = (self.dest / "settings-windows.txt").read_bytes()
        self.assertIn(b"\r\n", win)

    def test_default_dest_is_next_to_the_game(self):
        code, out = self.run_installer("--game", self.game, "--no-cd", "--no-ro")
        self.assertEqual(code, 0, out)
        self.assertEqual(md5(self.game.parent / "Colonization" / "MPS" / "COLONIZE" / "VICEROY.EXE"),
                         FIXED_MD5)

    def test_patched_game_with_backup(self):
        # a player who ran the patcher before: patched EXE and the patcher's backup
        shutil.copy2(self.game / "VICEROY.EXE", self.game / "VICEROY.EXE.0f5d5b00.bak")
        shutil.copy2(Path(GAME) / "VICEROY.EXE", self.game / "VICEROY.EXE")
        self.before = tree(self.game)
        code, out = self.run_installer("--game", self.game, "--dest", self.dest, "--no-cd", "--no-ro")
        self.assertEqual(code, 0, out)
        en = self.dest / "MPS" / "COLONIZE"
        self.assertEqual(md5(en / "VICEROY.EXE"), FIXED_MD5)
        self.assertEqual(sorted(p.name for p in en.glob("*.bak")), ["VICEROY.EXE.0f5d5b00.bak"])

    def test_patched_game_without_backup_is_reverted(self):
        shutil.copy2(Path(GAME) / "VICEROY.EXE", self.game / "VICEROY.EXE")
        self.before = tree(self.game)
        code, out = self.run_installer("--game", self.game, "--dest", self.dest, "--no-cd", "--no-ro")
        self.assertEqual(code, 0, out)
        self.assertEqual(md5(self.dest / "MPS" / "COLONIZE" / "VICEROY.EXE"), FIXED_MD5)

    def test_unknown_exe_refused(self):
        exe = self.game / "VICEROY.EXE"
        data = bytearray(exe.read_bytes())
        data[0x100] ^= 0xFF                      # header area, no patch site
        exe.write_bytes(bytes(data))
        self.before = tree(self.game)
        code, out = self.run_installer("--game", self.game, "--dest", self.dest, "--no-cd", "--no-ro")
        self.assertEqual(code, installer.EXIT_REFUSED, out)
        self.assertFalse((self.dest / "MPS" / "COLONIZE").exists())

    def test_no_game(self):
        code, _ = self.run_installer("--game", self.tmp / "nothing", "--dest", self.dest)
        self.assertEqual(code, installer.EXIT_NOFILE)

    def test_dest_inside_game_refused(self):
        code, _ = self.run_installer("--game", self.game, "--dest", self.game / "x", "--no-cd", "--no-ro")
        self.assertEqual(code, installer.EXIT_USAGE)

    def test_experimental_patch_by_name(self):
        code, out = self.run_installer("--game", self.game, "--dest", self.dest, "--no-cd", "--no-ro",
                        "--patch", "no-such-patch")
        self.assertEqual(code, installer.EXIT_REFUSED, out)


@unittest.skipUnless(HAVE_KIT, "set COL1_GAME_DIR and COL1_PRISTINE_EXE")
@unittest.skipUnless(RO_SOURCES, PRIVATE)
class InstallRomanian(Base, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # the text and fonts as the release build makes them
        cls.work = Path(tempfile.mkdtemp(prefix="col1-ro-"))
        out = cls.work / "OUT"
        shutil.copytree(GAME, out)
        r = subprocess.run([sys.executable, str(ROOT / "ro/tools/build.py"), GAME,
                            str(ROOT / "ro/work/ro-src"), str(ROOT / "ro/work/fonts"), str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        cls.ro_files = cls.work / "files"
        cls.ro_files.mkdir()
        en = tree(Path(GAME))
        for name, digest in tree(out).items():
            if en.get(name) != digest and name != "VICEROY.EXE":
                shutil.copy2(out / name, cls.ro_files / name)
        cls.out = out

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work)
        super().tearDownClass()

    def test_romanian(self):
        self.assertTrue(any(self.ro_files.iterdir()))
        code, out = self.run_installer("--game", self.game, "--dest", self.dest, "--no-cd", "--ro",
                        "--ro-files", self.ro_files)
        self.assertEqual(code, 0, out)
        ro = self.dest / "MPS" / "ROMANA"
        self.assertEqual(md5(ro / "VICEROY.EXE"), ROMANIAN_MD5)
        for f in self.ro_files.iterdir():
            self.assertEqual(md5(ro / f.name), md5(f), f.name)
        self.assertEqual(md5(self.dest / "MPS" / "COLONIZE" / "VICEROY.EXE"), FIXED_MD5)
        self.assertEqual(self.settings()["language"], "ROMANA")

    def test_rerun_adds_romanian_and_keeps_the_rest(self):
        code, out = self.run_installer("--game", self.game, "--dest", self.dest, "--no-cd", "--no-ro")
        self.assertEqual(code, 0, out)
        en = self.dest / "MPS" / "COLONIZE"
        (en / "COLONY00.SAV").write_bytes(b"a save made in play")
        first = tree(en)
        code, out = self.run_installer("--game", self.game, "--dest", self.dest, "--no-cd", "--ro",
                        "--ro-files", self.ro_files)
        self.assertEqual(code, 0, out)
        self.assertIn("already there", out)
        self.assertEqual(tree(en), first)
        self.assertEqual(md5(self.dest / "MPS" / "ROMANA" / "VICEROY.EXE"), ROMANIAN_MD5)


@unittest.skipUnless(HAVE_KIT and HAVE_BINUTILS, "needs the kit and binutils")
class InstallCd(Base, unittest.TestCase):

    def make_cue(self):
        d = self.tmp / "CD"
        d.mkdir()
        (d / "Disc.bin").write_bytes(b"\0" * 2352 * 10)
        cue = d / "Disc.cue"
        cue.write_text('FILE "Disc.bin" BINARY\n  TRACK 01 MODE1/2352\n    INDEX 01 00:00:00\n')
        return cue

    def test_cd(self):
        cue = self.make_cue()
        code, out = self.run_installer("--game", self.game, "--dest", self.dest, "--cd", cue, "--no-mp3", "--no-ro")
        self.assertEqual(code, 0, out)
        en = self.dest / "MPS" / "COLONIZE"
        self.assertEqual(md5(en / "PSOUND.FM"), installer.PSOUND_MD5)
        self.assertTrue(installer.is_shim(en / "PSOUND.COL"))
        cd = self.dest / "MPS" / "CD"
        self.assertEqual((cd / "Colonize.cue").read_text(), cue.read_text())
        self.assertTrue((cd / "Disc.bin").is_file())
        self.assertEqual(self.settings()["cdmusic"], "image")
        # again: nothing to do, nothing refused
        code, out = self.run_installer("--game", self.game, "--dest", self.dest, "--cd", cue, "--no-mp3", "--no-ro")
        self.assertEqual(code, 0, out)
        self.assertIn("CD shim up to date", out)

    def test_unknown_psound_refused(self):
        (self.game / "PSOUND.COL").write_bytes(b"someone else's driver")
        self.before = tree(self.game)
        code, out = self.run_installer("--game", self.game, "--dest", self.dest, "--cd", self.make_cue(),
                        "--no-mp3", "--no-ro")
        self.assertEqual(code, installer.EXIT_REFUSED, out)

    def test_cue_with_missing_file_refused(self):
        cue = self.make_cue()
        (cue.parent / "Disc.bin").unlink()
        code, out = self.run_installer("--game", self.game, "--dest", self.dest, "--cd", cue, "--no-mp3", "--no-ro")
        self.assertEqual(code, installer.EXIT_REFUSED, out)


@unittest.skipUnless(HAVE_KIT and PWSH, "needs the kit and pwsh")
class InstallPs(Install):
    impl = "ps"


@unittest.skipUnless(HAVE_KIT and PWSH, "needs the kit and pwsh")
class InstallRomanianPs(InstallRomanian):
    impl = "ps"


@unittest.skipUnless(HAVE_KIT and HAVE_BINUTILS and PWSH, "needs the kit, binutils and pwsh")
class InstallCdPs(InstallCd):
    impl = "ps"


if __name__ == "__main__":
    unittest.main()
