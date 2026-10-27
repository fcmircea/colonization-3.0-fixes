# Dead ends

Recorded so the next person does not spend a night re-deriving them. Each of
these looked correct and was wrong, or was correct but useless.

## `DS:0x1f54` is not the boycott state

Static analysis makes `DS:0x1f54` look definitive. Its reference set is exactly
what a boycott bitmap should have:

- `0x6F54C` — `mov [0x1f54], 0`  (clear all — this is Jakob Fugger's ability)
- `0x6F554` — `or  [0x1f54], ax` (set one bit)
- `0x6F56E` — `and [0x1f54], ax` (clear one bit — tea party / lift)
- `0x6F57E` — `is_boycotted(good)`, reading at `0x6F58D`
- `0x745AB` — a UI loop greying out boycotted goods

At runtime it reads `0x0000` in the middle of a game with three goods
boycotted. It is a **transient scratch copy**, populated and cleared during
specific per-colony processing. Do not build a patch on it, and do not trust a
watchpoint on it to fire during the drop-off — it does not fire at all.

The authoritative state is the nation record in RAM. In one measured
configuration it sat at physical `0x2981C` holding `0x187F`, findable by
scanning for the word preceded by `01 00` (signature `01 00 7F 18`). Its address
moves with DOS memory layout — notably, loading a mouse driver shifts it — so it
must be re-located every run.

Watching that word is also a disappointment: in that run it was read exactly
**once**, at load time, by a `rep movsw` at file `0x10374` copying the record.
(Correction, 2026-10-19: `skip_test` reads the nation record's `+0x20` on every
colony-stop unload, at the copy that `DS:84FC` points to. The earlier watch was
probably on another copy; not re-checked.)

## `0x6E43B` in the route processor — tested, does not fix the bug

The routine at `0x6E3D0` references the `ROUTELOOP` message and contains a loop
that looks precisely like the culprit:

```
0x6E432:  mov  cx, ax                ; good index
0x6E439:  shl  dx, cl                ; dx = 1 << good
0x6E43B:  and  dx, [0x1f54]          ; dx = bit & boycott
0x6E43F:  push dx                    ; arg3 = boycott flag
0x6E441:  push ax                    ; arg2 = good+1
0x6E443:  push es:bx                 ; arg1 = unit
0x6E445:  call 0x6F83A               ; unload handler thunk
```

It computes a per-good boycott flag and passes it straight into the unload
handler. It is the obvious patch, and an earlier round of analysis recommended
exactly that.

It was built (`23 16 54 1F` → `31 D2 90 90`, i.e. `xor dx,dx; nop; nop`) and
tested against the reproduction save. **The drop-off still failed.** This
routine is not on the arrival-unload path.

## Do not patch the handler-side branch at `0x2A786`

Inside the unload routine, the third parameter selects two paths:

- `[bp+0xA] == 0` → deposit everything, reaching the store at `0x2A874`
- `[bp+0xA] != 0` → ask the player how much (key HOWMUCH2 at `DS:0CCD`, then
  the number dialog), at `0x2A781` / `0x2A786` / `0x2A78A`

An earlier reading took this branch for a sale/boycott gate. It is not: the
boycott test is `skip_test` in the caller (see `docs/ANALYSIS.md`). Changing
the branch does not touch the bug and would bring up the prompt on a route.
Patch the caller instead.

## The caller cannot be found by unwinding the stack

The unload handler is invoked through a relocated far call inside a resident
coroutine trampoline (around file `0x14A1F`, a `lcall 0:0` patched at runtime).
Both segment and offset come from task state. Consequently:

- The saved-`BP` frame chain collapses — every frame reports the same return
  address into the trampoline, at any depth.
- Scanning the deep stack for plausible `CS:IP` pairs preceded by a call
  instruction yields nothing usable.

The caller at `0x411D8` was found by different means, not by unwinding.

## No free code space next to the C runtime

The Romanian upper-case fix (`ro-upcase`) needed about 30 more bytes in the
runtime code segment `0D1Dh` (file `0xF5D0`); a far jump elsewhere would need
a new MZ relocation, which a same-size patch cannot add. Two ideas failed:

- **Dead runtime functions.** Every function between `0x10000` and `0x10800`
  has far callers (the linker pulls in only what is used). `strlwr` has two:
  a name that the text engine lowercases then re-capitalises (`0x3DACD`), and
  a file mode string (`0x76E87`). None can go.
- **The 905 zero bytes before the data segment** (`0x1D617`-`0x1D99F`), after
  the overlay thunks. A RAM dump after loading a save shows 70 of them
  written: overlay manager state. Only the new-game screen still has them
  all zero. Do not put code there.

What worked: `strlwr`, `strupr` and `_fstrupr` share one loop with the
letter bounds in CX, which frees enough room in place.

## Emulator traps that cost real time

- **Ubuntu's packaged QEMU 8.2.2 aborts** on guest disk writes when using
  `vvfat` (`block/vvfat.c`, `get_cluster_count_for_direntry`, during
  `try_commit`). This presents as the VM dying mid-run and is easy to misread as
  the sandbox killing the process. Build QEMU from source and use a real FAT16
  disk image instead of `vvfat`.
- **Escape quits the game** from the main map — it lands on "Exit to DOS". An
  automation loop that presses Escape to back out of stray dialogs will silently
  end the session and produce a run full of nothing.
- QEMU's HMP `mouse_move` is **relative** and interacts badly with mouse
  acceleration, so large jumps land unpredictably — frequently on the top-left
  GAME menu or the wrong dialog option. Drive with the keyboard wherever
  possible.
- `socat -T 4` needs the space. `socat -T4` is silently wrong and every monitor
  command quietly does nothing, which looks exactly like a hung VM.
- The `pmemsave` filename must be quoted or the monitor rejects the expression.
