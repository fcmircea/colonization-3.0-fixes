"""Build the ro-menu-key-fold and ro-dialog-key-fold EXE patches: a hotkey
marked on a Romanian letter answers to its base letter (NEGO~Ț -> Alt+T).

    python3 ro/tools/hotkey_patch.py VICEROY.old.exe      # print both manifest entries

Why: VICEROY.EXE takes a hotkey from the text itself, as the byte after the
last '~' (runtime _fstrrchr), upper-cased with _ctype for a-z only. A Romanian
letter lives in a font slot (fontedit.SLOTS), so "NEGO~Ț" gave key 15h and
Alt+T never matched (rig test 2026-09-28). Florin's rule: Ă Â A, Î I, Ș S,
Ț T are the same key.

Two copies of the routine exist, both in overlay code:
  0x4477E  far, MENU.TXT (knows F10 "~F~1~0" and the Shift-F cheat keys
           "~F~0~1"); result in CX, then a fixed tail that reloads DS with
           1B5Ah (a word the overlay manager fixes up) and returns.
  0x6C492  near, dialog items (its caller at 0x6C9C2 also looks for the '|'
           layout code); F1-F9 only; result in AX, leave, ret 4.
Each patch rewrites only the part after the two runtime calls (_fstrrchr,
_fstrchr), which holds no far call and no fixed-up word, in the same bytes,
with the same results plus the fold. The fold table sits inside the code; a
`call` over it pushes its address, so it works at any overlay load address.
"""
import hashlib
import json
import sys

FOLD = {0x5C: "A", 0x1C: "A", 0x16: "A", 0x1D: "A",     # Ă ă Â â
        0x7F: "I", 0x1E: "I",                           # Î î
        0x14: "S", 0x1F: "S",                           # Ș ș
        0x15: "T", 0x60: "T"}                           # Ț ț
KEYS = bytes(FOLD)
LETTERS = "".join(FOLD.values()).encode()
CTYPE = 0x27ED                  # DS: _ctype + 1 (1 upper, 2 lower, 4 digit)


NEAR = (0xE8, 0xE9)             # call / jmp with a 16-bit displacement


def asm(org, items, extern=None):
    """Tiny two-pass assembler: items are bytes, a label name (str), or
    (opcode, label) for a short jump / (0xE8 or 0xE9, label) for a near call
    or jump. `extern` gives file offsets of labels outside the code."""
    def layout(addr):
        labels, a = dict(extern or {}), org
        for it in items:
            if isinstance(it, str):
                labels[it] = a
            elif isinstance(it, tuple):
                a += 3 if it[0] in NEAR else 2
            else:
                a += len(it)
        return labels
    labels, out = layout(org), bytearray()
    for it in items:
        if isinstance(it, str):
            continue
        if isinstance(it, tuple):
            op, lab = it
            nxt = org + len(out) + (3 if op in NEAR else 2)
            rel = labels[lab] - nxt
            if op in NEAR:
                out += bytes([op]) + (rel & 0xFFFF).to_bytes(2, "little")
            else:
                assert -128 <= rel <= 127, (lab, rel)
                out += bytes([op, rel & 0xFF])
        else:
            out += it
    return bytes(out), labels


JE, JNE, JMP, CALL = 0x74, 0x75, 0xEB, 0xE8

FOLD_CODE = [                   # AX = key (AH = 0); DI free; ends at 'done'
    "fold",
    (CALL, "tbl_end"),
    KEYS + LETTERS,             # 10 slot bytes, then the letter for each
    "tbl_end",
    b"\x5f",                    # pop di           (CS offset of KEYS)
    b"\x0e\x07",                # push cs / pop es
    b"\xb9\x0a\x00",            # mov cx,10
    b"\xf2\xae",                # repne scasb      (AL in KEYS?)
    (JNE, "done"),
    b"\x2e\x8a\x45\x09",        # mov al,cs:[di+9] (the matching letter)
]

# ---- MENU.TXT routine (far, file 0x4477E) ------------------------------------------
# On entry (after `add sp,6` at 0x447AB): DX:AX = first '~' (_fstrchr, DX = 0 if
# none), [bp-2]:SI = last '~'. Result goes in CX at 0x4482A.
MENU_START, MENU_END = 0x447AE, 0x4482A
MENU_CODE = [
    b"\x97",                    # xchg ax,di       DI = first '~'
    b"\x33\xc0",                # xor ax,ax
    b"\x0b\xd2",                # or dx,dx         no '~' at all?
    (JE, "fold"),               #   key 0 (0 is not in KEYS)
    b"\x8e\xc2",                # mov es,dx        (both '~' are in one string)
    b"\x26\x8a\x44\x01",        # mov al,es:[si+1] c = byte after the last '~'
    b"\x3b\xfe",                # cmp di,si
    (JE, "plain"),
    b"\x26\x80\x7d\x01\x46",    # cmp byte es:[di+1],'F'
    (JNE, "plain"),
    b"\x8b\xd8",                # mov bx,ax
    b"\x36\xf6\x87" + CTYPE.to_bytes(2, "little") + b"\x04",   # test ss:[bx+_ctype],DIGIT
    (JE, "plain"),
    b"\xb9\x3b\x01",            # mov cx,13Bh      F1
    b"\x26\x80\x7d\x03\x30",    # cmp byte es:[di+3],'0'   "~F~0~n": Shift-F
    (JNE, "f1"),
    b"\xb1\x54",                # mov cl,54h
    b"\x26\x80\x7d\x05\x30",    # cmp byte es:[di+5],'0'   "~F~0~0~n"
    (JNE, "f1"),
    b"\xb1\x5e",                # mov cl,5Eh
    "f1",
    b"\x26\x80\x7c\xff\x31",    # cmp byte es:[si-1],'1'   "...~1~0": F10
    (JNE, "f2"),
    b"\xb0\x3a",                # mov al,'1'+9
    "f2",
    b"\x03\xc8",                # add cx,ax
    b"\x83\xe9\x31",            # sub cx,'1'
    (JMP, "out"),
    "plain",
    b"\x8b\xd8",                # mov bx,ax
    b"\x36\xf6\x87" + CTYPE.to_bytes(2, "little") + b"\x02",   # test ss:[bx+_ctype],LOWER
    (JE, "fold"),
    b"\x2c\x20",                # sub al,20h
    (JMP, "done"),
    *FOLD_CODE,
    "done",
    b"\x91",                    # xchg ax,cx
]

# ---- dialog routine (near, file 0x6C492) --------------------------------------------
# On entry (after the second lcall, where `add sp,6` was): DX:AX = first '~'
# (DX = 0 if none), [bp-6]:[bp-4] = last '~'. SI and DI belong to the caller.
DIALOG_START, DIALOG_END = 0x6C4BE, 0x6C520
DIALOG_CODE = [
    b"\x57",                    # push di
    b"\x93",                    # xchg ax,bx       BX = first '~'
    b"\x33\xc0",                # xor ax,ax
    b"\x0b\xd2",                # or dx,dx
    (JE, "fold"),
    b"\x8e\xc2",                # mov es,dx
    b"\x8b\x7e\xfa",            # mov di,[bp-6]    DI = last '~'
    b"\x26\x8a\x45\x01",        # mov al,es:[di+1]
    b"\x3b\xdf",                # cmp bx,di
    (JE, "plain"),
    b"\x26\x80\x7f\x01\x46",    # cmp byte es:[bx+1],'F'
    (JNE, "plain"),
    b"\x8b\xd8",                # mov bx,ax
    b"\xf6\x87" + CTYPE.to_bytes(2, "little") + b"\x04",       # test [bx+_ctype],DIGIT
    (JE, "plain"),
    b"\x05\x0a\x01",            # add ax,10Ah
    (JMP, "done"),
    "plain",
    b"\x8b\xd8",                # mov bx,ax
    b"\xf6\x87" + CTYPE.to_bytes(2, "little") + b"\x02",       # test [bx+_ctype],LOWER
    (JE, "fold"),
    b"\x2c\x20",                # sub al,20h
    (JMP, "done"),
    *FOLD_CODE,
    "done",
    b"\x5f",                    # pop di
    b"\xc9",                    # leave  (also drops the _fstrchr arguments)
    b"\xc2\x04\x00",            # ret 4
]


def padded(start, end, items, tail=None):
    """Assemble, then pad with nop to `end`; label `tail` (if given) lands on `end`."""
    code, _ = asm(start, items + ([tail] if tail else []))
    assert len(code) <= end - start, (hex(start), len(code), end - start)
    code, labels = asm(start, items + [b"\x90" * (end - start - len(code))] + ([tail] if tail else []))
    assert tail is None or labels[tail] == end
    return code


def menu_bytes():
    return padded(MENU_START, MENU_END, MENU_CODE, "out")     # 'out': the kept tail at 0x4482A


def dialog_bytes():
    return padded(DIALOG_START, DIALOG_END, DIALOG_CODE)


ENTRIES = {
    "ro-menu-key-fold": dict(
        start=MENU_START, bytes=menu_bytes,
        title="Menu hotkeys: a Romanian letter answers to its base key (NEGO~Ț -> Alt+T)",
        asm_before="key = byte after the last '~', a-z upper-cased (_ctype)",
        asm_after="same, and Ă ă Â â -> A, Î î -> I, Ș ș -> S, Ț ț -> T",
        summary="The MENU.TXT key of a line or menu title is the byte after its last '~'. "
                "A Romanian letter is a font slot, so \"NEGO~Ț\" never matched Alt+T. The "
                "routine's tail after the two string calls is rewritten in the same bytes: "
                "same keys as before (F1-F10, Shift-F cheat keys, space, a-z), plus a "
                "10-letter fold table. Built by ro/tools/hotkey_patch.py.",
        notes="Far routine at file 0x4477E (overlay). Kept as they are: the two lcalls "
              "(0x44791, 0x447A6), `add sp,6`, and the tail at 0x4482A (mov cx,1B5Ah / "
              "mov ds,cx, a word the overlay manager fixes up). No MZ relocation in the "
              "site. tests/test_ro_patches.py runs old and new routines in x86mini."),
    "ro-dialog-key-fold": dict(
        start=DIALOG_START, bytes=dialog_bytes,
        title="Dialog hotkeys: a Romanian letter answers to its base key",
        asm_before="key = byte after the last '~', a-z upper-cased (_ctype)",
        asm_after="same, and Ă ă Â â -> A, Î î -> I, Ș ș -> S, Ț ț -> T",
        summary="The same rule for dialog lines (GAME.TXT options): the routine that "
                "reads their '~' key gets the same fold. Built by ro/tools/hotkey_patch.py.",
        notes="Near routine at file 0x6C492 (overlay), called from the item builder at "
              "0x6C9C2. Kept: both lcalls. The rewritten part ends with pop di / leave / "
              "ret 4 as before (leave also drops the _fstrchr arguments). No MZ "
              "relocation in the site."),
}


def manifest_entry(pristine: bytes, pid: str) -> dict:
    e = ENTRIES[pid]
    new = e["bytes"]()
    start = e["start"]
    whole = bytearray(pristine)
    whole[start:start + len(new)] = new
    return {
        "id": pid,
        "title": e["title"],
        "status": "recommended",
        "offset": f"0x{start:X}",
        "original": pristine[start:start + len(new)].hex(),
        "patched": new.hex(),
        "asm_before": e["asm_before"],
        "asm_after": e["asm_after"],
        "md5_alone": hashlib.md5(bytes(whole)).hexdigest(),
        "conflicts": [],
        "summary": e["summary"],
        "caveat": "Romanian only.",
        "notes": e["notes"],
    }


if __name__ == "__main__":
    exe = open(sys.argv[1], "rb").read()
    print(json.dumps([manifest_entry(exe, p) for p in ENTRIES], indent=2, ensure_ascii=False))
