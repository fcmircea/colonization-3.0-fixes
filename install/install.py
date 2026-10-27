#!/usr/bin/env python3
"""Install the Colonization 3.0 fixes into a new play folder.

    python3 install/install.py                      # asks the questions
    python3 install/install.py --game DIR --yes     # fixes only, no questions
    python3 install/install.py --game DIR --dest DIR --cd CUE --mp3 --ro --yes

Your game folder is only read. The installer makes a play folder (default: a
folder "Colonization" next to your game folder) laid out as the launchers
expect:

    MPS/COLONIZE   your game, English, with every recommended fix
    MPS/ROMANA     the same plus the Romanian translation (optional)
    MPS/CD         your CD image, for CD music (optional)
    MPS/CD/mp3     the CD as MP3 files, with the jingles if a Windows copy
                   of the game is given (optional, needs ffmpeg)
    play.sh, setup.sh, Play.cmd, Setup.cmd, ...   the launchers

Run it again to add what you skipped: folders already there are left as they
are. Exit codes: 0 ok, 1 file not found, 2 usage, 3 refused (unknown game
files or an unexpected byte), 4 a step failed.
"""
import argparse
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PRISTINE_MD5 = "0f5d5b0063721fbc6aca314e5a43ddaf"
PSOUND_MD5 = "c294733957d12c68fdbbad2b73442f34"      # the original PSOUND.COL
SHIM_TAG = b"CD shim"                                   # at file offset 40h (body 10h)
LAUNCHERS = ["play.sh", "Romana.sh", "English.sh", "setup.sh",
             "Play.ps1", "Play.cmd", "Romana.cmd", "English.cmd", "Setup.ps1", "Setup.cmd"]
EXIT_OK, EXIT_NOFILE, EXIT_USAGE, EXIT_REFUSED, EXIT_FAILED = 0, 1, 2, 3, 4

# The installer runs from the release zip or from the repo; the parts sit in
# slightly different places in each.
ROOT = Path(__file__).resolve().parent.parent


def part(*candidates):
    for c in candidates:
        p = ROOT / c
        if p.exists():
            return p
    return None


class Refused(Exception):
    code = EXIT_REFUSED


class Failed(Exception):
    code = EXIT_FAILED


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def say(msg=""):
    print(msg, flush=True)


# --- questions (only asked when the answer was not given on the command line)

def ask_yes(question, default, args):
    if args.yes:
        return default
    hint = "Y/n" if default else "y/N"
    try:
        ans = input(f"{question} [{hint}]: ").strip().lower()
    except EOFError:
        return default
    return default if not ans else ans[0] in "yd"


def ask_path(question, default, args):
    if args.yes:
        return default
    shown = f" [{default}]" if default else ""
    try:
        ans = input(f"{question}{shown}: ").strip().strip('"')
    except EOFError:
        return default
    return Path(ans).expanduser() if ans else default


# --- the patcher, loaded from its file so both layouts work

def patcher():
    spec = importlib.util.spec_from_file_location("apply_patch", part("patch/apply_patch.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_patcher(argv):
    code = patcher().main([str(a) for a in argv])
    if code != 0:
        raise Refused(f"the patcher stopped with exit code {code}; nothing more was done")


# --- steps

def pristine_exe(game: Path) -> Path:
    """The original VICEROY.EXE in the player's folder: the EXE itself, or a
    backup the patcher (or the player) kept."""
    names = ["VICEROY.EXE", f"VICEROY.EXE.{PRISTINE_MD5[:8]}.bak", "VICEROY.old.exe"]
    names += sorted(p.name for p in game.glob("VICEROY.EXE.*.bak"))
    for n in dict.fromkeys(names):
        p = game / n
        if p.is_file() and md5(p) == PRISTINE_MD5:
            return p
    return None


def copy_game(game: Path, dst: Path):
    """Copy the player's game, minus EXE backups, then put the original EXE in."""
    ignore = shutil.ignore_patterns("*.bak", "*.BAK", "VICEROY.old.exe", "PSOUND.FM")
    shutil.copytree(game, dst, ignore=ignore)
    exe = dst / "VICEROY.EXE"
    src = pristine_exe(game)
    if src is not None:
        shutil.copy2(src, exe)
        return
    # No original anywhere: try to undo the fixes this patcher knows.
    say("  no original VICEROY.EXE found; reverting known fixes on the copy")
    try:
        run_patcher([exe, "--all", "--revert"])
    except Refused:
        pass
    for b in dst.glob("VICEROY.EXE.*.bak"):
        b.unlink()
    if md5(exe) != PRISTINE_MD5:
        shutil.rmtree(dst)
        raise Refused(f"{game / 'VICEROY.EXE'} is not Colonization 3.0 as this patcher knows it, "
                      f"and no original copy is next to it. Put the original VICEROY.EXE "
                      f"(md5 {PRISTINE_MD5}) in that folder and run this again.")


def install_fixes(game: Path, dst: Path, extra):
    if dst.exists():
        say(f"{dst}: already there, left as it is")
        return False
    say(f"{dst}: copying your game")
    copy_game(game, dst)
    say("Fixes:")
    run_patcher([dst / "VICEROY.EXE", "--all"])
    if extra:
        run_patcher([dst / "VICEROY.EXE", *extra])
    return True


def install_romanian(english: Path, dst: Path, ro_files: Path):
    if dst.exists():
        say(f"{dst}: already there, left as it is")
        return
    manifest = part("ro/patches-ro.json")
    if ro_files is None or not ro_files.is_dir() or manifest is None:
        raise Failed("the Romanian files are missing from this package")
    say(f"{dst}: Romanian")
    shutil.copytree(english, dst, ignore=shutil.ignore_patterns("*.bak", "*.BAK"))
    for f in sorted(ro_files.iterdir()):
        if f.is_file():
            shutil.copy2(f, dst / f.name)
    run_patcher([dst / "VICEROY.EXE", "--all", "--manifest", manifest])


def shim_file(tmp: Path) -> Path:
    built = part("cdshim/PSOUND.COL")
    if built is not None:
        return built
    build = part("cdshim/build.py")                     # in the repo: build it
    if build is None:
        raise Failed("the CD shim is missing from this package")
    out = tmp / "PSOUND.COL"
    r = subprocess.run([sys.executable, str(build), "-o", str(out), "--fm", "PSOUND.FM"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise Failed(f"building the CD shim failed:\n{r.stdout}{r.stderr}")
    return out


def is_shim(path: Path) -> bool:
    return path.read_bytes()[0x40:0x40 + len(SHIM_TAG)] == SHIM_TAG


def install_shim(game: Path, shim: Path):
    """The rules of harness/deploy-play.sh: the original PSOUND.COL becomes
    PSOUND.FM, the shim becomes PSOUND.COL; anything unknown is refused."""
    col, fm = game / "PSOUND.COL", game / "PSOUND.FM"
    if not col.is_file():
        say(f"{game}: no PSOUND.COL, CD music skipped")
        return
    if fm.is_file() and md5(fm) != PSOUND_MD5:
        raise Refused(f"{fm} is not the original PSOUND.COL")
    if md5(col) == PSOUND_MD5:
        if not fm.is_file():
            shutil.copy2(col, fm)
    elif not is_shim(col):
        raise Refused(f"{col} is neither the original nor the CD shim")
    elif not fm.is_file():
        raise Refused(f"{col} is the shim but PSOUND.FM is missing")
    if col.read_bytes() == shim.read_bytes():
        say(f"{game}: CD shim up to date")
    else:
        shutil.copyfile(shim, col)
        say(f"{game}: CD shim installed")


def cue_files(cue: Path):
    names = re.findall(r'^\s*FILE\s+"([^"]+)"', cue.read_text(errors="replace"), re.M | re.I)
    if not names:
        raise Refused(f"{cue}: no FILE line, not a cue sheet")
    files = [cue.parent / n for n in names]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise Refused(f"{cue}: missing {', '.join(missing)}")
    return files


def install_cd(cue: Path, cd: Path):
    """Copy the cue sheet as MPS/CD/Colonize.cue and the files it names."""
    if (cd / "Colonize.cue").exists():
        say(f"{cd}: already there, left as it is")
        return
    files = cue_files(cue)
    cd.mkdir(parents=True)
    for f in files:
        say(f"{cd}: copying {f.name}")
        shutil.copy2(f, cd / f.name)
    shutil.copy2(cue, cd / "Colonize.cue")


def install_mp3(cd: Path, windows: Path):
    out = cd / "mp3"
    if (out / "Colonize.cue").exists():
        say(f"{out}: already there, left as it is")
        return
    tool = part("cdshim/cue2audio.py")
    cmd = [sys.executable, str(tool), str(cd / "Colonize.cue"), str(out)]
    if windows is not None:
        if "--jingles" in tool.read_text():
            cmd += ["--jingles", str(windows)]
        else:
            say("  this package cannot add the jingles yet; MP3 set without them")
    say(f"{out}: making the MP3 set (a few minutes)")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise Failed(f"making the MP3 set failed (exit {r.returncode})")


def install_launchers(dest: Path):
    src = part("launchers", "harness")
    for name in LAUNCHERS:
        f = src / name
        if f.is_file():
            shutil.copy2(f, dest / name)
            if name.endswith(".sh"):
                os.chmod(dest / name, 0o755)


def write_settings(dest: Path, values: dict):
    """Set these keys in both settings files, keeping every other line, so the
    launchers start what was just installed; setup.sh / Setup.cmd change them."""
    for name in ("settings-linux.txt", "settings-windows.txt"):
        p = dest / name
        lines = p.read_text().splitlines() if p.exists() else []
        lines = [l for l in lines if l.split("=", 1)[0] not in values]
        lines += [f"{k}={v}" for k, v in values.items()]
        p.write_text("\n".join(lines) + "\n", newline="\r\n" if name.endswith("windows.txt") else "\n")


# --- main

def find_cue(game: Path):
    for d in (game, game.parent, game.parent / "CD"):
        if d.is_dir():
            cues = sorted(d.glob("*.cue")) + sorted(d.glob("*.CUE"))
            if cues:
                return cues[0]
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", type=Path, help="your COLONIZE folder (it holds VICEROY.EXE)")
    ap.add_argument("--dest", type=Path, help="the play folder to make (default: Colonization next to the game folder)")
    ap.add_argument("--cd", type=Path, help="your CD image's .cue file: CD music")
    ap.add_argument("--no-cd", action="store_true", help="no CD music")
    ap.add_argument("--mp3", action=argparse.BooleanOptionalAction, default=None,
                    help="also make the MP3 set (needs ffmpeg)")
    ap.add_argument("--windows", type=Path, help="your Windows Colonization folder: jingles in the MP3 set")
    ap.add_argument("--ro", action=argparse.BooleanOptionalAction, default=None,
                    help="install the Romanian translation")
    ap.add_argument("--ro-files", type=Path, help=argparse.SUPPRESS)   # tests: the built text and fonts
    ap.add_argument("--patch", action="append", default=[], metavar="ID",
                    help="also apply this patch by id (e.g. an experimental one)")
    ap.add_argument("--setup", action=argparse.BooleanOptionalAction, default=None,
                    help="run setup.sh at the end (Linux)")
    ap.add_argument("--yes", action="store_true", help="ask nothing; what was not given is not installed")
    args = ap.parse_args(argv)

    say("Colonization 3.0 fixes: installer")
    say("Your game folder is only read; everything goes into a new play folder.")
    say()
    game = args.game or ask_path("Your COLONIZE folder (it holds VICEROY.EXE)", None, args)
    if game is None or not (game / "VICEROY.EXE").is_file():
        print(f"error: no VICEROY.EXE in {game}", file=sys.stderr)
        return EXIT_NOFILE if game else EXIT_USAGE
    game = game.resolve()
    dest = (args.dest or ask_path("New play folder", game.parent / "Colonization", args)).resolve()
    if dest == game or game in dest.parents:
        print("error: the play folder must not be the game folder or inside it", file=sys.stderr)
        return EXIT_USAGE

    cue = None
    if not args.no_cd:
        cue = args.cd
        if cue is None and not args.yes:
            found = find_cue(game)
            if ask_yes("CD music from your Colonization CD image?", found is not None, args):
                cue = ask_path("Its .cue file", found, args)
    if cue is not None and not cue.is_file():
        print(f"error: no such file: {cue}", file=sys.stderr)
        return EXIT_NOFILE
    mp3 = args.mp3
    if cue is not None and mp3 is None:
        mp3 = shutil.which("ffmpeg") is not None and ask_yes(
            "Also make an MP3 copy of the CD (smoother on slow disks, allows the jingles)?", False, args)
    if mp3 and shutil.which("ffmpeg") is None:
        print("error: the MP3 set needs ffmpeg, which is not installed", file=sys.stderr)
        return EXIT_NOFILE
    windows = args.windows
    if mp3 and windows is None and not args.yes:
        windows = ask_path("Your Windows Colonization folder, for the jingles (Enter: none)", None, args)
    ro = args.ro
    if ro is None:
        ro = ask_yes("Install the Romanian translation too?", False, args)

    mps = dest / "MPS"
    english, romana = mps / "COLONIZE", mps / "ROMANA"
    settings = {}
    try:
        mps.mkdir(parents=True, exist_ok=True)
        if install_fixes(game, english, args.patch):
            settings["language"] = "COLONIZE"
        if ro and not romana.exists():
            install_romanian(english, romana, args.ro_files or part("ro/files"))
            settings["language"] = "ROMANA"
        if cue is not None:
            settings["cdmusic"] = "mp3" if mp3 else "image"
            with tempfile.TemporaryDirectory() as tmp:
                shim = shim_file(Path(tmp))
                for g in (english, romana):
                    if g.is_dir():
                        install_shim(g, shim)
            install_cd(cue, mps / "CD")
            if mp3:
                install_mp3(mps / "CD", windows)
        elif not (dest / "settings-linux.txt").exists():
            settings["cdmusic"] = "off"
        install_launchers(dest)
        if settings:
            write_settings(dest, settings)
    except (Refused, Failed) as e:
        print(f"error: {e}", file=sys.stderr)
        return e.code

    say()
    say(f"Done. Play folder: {dest}")
    run_setup = args.setup
    if run_setup is None:
        run_setup = os.name != "nt" and ask_yes("Choose the emulator and screen now (setup.sh)?", True, args)
    if run_setup and (dest / "setup.sh").is_file():
        subprocess.run(["bash", str(dest / "setup.sh")])
    else:
        say(f"Start the game: {dest / ('Play.cmd' if os.name == 'nt' else 'play.sh')}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
