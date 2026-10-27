"""Build the ro-pedia-sort EXE patch: Colonizopedia lists sort in Romanian
dictionary order (a < ă < â, i < î, s < ș, t < ț).

    python3 ro/tools/sort_patch.py VICEROY.old.exe      # print the manifest entry

Why: the lists (units, buildings, terrain, skills, ...) are sorted by one
routine in overlay code (file 0x69058), a gnome sort over heap string
numbers. It compares two neighbours with the runtime _fstricmp (0D1D:103E),
which lower-cases A-Z and compares bytes. A Romanian letter lives in a font
slot (fontedit.SLOTS), so "Țesător" (Ț = 15h) came first and "Vânător"
(â = 1Dh) came before "Velnicer" (play-test S4, 2026-09-27).

_fstricmp has no other caller, but it is 69 bytes and has no room for a
table. So the sort's own compare-and-swap part (0x690B3-0x6913B) is
rewritten in the same bytes:
  - the compare runs inline on 16-bit keys: high byte = the byte as
    _fstricmp saw it (A-Z lower-cased), low byte = 0; a Romanian slot gets
    its base letter and a rank (ă î ș ț 1, â 2), so ASCII order is kept
    exactly and each Romanian letter sits right after its base letter;
  - the swap, 123 bytes inline before, is a call to the sort's own swap
    routine at 0x68FDC (same segment: 0x68F98 calls 0x6B6B5 with push cs /
    call), and the step back one place (gnome sort) is kept.
The lcall to _fstricmp stays in place as dead bytes, because the overlay
manager fixes up its segment word. The two lcalls before it (string
lookup, thunk 181F:0022) are outside the site.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hotkey_patch import asm  # noqa: E402

# slot -> key letter: lower case = rank 1, upper case = rank 2 (only â, Â)
RANK = {0x5C: "a", 0x1C: "a", 0x16: "A", 0x1D: "A",     # Ă ă Â â
        0x7F: "i", 0x1E: "i",                           # Î î
        0x14: "s", 0x1F: "s",                           # Ș ș
        0x15: "t", 0x60: "t"}                           # Ț ț
KEYS = bytes(RANK)
LETTERS = "".join(RANK.values()).encode()

START, END = 0x690B3, 0x6913C       # rewritten; 0x6913C on (inc i, loop test) is kept
LCALL = 0x690B5                     # lcall 0D1D:103E (_fstricmp), kept as dead bytes
EXTERN = {"restart": 0x69063,       # flag = 0, i = [bp-8], back to the loop test
          "next": 0x6913C,          # inc word [bp-0Ch], go on with the next pair
          "swap": 0x68FDC}          # far swap(i, j) of the three list arrays

JE, JNE, JB, JA, JBE, JNS, JMP, LOOP, CALL, JMPN = (
    0x74, 0x75, 0x72, 0x77, 0x76, 0x79, 0xEB, 0xE2, 0xE8, 0xE9)


def code(lcall_bytes):
    # On entry (0x690B3): DX:AX = s1, the string of item i; on the stack the
    # far pointer to s2, the string of item i+1. Both were pushed as the
    # _fstricmp arguments; here s2 is popped, so the stack is as before.
    return [
        (JMP, "go"),
        lcall_bytes,                # never run; its segment word stays for the fixup
        "go",
        b"\x8e\xc2",                # mov es,dx
        b"\x93",                    # xchg bx,ax        ES:BX = s1
        b"\x5e",                    # pop si
        b"\x59",                    # pop cx
        b"\x1e",                    # push ds
        b"\x8e\xd9",                # mov ds,cx         DS:SI = s2
        (CALL, "tbl_end"),
        KEYS + LETTERS,             # 10 slot bytes, then the key letter for each
        "tbl_end",
        b"\x5f",                    # pop di            (CS offset of KEYS)
        "top",
        b"\x26\x8a\x07",            # mov al,es:[bx]
        b"\x43",                    # inc bx
        (CALL, "key"),
        b"\x92",                    # xchg dx,ax        DX = key of s1
        b"\xac",                    # lodsb
        (CALL, "key"),              # AX = key of s2
        b"\x3b\xd0",                # cmp dx,ax
        (JNE, "done"),
        b"\x0b\xc0",                # or ax,ax          both at the end?
        (JNE, "top"),
        "done",
        b"\x1f",                    # pop ds
        (JBE, "next"),              # s1 <= s2: keep the pair
        b"\x8b\x46\xf4",            # mov ax,[bp-0Ch]   i
        b"\x40",                    # inc ax
        b"\x50",                    # push ax           i+1
        b"\x48",                    # dec ax
        b"\x50",                    # push ax           i
        b"\x48",                    # dec ax
        (JNS, "keep"),
        b"\x33\xc0",                # xor ax,ax
        "keep",
        b"\x89\x46\xf8",            # mov [bp-8],ax     start again at max(i-1, 0)
        b"\x0e",                    # push cs
        (CALL, "swap"),
        b"\x59\x59",                # pop cx / pop cx
        (JMPN, "restart"),
        # key: AL = byte -> AX = sort key. Uses CX; DI = table.
        "key",
        b"\x3c\x41",                # cmp al,'A'
        (JB, "k1"),
        b"\x3c\x5a",                # cmp al,'Z'
        (JA, "k1"),
        b"\x0c\x20",                # or al,20h         A-Z -> a-z, as _fstricmp
        "k1",
        b"\x8a\xe0",                # mov ah,al
        b"\x57",                    # push di
        b"\xb9\x0a\x00",            # mov cx,10
        "kl",
        b"\x2e\x3a\x05",            # cmp al,cs:[di]
        (JE, "hit"),
        b"\x47",                    # inc di
        (LOOP, "kl"),
        b"\xb0\x00",                # mov al,0          ASCII: rank 0
        (JMP, "kout"),
        "hit",
        b"\x2e\x8a\x45\x0a",        # mov al,cs:[di+10] key letter
        b"\x8a\xe0",                # mov ah,al
        b"\x80\xcc\x20",            # or ah,20h         base letter, lower case
        b"\xf6\xd0",                # not al            rank: 'a' -> 9Eh, 'A' -> 0BEh
        "kout",
        b"\x5f",                    # pop di
        b"\xc3",                    # ret
    ]


def patch_bytes(pristine: bytes) -> bytes:
    items = code(pristine[LCALL:LCALL + 5])
    out, labels = asm(START, items, EXTERN)
    assert labels["go"] == LCALL + 5
    assert len(out) <= END - START, (len(out), END - START)
    return out + b"\x90" * (END - START - len(out))


def manifest_entry(pristine: bytes) -> dict:
    new = patch_bytes(pristine)
    whole = bytearray(pristine)
    whole[START:END] = new
    return {
        "id": "ro-pedia-sort",
        "title": "Colonizopedia lists in Romanian order (Ș after S, Ț after T, â after ă)",
        "status": "recommended",
        "offset": f"0x{START:X}",
        "original": pristine[START:END].hex(),
        "patched": new.hex(),
        "asm_before": "_fstricmp(s[i], s[i+1]) > 0: swap inline (bytes, A-Z folded)",
        "asm_after": "same compare on keys: a Romanian letter right after its base letter; swap by call",
        "md5_alone": hashlib.md5(bytes(whole)).hexdigest(),
        "conflicts": [],
        "summary": "The Colonizopedia sorts its lists with _fstricmp, which compares bytes. "
                   "Romanian letters are font slots below 'a', so Ț and Ș words came first and "
                   "\"Vânător\" came before \"Velnicer\". The sort's compare-and-swap part is "
                   "rewritten in the same bytes: dictionary order a < ă < â, i < î, s < ș, t < ț, "
                   "and the same order as before for every other byte. Built by "
                   "ro/tools/sort_patch.py.",
        "caveat": "Romanian only.",
        "notes": "Gnome sort at file 0x69058 (overlay), the only caller of _fstricmp. Kept: "
                 "the two lcalls 181F:0022 before the site, the lcall to _fstricmp at "
                 "0x690B5 (dead bytes, jumped over), and the loop tail from 0x6913C. The "
                 "swap is the sort's own routine at 0x68FDC (push cs / call, same segment). "
                 "No MZ relocation in the site. tests/test_ro_patches.py runs the old and "
                 "new sort in x86mini.",
    }


if __name__ == "__main__":
    exe = open(sys.argv[1], "rb").read()
    print(json.dumps(manifest_entry(exe), indent=2, ensure_ascii=False))
