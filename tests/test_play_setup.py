"""The play folder's setup and launchers (harness/setup.sh, play.sh; Setup.ps1,
Play.ps1 when pwsh is found), run in a scratch play folder with stub emulators
that only record how they were called. Plan .claude/plans/2026-10-25-play-setup.md."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "harness"
STUB = '#!/bin/sh\nprintf "%s\\n" "$0" "$@" > "$STUBLOG"\n'


def autoexec(conf):
    text = Path(conf).read_text()
    return text[text.index("[autoexec]"):].splitlines()[1:]


class PlayFolder:
    """A play folder with empty game files, both cue sheets and stub tools."""

    def __init__(self, base):
        self.dir = Path(base) / "play"
        mps = self.dir / "MPS"
        for g in ("ROMANA", "COLONIZE"):
            (mps / g).mkdir(parents=True)
            (mps / g / "VICEROY.EXE").write_bytes(b"")
        (mps / "CD" / "mp3").mkdir(parents=True)
        (mps / "CD" / "Colonize.cue").write_text("")
        (mps / "CD" / "mp3" / "Colonize.cue").write_text("")
        (self.dir / "daum").mkdir()
        (self.dir / "daum" / "dosbox.conf").write_text("[sdl]\nfullscreen=true\n[autoexec]\nold\n")
        (self.dir / "daum" / "dosbox.exe").write_bytes(b"")
        for f in ("play.sh", "setup.sh", "Romana.sh", "English.sh"):
            shutil.copy(HARNESS / f, self.dir / f)
        self.home = Path(base) / "home"
        self.bin = Path(base) / "bin"
        self.bin.mkdir()
        staging = self.home / "apps" / "dosbox-staging-test" / "dosbox"
        staging.parent.mkdir(parents=True)
        for p in (self.bin / "wine", self.bin / "dosbox-x", staging):
            p.write_text(STUB)
            p.chmod(0o755)
        wp = self.bin / "winepath"
        wp.write_text('#!/bin/sh\n[ "$1" = -w ] && shift\nprintf "Z:%s\\n" "$1" | tr / "\\\\\\\\"\n')
        wp.chmod(0o755)
        self.staging = staging
        self.log = Path(base) / "stub.log"
        self.settings = self.dir / "settings-linux.txt"
        self.env = dict(os.environ, HOME=str(self.home), STUBLOG=str(self.log),
                        PATH=f"{self.bin}:/usr/bin:/bin")

    def run(self, script, *args, answers=None):
        return subprocess.run(["bash", str(self.dir / script), *args], env=self.env, text=True,
                              input=answers, stdin=None if answers is not None else subprocess.DEVNULL,
                              capture_output=True, timeout=30)

    def called(self):
        return self.log.read_text().splitlines()

    def write_settings(self, **kv):
        self.settings.write_text("".join(f"{k}={v}\n" for k, v in kv.items()))


# The launchers are tested on Linux (bash, and pwsh with stub emulators); on
# Windows they are checked by hand. CI runs the suite on Windows too.
POSIX = os.name == "posix"


@unittest.skipUnless(POSIX, "Linux/macOS: stub emulators and bash")
class SetupSh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = PlayFolder(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_answers_saved(self):
        # emulator 2 = Staging (found in ~/apps), English, MP3, volume 60, window, save, no start
        r = self.p.run("setup.sh", answers="2\n2\n2\n60\n2\ny\nn\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = [l for l in self.p.settings.read_text().splitlines() if not l.startswith("#")]
        self.assertEqual(lines, [f"emulator=staging", f"emulator_path={self.p.staging}",
                                 "language=COLONIZE", "cdmusic=mp3", "music_volume=60", "window=1"])

    def test_enter_keeps_current_values(self):
        self.p.run("setup.sh", answers="3\n2\n3\n2\ny\nn\n")          # X, English, music off, window
        before = self.p.settings.read_text().splitlines()[1:]
        r = self.p.run("setup.sh", answers="\n\n\n\ny\nn\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.p.settings.read_text().splitlines()[1:], before)
        self.assertIn("emulator=x", before)
        self.assertIn("cdmusic=off", before)

    def test_bad_number_asked_again(self):
        r = self.p.run("setup.sh", answers="9\nx\n1\n1\n1\n150\n70\n1\ny\nn\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("Type a number from 1 to"), 2)
        self.assertIn("Type a number from 0 to 100", r.stdout)
        self.assertIn("music_volume=70", self.p.settings.read_text())

    def test_reset_and_stop_without_answers(self):
        self.p.write_settings(emulator="daum")
        r = self.p.run("setup.sh", "--reset", answers="")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Saved answers removed.", r.stdout)
        self.assertFalse(self.p.settings.exists())

    def test_not_saved(self):
        r = self.p.run("setup.sh", answers="1\n1\n1\n\n1\nn\n")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.p.settings.exists())

    def test_daum_offered_only_with_daum_folder(self):
        # a play folder made by the installer has no daum/: Staging becomes choice 1
        shutil.rmtree(self.p.dir / "daum")
        r = self.p.run("setup.sh", answers="1\n2\n2\n60\n2\ny\nn\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("DOSBox-Daum", r.stdout)
        self.assertIn("emulator=staging", self.p.settings.read_text())

    def test_download_offered_only_without_staging(self):
        r = self.p.run("setup.sh", answers="")
        self.assertNotIn("Download DOSBox Staging", r.stdout)
        shutil.rmtree(self.p.home / "apps")
        r = self.p.run("setup.sh", answers="")
        self.assertIn("Download DOSBox Staging", r.stdout)


@unittest.skipUnless(POSIX, "Linux/macOS: stub emulators and bash")
class PlaySh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = PlayFolder(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, *args):
        r = self.p.run("play.sh", *args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return self.p.called()

    def test_no_settings_no_terminal_is_old_default(self):
        call = self.start()
        self.assertTrue(call[0].endswith("/wine"))
        conf = self.p.dir / "daum" / "run-ROMANA-wine.conf"
        self.assertEqual(autoexec(conf)[1:], [
            "imgmount D \"Z:" + str(self.p.dir / "MPS/CD/Colonize.cue").replace("/", "\\") + "\" -t iso",
            "C:", "CD ROMANA", "CLS", "OPENING -g", "mount -u D", "exit"])
        self.assertIn("fullscreen=true", conf.read_text())
        self.assertFalse(self.p.settings.exists())

    def test_staging(self):
        self.p.write_settings(emulator="staging", emulator_path=self.p.staging, language="COLONIZE",
                              cdmusic="mp3", music_volume=60, window=1)
        call = self.start()
        self.assertEqual(call[:4], [str(self.p.staging), "--noprimaryconf", "--nolocalconf", "--conf"])
        conf = Path(call[4])
        self.assertEqual(conf.name, "run-COLONIZE-staging.conf")
        text = conf.read_text()
        for want in ("fullscreen = off", "aspect = off", "shader = scaler/advinterp3x", "prebuffer = 20"):
            self.assertIn(want, text)
        auto = autoexec(conf)
        self.assertEqual(auto[1], f'imgmount D "{self.p.dir}/MPS/CD/mp3/Colonize.cue" -t iso')
        self.assertEqual(auto[2], "MIXER CDAUDIO 60:60")
        self.assertIn("CD COLONIZE", auto)

    def test_dosbox_x_volume_scale_and_fullscreen(self):
        self.p.write_settings(emulator="x", language="ROMANA", cdmusic="image", music_volume=60, window=0)
        call = self.start()
        self.assertTrue(call[0].endswith("/dosbox-x"))
        text = Path(call[3]).read_text()
        for want in ("fullscreen = true", "aspect_ratio = 16:10", "quit warning = false", "prebuffer = 25"):
            self.assertIn(want, text)
        self.assertIn("SET CDVOL=15", autoexec(call[3]))          # X reads CDVOL as n/64

    def test_daum_volume_window_and_options_override(self):
        self.p.write_settings(emulator="daum", language="COLONIZE", cdmusic="image", music_volume=100, window=0)
        self.start("ROMANA", "--window", "--music-volume=40", "--mp3")
        conf = self.p.dir / "daum" / "run-ROMANA-wine.conf"
        auto = autoexec(conf)
        self.assertIn("SET CDVOL=40", auto)
        self.assertIn("mp3", auto[1])
        self.assertIn("fullscreen=false", conf.read_text())

    def test_music_off_mounts_no_cd(self):
        self.p.write_settings(emulator="staging", emulator_path=self.p.staging, cdmusic="off", music_volume=30)
        call = self.start()
        auto = autoexec(call[4])
        self.assertFalse(any("imgmount" in l or "mount -u" in l or "MIXER" in l for l in auto), auto)

    def test_crlf_settings_and_language_launchers(self):
        self.p.settings.write_bytes(b"emulator=x\r\nlanguage=COLONIZE\r\ncdmusic=image\r\n")
        call = self.start()
        self.assertIn("CD COLONIZE", autoexec(call[3]))
        r = self.p.run("Romana.sh")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CD ROMANA", autoexec(self.p.called()[3]))

    def test_bad_settings_refused(self):
        self.p.write_settings(emulator="qemu")
        r = self.p.run("play.sh")
        self.assertEqual(r.returncode, 2)
        self.assertIn("daum, staging or x", r.stderr)


PWSH = os.environ.get("COL1_PWSH") or shutil.which("pwsh")


@unittest.skipUnless(POSIX, "Linux/macOS: stub emulators")
@unittest.skipUnless(PWSH, "set COL1_PWSH to a pwsh (PowerShell 7) to test Setup.ps1 and Play.ps1")
class PowerShellTwins(unittest.TestCase):
    """Setup.ps1 and Play.ps1 under pwsh on Linux: the logic, not Windows paths."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = PlayFolder(self.tmp.name)
        for f in ("Setup.ps1", "Play.ps1"):
            shutil.copy(HARNESS / f, self.p.dir / f)
        self.daum = self.p.dir / "daum" / "dosbox.exe"
        self.staging = self.p.dir / "emulators" / "staging-windows" / "v1" / "dosbox.exe"
        self.staging.parent.mkdir(parents=True)
        xexe = self.p.bin / "dosbox-x.exe"
        for f in (self.daum, self.staging, xexe):
            f.write_text(STUB)
            f.chmod(0o755)
        self.settings = self.p.dir / "settings-windows.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def pwsh(self, script, *args, answers=None):
        return subprocess.run([PWSH, "-NoProfile", "-File", str(self.p.dir / script), *args],
                              env=self.p.env, text=True, input=answers,
                              stdin=None if answers is not None else subprocess.DEVNULL,
                              capture_output=True, timeout=60)

    def start(self, *args):
        self.p.log.unlink(missing_ok=True)
        r = self.pwsh("Play.ps1", *args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for _ in range(50):                        # Start-Process does not wait
            if self.p.log.exists() and self.p.log.read_text():
                return self.p.called()
            time.sleep(0.1)
        self.fail("emulator stub not called: " + r.stdout)

    def conf(self, call):
        return next(a.strip('"') for a in call if a.strip('"').endswith(".conf"))

    def test_setup_saves_and_reset(self):
        # emulator 2 = Staging in emulators/staging-windows, English, MP3, 60, window
        r = self.pwsh("Setup.ps1", answers="2\n2\n2\n60\n2\ny\nn\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = [l for l in self.settings.read_text().splitlines() if not l.startswith("#")]
        self.assertEqual(lines, ["emulator=staging", f"emulator_path={self.staging}", "language=COLONIZE",
                                 "cdmusic=mp3", "music_volume=60", "window=1"])
        r = self.pwsh("Setup.ps1", "-Reset", answers="")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Saved answers removed.", r.stdout)
        self.assertFalse(self.settings.exists())

    def test_setup_without_daum_folder(self):
        shutil.rmtree(self.p.dir / "daum")
        r = self.pwsh("Setup.ps1", answers="1\n2\n2\n60\n2\ny\nn\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("DOSBox-Daum", r.stdout)
        self.assertIn("emulator=staging", self.settings.read_text())

    def test_play_per_emulator(self):
        self.settings.write_text(f"emulator=staging\nemulator_path={self.staging}\nlanguage=COLONIZE\n"
                                 "cdmusic=mp3\nmusic_volume=60\nwindow=1\n")
        call = self.start()
        self.assertEqual(call[0], str(self.staging))
        text = Path(self.conf(call)).read_text()
        self.assertIn("fullscreen = off", text)
        auto = autoexec(self.conf(call))
        self.assertIn("MIXER CDAUDIO 60:60", auto)
        self.assertIn("CD COLONIZE", auto)
        self.settings.write_text("emulator=x\ncdmusic=image\nmusic_volume=60\n")
        call = self.start()
        self.assertTrue(call[0].endswith("dosbox-x.exe"))
        self.assertIn("SET CDVOL=15", autoexec(self.conf(call)))
        self.settings.write_text("emulator=daum\ncdmusic=off\nmusic_volume=60\n")
        call = self.start("COLONIZE", "-Window")
        self.assertEqual(call[0], str(self.daum))
        conf = self.conf(call)
        auto = autoexec(conf)
        self.assertFalse(any("imgmount" in l or "CDVOL" in l for l in auto), auto)
        self.assertIn("CD COLONIZE", auto)
        self.assertIn("fullscreen=false", Path(conf).read_text())

    def test_no_settings_without_console_is_old_default(self):
        call = self.start()
        self.assertEqual(call[0], str(self.daum))
        auto = autoexec(self.conf(call))
        self.assertIn("CD ROMANA", auto)
        self.assertTrue(any(l.startswith("imgmount D") for l in auto), auto)
        self.assertFalse(self.settings.exists())


if __name__ == "__main__":
    unittest.main()
