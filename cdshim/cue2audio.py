#!/usr/bin/env python3
"""Split a one-file BIN/CUE disc into a data track and one audio file per track.

    python3 cdshim/cue2audio.py MPS/CD/Colonize.cue MPS/CD/mp3 [--format mp3|flac] [--jingles DIR]

Writes OUT/Colonize.cue, OUT/Colonize-01.bin (the data track, as is) and
OUT/Colonize-NN.mp3 (320 kbit/s, the MP3 maximum) or .flac. DOSBox-Daum (SVN-Daum 2013)
reads MP3 tracks through SDL_sound, so the game still sees the same CD through
MSCDEX: same tracks, same pregaps. FLAC tracks mount but play silence in that
Daum build (tested 2026-10-24); the option is kept for other DOSBox builds.
Each audio file holds the track from INDEX 01 to the next track's INDEX 00;
the gap before it becomes a PREGAP line, so the disc's layout is unchanged.
--jingles DIR: the Windows game folder. Its GOLDCITY, MEETKING and 1812.WAV
(the jingles of music commands 3C, 3E, 3F) become three more audio tracks after
the disc's last one, each with a 2-second PREGAP: 28, 29, 30 for the
Colonization disc, the numbers the CD shim plays for those commands.
Made from the user's own disc; the output never goes into git.
Needs ffmpeg. Exit codes: 0 ok, 1 jingle file missing, 2 usage, 3 unsupported cue.
"""
import re
import subprocess
import sys
from pathlib import Path

SECTOR = 2352
JINGLES = ("GOLDCITY.WAV", "MEETKING.WAV", "1812.WAV")   # music commands 3C, 3E, 3F
JINGLE_PREGAP = 150                                       # 2 seconds, in frames
CODECS = {"mp3": (["-c:a", "libmp3lame", "-b:a", "320k"], "MP3"), "flac": (["-c:a", "flac"], "WAVE")}


def msf(s):
    m, sec, f = (int(x) for x in s.split(":"))
    return (m * 60 + sec) * 75 + f


def fmt(frames):
    return "%02d:%02d:%02d" % (frames // 4500, frames // 75 % 60, frames % 75)


def parse(cue):
    """[{'n', 'mode', 'pregap', 'idx': {0: frame, 1: frame}}], bin file name."""
    tracks, binfile = [], None
    for line in cue.read_text().splitlines():
        w = line.split()
        if not w:
            continue
        if w[0] == "FILE":
            if binfile:
                raise ValueError("more than one FILE: already split")
            binfile = re.match(r'\s*FILE\s+"(.+)"\s+BINARY', line).group(1)
        elif w[0] == "TRACK":
            tracks.append({"n": int(w[1]), "mode": w[2], "pregap": 0, "idx": {}})
        elif w[0] == "PREGAP":
            tracks[-1]["pregap"] = msf(w[1])
        elif w[0] == "INDEX":
            tracks[-1]["idx"][int(w[1])] = msf(w[2])
    return tracks, binfile


def find_jingles(folder):
    """The jingle WAVs in folder, whatever their case: [Path] or the missing name."""
    have = {p.name.upper(): p for p in folder.iterdir()} if folder.is_dir() else {}
    for name in JINGLES:
        if name not in have:
            return name
    return [have[n] for n in JINGLES]


def option(argv, flag):
    """Remove 'flag VALUE' from argv; return VALUE, '' if it has none, None if absent."""
    if flag not in argv:
        return None
    i = argv.index(flag)
    value = argv[i + 1] if i + 1 < len(argv) else ""
    del argv[i:i + 2]
    return value


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    fmt_ = option(argv, "--format")
    fmt_ = "mp3" if fmt_ is None else fmt_
    jdir = option(argv, "--jingles")
    if len(argv) != 2 or fmt_ not in CODECS or jdir == "":
        print(__doc__, file=sys.stderr)
        return 2
    cue, out = Path(argv[0]), Path(argv[1])
    try:
        tracks, binname = parse(cue)
    except (ValueError, AttributeError) as e:
        print(f"unsupported cue: {e}", file=sys.stderr)
        return 3
    if tracks[0]["mode"] != "MODE1/2352" or any(t["mode"] != "AUDIO" for t in tracks[1:]):
        print("unsupported cue: expected one MODE1/2352 data track, then audio", file=sys.stderr)
        return 3
    jingles = []
    if jdir is not None:
        jingles = find_jingles(Path(jdir))
        if isinstance(jingles, str):
            print(f"--jingles: no {jingles} in {jdir}", file=sys.stderr)
            return 1
    binpath = cue.parent / binname
    total = binpath.stat().st_size // SECTOR
    out.mkdir(parents=True, exist_ok=True)
    stem = cue.stem
    lines = []
    with open(binpath, "rb") as b:
        for k, t in enumerate(tracks):
            start = t["idx"].get(0, t["idx"][1])                  # this track's data in the bin
            end = tracks[k + 1]["idx"].get(0, tracks[k + 1]["idx"][1]) if k + 1 < len(tracks) else total
            if k == 0:
                name = f"{stem}-{t['n']:02d}.bin"
                b.seek(start * SECTOR)
                (out / name).write_bytes(b.read((end - start) * SECTOR))
                lines += [f'FILE "{name}" BINARY', f"  TRACK {t['n']:02d} {t['mode']}", "    INDEX 01 00:00:00"]
                continue
            audio_start = t["idx"][1]                              # the gap before INDEX 01 -> PREGAP
            pregap = t["pregap"] + (audio_start - t["idx"][0] if 0 in t["idx"] else 0)
            name = f"{stem}-{t['n']:02d}.{fmt_}"
            b.seek(audio_start * SECTOR)
            raw = b.read((end - audio_start) * SECTOR)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le",
                            "-ar", "44100", "-ac", "2", "-i", "-", *CODECS[fmt_][0],
                            str(out / name)], input=raw, check=True)
            lines += [f'FILE "{name}" {CODECS[fmt_][1]}', f"  TRACK {t['n']:02d} AUDIO"]
            if pregap:
                lines.append(f"    PREGAP {fmt(pregap)}")
            lines.append("    INDEX 01 00:00:00")
            print(f"track {t['n']:2d}: {fmt(end - audio_start)}, pregap {fmt(pregap)} -> {name}")
    for n, wav in enumerate(jingles, tracks[-1]["n"] + 1):
        name = f"{stem}-{n:02d}.{fmt_}"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav),
                        "-ar", "44100", "-ac", "2", *CODECS[fmt_][0], str(out / name)], check=True)
        lines += [f'FILE "{name}" {CODECS[fmt_][1]}', f"  TRACK {n:02d} AUDIO",
                  f"    PREGAP {fmt(JINGLE_PREGAP)}", "    INDEX 01 00:00:00"]
        print(f"track {n:2d}: jingle {wav.name}, pregap {fmt(JINGLE_PREGAP)} -> {name}")
    (out / cue.name).write_text("\r\n".join(lines) + "\r\n")
    print(f"{out / cue.name}: {len(tracks) + len(jingles)} tracks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
