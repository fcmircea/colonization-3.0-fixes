#!/bin/bash
# One-shot: boot, load the first save, locate a colony's commodity word, attach
# gdb with a watchpoint on it, then drive turns until the time runs out.
#
#   ./run_watch.sh disk.img boot-floppy.img out-tag
#
# Writes hits_<tag>.log and snaps_<tag>/ in the current folder.
#
# The floppy must start the game by itself (make_boot_floppy.sh).
# Must run inside a SINGLE invocation: the VM does not survive between calls.
# Allow about 5 minutes. Only the QEMU started here is killed.
#
# Environment (defaults in brackets):
#   QEMU         qemu-system-i386 binary          [qemu-system-i386]
#   WATCH        gdb script: write or read         [write]
#   GDB_SECONDS  how long to drive the game        [230]
#   GDB_PORT     gdb stub port                     [1234]
#   BOOT_WAIT    seconds from power-on to menu     [47]
#   SCAN_ARGS    extra args for scan_stock.py      []
set -euo pipefail
IMG=${1:?usage: run_watch.sh <disk.img> <floppy.img> <tag>}
FLOPPY=${2:?usage: run_watch.sh <disk.img> <floppy.img> <tag>}
TAG=${3:-run}
QEMU=${QEMU:-qemu-system-i386}
WATCH=${WATCH:-write}
GDB_SECONDS=${GDB_SECONDS:-230}
GDB_PORT=${GDB_PORT:-1234}
BOOT_WAIT=${BOOT_WAIT:-47}
HERE="$(cd "$(dirname "$0")" && pwd)"

OUTDIR=$(pwd)
SNAPS="$OUTDIR/snaps_$TAG"
HITS="$OUTDIR/hits_$TAG.log"
WORK=$(mktemp -d)
SOCK="$WORK/qmon.sock"
QPID= ; GPID=
cleanup() {
  for p in $GPID $QPID; do kill -9 "$p" 2>/dev/null || true; done
  rm -rf "$WORK"
}
trap cleanup EXIT

rm -rf "$SNAPS"; mkdir -p "$SNAPS"; : > "$HITS"
cp "$IMG" "$WORK/disk.img"; cp "$FLOPPY" "$WORK/floppy.img"

"$QEMU" -m 16 \
  -drive file="$WORK/floppy.img",format=raw,if=floppy \
  -drive file="$WORK/disk.img",format=raw,if=ide,index=0,media=disk \
  -boot a -display none \
  -monitor unix:"$SOCK",server,nowait 2>"$WORK/qemu.err" &
QPID=$!

# NOTE: the space in "-T 4" is required. "-T4" silently does nothing.
M(){ printf '%s\n' "$1" | socat -T 4 - unix-connect:"$SOCK" >/dev/null 2>&1 || true; }
snap(){ M "screendump \"$WORK/s.ppm\""; sleep 0.6; cp -f "$WORK/s.ppm" "$SNAPS/$1.ppm" 2>/dev/null || true; }
grab(){ M "screendump \"$WORK/cur.ppm\""; sleep 0.5; }

sleep "$BOOT_WAIT"
kill -0 "$QPID" 2>/dev/null || { echo "qemu died:" >&2; cat "$WORK/qemu.err" >&2; exit 1; }
M 'sendkey down'; sleep .6
M 'sendkey down'; sleep .6
M 'sendkey down'; sleep .6
M 'sendkey ret';  sleep 4                  # LOAD Game
M 'sendkey ret';  sleep 11                 # first save slot
# the load confirmation is click-to-dismiss; the centre is forgiving
M 'mouse_move -3000 -3000'; sleep .4
M 'mouse_move 320 200';     sleep .4
M 'mouse_button 1'; sleep .3; M 'mouse_button 0'; sleep 2
snap 00_loaded

# addresses move every run - always re-locate
M "pmemsave 0 0x100000 \"$WORK/mem.bin\""; sleep 4
# shellcheck disable=SC2086
ADDR=$(python3 "$HERE/scan_stock.py" "$WORK/mem.bin" ${SCAN_ARGS:-}) || { echo "locate failed" >&2; exit 1; }
echo "watching $ADDR" >&2

M "gdbserver tcp::$GDB_PORT"; sleep 2
WWATCH=$ADDR HITS_LOG="$HITS" GDB_PORT=$GDB_PORT GDB_SECONDS=$GDB_SECONDS \
  gdb -q -batch -x "$HERE/gdb_watch_$WATCH.py" >"$WORK/gdb.out" 2>&1 &
GPID=$!

# SPACE only. Enter, Escape and map clicks derail the game (docs/DEAD-ENDS.md).
START=$(date +%s); n=0
while [ $(( $(date +%s) - START )) -lt "$GDB_SECONDS" ]; do
  grep -q GDB-DONE "$HITS" 2>/dev/null && break
  grab
  POP=$(python3 "$HERE/detect_popup.py" "$WORK/cur.ppm" 2>/dev/null || echo NOPOPUP)
  if [ "${POP%% *}" = "POPUP" ]; then
    Y=${POP#* }
    M 'mouse_move -3000 -3000'; sleep .3; M "mouse_move 150 $Y"; sleep .3
    M 'mouse_button 1'; sleep .2; M 'mouse_button 0'; sleep .4
  else
    M 'sendkey spc'; sleep .5
  fi
  n=$((n+1))
  [ $((n % 8)) -eq 0 ] && snap "t_$n"
done

snap 99_final
M quit; sleep 1
for f in "$SNAPS"/*.ppm; do convert "$f" "${f%.ppm}.png" 2>/dev/null && rm -f "$f"; done

echo "=== hits ($HITS) ==="
grep -E '^(HIT|READ|ARMED|SUMMARY)' "$HITS" | sed 's/ CODE=.*//' || echo "none"
