# Colonization (1994) — binary fixes

Small, verified binary patches for **Sid Meier's Colonization**, MS-DOS
version 3.0 (`VICEROY.EXE`, 494,910 bytes, md5 `0f5d5b0063721fbc6aca314e5a43ddaf`).
Each one was found by runtime measurement, not by guessing.

| patch | offset | status | what it fixes |
|---|---|---|---|
| `traderoute-boycott-v2` (+ `-tail`) | `0x41215`, `0x428C6` | **recommended** | trade routes silently refuse to deliver boycotted goods to a colony |
| `traderoute-topup` | `0x41384` | **recommended** | a later pick-up stop on a route loads nothing when every hold is in use, even part-full |
| `rng-idle-stir` | `0xC2FD` | **recommended** | the RNG is reset to the clock about 18 times a second, so battles close in time draw nearly the same number |
| `traderoute-unload-cap` (+ `-loop`) | `0x2A7CA`, `0x411F0` | **recommended** | a route unloads everything into a full colony and the excess spoils; now it stops at the storage limit and the rest stays on board |

Start from the original `VICEROY.EXE`. `--all` applies every **recommended**
patch (today: all of them). An **experimental** patch is applied only when named.

## Apply

```
python3 patch/apply_patch.py /path/to/COLONIZE/VICEROY.EXE --list
python3 patch/apply_patch.py /path/to/COLONIZE/VICEROY.EXE --status
python3 patch/apply_patch.py /path/to/COLONIZE/VICEROY.EXE --all
python3 patch/apply_patch.py /path/to/COLONIZE/VICEROY.EXE rng-idle-stir
python3 patch/apply_patch.py /path/to/COLONIZE/VICEROY.EXE --all --revert
```

No Python? `patch/Apply-Patch.ps1` does the same job with the same rules and
exit codes, on Windows PowerShell 5.1 or PowerShell 7+:

```powershell
.\patch\Apply-Patch.ps1 "D:\...\COLONIZE\VICEROY.EXE" -Status
.\patch\Apply-Patch.ps1 "D:\...\COLONIZE\VICEROY.EXE" -All
.\patch\Apply-Patch.ps1 "D:\...\COLONIZE\VICEROY.EXE" rng-idle-stir
```

Both patchers:

- check every byte at every patch site before writing, and write nothing if
  one site is wrong;
- copy the current file to `VICEROY.EXE.<first 8 of md5>.bak` before each
  change (the pristine file becomes `VICEROY.EXE.0f5d5b00.bak`). A backup is
  never overwritten, so every state you had is kept;
- write to a temporary file and rename it, so an interrupted run cannot leave
  a half-written EXE;
- refuse patch combinations that conflict, and never block `--revert`.

Exit codes: 0 ok · 1 file not found · 2 bad command line · 3 unexpected bytes
or size · 5 conflict · 6 invalid `patches.json`.

---

# Patch 1: trade-route boycott drop-off (`traderoute-boycott-v2`)

## The bug

Trade routes automate cargo carriers between colonies. A delivery from one
colony to another is a domestic transfer, so it should have nothing to do with
European trade policy.

It does. Once a good is **boycotted** — after refusing the King's tax demand —
a trade route can silently stop delivering that good. The carrier picks the
cargo up correctly, travels correctly, arrives at the destination colony, and
then fails to drop off. No message is shown. The carrier keeps shuttling the
same cargo forever.

Unloading by hand still works. MicroProse fixed the *manual* `U`-key case in
the v3.0 patch (README, fix #1: *"The 'U'nload key working during a
boycott"*) and missed the automated trade-route entry point.

## Why it happens

The drop-off is a loop over the carrier's cargo items:

```
i = 0
while i < cargo_count:
    good = unload_list_get(i)           ; lcall 1a1f:021c
    if skip_test(good) != 0:            ; boycotted for the "current" nation?
        i++ ; continue                  ; 0x4121A: jne 0x411F6
    while find_hold(unit, good) >= 0:   ; lcall 181f:0c2c
        unload(unit, good, 0)           ; lcall 191f:0594 (colony) / 191f:0d02 (Europe)
    i++
```

`skip_test` (file `0x30B38`) tests the boycott bit of whichever nation the game
selected **last**. The route code selects the carrier's own nation only when
the stop is **Europe**. A route may have Europe as a stop (any stop after the
first: the editor's first step lists only New World places). At a Europe stop
the test is right: you cannot sell a boycotted good in London. At a colony stop
it reads a stale nation, and the delivery is refused.

## The fix (v2)

Keep the skip at Europe stops, drop it at colony stops. Two sites, used
together:

```
0x41215:  83 C4 02   add sp,2     ->  E9 AE 16   jmp 0x428C6
0x428C6:  00 x 9     (padding)    ->  pop cx / shr ax,cl / and al,[bp-17h] / jmp 0x41218
```

The tail turns the test into 0 or 1 and ANDs it with the high byte of the stop
number (`03h` for Europe = 999, `00h` for a colony). The original
`or ax,ax / jne skip` at `0x41218` then decides. Ids: `traderoute-boycott-v2`
and `traderoute-boycott-v2-tail`; `--all` applies both.

| | MD5 |
|---|---|
| `VICEROY.EXE` original (v3.0, 494,910 bytes) | `0f5d5b0063721fbc6aca314e5a43ddaf` |
| original + v2 pair | `8610ad255a2940448439d63b87e931bd` |
| original + all recommended (v2 pair, topup, idle-stir, cap pair) | `40c5827211da71be83722c3676ab8ec4` |
| original + v2 pair, topup, idle-stir (the recommended set before 2026-10-27) | `1905cb45ddc4ad95a8497e50a0276d8e` |

## The first fix (withdrawn)

From August to October 2026 this repository recommended one change,
`0x4121A` `75 DA` → `90 90`, which removes the skip everywhere. It fixes
colony deliveries, but a route's Europe stop then **sells boycotted goods**.
It is no longer in `patches.json`.

If you applied it, go back to the original first. The patcher kept it as
`VICEROY.EXE.0f5d5b00.bak` next to the game:

```
copy VICEROY.EXE.0f5d5b00.bak VICEROY.EXE
python3 patch/apply_patch.py /path/to/COLONIZE/VICEROY.EXE --all
```

v2's check covers the bytes the first fix changed, so on an EXE that still
has it the patchers refuse (exit 3) and write nothing.

## What has been verified

In QEMU (2026-10-19), with v2 alone and with all recommended patches:

- A Europe stop keeps boycotted furs on board and sells the sugar (gold +180
  only), as the original game does. The old patch sold the furs too (gold +270).
- At a colony stop, where the original refuses, the wagon delivers (cotton
  0 → 5, furs 62 → 77).
- No hang. The 9 tail bytes load with overlay 8 in every run.

The old patch was play-tested in DOSBox-Daum for 50+ turns (colony delivery,
a 5-stop / 7-cargo route, manual Europe sales, the Custom House); a route
Europe stop was not tested then. v2 has not yet had a long DOSBox game.

Full reasoning, offsets and evidence: [docs/ANALYSIS.md](docs/ANALYSIS.md).

---

# Patch 2: battle randomness (`rng-idle-stir`)

The game's `rand()` is the standard Microsoft C generator and is fine. The
problem is the **idle loop**: while the game waits for input, it calls
`srand(tick)` about 18 times a second. That resets the whole RNG state to the
current tick count, so draws made close in time are close in value.

The patch changes 13 bytes in that idle wrapper so it **adds** the tick into
the state instead of resetting it. `srand()` itself is left byte-identical,
because the game uses it on purpose for content that must stay the same
(colony screen layout, which skill a village teaches).

Measured in QEMU on 2026-09-23 (same save and script for both builds, 40
samples each):

| | r1 (0 = no correlation) | chi² (pass < 14.07) | high state word = 0 |
|---|---|---|---|
| stock | +0.925 | 152.4 | 40 / 40 |
| patched | +0.060 | 4.8 | 0 / 40 |

**Play-checked** in DOSBox-Daum in October 2026, over many games: colony layout
stable across visits, village teaching stable turn to turn, new worlds differ.
Full workings, including the MZ relocation trap this patch has to avoid:
[docs/RNG-ANALYSIS.md](docs/RNG-ANALYSIS.md).

---

# Patch 3: part-full holds on a route (`traderoute-topup`)

Not a boycott bug, though it looks like one. On a route with two pick-up stops,
the second stop loads nothing if the first stop put something in **every**
hold, even if a hold is only part full. Example: a caravel takes 124 sugar at
the first stop (100 + 24), then passes a colony with 100 sugar and takes none.

The colony load loop checks "holds in use == capacity" before it tries to load:

```
0x41384:  74 03   je 0x41389     ->   90 90   nop / nop
```

With the check removed, the load call fills up the part-full hold and stops
the loop by itself when nothing more fits. Europe stops use a different path
and are not changed.

Measured in QEMU on 2026-09-24 (6 runs, one-bit differential on the boycott,
plus controls): the boycott makes no difference; with the patch the second
stop fills 24 → 100; a ship full of another good loads nothing and the game
does not hang; a 24-turn run topped up in all 6 cycles. Details: [docs/ANALYSIS.md](docs/ANALYSIS.md#second-bug-a-later-pick-up-stop-is-skipped-traderoute-topup).

**Play-checked** in DOSBox-Daum in October 2026: trade routes in many long
games, no hang.

| | MD5 |
|---|---|
| original + `traderoute-topup` only | `f5399c8b96e37bd5b3c778fbae781b25` |
| original + the first boycott fix + `traderoute-topup` (QEMU test EXE) | `f827c0bdd89a27de811941315b027c14` |

---

# Patch 4: a route unload stops at the storage limit (`traderoute-unload-cap`)

A trade route unloads everything it carries, even into a full colony; the
excess spoils at the end of the turn. The unload routine (`0x2A6A6`) does
check the capacity, but only warns, and only inside the colony screen. This
is the original game, not one of these patches.

On a route the patch gives the free room to the routine's own partial-unload
path (the one the "how much?" prompt uses): it deposits what fits and puts
the rest back on the carrier. A second site in the route loop moves on to the
next good when an unload left goods on board; without it the loop would look
for the same hold forever. Food is never capped, the colony screen still
shows its warning, Europe stops are not changed.

```
0x2A7CA:  35 bytes   room = capacity - stock; on a route, unload only room
0x411F0:  83 7E E6 00 7D 26   ->   0B C0 74 28 90 90   (or ax,ax / jz 0x4121C)
```

`--all` applies both. By name, always both:

```
python3 patch/apply_patch.py /path/to/COLONIZE/VICEROY.EXE traderoute-unload-cap traderoute-unload-cap-loop
```

Measured in QEMU on 2026-10-27: Jamestown furs 195, capacity 200, a wagon
brings 15. Without the patch the colony gets 210; with it 200, and the wagon
keeps 10 furs in one hold (at its pick-up stop `traderoute-topup` fills that
hold to 20). The game runs on, no hang. The boycott Europe stop (gold +180,
boycotted furs kept) and colony stop (cotton 0 → 5, furs 62 → 77) give the
same numbers with and without the cap. **Recommended** since 2026-10-27; `--all`
applies both sites.
Details: [docs/ANALYSIS.md](docs/ANALYSIS.md#a-route-unload-ignores-the-colonys-storage-vanilla).

| | MD5 |
|---|---|
| original + the cap pair only | `e228de0a7f1e82d90f7616bdc19c661f` |

---

## Repository contents

- `patch/` — `patches.json` (the patch list) and the two patchers.
- `tests/` — tests for both patchers (`python3 -m unittest discover -s tests`).
  Set `COL1_PRISTINE_EXE` to your own unmodified `VICEROY.EXE` to also run
  the real-binary checks (md5 of every patch, `srand()` untouched,
  relocation audits of the MZ image and the overlays).
- `docs/ANALYSIS.md` — the trade-route bugs: mechanism, offsets, runtime evidence.
- `docs/TAX-ANALYSIS.md` — when the King raises the tax, the Tea Party, boycotts.
- `docs/COMBAT.md` — how a battle is decided; the CHEAT menu flag.
- `docs/data/` — raw measurement logs.
- `docs/sessions/` — session logs.
- `docs/RNG-ANALYSIS.md` — the RNG defect, the patch, and the measurements.
- `docs/METHOD.md` — how to reproduce the investigation.
- `docs/DEAD-ENDS.md` — what looked right and wasn't. Read this before re-deriving it.
- `harness/` — headless QEMU + gdb rig for runtime RE of a DOS game:
  hardware watchpoints, RNG state sampling, scripted input.

## Legal

No game code is redistributed here. The patcher modifies a copy of
`VICEROY.EXE` that you already own. Colonization is © MicroProse / 2K.
Tooling and documentation in this repository are MIT licensed (see `LICENSE`).
