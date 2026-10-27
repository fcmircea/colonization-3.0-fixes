"""Build the ro-upcase EXE patch: strlwr, strupr and _fstrupr of VICEROY.EXE
share one loop, and strupr maps the Romanian font slots to their capitals.

    python3 ro/tools/upcase_patch.py VICEROY.old.exe      # print the manifest entry

Why: the EXE upper-cases some labels itself (nation cards, the colony build
list) with the C runtime strupr, which knows only a-z. KB
exe-limits-uppercase-ascii-only. The three routines sit in the runtime code
segment 0D1Dh (file 0xF5D0); every far call into them uses that segment.
strlwr (0x10316) and strupr (0x10334) are 30 bytes each and back to back;
_fstrupr (0x106E8) is 36. The shared loop takes its bounds from CX (CL: first
letter to change, CH: first letter of the result), which frees room for the
mapping and a 5-byte table. Entry points stay where they are; no MZ
relocation falls inside either region.

Behaviour: strlwr and ASCII upper-casing are unchanged. strupr and _fstrupr
also map 1Ch->5Ch (ă Ă), 1Dh->16h (â Â), 1Eh->7Fh (î Î), 1Fh->14h (ș Ș),
60h->15h (ț Ț); 1Bh (ESC, never in text) -> 15h as a side effect.
Return values: AX = the string offset, as before; DX = its segment.
"""
import hashlib
import json
import sys

A, B, CSBASE = 0x10316, 0x106E8, 0xF5D0

def rel8(frm_next, to):
    r = to - frm_next; assert -128 <= r <= 127, (hex(frm_next), hex(to)); return r & 0xFF
def rel16(frm_next, to):
    return ((to - frm_next) & 0xFFFF).to_bytes(2, 'little')

a = {k: A + v for k, v in dict(common=10, top=11, st=25, nx=27, strupr=30, fsjoin=37, end=42, ro=47).items()}
b = {k: B + v for k, v in dict(ro=10, r1=18, ret=29, tbl=30).items()}
A_code = bytes([
    0x1e,                          # +0  strlwr: push ds
    0x8b, 0xdc,                    # +1  mov bx,sp
    0x36, 0x8b, 0x5f, 0x06,        # +3  mov bx,ss:[bx+6]
    0xb9, 0x41, 0x61,              # +7  mov cx,6141h   (cl: subtract 'A', ch: add 'a')
    0x53,                          # +10 common: push bx
    0x8a, 0x07,                    # +11 top: mov al,[bx]
    0x0a, 0xc0,                    # +13 or al,al
    0x74, rel8(A+17, a['end']),    # +15 je end
    0x2a, 0xc1,                    # +17 sub al,cl
    0x3c, 0x1a,                    # +19 cmp al,1Ah
    0x73, rel8(A+23, a['ro']),     # +21 jae ro
    0x02, 0xc5,                    # +23 add al,ch
    0x88, 0x07,                    # +25 st: mov [bx],al
    0x43,                          # +27 nx: inc bx
    0xeb, rel8(A+30, a['top']),    # +28 jmp top
    0x1e,                          # +30 strupr: push ds
    0x8b, 0xdc,                    # +31 mov bx,sp
    0x36, 0x8b, 0x5f, 0x06,        # +33 mov bx,ss:[bx+6]
    0xb9, 0x61, 0x41,              # +37 fsjoin: mov cx,4161h (cl: subtract 'a', ch: add 'A')
    0xeb, rel8(A+42, a['common']), # +40 jmp common
    0x58,                          # +42 end: pop ax      (start of string)
    0x8c, 0xda,                    # +43 mov dx,ds        (segment, for _fstrupr)
    0x1f,                          # +45 pop ds
    0xcb,                          # +46 retf
    0x80, 0xf9, 0x61,              # +47 ro: cmp cl,61h   (upper mode?)
    0x75, rel8(A+52, a['nx']),     # +50 jne nx
    0xe8, *rel16(A+55, b['ro']),   # +52 call B_ro       (CF=1: al mapped)
    0x72, rel8(A+57, a['st']),     # +55 jc st
    0xeb, rel8(A+59, a['nx']),     # +57 jmp nx
    0x90,                          # +59 (spare)
])
assert len(A_code) == 60, len(A_code)
tbl_cs = (b['tbl'] - CSBASE).to_bytes(2, 'little')
B_code = bytes([
    0x1e,                          # +0  _fstrupr: push ds
    0x8b, 0xdc,                    # +1  mov bx,sp
    0x36, 0xc5, 0x5f, 0x06,        # +3  lds bx,ss:[bx+6]
    0xe9, *rel16(B+10, a['fsjoin']),  # +7 jmp fsjoin
    0x04, 0x46,                    # +10 B_ro: add al,46h  (al was c-61h; now c-1Bh)
    0x3c, 0x45,                    # +12 cmp al,45h        (c = 60h, t-comma)
    0x75, rel8(B+16, b['r1']),     # +14 jne r1
    0xb0, 0x00,                    # +16 mov al,0
    0x3c, 0x05,                    # +18 r1: cmp al,5      (CF=1 when 0..4)
    0x73, rel8(B+22, b['ret']),    # +20 jae ret           (CF=0: leave it)
    0x53,                          # +22 push bx
    0xbb, *tbl_cs,                 # +23 mov bx,TBL        (CS offset; all callers use CS 0D1Dh)
    0x2e, 0xd7,                    # +26 cs: xlatb
    0x5b,                          # +28 pop bx
    0xc3,                          # +29 ret: ret
    0x15, 0x5c, 0x16, 0x7f, 0x14,  # +30 TBL: Ț Ă Â Î Ș  (for 60h 1Ch 1Dh 1Eh 1Fh)
])
assert len(B_code) == 35, len(B_code)

START, END = A, B + len(B_code)              # one patch site: the two regions and what lies between


def patched_span(exe: bytes) -> bytes:
    span = bytearray(exe[START:END])
    span[0:len(A_code)] = A_code
    span[B - START:B - START + len(B_code)] = B_code
    return bytes(span)


def manifest_entry(pristine: bytes) -> dict:
    new = patched_span(pristine)
    whole = bytearray(pristine)
    whole[START:END] = new
    return {
        "id": "ro-upcase",
        "title": "Upper-case labels keep Romanian letters (FRANȚA, ȚESĂTORIE)",
        "status": "recommended",
        "offset": f"0x{START:X}",
        "original": pristine[START:END].hex(),
        "patched": new.hex(),
        "asm_before": "strlwr / strupr / _fstrupr: a-z only",
        "asm_after": "one shared loop; strupr also maps 1Ch-1Fh, 60h to the Romanian capitals",
        "md5_alone": hashlib.md5(bytes(whole)).hexdigest(),
        "conflicts": [],
        "summary": "The EXE upper-cases some labels itself with the C runtime strupr, which knows only a-z, so ă â î ș ț stay small or turn into other glyphs. strlwr, strupr and _fstrupr now share one loop, which frees room for a 5-letter table. Built by ro/tools/upcase_patch.py.",
        "caveat": "Romanian only. The site spans 0x10316-0x1070A; only its first 60 and last 35 bytes change, the rest is checked as is.",
        "notes": "Runtime segment 0D1Dh (file 0xF5D0); the table is read as CS:1136h. No MZ relocation in the site. tests/test_ro_patches.py runs all three routines on every byte value in a small 8086 interpreter.",
    }


if __name__ == "__main__":
    print(json.dumps(manifest_entry(open(sys.argv[1], "rb").read()), indent=2, ensure_ascii=False))
