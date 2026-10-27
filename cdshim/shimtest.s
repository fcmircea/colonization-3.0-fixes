# SHIMTEST.COM - load a ?SOUND.COL the way VICEROY does and drive it.
#   SHIMTEST [/E | /J] [file] default PSOUND.COL; output to stdout (redirect it)
#   /E: the effects test instead (digitised samples after an FM-only song)
#   /J: the jingles test instead (3E, 3C, 3F each after a CD background song;
#       plan .claude/plans/2026-10-25-cd-jingles.md)
# Loads with INT 21h 48h (largest - 2), 4B03h, shrink to [2Ah] - seg +
# ceil([2Ch]/16) + 8 (VICEROY file 0x12879); entries from [28h]:[32h + 2n].
# Calls init with CONFIG.COL's first 7 words (what VICEROY passes, logged by a
# --log shim), runs the driver's tick (entry 3) from INT 1Ch, sends commands,
# and checks the CD through MSCDEX itself. Plan step 4,
# .claude/plans/2026-10-24-cd-audio-shim.md.
# Build: cdshim/build.py --shimtest OUT.COM
        .intel_syntax noprefix
        .code16
        .arch i286
        .text
        .globl start
start:
        cld
        mov sp, offset stack_top
        mov bx, offset prog_end + 15       # keep only what this program needs
        shr bx, 4
        mov ah, 0x4a
        int 0x21
        mov si, 0x81                       # [/E] and file name from the command line
        call skipsp
        mov al, [si + 1]                   # "/E" or "/J", any case
        and al, 0xdf
        cmp byte ptr [si], '/'
        jne 21f
        cmp al, 'E'                        # /E: mode 1
        je 20f
        cmp al, 'J'                        # /J: mode 2
        jne 21f
        inc byte ptr eff_mode
20:     inc byte ptr eff_mode
        add si, 2
        call skipsp
21:
        cmp byte ptr [si], 13
        je 1f
        mov di, offset drv_name
2:      lodsb
        cmp al, ' '
        jbe 3f
        stosb
        jmp 2b
3:      mov byte ptr [di], 0
1:      call free_paras
        mov free0, ax
        mov dx, offset m_free0
        call putsdec

        # --- load as VICEROY does
        mov ah, 0x48
        mov bx, 0xffff
        int 0x21
        sub bx, 2
        mov ah, 0x48
        int 0x21
        jnc 4f
        mov dx, offset m_nomem
        jmp die
4:      mov dseg, ax
        mov parblk, ax
        mov parblk + 2, ax
        push ds
        pop es
        mov bx, offset parblk
        mov dx, offset drv_name
        mov ax, 0x4b03
        int 0x21
        push cs
        pop ds
        push cs
        pop es
        jnc 5f
        mov dx, offset m_noload
        jmp die
5:      mov es, dseg
        mov ax, es:0x2c
        add ax, 15
        rcr ax, 1
        shr ax, 3
        add ax, es:0x2a
        sub ax, dseg
        add ax, 8
        mov bx, ax
        mov ah, 0x4a
        int 0x21
        mov dx, es:0x28
        mov si, 0x32
        mov di, offset ent
        mov cx, 5
6:      mov ax, es:[si]
        mov [di], ax
        mov [di + 2], dx
        add si, 2
        add di, 4
        loop 6b
        push cs
        pop es
        mov dx, offset m_loaded
        call puts
        mov ax, dseg
        call puthex16
        call crlf

        # --- init with CONFIG.COL's first 7 words (VICEROY pushes them in order)
        mov ax, 0x3d00
        mov dx, offset cfg_name
        int 0x21
        jc 7f
        mov bx, ax
        mov ah, 0x3f
        mov cx, 14
        mov dx, offset cfg
        int 0x21
        mov ah, 0x3e
        int 0x21
7:      mov si, offset cfg + 12
        mov cx, 7
8:      push word ptr [si]
        sub si, 2
        loop 8b
        call dword ptr ent
        add sp, 14
        push cs
        pop ds
        mov dx, offset m_init
        call putsdec

        # --- the timer: entry 3 from INT 1Ch (18.2 Hz; VICEROY uses 60.88 Hz)
        mov ax, 0x351c
        int 0x21
        mov old1c, bx
        mov old1c + 2, es
        push cs
        pop es
        mov ax, 0x251c
        mov dx, offset tick
        int 0x21

 cmp byte ptr eff_mode, 1
        je effects
        ja jingles
        mov ax, 8                          # nothing plays yet
        call cmd_show
        mov ax, 0x20                       # Bird Song -> track 9
        call cmd_show
        mov cx, 2
        call wait_s
        call cd_show
        mov ax, 8
        call cmd_show
        mov cx, 4
        call wait_s
        mov ax, 0x40                       # an effect while the CD plays
        call cmd_show
        mov cx, 3
        call wait_s
        mov ax, 1                          # stop all
        call cmd_show
        call cd_show
        mov ax, 8
        call cmd_show
        mov ax, 0x2a                       # York Fusiliers: no track, FM
        call cmd_show
        mov cx, 4
        call wait_s
        call cd_show
        mov ax, 8
        call cmd_show
        mov ax, 0x35                       # Tenochtitlan, track 12 (24 s)
        call cmd_show
        call cd_show
        call wait_idle
        call cd_show

        # --- shutdown, unload, check that all memory came back
shutdown:
        call dword ptr ent + 8
        push cs
        pop ds
        mov dx, offset m_shut
        call putsdec
        push ds
        lds dx, old1c
        mov ax, 0x251c
        int 0x21
        pop ds
        mov es, dseg                       # VICEROY frees the driver block (0x12959)
        mov ah, 0x49
        int 0x21
        push cs
        pop es
        call free_paras
        mov dx, offset m_free1
        call putsdec
        mov ax, 0x4c00
        int 0x21

# effects (/E): PSOUND plays an effect's digitised sample only while its
# [DS:1CAh] = FFh (body 0x1CCC); some songs set it to 2-5, every music command
# sets FFh again. An effect that played its sample returns the address after
# its "call 1CCCh" (40h: 1D2Ch); the FM fallback returns something else.
effects:
        mov ax, 0x40                       # sample expected
        call cmd_show
        call ca_show
        mov cx, 2
        call wait_s
        mov ax, 0x2a                       # York Fusiliers, no CD track: FM, sets [1CAh] = 4
        call cmd_show
        call ca_show
        mov cx, 3
        call wait_s
        mov ax, 0x40                       # FM fallback expected (as without the shim)
        call cmd_show
        call ca_show
        mov cx, 2
        call wait_s
        mov ax, 0x20                       # Bird Song: CD track 9 with the shim, FM without
        call cmd_show
        call ca_show
        mov cx, 3
        call wait_s
        mov ax, 0x40                       # sample expected again
        call cmd_show
        call ca_show
        mov cx, 2
        call wait_s
        mov ax, 1
        call cmd_show
        jmp shutdown

# jingles (/J): with tracks 28-30 on the CD (cue2audio.py --jingles) each
# jingle cuts the background track and plays its own; command 8 is busy until
# it ends. On the 27-track disc the shim refuses them (err 01) and FM plays.
jingles:
        call disk_show
        mov ax, 0x20                       # Bird Song, track 9
        mov bx, 0x3e                       # meetking -> 29
        call jingle_run
        mov ax, 0x21                       # track 6
        mov bx, 0x3c                       # goldcity -> 28
        call jingle_run
        mov ax, 0x22                       # track 13
        mov bx, 0x3f                       # 1812 -> 30
        call jingle_run
        mov ax, 1
        call cmd_show
        jmp shutdown

# jingle_run: background command AX for 3 s, then jingle BX, then wait for idle
jingle_run:
        mov jingle, bx
        call cmd_show
        mov cx, 3
        call wait_s
        call cd_show
        mov ax, jingle
        call cmd_show
        mov cx, 1
        call wait_s
        call cd_show
        mov ax, 8
        call cmd_show
        call wait_idle
        jmp cd_show

# disk_show: ask MSCDEX for the audio disk info: first and last track
disk_show:
        mov ax, 0x1500
        xor bx, bx
        int 0x2f
        test bx, bx
        jz 23f
        mov word ptr rq_io + 14, offset cb_disk
        mov rq_io + 16, ds
        mov word ptr rq_io + 18, 7
        mov word ptr rq_io + 3, 0
        mov bx, offset rq_io
        mov ax, 0x1510
        int 0x2f
        mov dx, offset m_disk
        call puts
        mov al, cb_disk + 1
        xor ah, ah
        call putdec
        mov dl, '-'
        mov ah, 2
        int 0x21
        mov al, cb_disk + 2
        xor ah, ah
        call putdec
        jmp crlf
23:     mov dx, offset m_nocd
        jmp puts

# ca_show: print the FM driver's [DS:1CAh]. Its DS is its body word 2Ah; the
# FM driver is our file, or the one a shim loaded (shim body 57h, fm_seg).
ca_show:
        push es
        mov es, dseg
        cmp word ptr es:0x10, 0x4443       # "CD shim" tag
        jne 22f
        mov es, es:0x57
22:     mov es, es:0x2a
        mov dx, offset m_ca
        call puts
        mov al, es:0x1ca
        call puthex8
        pop es
        jmp crlf

# wait_idle: wait (at most 40 s) for command 8 to return 0, print the seconds
# to a tenth. Asks on every BIOS tick: the shim asks MSCDEX on every 16th
# command 8 only, so asking once a second would see the end 16 s late.
wait_idle:
        xor di, di
9:      call wait_tick
        inc di
        mov ax, 8
        call cmd
        test ax, ax
        jz 10f
        cmp di, 40 * 18
        jb 9b
10:     mov dx, offset m_end
        call puts
        mov ax, di                         # ticks * 10 / 18 = tenths of a second
        mov bx, 10
        mul bx
        mov bx, 18
        div bx
        xor dx, dx
        mov bx, 10
        div bx
        push dx
        call putdec
        mov dl, '.'
        mov ah, 2
        int 0x21
        pop ax
        call putdec
        jmp crlf

# wait_tick: until the BIOS tick count changes
wait_tick:
        push es
        xor ax, ax
        mov es, ax
        mov bx, es:0x46c
24:     cmp bx, es:0x46c
        je 24b
        pop es
        ret

die:    call puts
        mov ax, 0x4c01
        int 0x21

# tick: INT 1Ch handler, calls the driver's entry 3 (and 4 when it asks)
tick:   pusha
        push ds
        push es
        push cs
        pop ds
        call dword ptr cs:ent + 12
        test ax, ax
        jle 11f
        call dword ptr cs:ent + 16
11:     pop es
        pop ds
        popa
        jmp dword ptr cs:old1c

# cmd: AX = command -> AX = the driver's answer
cmd:    push ax
        call dword ptr ent + 4
        add sp, 2
        push cs
        pop ds
        push cs
        pop es
        ret

# cmd_show: send AX, print "cmd XX -> YYYY"
cmd_show:
        push ax
        mov dx, offset m_cmd
        call puts
        pop ax
        push ax
        call puthex16
        pop ax
        call cmd
        push ax
        mov dx, offset m_arrow
        call puts
        pop ax
        call puthex16
        mov dx, offset m_state             # the shim's own CD state (cdshim.s 0ADh-0B3h)
        call puts
        push es
        mov es, dseg
        mov si, 0xad
        mov cx, 7
19:     mov al, es:[si]
        call puthex8
        mov dl, ' '
        mov ah, 2
        int 0x21
        inc si
        loop 19b
        pop es
        jmp crlf

# cd_show: ask MSCDEX directly: status word, Q-channel track (BCD), run time
cd_show:
        mov ax, 0x1500
        xor bx, bx
        int 0x2f
        test bx, bx
        jz 12f
        mov word ptr rq_io + 14, offset cb_q
        mov rq_io + 16, ds
        mov word ptr rq_io + 18, 11
        mov word ptr rq_io + 3, 0
        mov bx, offset rq_io
        mov ax, 0x1510
        int 0x2f
        mov dx, offset m_cd
        call puts
        mov ax, rq_io + 3
        call puthex16
        mov dx, offset m_trk
        call puts
        mov al, cb_q + 2
        xor ah, ah
        call puthex16
        mov dx, offset m_run
        call puts
        mov al, cb_q + 4
        xor ah, ah
        call putdec
        mov dl, ':'
        mov ah, 2
        int 0x21
        mov al, cb_q + 5
        xor ah, ah
        call putdec
        jmp crlf
12:     mov dx, offset m_nocd
        jmp puts

# wait_s: CX seconds (18 BIOS ticks each)
wait_s: push es
        xor ax, ax
        mov es, ax
13:     mov bx, es:0x46c
14:     mov ax, es:0x46c
        sub ax, bx
        cmp ax, 18
        jb 14b
        loop 13b
        pop es
        ret

# free_paras: AX = largest free DOS block, paragraphs
free_paras:
        mov ah, 0x48
        mov bx, 0xffff
        int 0x21
        mov ax, bx
        ret

skipsp: cmp byte ptr [si], ' '
        jne 15f
        inc si
        jmp skipsp
15:     ret

puts:   mov ah, 9
        int 0x21
        ret
crlf:   mov dx, offset m_crlf
        jmp puts
# putsdec: print string DX, then AX in decimal, then a new line
putsdec:
        push ax
        call puts
        pop ax
        call putdec
        jmp crlf
putdec: push bx
        push cx
        push dx
        mov bx, 10
        xor cx, cx
16:     xor dx, dx
        div bx
        push dx
        inc cx
        test ax, ax
        jnz 16b
17:     pop dx
        add dl, '0'
        mov ah, 2
        int 0x21
        loop 17b
        pop dx
        pop cx
        pop bx
        ret
puthex16:
        push ax
        mov al, ah
        call puthex8
        pop ax
puthex8:
        push ax
        shr al, 4
        call hexdig
        pop ax
hexdig: and al, 15
        add al, '0'
        cmp al, '9'
        jbe 18f
        add al, 7
18:     mov dl, al
        mov ah, 2
        int 0x21
        ret

m_free0:  .ascii "free paragraphs before load $"
m_free1:  .ascii "free paragraphs after unload $"
m_nomem:  .ascii "no memory\r\n$"
m_noload: .ascii "4B03h failed\r\n$"
m_loaded: .ascii "loaded at $"
m_init:   .ascii "init returned $"
m_cmd:    .ascii "cmd $"
m_arrow:  .ascii " -> $"
m_state:  .ascii "   shim ok/playing/drive/err/stat: $"
m_cd:     .ascii "  mscdex status $"
m_trk:    .ascii " track $"
m_run:    .ascii " run $"
m_nocd:   .ascii "  no MSCDEX drive\r\n$"
m_end:    .ascii "cmd 8 returned 0 after seconds: $"
m_shut:   .ascii "shutdown returned $"
m_ca:     .ascii "  fm [1CAh] = $"
m_disk:   .ascii "  mscdex audio tracks $"
m_crlf:   .ascii "\r\n$"
jingle:   .word 0
cb_disk:  .byte 0x0a                 # audio disk info
          .fill 6, 1, 0
eff_mode: .byte 0
drv_name: .asciz "PSOUND.COL"
          .fill 16, 1, 0
cfg_name: .asciz "CONFIG.COL"
        .balign 2
cfg:      .fill 14, 1, 0
free0:    .word 0
dseg:     .word 0
parblk:   .word 0, 0
ent:      .fill 5 * 4, 1, 0
old1c:    .word 0, 0
cb_q:     .byte 0x0c                 # Q channel
          .fill 10, 1, 0
        .balign 2
rq_io:    .byte 26, 0, 3
          .word 0
          .fill 8, 1, 0
          .byte 0
          .long 0
          .word 0, 0
          .long 0
        .balign 2
          .fill 512, 1, 0
stack_top:
prog_end:
