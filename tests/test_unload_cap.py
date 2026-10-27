"""traderoute-unload-cap and -loop: run the patched code in x86mini.

The unload routine (file 0x2A6A6, overlay code base 0x25900) is run from the
capacity call at 0x2A7C2 to the point where it decides: deposit (0x2A854),
the colony-screen warning (0x2A7ED) or return without a change (0x2A8E6).
The route loop (overlay code base 0x404B0) is run from 0x411F0 to its next
step: find another hold (0x4121C) or the next good (0x411F6).
"""
import unittest
from pathlib import Path

from test_apply_patch import HAVE_PRISTINE, PRISTINE, REAL_MANIFEST
from x86mini import CPU

UNLOAD_OV, LOOP_OV = 0x25900, 0x404B0       # file offset of each overlay's code
CODE, DATA = 0x1000, 0x2000
DEPOSIT, WARNING, NO_CHANGE = 0x2A854, 0x2A7ED, 0x2A8E6
REFIND, NEXT_GOOD = 0x4121C, 0x411F6
COLONY = 0x6000                             # any colony record; [8542h] points at it
FURS, FOOD = 4, 0
BP = 0xE800


def exe_with(*pids):
    d = bytearray(Path(PRISTINE).read_bytes())
    for pid in pids:
        p = next(p for p in REAL_MANIFEST["patches"] if p["id"] == pid)
        off = int(p["offset"], 16)
        new = bytes.fromhex(p["patched"])
        d[off:off + len(new)] = new
    return bytes(d)


def run_to(cpu, base, start, stops, limit=1000):
    cpu.r["cs"], cpu.r["ip"] = CODE, start - base
    for _ in range(limit):
        if cpu.r["ip"] + base in stops:
            return cpu.r["ip"] + base
        cpu.step()
    raise RuntimeError("no stop reached")


def unload(exe, stock, amount, cap, good=FURS, colony_screen=False):
    """(where it went, colony stock, [8DC4h] amount to deposit, [bp-0Ah] kept on board, [bp-6])."""
    cpu = CPU()
    cpu.mem[CODE] = bytearray(exe[UNLOAD_OV:UNLOAD_OV + 0x10000].ljust(0x10000, b"\0"))
    cpu.r.update(ds=DATA, ss=DATA, sp=BP - 0x10, bp=BP)
    w = lambda o, v: cpu.seg(DATA).__setitem__(slice(o, o + 2), (v & 0xFFFF).to_bytes(2, "little"))
    w(0x8542, COLONY)
    w(COLONY + 0x9A + 2 * good, stock)
    w(0x8DC4, amount)
    w(0x0890, 1 if colony_screen else 0)
    w(BP + 8, good)
    w(BP - 6, 1)                            # as set at 0x2A6AB
    w(BP - 0xA, 0)                          # as set at 0x2A6B0

    def capacity(cpu):
        cpu.r["ax"] = cap
    cpu.far_hooks[(0x181F, 0x0D3A)] = capacity
    where = run_to(cpu, UNLOAD_OV, 0x2A7C2, {DEPOSIT, WARNING, NO_CHANGE})
    s = lambda v: v - 0x10000 if v & 0x8000 else v
    r = lambda o: s(cpu.rw(DATA, o))
    return where, r(COLONY + 0x9A + 2 * good), r(0x8DC4), r(BP - 0xA), r(BP - 6)


def loop_step(exe, ax, hold):
    cpu = CPU()
    cpu.mem[CODE] = bytearray(exe[LOOP_OV:LOOP_OV + 0x10000].ljust(0x10000, b"\0"))
    cpu.r.update(ds=DATA, ss=DATA, sp=BP - 0x50, bp=BP, ax=ax & 0xFFFF)
    cpu.seg(DATA)[BP - 0x1A:BP - 0x18] = (hold & 0xFFFF).to_bytes(2, "little")
    return run_to(cpu, LOOP_OV, 0x411F0, {REFIND, NEXT_GOOD})


@unittest.skipUnless(HAVE_PRISTINE, "set COL1_PRISTINE_EXE to an unmodified VICEROY.EXE")
class UnloadCapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = exe_with()
        cls.new = exe_with("traderoute-unload-cap", "traderoute-unload-cap-loop")

    def test_route_unload_stops_at_capacity(self):
        # Jamestown in the 2026-10-19 QEMU run: furs 195, capacity 200, 15 on the wagon
        self.assertEqual(unload(self.new, 195, 15, 200), (DEPOSIT, 195, 5, 10, 2))
        self.assertEqual(unload(self.old, 195, 15, 200)[:3], (DEPOSIT, 195, 15))

    def test_fits_unchanged(self):
        for exe in (self.old, self.new):
            self.assertEqual(unload(exe, 100, 100, 200), (DEPOSIT, 100, 100, 0, 1))
            self.assertEqual(unload(exe, 150, 50, 200), (DEPOSIT, 150, 50, 0, 1))

    def test_full_colony_takes_nothing(self):
        # Stock at or over capacity (production can push it over): no deposit,
        # non-zero return, so the patched loop moves on.
        for stock in (200, 230):
            where, st, _, _, ret = unload(self.new, stock, 40, 200)
            self.assertEqual((where, st, ret), (NO_CHANGE, stock, 2))

    def test_food_never_capped(self):
        for exe in (self.old, self.new):
            self.assertEqual(unload(exe, 195, 100, 200, good=FOOD), (DEPOSIT, 195, 100, 0, 1))

    def test_colony_screen_still_warns(self):
        for exe in (self.old, self.new):
            where, st, amount, kept, ret = unload(exe, 195, 15, 200, colony_screen=True)
            self.assertEqual((where, st, amount, kept, ret), (WARNING, 195, 15, 0, 1))

    def test_route_loop(self):
        # (ax from the unload, hold index): (pristine, patched)
        cases = {(0, 0): (REFIND, REFIND),          # delivered: look for another hold
                 (2, 0): (REFIND, NEXT_GOOD),       # goods kept on board: pristine would loop
                 (-1, -1): (NEXT_GOOD, NEXT_GOOD)}  # no hold with this good
        for (ax, hold), want in cases.items():
            self.assertEqual((loop_step(self.old, ax, hold), loop_step(self.new, ax, hold)), want)

    def test_two_sites_share_one_status(self):
        cap, loop = (next(p for p in REAL_MANIFEST["patches"] if p["id"] == i)
                     for i in ("traderoute-unload-cap", "traderoute-unload-cap-loop"))
        self.assertEqual(cap["status"], loop["status"])


if __name__ == "__main__":
    unittest.main()
