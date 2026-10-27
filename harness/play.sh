#!/usr/bin/env bash
# play.sh - start Colonization from this folder. Linux twin of Play.ps1;
# harness/deploy-play.sh copies both next to daum/ and MPS/, so Linux and
# Windows play the same games and saves.
# The emulator, default language, CD music and window come from
# settings-linux.txt, written by setup.sh. With no such file a start from a
# terminal runs setup.sh first; otherwise DOSBox-Daum, Romanian, CD image,
# fullscreen. Options override the settings for one start:
#   ./play.sh                       # the default language from the settings
#   ./play.sh COLONIZE              # English (ROMANA: Romanian)
#   ./play.sh ROMANA --window --keep-open
#   ./play.sh --mp3                 # CD music from the MP3 set instead of the CD image
#   ./play.sh --music-volume=60     # CD music at 60 % (effects and FM music unchanged)
# The CD drive D: is MPS/CD/Colonize.cue (the disc image), or MPS/CD/mp3/Colonize.cue
# (the same disc, audio tracks as MP3 files, made by col1/cdshim/cue2audio.py).
# The CD shim (PSOUND.COL, installed by deploy-play.sh) plays the music from it;
# with no CD (cdmusic=off) the game plays FM.
# Emulators: daum = DOSBox-Daum (the Windows build) under Wine, settings from
# daum/dosbox.conf before [autoexec]; staging = DOSBox Staging; x = DOSBox-X.
# Staging and X get the settings below, chosen to look and sound like Daum.
# The config is written to daum/run-<FOLDER>-<wine|staging|x>.conf.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
mps=$here/MPS
daum=$here/daum
settings=$here/settings-linux.txt

if [ ! -f "$settings" ] && [ -t 0 ] && [ -f "$here/setup.sh" ]; then
  bash "$here/setup.sh" --from-play || exit $?
fi
# key=value lines, read as data, never run
setting() { [ -f "$settings" ] && sed -n "s/^$1=//p" "$settings" | tr -d '\r' | tail -n 1 || true; }
emu=$(setting emulator);     emu=${emu:-daum}
emupath=$(setting emulator_path)
folder=$(setting language);  folder=${folder:-ROMANA}
cdmusic=$(setting cdmusic);  cdmusic=${cdmusic:-image}
musicvol=$(setting music_volume)
window=$(setting window);    window=${window:-0}
keep=0
for a in "$@"; do
  case $a in
    --keep-open) keep=1 ;;     # DOSBox stays open after the game ends, so error text stays readable
    --window)    window=1 ;;   # windowed instead of fullscreen
    --mp3)       cdmusic=mp3 ;;
    --music-volume=*) musicvol=${a#*=} ;;   # 0-100: CD tracks only
    -*) echo "unknown option: $a" >&2; exit 2 ;;
    *) folder=$a ;;
  esac
done

case $emu in daum|staging|x) ;; *) echo "emulator '$emu' in $settings: daum, staging or x" >&2; exit 2 ;; esac
case $cdmusic in
  image) cue=$mps/CD/Colonize.cue ;;
  mp3)   cue=$mps/CD/mp3/Colonize.cue ;;
  off)   cue= ;;
  *) echo "cdmusic '$cdmusic' in $settings: image, mp3 or off" >&2; exit 2 ;;
esac
[[ $folder =~ ^[A-Za-z0-9_-]{1,8}$ ]] || { echo "'$folder' is not a DOS 8.3 name" >&2; exit 2; }
[ -f "$mps/$folder/VICEROY.EXE" ] || { echo "no VICEROY.EXE in $mps/$folder" >&2; exit 1; }
[ -z "$cue" ] || [ -f "$cue" ] || { echo "no $cue" >&2; exit 1; }
[ -z "$musicvol" ] || [[ $musicvol =~ ^[0-9]+$ && $musicvol -le 100 ]] || { echo "music volume takes 0-100" >&2; exit 2; }

# CD music volume, per emulator (measured 2026-10-25): Daum honours the shim's
# CDVOL; Staging ignores it and takes MIXER CDAUDIO; DOSBox-X reads CDVOL as
# n/64, so CDVOL = p/4 gives p %. Nothing at 100 or with no CD.
vol=
if [ -n "$cue" ] && [ -n "$musicvol" ] && [ "$musicvol" -lt 100 ]; then
  case $emu in
    daum)    vol="SET CDVOL=$musicvol" ;;
    staging) vol="MIXER CDAUDIO $musicvol:$musicvol" ;;
    x)       vol="SET CDVOL=$(( (musicvol + 2) / 4 ))" ;;
  esac
fi

case $emu in
  daum)
    command -v wine >/dev/null || { echo "wine is not installed" >&2; exit 1; }
    conf=$daum/run-$folder-wine.conf
    sed '/^\[autoexec\]/,$d' "$daum/dosbox.conf" > "$conf"
    [ "$window" = 1 ] && sed -i 's/^fullscreen=.*/fullscreen=false/' "$conf"
    cdir="$(winepath -w "$mps")\\"; ccue=${cue:+$(winepath -w "$cue")} ;;
  staging)
    [ -x "$emupath" ] || { echo "DOSBox Staging not found ('$emupath'): run setup.sh" >&2; exit 1; }
    conf=$daum/run-$folder-staging.conf
    if [ "$window" = 1 ]; then fs=off; else fs=on; fi
    cat > "$conf" <<CONF
[sdl]
fullscreen = $fs
[render]
shader = scaler/advinterp3x
aspect = off
integer_scaling = off
viewport = fit
image_adjustments = off
[dosbox]
machine = svga_s3
memsize = 16
[cpu]
core = auto
cpu_cycles = 105000
cpu_cycles_protected = 105000
[mixer]
rate = 44100
blocksize = 1024
prebuffer = 20
negotiate = off
[sblaster]
sbtype = sb16
sbbase = 220
irq = 7
dma = 1
hdma = 5
oplmode = auto
[dos]
xms = on
ems = on
umb = on
CONF
    cdir=$mps/; ccue=$cue ;;
  x)
    emupath=${emupath:-$(command -v dosbox-x || true)}
    [ -x "$emupath" ] || { echo "DOSBox-X not found ('$emupath'): run setup.sh" >&2; exit 1; }
    conf=$daum/run-$folder-x.conf
    if [ "$window" = 1 ]; then fs=false; else fs=true; fi
    cat > "$conf" <<CONF
[sdl]
fullscreen = $fs
output = openglnb
[render]
scaler = hq3x
# with OpenGL, aspect = false stretches to the screen (16:9); Daum shows 16:10
aspect = true
aspect_ratio = 16:10
doublescan = true
autofit = true
[dosbox]
quit warning = false
machine = svga_s3
memsize = 16
[cpu]
core = auto
cycles = fixed 105000
[mixer]
rate = 44100
blocksize = 1024
prebuffer = 25
[sblaster]
sbtype = sb16
sbbase = 220
irq = 7
dma = 1
hdma = 5
oplmode = auto
[dos]
xms = true
ems = true
umb = true
CONF
    cdir=$mps/; ccue=$cue ;;
esac
{
  echo '[autoexec]'
  echo "mount C \"$cdir\""
  [ -z "$cue" ] || echo "imgmount D \"$ccue\" -t iso"
  [ -z "$vol" ] || echo "$vol"
  echo 'C:'
  echo "CD $folder"
  echo 'CLS'
  echo 'OPENING -g'
  [ -z "$cue" ] || echo 'mount -u D'
  [ "$keep" = 1 ] || echo 'exit'
} >> "$conf"

echo "Game     : $mps/$folder"
echo "Emulator : $emu${emupath:+ ($emupath)}"
echo "CD       : ${cue:-none (FM music)}${vol:+, $vol}"
echo "Config   : $conf"
case $emu in
  daum)
    cd "$daum"
    # mscoree/mshtml off: no Mono/Gecko install prompts on a fresh Wine prefix.
    WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml=" exec wine dosbox.exe -conf "$(winepath -w "$conf")" ;;
  staging) exec "$emupath" --noprimaryconf --nolocalconf --conf "$conf" ;;
  x)       exec "$emupath" -defaultconf -conf "$conf" ;;
esac
