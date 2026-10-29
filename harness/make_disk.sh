#!/bin/bash
# Build a FAT16 hard-disk image containing the game.
#
#   ./make_disk.sh /path/to/COLONIZE disk.img
#
# Use a real partitioned image, NOT qemu's vvfat: vvfat limits the root
# directory to 255 entries (COLONIZE has ~300) and QEMU 8.2.2 (Ubuntu's
# package) aborts on guest writes through it.
set -euo pipefail
SRC=${1:?usage: make_disk.sh <COLONIZE dir> <out.img>}
OUT=${2:?usage: make_disk.sh <COLONIZE dir> <out.img>}
[ -f "$SRC/VICEROY.EXE" ] || { echo "no VICEROY.EXE in $SRC" >&2; exit 1; }
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT

rm -f "$OUT"
truncate -s $((101808 * 512)) "$OUT"
printf 'label: dos\nstart=63, size=101745, type=06\n' | sfdisk "$OUT" >/dev/null
mkfs.vfat -F 16 -g 16/63 -h 63 --offset 63 "$OUT" >/dev/null
mcopy -i "$OUT"@@32256 -s "$SRC" ::/COLONIZE

# 20 zero bytes = "no sound card". Otherwise the game hangs on a black screen.
head -c 20 /dev/zero > "$WORK/CONFIG.COL"
mcopy -i "$OUT"@@32256 -o "$WORK/CONFIG.COL" ::/COLONIZE/CONFIG.COL

echo "built $OUT"
mdir -i "$OUT"@@32256 ::/COLONIZE | tail -2
