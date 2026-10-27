# Root-cause analysis

Target: `VICEROY.EXE`, Sid Meier's Colonization, MS-DOS version 3.0
(7-Feb-95, 494,910 bytes, MD5 `0f5d5b0063721fbc6aca314e5a43ddaf`).

## Binary shape

16-bit real-mode MZ executable built with Microsoft C, no DOS extender. The file
is roughly a 132 KB resident load module followed by ~362 KB of demand-loaded
**overlays**. 2,260 relocations. Entry `110d:071d`.

Overlay code is reached through thunk tables via far calls of the form
`lcall 181f:xxxx`, `lcall 191f:xxxx`, `lcall 1a1f:xxxx`. This matters: overlay
segments load at bases that differ from the resident base, so a far address seen
at runtime cannot be converted to a file offset with a single global delta.

Useful constant: the **resident** module loads such that

```
file_offset = (segment * 16 + offset) - 0x32A0
```

That relation was calibrated by matching a runtime memory window back to the
file. It holds for resident code only.

## The bug

Boycott state is a 16-bit bitmap, one bit per good, stored per nation. Furs are
bit 4. In the reproduction save the Dutch player is `nation[3]` and the bitmap
reads `0x187F` (furs boycotted); the control reads `0x186F`.

In the save file the word is at offset `+0x20` of the nation record. Nation
records are 316 bytes each and follow the unit table (units are 28 bytes each;
the unit and colony counts are the words at file `0x2C` and `0x2E`, and the
colony table starts at `0x186` with 202-byte records).

A wagon train on a trade route from New Amsterdam to Fort Orange loads 100 furs,
travels, arrives — and never deposits. Fort Orange's fur stock does not change.

## Where the goods are actually deposited

The colony commodity store is a 16-word array `[food, sugar, tobacco, cotton,
furs, lumber, ore, silver, horses, rum, cigars, cloth, coats, trade_goods,
tools, muskets]` at offset `0x9A` inside the colony record.

The cargo unload routine begins at file `0x2A6A6` (`enter 0xe,0`) and its
deposit instruction is:

```
0x2A874:  01 80 9A 00     add word ptr [bx+si+0x9A], ax
```

with `bx` = colony pointer (loaded from near data `[0x8542]`), `si` = good index
× 2, and `ax` = the amount being unloaded.

That single instruction is the ground truth for "cargo was delivered", and
watching it is what settled the diagnosis.

## The decisive experiment

Two saves identical except for one bit — the furs boycott flag — were run under
QEMU with a **hardware write-watchpoint** on Fort Orange's fur word.

**Boycott lifted (control).** The watchpoint fires with `AX = 0x64` (100), fur
stock 31 → 147 → 181. The cargo is deposited. Reading the handler's stack frame
at that moment shows it was entered with `good = 4` (furs) and its third
parameter `flag = 0`.

**Boycott set (repro).** The watchpoint fires only with `AX = 8` and `AX = 10`,
once per turn — that is fur *production* from the colony's trappers, a different
instruction entirely. The deposit store never executes, even though the
on-screen banner confirms "Wagon Train arrives in Fort Orange".

A **read**-watchpoint on the same word in the broken run then showed the decisive
fact: none of the unload routine's own read instructions ever execute. The
handler is **never entered** for the boycotted good.

That rules out the intuitive explanation. The unload routine does not inspect
the boycott flag and refuse. Something upstream declines to call it.

## The caller

The caller is a loop over the stop's unload list, at file `0x411D8`–`0x41248`.
It is inside the route-stop function that starts at `0x41080`. That function's
argument `[bp+6]` is the **unit** index, not a colony: the code reads the unit
record at `DS:0x3144 + unit*0x1C` (for example the orders byte at `0x314C`,
where 2 = trade route). `[bp-0x18] == 999` means the stop is Europe.

```
0x411D8:  mov  word [bp-0x1E], 0        ; i = 0
0x411DD:  jmp  0x411F9
0x411F6:  inc  word [bp-0x1E]           ; i++
0x411F9:  mov  ax, [bp-0x42]            ; ax = cargo_count
0x411FC:  cmp  [bp-0x1E], ax
0x411FF:  jge  0x41248                  ; loop done
0x41201:  push [bp-0x1E]
0x41204:  lcall 1a1f:021c               ; good = unload_list_get(i)
0x4120C:  mov  [bp-0x20], ax
0x4120F:  push ax
0x41210:  lcall 191f:0cd8               ; test = skip_test(good)
0x41218:  or   ax, ax
0x4121A:  jne  0x411F6                  ; if (test) { i++; continue; }   <-- THE BUG
0x4121C:  push [bp-0x20]
0x4121F:  push [bp+6]
0x41222:  lcall 181f:0c2c               ; h = find_hold(unit, good)
0x4122D:  or   ax, ax
0x4122F:  jl   0x411F0                  ; no hold with this good -> next i
0x41231:  cmp  [bp-0x18], 999
0x41236:  jne  0x411E0                  ; colony: lcall 191f:0594 (unit, good, 0)
0x41240:  lcall 191f:0d02               ; Europe: (unit, good, 0)
```

The real unload is `191f:0594(unit, good, 0)`; its third argument is the
`flag = 0` seen in the handler's stack frame above. `find_hold` is called
again after each unload, so every hold holding that good is emptied.

When the good is boycotted, `skip_test` returns non-zero and the `jne` at
`0x4121A` jumps past the unload call. The cargo is silently left on board, which
is exactly the observed symptom: pickup fine, travel fine, drop-off nothing, no
message.

## The fix

```
0x4121A:  75 DA   jne 0x411F6   ->   90 90   nop / nop
```

The loop stops skipping items and the unload proceeds.

This is caller-side. The manual European sale and the Custom House reach the
unload machinery through *different* callers and are not touched; that was
confirmed in play. **But the same loop also serves a route's Europe stop**, and
there the skip is correct. This NOP removed it too, so a route ship sold
boycotted goods in London. It was replaced by `traderoute-boycott-v2` in
October 2026 and removed from `patches.json`; see "skip_test, and the v2 fix"
below.

## Corroboration

The v3.0 README lists, among its fixes, *"The 'U'nload key working during a
boycott."* MicroProse hit this same defect class from the manual-unload entry
point and fixed that one caller. The automated trade-route caller at `0x411D8`
was missed. `GAME.TXT` still carries the matching message key `@SOMEBOYCOTT`
("Some of the cargo could not be unloaded because of a parliamentary boycott"),
which is never raised on the automated path — hence the silence.

## Confirmed offsets

| What | File offset | Bytes / note |
|---|---|---|
| **Patch site** (per-cargo skip branch) | `0x4121A` | `75 DA` → `90 90` |
| Route-stop function | `0x41080` | `[bp+6]` = unit index |
| Unload loop start | `0x411D8` | `i = 0` |
| `unload_list_get(i)` | `0x41204` | `lcall 1a1f:021c` |
| `skip_test(good)` | `0x41210` | `lcall 191f:0cd8` = overlay 4 offset `05E8`, file `0x30B38` (see below) |
| `find_hold(unit, good)` | `0x41222` | `lcall 181f:0c2c`, < 0 = none |
| `unload(unit, good, 0)`, colony | `0x411E8` | `lcall 191f:0594` |
| Unload to Europe | `0x41240` | `lcall 191f:0d02` |
| Hold-count gate (second bug) | `0x41384` | `74 03` → `90 90`, see below |
| Colony load call | `0x41354` | `lcall 191f:07f8 (unit, good, 1, 0)` |
| Load weight table | `DS:0x84BC` | byte `[nation*16 + good]` |
| Unit table | `DS:0x3144` | 28 bytes per unit, holds used at `+0x0C` |
| Unload routine entry | `0x2A6A6` | `enter 0xe,0`, far, `retf` |
| **Deposit store** | `0x2A874` | `add [bx+si+0x9A], ax` |
| Unload "how much?" prompt | `0x2A781`, `0x2A786` | third argument: ask the amount (HOWMUCH2); not a boycott gate |
| Fur production writer | overlay `IP=0x09A2` | colony output, not cargo |
| Load-time nation-record copy | `0x10374` | `rep movsw` |
| `SOMEBOYCOTT` message key string | `0x1E9C7` | |

## Second bug: a later pick-up stop is skipped (`traderoute-topup`)

This one is **not** caused by the boycott. It was first taken for a boycott
problem, so it is recorded here.

### Symptom

A 3-stop sea route: load sugar at Santo Domingo, load sugar at Veracruz,
unload sugar at Isabella. The caravel (2 holds) never takes sugar at Veracruz.
Santo Domingo gives it 124 sugar, stored as 100 + 24, so the caravel reaches
Veracruz with both holds in use and 76 free space in the second one.

### Mechanism

The colony load path starts at `0x41296`. Its loop test is at `0x41366`:

```
0x41366:  imul bx, [bp+6], 0x1C
0x4136A:  mov  al, [bx+0x3150]          ; holds used (unit +0x0C)
0x4136E:  mov  bl, [bx+0x3146]          ; unit type (unit +0x02)
          ...                           ; bx = type * 14
0x41380:  cmp  [bx+0x5237], al          ; capacity == holds used?
0x41384:  je   0x41389                  ; yes -> load nothing   <-- SECOND BUG
0x41386:  jmp  0x4129E                  ; loop body
```

The loop body builds a weight for each good in the stop's load list,
`byte DS:0x84BC[nation*16 + good] * colony.stock[good]`, sorts them
(`191f:0ed0`), stops if the best weight is 0, and calls
`191f:07f8(unit, good, 1, 0)`. It runs again only while `07f8` returns 0.

The capacity test counts holds **in use**, not free space. A ship with a
part-full hold in every slot is treated as full.

The Europe load path (`0x4124F`–`0x41294`) jumps straight to `0x41389` and
does not use this test.

### Measurements (QEMU, 2026-09-24)

Same save, patched EXE (`traderoute-boycott`), SPACE for 420 s (about 8
turns, 2 route cycles), RAM read every 6 steps with
`harness/run_probe.sh`. Raw logs: `docs/data/traderoute-2026-09-24/`.

| run | change | at Veracruz |
|---|---|---|
| boy | none, sugar boycotted (`0x5B73`) | holds 100 + 24; Veracruz sugar stays 100 |
| nob | sugar boycott bit off (`0x5B71`) | same as boy, same values in every sample |
| C | stop 1 loads nothing | arrives empty, loads 100 |
| D | stop 1 loads 40 cotton | loads 100 sugar into the free hold; next cycle 24 |
| X | + `0x41384` `74 03` → `90 90` | tops 24 up to 100 (Veracruz 100 → 24); next cycle 24 → 72 |
| E | X + stop 1 loads horses (both holds 100 horses) | loads nothing, no hang, 3 cycles |
| XL | as X, 1200 s (about 24 turns) | 6 cycles, tops up every time (24 → 72, 24 → 48), no hang |

Wagon train, land route (runs W0 / W1, 600 s, about 12 trips each). Unit 1
was changed to a wagon train on a 2-stop land route: load cotton at Isabella
(105 cotton), then load cotton at Veracruz (60 cotton). Cotton is not
boycotted. The wagon leaves Isabella with 100 + 5.

| run | EXE | at Veracruz |
|---|---|---|
| W0 | boycott patch only | wagon stays 100 + 5; Veracruz keeps 60 on every visit |
| W1 | + `traderoute-topup` | 5 → 65, Veracruz 60 → 0; later visits load nothing (no stock), no hang |

So the same gate stops wagon trains, and the patch fixes it for them too.

Building that save needed one more field: unit byte `+0x17` holds the route
(low nibble, read at `0x75D4`) and the current stop, counted from 0 (high
nibble, read at `0x75FE`). A stop number past the end of the route reads an
empty stop, whose colony index 0 sends the unit to colony 0.

The boycott has no effect: boy and nob match, and C and D (both with sugar
boycotted) load at Veracruz when a hold is free. The weight table row for
Spain was the same in boy and nob.

Runs X and E show that `07f8` fills up a part-full hold of the same good,
and returns non-zero when nothing more fits. So the loop ends by itself once
the gate is gone.

### The fix (recommended)

```
0x41384:  74 03   je 0x41389     ->   90 90   nop / nop
```

Play-checked in October 2026: many long games with trade routes in
DOSBox-Daum, no hang.

## Overlays and thunks

The resident image ends at file `0x20665`. 31 overlays follow, one after
another. Each starts with a 16-byte header: `w0` = size in paragraphs (header
included), `w1` = header size in paragraphs, `w2` = `0x2BC`, `w4` = relocation
count. The relocation table follows the header; the code starts at
header + `w1`·16. The route code is overlay 8 (header `0x400F0`, code
`0x404B0`, ends `0x428D0`).

A thunk is 12 bytes: `9A AB 0D 0D 11` (call the overlay manager at
`110D:0DAB`), `EA <offset> 00 00`, then the overlay number. The thunk
`191F:0CD8` (file `0x1C2C8`) reads `… EA E8 05 00 00 04 00`: overlay 4, offset
`05E8`.

## skip_test, and the v2 fix

`skip_test` is overlay 4 offset `05E8`, file `0x30B38`:

```
0x30B3B:  mov  ax, 1
0x30B3E:  mov  cl, [bp+6]               ; good
0x30B41:  shl  ax, cl                   ; 1 << good
0x30B43:  mov  bx, [0x84FC]             ; the "current" nation record
0x30B47:  and  ax, [bx+0x20]            ; its boycott bitmap
0x30B4B:  retf
```

`DS:84FC` is written only by overlay 4 offset `0000` (file `0x30550`):
`[9E12] = n; [84FC] = 0x8808 + n*0x13C`. So it tests the boycott bit of
whichever nation was **selected last**. The route function selects the
unit's own nation only on the Europe branch (`0x4119A`: stop == 999, then the
call at `0x411AD`). At a colony stop it does not, so the test reads a stale
nation. That is the original bug: a colony delivery is refused when the last
selected nation has the good boycotted.

Proof (QEMU, 2026-10-19): `EB FE` at `0x30B38` hung at
`995B:05E8` while an English wagon unloaded at Jamestown; at that moment
`[84FC]` pointed at the Dutch record. With the pristine EXE the same wagon
delivered furs and cotton that were boycotted for England.

The manual `U` unload calls `skip_test` only on its Europe path (`0x4159D`).
So MicroProse meant the check for Europe only.

**v1 went too far.** With `0x4121A` NOPed, a route's Europe stop also stops
skipping. Test with a caravel on a route whose stop is Europe, carrying
100 furs (boycotted for England) and 100 sugar:

| build | furs | gold |
|---|---|---|
| pristine | stay on board | 4612 → 4792 (sugar only) |
| v1 (`0x4121A` NOP) | **sold** | 4612 → 4882 |
| v2 | stay on board | 4612 → 4792 |

No build hung. The boycott word stayed `0xD979` in all.

**v2** keeps the skip at Europe stops only. Two sites, no relocation word
in either (overlay 8 has 228; none there):

```
0x41215:  83 C4 02            add sp,2   ->  E9 AE 16   jmp 0x428C6
0x428C6:  00 x 9  (end of overlay 8)     ->  59         pop cx         ; cx = good
                                             D3 E8      shr ax,cl      ; 1 if boycotted
                                             22 46 E9   and al,[bp-17h]; high byte of stop:
                                                                        ; 03h Europe, 00h colony
                                             E9 49 E9   jmp 0x41218
0x41218:  or ax,ax / jne 0x411F6         (unchanged: skip when ax != 0)
```

Measured with v2 alone and with v2 + topup + idle-stir: the Europe stop keeps
the boycotted furs and sells the sugar (gold +180 only); at a colony stop,
with the stale nation's bitmap set so that pristine refuses, the wagon
delivers (cotton 0 → 5, furs 62 → 77). The 9 bytes are in RAM at overlay 8
offset `0x2416` in every run. Not yet tested: DOSBox and a long game.

## A route unload ignores the colony's storage (vanilla)

Players see a route dump goods into a full colony; the excess spoils at the end
of the turn. This is the original game, not one of these patches.

- Capacity is file `0x8D00` (thunk `181F:0D3A`): 100 if colony byte `+0x95`
  (warehouse level) is 0, else (level + 1) × 100.
- The unload routine (`0x2A6A6`) compares stock + amount with capacity at
  `0x2A7DB`, but it only warns, and only when the good is not food and
  `DS:0890` is set. `0890` is set only inside the colony screen (`0x2C60F`),
  so it is 0 on a route. Then everything is deposited.
- Runtime: Jamestown furs set to 195 (capacity 200), a route unload made it
  210 with no prompt.
- The manual `U` unload on the map has its own inline code (`0x41520`-`0x415E8`):
  it shows WAREHOUSEFULL ("Unload anyway") but does not cap the amount either.
  Not changed by the patch below.

### The cap (`traderoute-unload-cap` + `-loop`, October 2026)

Who calls the unload routine. The route loop (`0x411E8`, flag 0), and three
colony-screen paths through the overlay's local stub `0x2C9C9` (`ljmp
191F:0594`, called at `0x2AD5E`, `0x2BA1B`, `0x2BFD7`; a byte search for the
far call finds only the route). Every one of those chains starts in the
colony screen `0x2C5D4`, which sets `[0890]` = 1 at `0x2C60F` and clears it at
`0x2C965`, the only two writers. So inside the routine `[0890]` = 0 means:
called by the route loop. Two colony-screen callers also pass flag 0, so the
flag cannot tell them apart.

The routine already knows how to unload part of a hold. Its "how much?" path
(`0x2A79E`-`0x2A7C1`) takes `ax` = amount wanted and sets `[bp-0Ah]` = amount
- wanted, `[8DC4h]` = min(wanted, amount); wanted <= 0 returns without a
change; otherwise it re-enters the capacity check. The deposit at `0x2A854`
then removes the hold (`181F:0AEC`), adds `[8DC4h]` to the colony and gives
`[bp-0Ah]` back to the unit (`181F:0D58`, which tops up a hold of the same good
or takes a free one).

Site 1, `0x2A7CA` (35 bytes, the capacity check rewritten in place; `ax` is the
capacity returned by `181F:0D3A`):

```
2A7CA  mov si,[bp+8] / shl si,1 / mov bx,[8542]
2A7D3  sub ax,[bx+si+9Ah]     ; room = capacity - stock
2A7D7  cmp ax,[8DC4]          ; room >= amount: deposit all (0x2A854)
2A7DD  or si,si               ; food: deposit all
2A7E1  cmp word [0890],0      ; colony screen: the warning at 0x2A7ED, unchanged
2A7E8  inc word [bp-6]        ; return 2 if nothing fits
2A7EB  jmp 2A79E              ; partial unload of `room`
```

Site 2, `0x411F0` (route loop): `cmp [bp-1Ah],0 / jge 0x4121C` becomes `or
ax,ax / jz 0x4121C`. The loop looks for another hold of the same good only
when the unload returned 0. The original loop ignores the return value and
re-finds the hold until none is left, so with goods kept on board it would
never end. After a capped deposit the routine returns 0, the loop re-finds the
remainder, the next call has room 0 and returns 2, and the loop moves on to
the next good. The Europe unload (`0x32914`) returns 0 after a sale; its
returns of 1 (no hold, the prompt) cannot happen from the route loop.

No relocation in either site; no public or Romanian patch site overlaps.
`tests/test_unload_cap.py` runs both sites in `x86mini`: over capacity on a
route (195 + 15 at 200: deposit 5, keep 10), full colony (no change, return
2), food, the colony-screen warning, and the loop's three cases.

QEMU, 2026-10-27 (KVM, `play/unload-cap`, untracked; the 2026-10-19
overflow save):

| EXE | Jamestown furs | wagon after the stop |
|---|---|---|
| `--all` (`1905cb45`) | 195 → 210 | empty |
| `--all` + cap pair (`40c58272`) | 195 → 200 | 1 hold, 10 furs |

15 SPACE steps (into the next turn), no hang. At the wagon's pick-up stop `traderoute-topup` filled
the kept hold to 20. Regression with the same EXE pair on the v2 scenarios:
Europe stop gold 4612 → 4792 and the boycotted 100 furs kept on board in both;
colony stop cotton 0 → 5, furs 62 → 77 in both. Not yet checked: DOSBox and a
long game.
