"""A tiny 16-bit x86 interpreter, just enough to run the string routines of
VICEROY.EXE (strlwr, strupr, _fstrupr), the label builders and the hotkey
readers in tests.

It knows only the instructions those routines use, and stops with an error on
anything else, so a test can never pass by skipping code it does not model.
Flags: ZF, CF, SF and OF (the signed jumps of the Colonizopedia sort).
A far call to a (segment, offset) in `far_hooks` runs the Python stub instead
(file segment values, before relocation); the caller still pops the arguments.
"""

REG16 = ["ax", "cx", "dx", "bx", "sp", "bp", "si", "di"]
REG8 = ["al", "cl", "dl", "bl", "ah", "ch", "dh", "bh"]
SREG = ["es", "cs", "ss", "ds"]


class Unsupported(Exception):
    pass


class CPU:
    def __init__(self):
        self.r = dict.fromkeys(REG16 + SREG + ["ip"], 0)
        self.zf = self.cf = self.sf = self.of = False
        self.mem = {}                     # segment -> bytearray(0x10000)
        self.far_hooks = {}               # (seg, off) -> fn(cpu), for lcall

    # ---- registers and memory ---------------------------------------------------

    def get8(self, i):
        v = self.r[REG16[i & 3]]
        return (v >> 8) & 0xFF if i >= 4 else v & 0xFF

    def set8(self, i, val):
        name = REG16[i & 3]
        v = self.r[name]
        self.r[name] = (v & 0x00FF) | (val << 8) if i >= 4 else (v & 0xFF00) | val

    def seg(self, s):
        return self.mem.setdefault(s, bytearray(0x10000))

    def rb(self, s, o):
        return self.seg(s)[o & 0xFFFF]

    def wb(self, s, o, v):
        self.seg(s)[o & 0xFFFF] = v & 0xFF

    def rw(self, s, o):
        return self.rb(s, o) | self.rb(s, o + 1) << 8

    def push(self, v):
        self.r["sp"] = (self.r["sp"] - 2) & 0xFFFF
        self.wb(self.r["ss"], self.r["sp"], v)
        self.wb(self.r["ss"], self.r["sp"] + 1, v >> 8)

    def pop(self):
        v = self.rw(self.r["ss"], self.r["sp"])
        self.r["sp"] = (self.r["sp"] + 2) & 0xFFFF
        return v

    def fetch(self):
        b = self.rb(self.r["cs"], self.r["ip"])
        self.r["ip"] = (self.r["ip"] + 1) & 0xFFFF
        return b

    def fetch16(self):
        return self.fetch() | self.fetch() << 8

    def sub(self, a, b, bits, borrow=0):
        mask = (1 << bits) - 1
        res = (a - b - borrow) & mask
        self.cf = a < b + borrow
        self.of = bool(((a ^ b) & (a ^ res)) >> (bits - 1) & 1)
        self.zf, self.sf = res == 0, bool(res >> (bits - 1))
        return res

    def add(self, a, b, bits):
        mask = (1 << bits) - 1
        res = (a + b) & mask
        self.cf = a + b > mask
        self.of = bool(((a ^ res) & (b ^ res)) >> (bits - 1) & 1)
        self.zf, self.sf = res == 0, bool(res >> (bits - 1))
        return res

    def logic(self, res, bits):
        self.zf, self.sf, self.cf, self.of = res == 0, bool(res >> (bits - 1)), False, False
        return res

    def sub8(self, a, b):
        return self.sub(a, b, 8)

    # ---- ModRM: all 8086 memory forms ---------------------------------------------

    EA_BASE = [("bx", "si"), ("bx", "di"), ("bp", "si"), ("bp", "di"), ("si",), ("di",), ("bp",), ("bx",)]

    def modrm_ea(self, modrm, seg_override):
        mod, rm = modrm >> 6, modrm & 7
        if mod == 3:
            return None
        if (mod, rm) == (0, 6):          # [disp16], default segment DS
            return (seg_override or self.r["ds"], self.fetch16())
        regs = self.EA_BASE[rm]
        base = sum(self.r[x] for x in regs)
        seg = self.r["ss"] if "bp" in regs else self.r["ds"]
        disp = 0
        if mod == 1:
            disp = self.fetch()
            disp = disp - 256 if disp > 127 else disp
        elif mod == 2:
            disp = self.fetch16()
        return (seg_override or seg, (base + disp) & 0xFFFF)

    def rm16(self, m, ea):
        return self.r[REG16[m & 7]] if ea is None else self.rw(*ea)

    def rm8(self, m, ea):
        return self.get8(m & 7) if ea is None else self.rb(*ea)

    def sub16(self, a, b):
        return self.sub(a, b, 16)

    # ---- run ------------------------------------------------------------------------

    def step(self):
        seg_override = None
        op = self.fetch()
        while op in (0x26, 0x2E, 0x36):
            seg_override = self.r[{0x26: "es", 0x2E: "cs", 0x36: "ss"}[op]]
            op = self.fetch()
        r = self.r
        if op == 0x1E:   self.push(r["ds"])
        elif op == 0x16: self.push(r["ss"])
        elif op == 0x1F: r["ds"] = self.pop()
        elif 0x50 <= op <= 0x57: self.push(r[REG16[op - 0x50]])          # push r16 (push sp: 286+)
        elif 0x58 <= op <= 0x5F: r[REG16[op - 0x58]] = self.pop()
        elif 0x40 <= op <= 0x4F:                                          # inc / dec r16 (CF kept)
            n, a = REG16[op & 7], r[REG16[op & 7]]
            v = (a + (1 if op < 0x48 else -1)) & 0xFFFF
            self.of = v == (0x8000 if op < 0x48 else 0x7FFF)
            self.zf, self.sf = v == 0, bool(v >> 15)
            r[n] = v
        elif op == 0xAC:                                                  # lodsb (DF = 0)
            self.set8(0, self.rb(seg_override or r["ds"], r["si"]))
            r["si"] = (r["si"] + 1) & 0xFFFF
        elif op == 0xA1: r["ax"] = self.rw(seg_override or r["ds"], self.fetch16())   # mov ax, [moffs]
        elif op == 0xA3:                                                  # mov [moffs], ax
            s, o = seg_override or r["ds"], self.fetch16()
            self.wb(s, o, r["ax"])
            self.wb(s, o + 1, r["ax"] >> 8)
        elif op == 0xA0: self.set8(0, self.rb(seg_override or r["ds"], self.fetch16()))  # mov al, [moffs]
        elif op == 0xA8: self.logic(self.get8(0) & self.fetch(), 8)                   # test al, imm8
        elif op == 0x0C: self.set8(0, self.logic(self.get8(0) | self.fetch(), 8))     # or al, imm8
        elif op == 0x1C: self.set8(0, self.sub(self.get8(0), self.fetch(), 8, self.cf))   # sbb al, imm8
        elif op == 0x98: r["ax"] = (r["ax"] & 0xFF) | (0xFF00 if r["ax"] & 0x80 else 0)   # cbw
        elif op == 0xC9:                                                  # leave
            r["sp"] = r["bp"]
            r["bp"] = self.pop()
        elif op == 0x92: r["ax"], r["dx"] = r["dx"], r["ax"]
        elif op == 0x90: pass
        elif op in (0x8B, 0x8A, 0x88, 0x0A, 0x2A, 0x02, 0x8C, 0x8E, 0xC5, 0xC4, 0x3A, 0x1A, 0x86):
            m = self.fetch()
            reg = (m >> 3) & 7
            ea = self.modrm_ea(m, seg_override)
            if op == 0x8B:                                   # mov r16, r/m16
                r[REG16[reg]] = r[REG16[m & 7]] if ea is None else self.rw(*ea)
            elif op == 0x8A:                                 # mov r8, r/m8
                self.set8(reg, self.get8(m & 7) if ea is None else self.rb(*ea))
            elif op == 0x88:                                 # mov r/m8, r8
                if ea is None:
                    self.set8(m & 7, self.get8(reg))
                else:
                    self.wb(*ea, self.get8(reg))
            elif op == 0x0A:                                 # or r8, r8
                if ea is not None:
                    raise Unsupported("or r8, mem")
                self.set8(reg, self.logic(self.get8(reg) | self.get8(m & 7), 8))
            elif op == 0x2A:                                 # sub r8, r8
                if ea is not None:
                    raise Unsupported("sub r8, mem")
                self.set8(reg, self.sub8(self.get8(reg), self.get8(m & 7)))
            elif op == 0x02:                                 # add r8, r8
                if ea is not None:
                    raise Unsupported("add r8, mem")
                self.set8(reg, self.add(self.get8(reg), self.get8(m & 7), 8))
            elif op == 0x3A:                                 # cmp r8, r/m8
                self.sub8(self.get8(reg), self.rm8(m, ea))
            elif op == 0x1A:                                 # sbb r8, r8
                if ea is not None:
                    raise Unsupported("sbb r8, mem")
                self.set8(reg, self.sub(self.get8(reg), self.get8(m & 7), 8, self.cf))
            elif op == 0x86:                                 # xchg r8, r8
                if ea is not None:
                    raise Unsupported("xchg r8, mem")
                a, b = self.get8(reg), self.get8(m & 7)
                self.set8(reg, b)
                self.set8(m & 7, a)
            elif op == 0x8C:                                 # mov r/m16, sreg
                if ea is None:
                    r[REG16[m & 7]] = r[SREG[reg]]
                else:
                    self.wb(*ea, r[SREG[reg]])
                    self.wb(ea[0], ea[1] + 1, r[SREG[reg]] >> 8)
            elif op == 0x8E:                                 # mov sreg, r/m16
                r[SREG[reg]] = self.rm16(m, ea)
            elif op in (0xC5, 0xC4):                         # lds / les r16, m32
                r[REG16[reg]] = self.rw(*ea)
                r["ds" if op == 0xC5 else "es"] = self.rw(ea[0], ea[1] + 2)
        elif op == 0x0E: self.push(r["cs"])
        elif op == 0x07: r["es"] = self.pop()
        elif op in (0x91, 0x93, 0x97):                                    # xchg ax, r16
            n = REG16[op & 7]
            r["ax"], r[n] = r[n], r["ax"]
        elif op == 0x6A:                                                  # push imm8 (sign-extended)
            v = self.fetch()
            self.push(v - 256 & 0xFFFF if v > 127 else v)
        elif op == 0x2D: r["ax"] = self.sub16(r["ax"], self.fetch16())    # sub ax, imm16
        elif op == 0xC8:                                                  # enter n, 0
            n, lvl = self.fetch16(), self.fetch()
            if lvl:
                raise Unsupported("enter with nesting level")
            self.push(r["bp"])
            r["bp"] = r["sp"]
            r["sp"] = (r["sp"] - n) & 0xFFFF
        elif op == 0xC2:                                                  # ret imm16 (near)
            n = self.fetch16()
            r["ip"] = self.pop()
            r["sp"] = (r["sp"] + n) & 0xFFFF
        elif op == 0xF2:                                                  # repne scasb (DF = 0)
            if self.fetch() != 0xAE:
                raise Unsupported("repne with anything but scasb")
            while r["cx"]:
                r["cx"] -= 1
                b = self.rb(r["es"], r["di"])
                r["di"] = (r["di"] + 1) & 0xFFFF
                self.sub8(self.get8(0), b)
                if self.zf:
                    break
        elif op in (0x33, 0x0B, 0x3B, 0x2B, 0x89, 0xC7):
            m = self.fetch()
            reg = (m >> 3) & 7
            ea = self.modrm_ea(m, seg_override)
            if op == 0x89:                                   # mov r/m16, r16
                v = r[REG16[reg]]
            elif op == 0xC7:                                 # mov r/m16, imm16
                if reg:
                    raise Unsupported("c7 /%d" % reg)
                v = self.fetch16()
            elif op == 0x3B:                                 # cmp r16, r/m16
                self.sub16(r[REG16[reg]], self.rm16(m, ea))
                return
            elif op == 0x2B:                                 # sub r16, r/m16
                r[REG16[reg]] = self.sub16(r[REG16[reg]], self.rm16(m, ea))
                return
            else:                                            # xor / or r16, r/m16
                a, b = r[REG16[reg]], self.rm16(m, ea)
                r[REG16[reg]] = self.logic(a ^ b if op == 0x33 else a | b, 16)
                return
            if ea is None:
                r[REG16[m & 7]] = v
            else:
                self.wb(*ea, v)
                self.wb(ea[0], ea[1] + 1, v >> 8)
        elif 0xB0 <= op <= 0xB7: self.set8(op - 0xB0, self.fetch())      # mov r8, imm8
        elif 0xB8 <= op <= 0xBF: r[REG16[op - 0xB8]] = self.fetch16()     # mov r16, imm16
        elif op == 0x3C: self.sub8(self.get8(0), self.fetch())            # cmp al, imm8
        elif op == 0x2C: self.set8(0, self.sub8(self.get8(0), self.fetch()))  # sub al, imm8
        elif op == 0x68: self.push(self.fetch16())                        # push imm16
        elif op == 0x05: r["ax"] = self.add(r["ax"], self.fetch16(), 16)  # add ax, imm16
        elif op == 0x04: self.set8(0, self.add(self.get8(0), self.fetch(), 8))   # add al, imm8
        elif op == 0xF6 and self.rb(r["cs"], r["ip"]) >> 3 & 7 == 2:     # not r/m8 (flags kept)
            m = self.fetch()
            ea = self.modrm_ea(m, seg_override)
            if ea is not None:
                raise Unsupported("not mem")
            self.set8(m & 7, ~self.get8(m & 7) & 0xFF)
        elif op in (0x80, 0xC6, 0xF6):                                    # add/or/and/cmp, mov, test r/m8, imm8
            m = self.fetch()
            ea = self.modrm_ea(m, seg_override)
            ext, imm = (m >> 3) & 7, self.fetch()
            a = self.rm8(m, ea)
            if (op, ext) == (0x80, 7):
                self.sub8(a, imm)
                return
            if (op, ext) == (0x80, 0):
                v = self.add(a, imm, 8)
            elif (op, ext) == (0x80, 1):
                v = self.logic(a | imm, 8)
            elif (op, ext) == (0x80, 4):
                v = self.logic(a & imm, 8)
            elif (op, ext) == (0xC6, 0):
                v = imm
            elif (op, ext) == (0xF6, 0):
                self.logic(a & imm, 8)
                return
            else:
                raise Unsupported(f"{op:#04x} /{ext}")
            if ea is None:
                self.set8(m & 7, v)
            else:
                self.wb(*ea, v)
        elif op in (0x03, 0x8D, 0x83, 0x81, 0x69, 0x6B, 0xC1, 0xD1, 0xFF):
            m = self.fetch()
            reg = (m >> 3) & 7
            ea = self.modrm_ea(m, seg_override)
            if op == 0x03:                                   # add r16, r/m16
                r[REG16[reg]] = self.add(r[REG16[reg]], self.rm16(m, ea), 16)
            elif op == 0x8D:                                 # lea r16, m
                r[REG16[reg]] = ea[1]
            elif op in (0x69, 0x6B):                         # imul r16, r/m16, imm (low word)
                a = self.rm16(m, ea)
                imm = self.fetch16() if op == 0x69 else self.fetch()
                if op == 0x6B and imm > 127:
                    imm -= 256
                r[REG16[reg]] = (a * imm) & 0xFFFF
            elif op in (0x83, 0x81) and reg in (0, 5, 7):    # add / sub / cmp r/m16, imm8 (sign-extended) / imm16
                if op == 0x81:
                    imm = self.fetch16()
                else:
                    imm = self.fetch()
                    imm = (imm - 256 if imm > 127 else imm) & 0xFFFF
                a = self.rm16(m, ea)
                if reg == 7:
                    self.sub16(a, imm)
                else:
                    v = self.sub16(a, imm) if reg == 5 else self.add(a, imm, 16)
                    if ea is None:
                        r[REG16[m & 7]] = v
                    else:
                        self.wb(*ea, v)
                        self.wb(ea[0], ea[1] + 1, v >> 8)
            elif op == 0x83 and reg == 4 and ea is None:     # and r16, imm8 (sign-extended)
                imm = self.fetch()
                imm = (imm - 256 if imm > 127 else imm) & 0xFFFF
                r[REG16[m & 7]] = self.logic(r[REG16[m & 7]] & imm, 16)
            elif op == 0xD1 and reg == 4 and ea is None:     # shl r16, 1
                v = r[REG16[m & 7]] << 1
                self.cf, v = v > 0xFFFF, v & 0xFFFF
                self.zf, self.sf = v == 0, bool(v >> 15)
                self.of = self.sf != self.cf
                r[REG16[m & 7]] = v
            elif op == 0xC1 and reg == 4 and ea is None:     # shl r16, imm8 (OF not set)
                v = r[REG16[m & 7]] << (self.fetch() & 0x1F)
                self.cf, v = bool(v >> 16 & 1), v & 0xFFFF
                self.zf, self.sf = v == 0, bool(v >> 15)
                r[REG16[m & 7]] = v
            elif op == 0xFF and reg == 4:                    # jmp r/m16
                r["ip"] = self.rm16(m, ea)
            elif op == 0xFF and reg == 6:                    # push r/m16
                self.push(self.rm16(m, ea))
            elif op == 0xFF and reg == 0:                    # inc r/m16 (CF kept)
                v = (self.rm16(m, ea) + 1) & 0xFFFF
                self.zf, self.sf, self.of = v == 0, bool(v >> 15), v == 0x8000
                if ea is None:
                    r[REG16[m & 7]] = v
                else:
                    self.wb(*ea, v)
                    self.wb(ea[0], ea[1] + 1, v >> 8)
            else:
                raise Unsupported(f"{op:#04x} /{reg}")
        elif op == 0x9A:                                                  # lcall seg:off
            off, seg = self.fetch16(), self.fetch16()
            hook = self.far_hooks.get((seg, off))
            if hook:
                hook(self)
            else:
                self.push(r["cs"])
                self.push(r["ip"])
                r["cs"], r["ip"] = seg, off
        elif op == 0xD7:                                                  # xlatb
            s = seg_override or r["ds"]
            self.set8(0, self.rb(s, r["bx"] + self.get8(0)))
        elif op in (0x72, 0x73, 0x74, 0x75, 0x76, 0x77, 0x79, 0x7C, 0x7D, 0x7E, 0x7F, 0xEB, 0xE2):
            d = self.fetch()
            d = d - 256 if d > 127 else d
            if op == 0xE2:                                   # loop
                r["cx"] = (r["cx"] - 1) & 0xFFFF
            take = {0x72: self.cf, 0x73: not self.cf, 0x74: self.zf,
                    0x75: not self.zf, 0x76: self.cf or self.zf, 0x77: not self.cf and not self.zf,
                    0x79: not self.sf, 0x7C: self.sf != self.of, 0x7D: self.sf == self.of, 0x7E: self.zf or self.sf != self.of,
                    0x7F: not self.zf and self.sf == self.of, 0xEB: True,
                    0xE2: r["cx"] != 0}[op]
            if take:
                r["ip"] = (r["ip"] + d) & 0xFFFF
        elif op == 0xE9:
            d = self.fetch16()
            r["ip"] = (r["ip"] + d) & 0xFFFF
        elif op == 0xE8:
            d = self.fetch16()
            self.push(r["ip"])
            r["ip"] = (r["ip"] + d) & 0xFFFF
        elif op == 0xC3:
            r["ip"] = self.pop()
        elif op == 0xCB:
            r["ip"] = self.pop()
            r["cs"] = self.pop()
        else:
            raise Unsupported(f"opcode {op:#04x} at {r['cs']:04x}:{(r['ip'] - 1) & 0xFFFF:04x}")

    def call_far(self, cs, ip, args, limit=100000):
        """Far-call cs:ip with word args (pushed right to left, C order) and
        run until it returns to the sentinel. Returns the step count."""
        for a in reversed(args):
            self.push(a)
        self.push(0xFFFF)                    # return cs
        self.push(0xFFFF)                    # return ip
        self.r["cs"], self.r["ip"] = cs, ip
        for n in range(limit):
            self.step()
            if self.r["cs"] == 0xFFFF and self.r["ip"] == 0xFFFF:
                self.r["sp"] = (self.r["sp"] + 2 * len(args)) & 0xFFFF
                return n
        raise RuntimeError("no return")

    def run_until(self, cs, ip, stop_ip, limit=100000):
        """Run from cs:ip until ip reaches stop_ip in the same segment."""
        self.r["cs"], self.r["ip"] = cs, ip
        for n in range(limit):
            if self.r["cs"] == cs and self.r["ip"] == stop_ip:
                return n
            self.step()
        raise RuntimeError("stop address not reached")
