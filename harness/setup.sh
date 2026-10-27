#!/usr/bin/env bash
# setup.sh - choose how this folder starts Colonization on Linux: emulator,
# default language, CD music and its volume, fullscreen or window. Saves the
# answers in settings-linux.txt, which play.sh, Romana.sh and English.sh read.
#   ./setup.sh            # ask; Enter keeps the value shown in [ ]
#   ./setup.sh --reset    # forget the saved answers, then ask again
# Windows twin: Setup.cmd (Setup.ps1), which keeps settings-windows.txt.
# Nothing is downloaded unless "Download DOSBox Staging" is chosen and confirmed.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
mps=$here/MPS
settings=$here/settings-linux.txt
from_play=0 reset=0
for a in "$@"; do
  case $a in
    --reset) reset=1 ;;
    --from-play) from_play=1 ;;   # started by play.sh: no "start now?" at the end
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done
if [ $reset = 1 ] && [ -f "$settings" ]; then rm -f "$settings"; echo "Saved answers removed."; fi

setting() { [ -f "$settings" ] && sed -n "s/^$1=//p" "$settings" | tr -d '\r' | tail -n 1 || true; }

# choose TITLE DEFAULT LABEL... : numbered menu; sets idx (0-based)
choose() {
  local title=$1 def=$2 i ans; shift 2
  echo; echo "$title"
  i=0; for l in "$@"; do i=$((i + 1)); echo "  $i) $l"; done
  while :; do
    printf 'Choice [%s]: ' "$((def + 1))"
    read -r ans || { echo; echo "No answer, stopped." >&2; exit 1; }
    ans=${ans:-$((def + 1))}
    if [[ $ans =~ ^[0-9]+$ ]] && [ "$ans" -ge 1 ] && [ "$ans" -le $# ]; then idx=$((ans - 1)); return; fi
    echo "  Type a number from 1 to $#."
  done
}
# confirm QUESTION DEFAULT(y|n)
confirm() {
  local ans; printf '%s [%s]: ' "$1" "$([ "$2" = y ] && echo Y/n || echo y/N)"
  read -r ans || ans=$2; ans=${ans:-$2}
  [[ $ans =~ ^[YyDd] ]]
}

# --- emulators found on this system
find_staging() {
  local p
  while IFS= read -r p; do
    [ -x "$p" ] && { echo "$p"; return; }
  done < <(printf '%s\n' "$here"/emulators/staging-linux/*/dosbox "$HOME"/apps/dosbox-staging*/dosbox | sort -V -r)
  p=$(command -v dosbox-staging || true); [ -n "$p" ] && { echo "$p"; return; }
  p=$(command -v dosbox || true)
  [ -n "$p" ] && "$p" --version 2>/dev/null | grep -qi staging && echo "$p"
  return 0
}
download_staging() {
  local api=https://api.github.com/repos/dosbox-staging/dosbox-staging/releases/latest json info url digest name size tmp
  for t in curl tar sha256sum python3; do command -v $t >/dev/null || { echo "  $t is needed for the download." >&2; return 1; }; done
  json=$(curl -fsSL "$api") || { echo "  GitHub did not answer." >&2; return 1; }
  info=$(printf '%s' "$json" | python3 -I -c '
import json, sys
r = json.load(sys.stdin)
for a in r["assets"]:
    if "linux-x86_64" in a["name"] and a["name"].endswith(".tar.xz"):
        print(a["browser_download_url"], a.get("digest") or "-", a["name"], a["size"]); break') || true
  read -r url digest name size <<< "$info" || true
  [ -n "${url:-}" ] || { echo "  No Linux build in the latest release." >&2; return 1; }
  [[ $digest == sha256:* ]] || { echo "  The release gives no checksum; not downloaded." >&2; return 1; }
  confirm "  Download $name ($((size / 1048576)) MB) from github.com?" n || return 1
  tmp=$(mktemp -d)
  curl -fL --progress-bar -o "$tmp/$name" "$url" || { rm -rf "$tmp"; return 1; }
  if [ "$(sha256sum "$tmp/$name" | cut -d' ' -f1)" != "${digest#sha256:}" ]; then
    echo "  Checksum does not match; deleted." >&2; rm -rf "$tmp"; return 1
  fi
  mkdir -p "$here/emulators/staging-linux"
  tar -xJf "$tmp/$name" -C "$here/emulators/staging-linux"; rm -rf "$tmp"
  echo "  Checksum OK; unpacked into emulators/staging-linux."
}

staging=$(find_staging)
xpath=$(command -v dosbox-x || true)
cur_emu=$(setting emulator)

while :; do
  labels=() values=() paths=()
  wine_note=; command -v wine >/dev/null || wine_note=" (needs wine, not installed)"
  labels+=("DOSBox-Daum under Wine (the Windows build in daum/)$wine_note"); values+=(daum); paths+=("")
  if [ -n "$staging" ]; then labels+=("DOSBox Staging ($staging)"); values+=(staging); paths+=("$staging"); fi
  if [ -n "$xpath" ]; then labels+=("DOSBox-X ($xpath)"); values+=(x); paths+=("$xpath"); fi
  if [ -z "$staging" ]; then labels+=("Download DOSBox Staging from GitHub (native, recommended)"); values+=(download); paths+=(""); fi
  def=0; for i in "${!values[@]}"; do [ "${values[$i]}" = "$cur_emu" ] && def=$i; done
  choose "Emulator:" $def "${labels[@]}"
  if [ "${values[$idx]}" = download ]; then
    download_staging || true
    staging=$(find_staging); cur_emu=staging
    continue
  fi
  emu=${values[$idx]} emupath=${paths[$idx]}
  break
done

labels=() values=()
[ -f "$mps/ROMANA/VICEROY.EXE" ] && { labels+=("Romanian (MPS/ROMANA)"); values+=(ROMANA); }
[ -f "$mps/COLONIZE/VICEROY.EXE" ] && { labels+=("English (MPS/COLONIZE)"); values+=(COLONIZE); }
[ ${#values[@]} -gt 0 ] || { echo "No game in $mps." >&2; exit 1; }
def=0; for i in "${!values[@]}"; do [ "${values[$i]}" = "$(setting language)" ] && def=$i; done
choose "Default language (Romana.sh and English.sh always start their own):" $def "${labels[@]}"
language=${values[$idx]}

labels=() values=()
[ -f "$mps/CD/Colonize.cue" ] && { labels+=("CD music from the disc image (MPS/CD/Colonize.cue)"); values+=(image); }
[ -f "$mps/CD/mp3/Colonize.cue" ] && { labels+=("CD music from the MP3 set (MPS/CD/mp3)"); values+=(mp3); }
labels+=("No CD music: the game's own FM music"); values+=(off)
def=0; for i in "${!values[@]}"; do [ "${values[$i]}" = "$(setting cdmusic)" ] && def=$i; done
choose "Music:" $def "${labels[@]}"
cdmusic=${values[$idx]}

volume=$(setting music_volume); volume=${volume:-100}
if [ "$cdmusic" != off ]; then
  echo; echo "CD music volume, 0-100 (only the CD tracks; game sounds keep theirs)."
  while :; do
    printf 'Volume [%s]: ' "$volume"
    read -r ans || ans=; ans=${ans:-$volume}
    if [[ $ans =~ ^[0-9]+$ ]] && [ "$ans" -le 100 ]; then volume=$ans; break; fi
    echo "  Type a number from 0 to 100."
  done
fi

def=0; [ "$(setting window)" = 1 ] && def=1
choose "Screen:" $def "Fullscreen" "Window"
window=$idx

echo
echo "Emulator : $emu${emupath:+ ($emupath)}"
echo "Language : $language"
echo "Music    : $cdmusic$([ "$cdmusic" != off ] && echo ", volume $volume")"
echo "Screen   : $([ "$window" = 1 ] && echo window || echo fullscreen)"
if ! confirm "Save these settings?" y; then echo "Not saved."; exit 1; fi
{
  echo "# Written by setup.sh on $(date +%F). Change: ./setup.sh   Start over: ./setup.sh --reset"
  echo "emulator=$emu"
  [ -z "$emupath" ] || echo "emulator_path=$emupath"
  echo "language=$language"
  echo "cdmusic=$cdmusic"
  echo "music_volume=$volume"
  echo "window=$window"
} > "$settings"
echo "Saved in $settings."
[ $from_play = 1 ] && exit 0
if confirm "Start the game now?" y; then exec bash "$here/play.sh"; fi
