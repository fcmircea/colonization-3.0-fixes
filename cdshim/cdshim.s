# cdshim.s - a ?SOUND.COL for Colonization that plays the music from CD audio.
#
# VICEROY loads #SOUND.COL with DOS "load overlay" (INT 21h AX=4B03h), keeps
# [2Ah] - loadseg + ceil([2Ch] / 16) + 8 paragraphs, and takes entries 0-4 as
# far pointers [28h]:[32h + 2n]. This file has the same shape. At init it loads
# the original driver (renamed, name in fm_name) the same way and passes calls
# on to it, except:
#   - a music command (20h-3Fh) with a CD track in track_tab: the FM driver
#     gets command 1 (stop all, as every PSOUND song does first) and the byte
#     its dispatcher sets to FFh before every song is set to FFh (fm_mflag),
#     then the CD plays the track once, from its start to the next track's
#     start;
#   - command 8 ("still playing?", the song chooser waits for 0): the FM
#     driver's answer OR the CD busy bit;
#   - commands 0-3 (reset, stop all, fade/stop music) and music with no track,
#     or with a track past the disc's last one: the CD stops, then the FM
#     driver gets the command. The jingles 3C, 3E, 3F have tracks 28-30, which
#     only the MP3 set made by cue2audio.py --jingles has (plan
#     .claude/plans/2026-10-25-cd-jingles.md); on the 27-track disc they stay FM.
# CDVOL=n (0-100) in the DOS environment sets the CD audio volume once at init
# (MSCDEX audio channel control); without it the volume is left alone. Measured
# 2026-10-25: DOSBox-Daum honours it; DOSBox Staging ignores it (there the DOS
# command MIXER CDAUDIO n works); DOSBox-X reads the volume as n/64, so there
# CDVOL=25 is full volume and more is louder. Daum and X ignore MIXER CDAUDIO.
# With no MSCDEX drive every call passes through. Plan:
# .claude/plans/2026-10-24-cd-audio-shim.md. --log builds write CDSHIM.LOG.
#
# Entries 3 and 4 run inside VICEROY's timer interrupt: no DOS or MSCDEX there.
# Entries 0-2 run from the game's main code. All data sits in this one segment.
#
# Build: cdshim/build.py (assembles this with GNU as and adds the MZ header).
        .intel_syntax noprefix
        .code16
        .arch i286                         # no 386 opcodes (VICEROY needs a 186)
        .text
        .globl start
start:
        .fill 16, 1, 0
        .ascii "CD shim 2026-10-24  "      # tag, 20 bytes like the originals
        .ascii "NO"
        .word 0
        .org 0x28
code_seg:  .word 0                         # relocated: our segment
data_seg:  .word 0                         # relocated: our segment
image_size: .word 0                        # bytes VICEROY keeps; set by build.py
        .word 0x64                         # as in all four originals
        .word 11                           # entry count
        .org 0x32
entries: .word e0, e1, e2, e3, e4, e5, e6, e7, e8, e9, e10
        .org 0x48
fm_name: .fill 13, 1, 0                    # 8.3 name of the FM driver; build.py
        .org 0x55
log_on:  .byte 0                           # 1 = write CDSHIM.LOG after each command
        .org 0x56
loaded:  .byte 0                           # 1 = FM driver loaded, entries valid
fm_seg:  .word 0                           # its DOS memory block
fm_ent:  .fill 11 * 4, 1, 0                # its entries: offset, segment
parblk:  .word 0, 0                        # 4B03h: load segment, relocation factor
save_ss: .word 0
save_sp: .word 0
        .org 0x8d
track_tab: .fill 32, 1, 0                  # CD track per command 20h-3Fh, 0 = FM; build.py
cd_ok:   .byte 0                           # 1 = MSCDEX drive with audio tracks found
cd_playing: .byte 0                        # 1 = we started a track that may still play
cd_drive: .word 0                          # MSCDEX drive number (0 = A:)
cd_err:  .byte 0                           # why cd_play last refused (0 = it played)
cd_stat: .word 0                           # the last MSCDEX status word
cd_calls: .byte 0                          # cd_busy calls, for asking every 16th
fm_mflag: .word 0                          # FM data byte its dispatcher sets to FFh
                                           # before every song (PSOUND 1CAh, ASOUND 24Dh;
                                           # 0 = none). PSOUND plays an effect's sample
                                           # only while it is FFh; songs 28-31, 3D lower it.

# --- entries ----------------------------------------------------------------

# entry 0, init: load the FM driver, find the CD, then jump into the FM init
# with the caller's registers and stack (it reads 7 argument words).
e0:     push ax
        push bx
        push cx
        push dx
        push si
        push di
        push bp
        push ds
        push es
        mov bp, sp
        push cs
        pop ds
        cmp byte ptr log_on, 0
        je 1f
        push ss                            # log the 7 argument words (shimtest uses them)
        pop ds
        lea si, [bp + 22]
        push cs
        pop es
        mov di, offset log_init
        mov cx, 7
        cld
        rep movsw
        push cs
        pop ds
1:      call load_fm
        cmp byte ptr loaded, 0
        je 2f
        call cd_init
        cmp byte ptr log_on, 0
        je 2f
        call write_log
2:      pop es
        pop ds
        pop bp
        pop di
        pop si
        pop dx
        pop cx
        pop bx
        pop ax
        cmp byte ptr cs:loaded, 0
        je none
        jmp dword ptr cs:fm_ent

# entry 1, command: the argument word is above the far return address.
e1:     cmp byte ptr cs:loaded, 0
        je none
        push bp
        mov bp, sp
        push bx
        push cx
        push dx
        push si
        push di
        push ds
        push es
        push cs
        pop ds
        mov ax, [bp + 6]
        cmp byte ptr log_on, 0
        je 1f
        push ax
        call log_cmd
        cmp ax, 8                          # command 8 is written only every 256th
        jne 6f                             # time: the menu sends it many times a
        cmp byte ptr log_pos, 0            # second, and a file write each time
        jne 2f                             # made the game crawl (2 fps)
6:      call write_log
2:      pop ax
1:      cmp byte ptr cd_ok, 0
        je pass
        cmp ax, 8
        je query
        cmp ax, 3
        jbe stop_pass                      # 0 reset, 1 stop all, 2 fade, 3 stop music
        cmp ax, 0x20
        jb pass
        cmp ax, 0x3f
        ja pass
        mov bx, ax
        mov bl, track_tab - 0x20[bx]
        or bl, bl
        jz stop_pass                       # music with no track: FM plays it
        cmp bl, cb_disk + 2                # a track past the disc's last one (the
        jbe 7f                             # jingles 28-30 on the 27-track disc):
        mov byte ptr cd_err, 1             # FM plays it, exactly as with no track
        jmp stop_pass
7:      call cd_play
        jc pass                            # the CD refused: FM plays it
        xor ax, ax
        jmp ret_ax
query:  push 8
        call dword ptr fm_ent + 4
        add sp, 2
        push cs
        pop ds
        push ax
        call cd_busy
        pop bx
        cmp byte ptr log_on, 0
        je 3f
        test bx, bx                        # count the answers: FM busy, CD busy, idle
        jz 4f
        inc word ptr log_qfm
4:      test ax, ax
        jz 5f
        inc word ptr log_qcd
5:      mov cx, ax
        or cx, bx
        jnz 3f
        inc word ptr log_qidle
3:      or ax, bx
        jmp ret_ax
stop_pass:
        call cd_stop
pass:   pop es
        pop ds
        pop di
        pop si
        pop dx
        pop cx
        pop bx
        pop bp
        jmp dword ptr cs:fm_ent + 4
ret_ax: pop es
        pop ds
        pop di
        pop si
        pop dx
        pop cx
        pop bx
        pop bp
        retf

# entry 2, shutdown (no arguments): stop the CD, call the FM driver's shutdown,
# keep its AX, write the log, free the FM driver's memory.
e2:     cmp byte ptr cs:loaded, 0
        je none
        push bx
        push cx
        push dx
        push si
        push di
        push ds
        push es
        push cs
        pop ds
        call cd_stop
        call dword ptr fm_ent + 8
        push cs
        pop ds
        mov byte ptr loaded, 0             # the timer may still call 3 and 4
        push ax
        cmp byte ptr log_on, 0
        je 2f
        call write_log
2:      mov es, fm_seg
        mov ah, 0x49
        int 0x21
        pop ax
        pop es
        pop ds
        pop di
        pop si
        pop dx
        pop cx
        pop bx
        retf

e3:     cmp byte ptr cs:loaded, 0
        je none
        jmp dword ptr cs:fm_ent + 12
e4:     cmp byte ptr cs:loaded, 0
        je none
        jmp dword ptr cs:fm_ent + 16
e5:     cmp byte ptr cs:loaded, 0
        je none
        jmp dword ptr cs:fm_ent + 20
e6:     cmp byte ptr cs:loaded, 0
        je none
        jmp dword ptr cs:fm_ent + 24
e7:     cmp byte ptr cs:loaded, 0
        je none
        jmp dword ptr cs:fm_ent + 28
e8:     cmp byte ptr cs:loaded, 0
        je none
        jmp dword ptr cs:fm_ent + 32
e9:     cmp byte ptr cs:loaded, 0
        je none
        jmp dword ptr cs:fm_ent + 36
e10:    cmp byte ptr cs:loaded, 0
        je none
        jmp dword ptr cs:fm_ent + 40

# no FM driver: every call returns 0 (silence, "not playing")
none:   xor ax, ax
        retf

# --- loading the FM driver ----------------------------------------------------

# load_fm: DS = CS. As VICEROY does it (file 0x12879): largest block minus 2,
# 4B03h, shrink to what the driver's header asks for. Sets loaded = 1.
load_fm:
        mov ah, 0x48
        mov bx, 0xffff
        int 0x21                           # fails; BX = largest free block
        sub bx, 2
        jbe 9f
        mov ah, 0x48
        int 0x21
        jc 9f
        mov fm_seg, ax
        mov parblk, ax
        mov parblk + 2, ax
        push cs
        pop es
        mov bx, offset parblk
        mov dx, offset fm_name
        mov save_ss, ss
        mov save_sp, sp
        mov ax, 0x4b03
        int 0x21
        cli                                # DOS may not keep SS:SP over EXEC
        mov ss, cs:save_ss
        mov sp, cs:save_sp
        sti
        push cs
        pop ds
        jc 8f
        mov es, fm_seg
        cmp word ptr es:0x30, 11           # the shape VICEROY expects
        jne 8f
        mov ax, es:0x2c                    # keep [2Ah] - seg + ceil([2Ch]/16) + 8
        add ax, 15
        rcr ax, 1
        shr ax, 3
        add ax, es:0x2a
        sub ax, fm_seg
        add ax, 8
        mov bx, ax
        mov ah, 0x4a
        int 0x21
        jc 8f
        mov dx, es:0x28                    # relocated code segment
        mov si, 0x32
        mov di, offset fm_ent
        mov cx, 11
3:      mov ax, es:[si]
        mov [di], ax
        mov [di + 2], dx
        add si, 2
        add di, 4
        loop 3b
        mov si, es:0x34                    # entry 1, the dispatcher: find its
        mov cx, 0x80                       # "mov byte ptr [X], 0FFh" before
4:      cmp word ptr es:[si], 0x06c6       # "call cs:[bx + music table]"
        jne 5f                             # (C6 06 lo hi FF 2E FF 97)
        cmp word ptr es:[si + 4], 0x2eff
        jne 5f
        cmp word ptr es:[si + 6], 0x97ff
        jne 5f
        mov ax, es:[si + 2]
        mov fm_mflag, ax
        jmp 6f
5:      inc si
        loop 4b
6:      mov byte ptr loaded, 1
        ret
8:      mov es, fm_seg
        mov ah, 0x49
        int 0x21
9:      ret

# --- CD audio through MSCDEX (all with DS = CS; never from entries 3, 4) -------

# cd_init: find the first MSCDEX drive and its audio track range.
cd_init:
        mov ax, 0x1500
        xor bx, bx
        int 0x2f
        test bx, bx
        jz 9f
        mov cd_drive, cx
        mov si, offset cb_disk
        mov cx, 7
        call ioctl
        test ah, 0x80                      # error bit
        jnz 9f
        mov al, cb_disk + 1
        cmp al, cb_disk + 2
        ja 9f
        mov byte ptr cd_ok, 1
        call cd_volume
9:      ret

# cd_volume: CDVOL=n (0-100) in the environment of the program that called
# init (VICEROY) -> MSCDEX audio channel control, left and right at n * 255 / 100.
# The emulators' own mixer settings do not reach the CD audio in DOSBox-Daum
# and DOSBox-X; this request does. Not set: nothing is sent.
cd_volume:
        push es
        cld
        mov ah, 0x62                       # BX = current PSP
        int 0x21
        mov es, bx
        mov es, es:0x2c                    # its environment: NAME=VALUE\0 ... \0
        xor di, di
1:      cmp byte ptr es:[di], 0
        je 9f                              # end: CDVOL not set
        mov dx, di
        mov si, offset s_cdvol
        mov cx, 6
        repe cmpsb
        je 2f
        mov di, dx                         # not this one: skip past its 0
        xor al, al
        mov cx, 0xffff
        repne scasb
        jmp 1b
2:      xor ax, ax                         # decimal value, capped at 100
3:      mov bl, es:[di]
        sub bl, '0'
        cmp bl, 9
        ja 4f
        mov cl, 10
        mul cl
        xor bh, bh
        add ax, bx
        inc di
        cmp ax, 100
        jbe 3b
        mov ax, 100
4:      mov cx, 255
        mul cx
        mov cx, 100
        div cx
        mov cb_vol + 2, al
        mov cb_vol + 4, al
        mov byte ptr rq_io + 2, 0x0c       # IOCTL output, then back to input
        mov si, offset cb_vol
        mov cx, 9
        call ioctl
        mov byte ptr rq_io + 2, 3
9:      pop es
        ret

# cd_play: BL = track. Stops the FM music and the CD, then plays the track
# from its start to the next track's start (or the lead-out). CF = refused.
cd_play:
        push bx
        push 1                             # FM: stop all, as a song command does
        call dword ptr fm_ent + 4
        add sp, 2
        push cs
        pop ds
        mov bx, fm_mflag                   # and set the byte its dispatcher sets
        test bx, bx                        # before every song: without it a song
        jz 7f                              # that lowered it (York on FM) keeps
        push es                            # the effect samples off for good
        mov es, fm_seg
        mov es, es:0x2a                    # the FM driver's data segment
        mov byte ptr es:[bx], 0xff
        pop es
7:      call cd_stop
        pop bx
        mov byte ptr cd_err, 1             # track out of range
        cmp bl, cb_disk + 1
        jb 8f
        cmp bl, cb_disk + 2
        ja 8f
        mov byte ptr cd_err, 2             # track info refused
        mov cb_trk + 1, bl
        mov si, offset cb_trk
        mov cx, 7
        call ioctl
        test ah, 0x80
        jnz 8f
        mov byte ptr cd_err, 3             # a data track
        test byte ptr cb_trk + 6, 0x40
        jnz 8f
        mov ax, cb_trk + 2
        mov play_start, ax
        mov ax, cb_trk + 4
        mov play_start + 2, ax
        mov si, offset play_start
        call rb2hsg
        mov hsg_start, ax
        mov hsg_start + 2, dx
        mov si, offset cb_disk + 3         # the lead-out after the last track
        cmp bl, cb_disk + 2
        je 1f
        inc bl
        mov cb_trk + 1, bl
        mov si, offset cb_trk
        mov cx, 7
        call ioctl
        mov byte ptr cd_err, 4             # next track info refused
        test ah, 0x80
        jnz 8f
        mov si, offset cb_trk + 2
1:      call rb2hsg
        sub ax, hsg_start
        sbb dx, hsg_start + 2
        mov play_count, ax
        mov play_count + 2, dx
        mov bx, offset rq_play
        call request
        mov byte ptr cd_err, 5             # play refused
        test ah, 0x80
        jnz 8f
        mov byte ptr cd_err, 0
        mov byte ptr cd_playing, 1
        clc
        ret
8:      stc
        ret

# cd_stop: stop a track we started.
cd_stop:
        cmp byte ptr cd_playing, 0
        je 9f
        mov bx, offset rq_stop
        call request
        mov byte ptr cd_playing, 0
9:      ret

# cd_busy: AX = 1 while a track we started still plays, else 0. MSCDEX is
# asked on every 16th call only: the main menu sends command 8 some 270 times
# a second. Counted by calls, not by a clock: VICEROY takes over the timer
# and the BIOS tick count (0:46Ch) stands still while it runs.
cd_busy:
        xor ax, ax
        cmp byte ptr cd_playing, 0
        je 9f
        inc ax                             # AX = 1: still playing, as last asked
        inc byte ptr cd_calls
        test byte ptr cd_calls, 15
        jnz 9f
        mov si, offset cb_q
        mov cx, 11
        call ioctl
        test ah, 2                         # busy bit (0200h) of the status word
        mov ax, 1
        jnz 9f
        mov byte ptr cd_playing, 0         # the track has ended
        xor ax, ax
9:      ret

# ioctl: IOCTL input, control block at SI, length CX. Returns status in AX.
# Keeps BX (cd_play holds the track in BL).
ioctl:  mov word ptr rq_io + 14, si
        mov word ptr rq_io + 16, ds
        mov rq_io + 18, cx
        push bx
        mov bx, offset rq_io
        call request
        pop bx
        ret
# request: send the request header at BX to the drive. Returns status in AX.
request:
        mov word ptr [bx + 3], 0
        push ds
        pop es
        mov cx, cd_drive
        mov ax, 0x1510
        int 0x2f
        mov ax, [bx + 3]
        mov cd_stat, ax
        ret

# rb2hsg: Red Book address at SI (frame, sec, min, 0) -> sector in DX:AX
rb2hsg: push bx
        push cx
        mov al, [si + 2]
        xor ah, ah
        mov cx, 4500
        mul cx
        mov bx, ax
        mov cx, dx
        mov al, [si + 1]
        mov dl, 75
        mul dl
        add bx, ax
        adc cx, 0
        mov al, [si]
        xor ah, ah
        add bx, ax
        adc cx, 0
        sub bx, 150
        sbb cx, 0
        mov ax, bx
        mov dx, cx
        pop cx
        pop bx
        ret

cb_disk: .byte 0x0a, 0, 0, 0, 0, 0, 0      # audio disk info: low, high, lead-out
cb_trk:  .byte 0x0b, 0, 0, 0, 0, 0, 0      # audio track info: track, start, control
cb_q:    .byte 0x0c, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0   # Q channel
cb_vol:  .byte 3, 0, 0xff, 1, 0xff, 2, 0, 3, 0     # audio channel control: input, volume x 4
s_cdvol: .ascii "CDVOL="
        .balign 2
hsg_start: .long 0
rq_io:   .byte 26, 0, 3                    # IOCTL input
         .word 0
         .fill 8, 1, 0
         .byte 0
         .long 0
         .word 0, 0
         .long 0
rq_play: .byte 22, 0, 0x84                 # Play Audio, Red Book addressing
         .word 0
         .fill 8, 1, 0
         .byte 1
play_start: .long 0
play_count: .long 0
rq_stop: .byte 13, 0, 0x85                 # Stop Audio
         .word 0
         .fill 8, 1, 0

# --- command log (--log builds) -------------------------------------------------

# log_cmd: AX = command. Count it (slot = low byte; 80xxh commands -> A0h + n)
# and keep the last 256 in a ring. Uses AX, BX. Called from entry 1 only:
# the game's main code, never the timer interrupt, so DOS may be called there.
log_cmd:
        mov bx, ax
        test ah, 0x80
        jz 4f
        and bl, 0x1f
        add bl, 0xa0
4:      xor bh, bh
        shl bx, 1
        inc word ptr log_count[bx]
        mov bx, log_pos
        shl bx, 1
        mov log_ring[bx], ax
        inc byte ptr log_pos
        ret

# write_log: DS = CS. CDSHIM.LOG = counts[256], ring[256], position, init
# arguments[7], cd_ok, command 8 answers (FM busy, CD busy, idle) (words).
# Written after every command but 8, and at shutdown: a game that never exits
# (the demo) still leaves a current log. Uses AX, BX, CX, DX.
write_log:
        mov al, cd_ok
        mov log_cd, al
        mov ah, 0x3c
        xor cx, cx
        mov dx, offset log_file
        int 0x21
        jc 5f
        mov bx, ax
        mov ah, 0x40
        mov cx, log_end - log_count
        mov dx, offset log_count
        int 0x21
        mov ah, 0x3e
        int 0x21
5:      ret

log_file: .asciz "CDSHIM.LOG"
        .balign 2
log_count: .fill 256, 2, 0
log_ring:  .fill 256, 2, 0
log_pos:   .word 0
log_init:  .fill 7, 2, 0
log_cd:    .word 0
log_qfm:   .word 0                         # command 8 answers: FM driver busy
log_qcd:   .word 0                         #                    CD busy
log_qidle: .word 0                         #                    neither (a new song may start)
log_end:
image_end:
