#!/usr/bin/env python3
"""Differential MIPS I interpreter.

The repo's verification standard is byte-identical output. That is the right
default, but it has a blind spot: cc1psx fills every branch delay slot while
Konami's compiler left 17% of them as `nop`, so a large class of functions can
never be byte-identical however the source is phrased.

This tool asks a weaker but still meaningful question instead: **does the
generated code compute the same thing?** It runs the original dump and the
cc1psx output side by side on one memory image with the same stubbed callees,
then compares

  * the sequence of outgoing calls and their arguments,
  * the return value,
  * the final contents of memory.

Divergence in any of those means the C is not equivalent, whatever the
instruction text looks like. The verdict is reported separately from
`cmp_func.py` and never replaces it.

    mipsemu.py <orig.s> <obj/path.asm> <symbol> [--trials N]

Notes on the design
------------------
* Both sides are parsed into one normalised instruction list, so the mnemonics
  that differ only in spelling (`sllv`/`sll`, `li`/`la`/`lui`+`addiu`,
  `move`/`or`, `j $ra`/`jr $ra`) are folded on both sides.
* Callees are stubs. `jal` records its arguments and returns a value derived
  from (trial, call index), so both sides see the same reply and several
  different replies get tried. Call targets are not compared here; `cmp_func.py`
  already resolves those by name.
* Memory is a sparse dict keyed by the real address, so nothing aliases.
* The GTE (cop2) is a traced black box: each operation joins the trace and
  returns a value derived from its operands, so a divergence in the cop2
  sequence is caught without emulating the coprocessor.
* Symbol addresses are *learned*, not looked up: when both sides have the same
  instruction count the two streams align index for index, and wherever the
  original says `lui $v0,0x800e` while the rebuild says `la $v0,NAME` the
  address of NAME follows. The table is cached in symtab.json.
"""
import json
import os
import random
import re
import struct
import sys

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get('MGS_ROOT') or os.path.join(HERE, 'mgs_rev2')
SYMTAB = os.path.join(HERE, 'symtab.json')
ARITY = os.path.join(HERE, 'arity.json')

ABI = ['zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3',
       't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
       's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7',
       't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra']
REGNUM = {n: i for i, n in enumerate(ABI)}


def is_reg(tok):
    """Is this operand a register? `$16` is a register even though it is all
    digits, and `0x10` is an immediate even though it looks like a number."""
    t = str(tok).strip()
    if t.startswith('$'):
        return True
    return t in REGNUM
LO, HI = 32, 33          # r[LO] / r[HI] carry mflo / mfhi
SENTINEL_RA = 0x7FFF0000
STEP_LIMIT = 300000
WORK_BASE = 0x80010000
WORK_BYTES = 0x4000
STACK_TOP = 0x7FF80000
GLOBALS_BASE = 0x800C0000
GLOBALS_BYTES = 0x40000

BRANCH2 = ('beqz', 'bnez', 'blez', 'bgtz', 'bltz', 'bgez')
# The assembler expands these to branch + nop in macro mode. `j`, `jal` and
# `jr` are plain: their delay slot is the next line of the file.
MACRO_BRANCHES = ('beq', 'bne', 'beqz', 'bnez', 'bgez', 'bgtz', 'bltz', 'blez')
BRANCH3 = ('beq', 'bne')
JUMPS = ('j', 'jal')


class Trap(Exception):
    pass


class Unsupported(Exception):
    pass


# ------------------------------------------------------------- operands


def reg(tok):
    tok = str(tok).strip().lstrip('$')
    if tok in REGNUM:
        return REGNUM[tok]
    if tok.isdigit():
        return int(tok)
    raise Unsupported(f'not a register: {tok!r}')


def num(tok):
    tok = str(tok).strip()
    m = re.fullmatch(r'(-?)0x([0-9a-fA-F]+)', tok)
    if m:
        v = int(m.group(2), 16)
        return -v if m.group(1) else v
    if re.fullmatch(r'-?\d+', tok):
        return int(tok)
    raise Unsupported(f'not a number: {tok!r}')


def s16(v):
    """Sign-extend a 16-bit immediate.

    The reduction has to come first: Python's `&` on a negative int uses
    infinite two's complement, so `-1 & 0x8000` is 0x8000 and a naive
    `v - 0x10000` turns -1 into -65537.
    """
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def sx32(v):
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


# -------------------------------------------------------------- parsing


class Insn:
    __slots__ = ('m', 'ops', 'tgt', 'ext', 'sym', 'addr')

    def __init__(self, m, ops, addr=None):
        self.m = m
        self.ops = ops
        self.tgt = None
        self.ext = False
        self.sym = None
        self.addr = addr

    def __repr__(self):
        return f'{self.m} {", ".join(self.ops)}'


def parse_dump(path):
    code, addr = b'', None
    for line in open(path, errors='ignore'):
        if line.strip().startswith('dw '):
            if addr is None:
                m = re.search(r';\s*([0-9a-fA-F]+)\s*$', line)
                if m:
                    addr = int(m.group(1), 16)
            code += struct.pack('<I', int(line.split()[1], 16))
    if addr is None:
        raise SystemExit(f'no instructions in {path}')
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32)
    out = []
    for ins in md.disasm(code, addr):
        ops = [x.strip() for x in ins.op_str.split(',')] if ins.op_str else []
        out.append(Insn(ins.mnemonic, ops, ins.address))
    return out, addr


LABEL_RE = re.compile(r'^([A-Za-z_$][\w$.]*):$')
INSN_RE = re.compile(r'^\s*([a-z][a-z0-9._]*)\s*(.*)$')


def parse_obj(path, symbol):
    lines = open(path, errors='ignore').read().split('\n')
    start = None
    for i, line in enumerate(lines):
        if line.strip() == symbol + ':':
            start = i
            break
    if start is None:
        raise SystemExit(f'symbol {symbol} not found in {path}')
    out, labels = [], {}
    macro = True
    for raw in lines[start + 1:]:
        s = raw.strip()
        # The body ends at `.end` / the next `.ent`; local labels such as
        # `$L920:` appear inline and must not end it.
        if s.startswith('.end') or s.startswith('.ent'):
            break
        lm = LABEL_RE.match(s)
        if lm:
            labels[lm.group(1)] = len(out)
            continue
        if s.startswith('.set'):
            if 'nomacro' in s:
                macro = False
            elif re.search(r'\bmacro\b', s):
                macro = True
            continue
        if re.match(r'^\s*\.(ident|size|section|file|align|comm|lcomm|'
                    r'data|text|globl|global|set|frame|mask|fmask)\b', raw):
            continue
        if s == '#nop':
            out.append(Insn('nop', []))
            continue
        line = raw.split('#')[0].rstrip()
        if not line.strip():
            continue
        m = INSN_RE.match(line)
        if not m:
            continue
        ops = [p.strip() for p in m.group(2).split(',')] \
            if m.group(2).strip() else []
        out.append(Insn(m.group(1), ops))
        # In the assembler's macro mode the *conditional* branches expand to
        # the branch plus a nop delay slot, so the next line of the file is a
        # separate instruction and not the delay slot. `j`, `jal` and `jr` are
        # plain: their delay slot is the next line. With `.set nomacro` even
        # the conditional branches are plain.
        if macro and m.group(1) in MACRO_BRANCHES:
            out.append(Insn('nop', []))
    return out, labels


RELOC_RE = re.compile(r'^%(hi|lo)\(([A-Za-z_][\w.]*)\)$')

# `sw $16,16($sp)` and also `lh $5,%lo(name)($2)`, which GAS emits when the
# offset is a relocation rather than a plain constant.
MEMOP_RE = re.compile(
    r'^\s*((?:%(?:hi|lo)\([A-Za-z_][\w.]*\))|(?:-?(?:0x[0-9a-fA-F]+|\d+))?)'
    r'\s*\(\s*(\$?\w+)\s*\)\s*$')

LOADSTORE = {'lw', 'lh', 'lhu', 'lb', 'lbu', 'lwl', 'lwr',
             'sw', 'sh', 'sb', 'swl', 'swr'}


def flatten_mem(ops):
    """`sw $s0, 0x8a0($s1)` -> ['$s0', '0x8a0', '$s1'].

    Both disassemblers spell a memory operand as `offset(base)`, so splitting
    on commas alone leaves the base register buried inside the string.
    """
    out = []
    for x in ops:
        m = MEMOP_RE.match(str(x))
        if m:
            out.append(m.group(1) if m.group(1) else '0')
            out.append(m.group(2))
        else:
            out.append(x)
    return out


RELOC_RE = re.compile(r'^%(hi|lo)\(([A-Za-z_][\w.]*)\)$')
HEXNAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*_([0-9A-Fa-f]{8})$')


def lookup(symtab, name):
    """Resolve a symbol: the repo's naming convention first, then the cache.

    Overlay symbols are spelled `<prefix>_<8 hex digits>` even when they have
    no pragma of their own (interior labels such as s14e_800D8350), so the name
    alone gives the address. That rule outranks anything learned by index
    alignment, which only ever sees part of a `%hi`/`%lo` pair.
    """
    m = HEXNAME_RE.match(name)
    if m:
        return 0x80000000 | int(m.group(1), 16)
    return symtab.get(name)


def expand(insns):
    """Fold pseudo-instructions. Addresses ride along, so branches still map."""
    prog = []
    for ins in insns:
        m, o = ins.m, list(ins.ops)
        if m in LOADSTORE:
            o = flatten_mem(o)
        if m in ('move', 'dmove'):
            prog.append(Insn('or', [o[0], o[1], 'zero'], ins.addr))
            continue
        if m in ('neg', 'negu'):
            # `negu rd, rs` e' `subu rd, $zero, rs`
            prog.append(Insn('subu', [o[0], 'zero', o[1]], ins.addr))
            continue
        # `lui $rd,%hi(S)` / `addiu $rd,$rd,%lo(S)` name a symbol the rebuild
        # has not resolved; keep the name and look it up at run time.
        if m == 'lui' and len(o) == 2:
            lm = RELOC_RE.match(o[1].strip())
            if lm and lm.group(1) == 'hi':
                ins2 = Insn('hisym', [o[0], lm.group(2)], ins.addr)
                ins2.sym = lm.group(2)
                prog.append(ins2)
                continue
        if m in ('addiu', 'addi') and len(o) == 3:
            lm = RELOC_RE.match(o[2].strip())
            if lm and lm.group(1) == 'lo':
                ins2 = Insn('addiusym', [o[0], o[1], lm.group(2)], ins.addr)
                ins2.sym = lm.group(2)
                prog.append(ins2)
                continue
        if m in ('li', 'la'):
            try:
                prog.append(Insn('li32', [o[0], str(num(o[1]) & 0xFFFFFFFF)],
                                 ins.addr))
            except Unsupported:
                ins2 = Insn('lisym', [o[0], o[1]], ins.addr)
                ins2.sym = o[1]
                prog.append(ins2)
            continue
        if m == 'j' and o and o[0] in ('ra', '$31', '31'):
            prog.append(Insn('jr', ['ra'], ins.addr))
            continue
        # cc1psx spells the immediate forms under their register mnemonics
        if m in ('slt', 'sltu') and len(o) == 3:
            try:
                num(o[2])
            except Unsupported:
                pass
            else:
                prog.append(Insn('slti' if m == 'slt' else 'sltiu', o, ins.addr))
                continue
        if m in ('addu', 'subu') and len(o) == 3:
            try:
                v = num(o[2])
            except Unsupported:
                pass
            else:
                if m == 'subu':
                    v = -v
                prog.append(Insn('addiu', [o[0], o[1], str(v)], ins.addr))
                continue
        prog.append(Insn(m, o, ins.addr))
    return prog


def resolve_targets(prog, labels, base):
    if base is not None:
        addr_index = {i.addr: k for k, i in enumerate(prog) if i.addr is not None}
    for ins in prog:
        if ins.m in BRANCH2 or ins.m in BRANCH3 or ins.m in JUMPS:
            if not ins.ops:
                continue
            tgt = None
            if base is not None:
                try:
                    tgt = addr_index.get(num(ins.ops[-1]))
                except Unsupported:
                    tgt = None
            else:
                last = ins.ops[-1]
                if last in labels:
                    tgt = labels[last]
                else:
                    m2 = re.fullmatch(r'\$L?(\d+)', last)
                    if m2:
                        tgt = int(m2.group(1))
            if tgt is None:
                ins.ext = True
            else:
                ins.tgt = tgt
    return prog


# --------------------------------------------------------------- memory


class Mem:
    def __init__(self):
        self.w = {}

    def lw(self, a):
        return self.w.get(a & ~3, 0)

    def lh(self, a):
        v = self.lw(a)
        return (v >> 16) & 0xFFFF if (a & 2) else v & 0xFFFF

    def lb(self, a):
        v = self.lh(a)
        return v - 0x10000 if v & 0x8000 else v

    def lbu(self, a):
        return self.lh(a) & 0xFF

    def sw(self, a, v):
        self.w[a & ~3] = v & 0xFFFFFFFF

    def sh(self, a, v):
        b = a & ~3
        old = self.w.get(b, 0)
        if a & 2:
            self.w[b] = (old & 0x0000FFFF) | ((v & 0xFFFF) << 16)
        else:
            self.w[b] = (old & 0xFFFF0000) | (v & 0xFFFF)

    def sb(self, a, v):
        b = a & ~3
        old = self.w.get(b, 0)
        sh = 16 if a & 2 else 0
        mask = 0xFFFF << sh
        self.w[b] = (old & ~mask & 0xFFFFFFFF) | ((v & 0xFF) << sh)

    def lwl(self, a):
        v = self.lw(a)
        n = a & 3
        return v if n == 0 else (v << ((4 - n) * 8)) & 0xFFFFFFFF

    def lwr(self, a):
        v = self.lw(a)
        n = a & 3
        return v if n == 3 else v >> ((3 - n) * 8)

    def swl(self, a, v):
        b = a & ~3
        old = self.w.get(b, 0)
        keep = 0
        for k in range(a & 3):
            keep |= 0xFF << (k * 8)
        self.w[b] = (old & keep) | (v & ~keep & 0xFFFFFFFF)

    def swr(self, a, v):
        b = a & ~3
        old = self.w.get(b, 0)
        keep = 0
        for k in range(a & 3, 4):
            keep |= 0xFF << (k * 8)
        self.w[b] = (old & keep) | (v & ~keep & 0xFFFFFFFF)

    def snapshot(self):
        return dict(self.w)


# ------------------------------------------------------------ execution


def step(ins, r, mem, symtab, stub, trace):
    """Execute one instruction. Returns 'fall' | 'taken' | 'jump' | 'ret'."""
    m, o = ins.m, ins.ops

    def A(i):
        return r[reg(o[i])]

    def ea(i_off, i_base):
        """Effective address. Both assemblers print `off(base)`, which
        `flatten_mem` turns into operands `rd, off, base` - and MIPS order
        really is `rs, offset(rt)`.

        The offset can be a relocation: `lh $5,%lo(name)($2)` is how GAS spells
        a load through a symbol that arrived in a register.
        """
        raw = str(o[i_off]).strip()
        rm = RELOC_RE.match(raw)
        if rm:
            addr = lookup(symtab, rm.group(2))
            if addr is None:
                raise Unsupported(f'unresolved symbol {rm.group(2)}')
            return A(i_base) + s16(addr & 0xFFFF)
        return A(i_base) + s16(num(raw))

    def setd(v):
        r[reg(o[0])] = v & 0xFFFFFFFF

    if m == 'nop':
        return 'fall'
    if m in ('addiu', 'addi'):
        setd(A(1) + s16(num(o[2])))
    elif m in ('addu', 'add'):
        setd(A(1) + A(2))
    elif m == 'subu':
        setd(A(1) - A(2))
    elif m == 'sub':
        setd(A(1) - s16(num(o[2])))
    elif m == 'and':
        setd(A(1) & A(2))
    elif m == 'or':
        setd(A(1) | A(2))
    elif m == 'xor':
        setd(A(1) ^ A(2))
    elif m == 'nor':
        setd(~(A(1) | A(2)))
    elif m == 'andi':
        setd(A(1) & (num(o[2]) & 0xFFFF))
    elif m == 'ori':
        setd(A(1) | (num(o[2]) & 0xFFFF))
    elif m == 'xori':
        setd(A(1) ^ (num(o[2]) & 0xFFFF))
    elif m in ('sllv', 'srlv', 'srav'):
        # rd = rt shifted by (rs & 31)
        k = A(2) & 31
        v = A(1)
        if m == 'sllv':
            setd(v << k)
        elif m == 'srlv':
            setd((v & 0xFFFFFFFF) >> k)
        else:
            setd(sx32(v) >> k)
    elif m == 'sll':
        # PSYQ's dialect writes the variable shift as `sll rd,rt,rs`, with a
        # register in the shift field; GAS spells it `sllv`.
        if len(o) > 2 and is_reg(o[2]):
            setd(A(1) << (A(2) & 0x1F))
        else:
            setd(A(1) << (num(o[2]) & 0x1F))
    elif m == 'srl':
        if len(o) > 2 and is_reg(o[2]):
            setd((A(1) & 0xFFFFFFFF) >> (A(2) & 0x1F))
        else:
            setd((A(1) & 0xFFFFFFFF) >> (num(o[2]) & 0x1F))
    elif m == 'sra':
        if len(o) > 2 and is_reg(o[2]):
            setd(sx32(A(1)) >> (A(2) & 0x1F))
        else:
            setd(sx32(A(1)) >> (num(o[2]) & 0x1F))
    elif m == 'sllv':
        setd(A(1) << (A(2) & 0x1F))
    elif m == 'srlv':
        setd(A(1) >> (A(2) & 0x1F))
    elif m == 'srav':
        setd(sx32(A(1)) >> (A(2) & 0x1F))
    elif m == 'slt':
        setd(1 if sx32(A(1)) < sx32(A(2)) else 0)
    elif m == 'sltu':
        setd(1 if A(1) < A(2) else 0)
    elif m == 'slti':
        setd(1 if sx32(A(1)) < s16(num(o[2])) else 0)
    elif m == 'sltiu':
        setd(1 if A(1) < (num(o[2]) & 0xFFFF) else 0)
    elif m == 'lui':
        setd(num(o[1]) << 16)
    elif m == 'li32':
        setd(num(o[1]))
    elif m == 'lisym':
        addr = lookup(symtab, ins.sym)
        if addr is None:
            raise Unsupported(f'unresolved symbol {ins.sym}')
        setd(addr)
    elif m == 'hisym':
        addr = lookup(symtab, ins.sym)
        if addr is None:
            raise Unsupported(f'unresolved symbol {ins.sym}')
        # GAS defines %hi(S) = ((S + 0x8000) >> 16) & 0xFFFF and
        # %lo(S) = S - ((S + 0x8000) & 0xFFFF0000): the high half is *rounded*
        # and the low half carries the sign. Truncating instead would leave
        # 0x800C94A8 coming out as lui 0x800C / addiu -0x6B58 = 0x800B94A8.
        setd((addr + 0x8000) & 0xFFFF0000)
    elif m == 'addiusym':
        addr = lookup(symtab, ins.sym)
        if addr is None:
            raise Unsupported(f'unresolved symbol {ins.sym}')
        setd(A(1) + s16(addr & 0xFFFF))
    elif m == 'lw':
        setd(mem.lw(ea(1, 2)))
    elif m == 'lh':
        setd(s16(mem.lh(ea(1, 2))))
    elif m == 'lhu':
        setd(mem.lh(ea(1, 2)))
    elif m == 'lb':
        setd(mem.lb(ea(1, 2)))
    elif m == 'lbu':
        setd(mem.lbu(ea(1, 2)))
    elif m == 'sw':
        mem.sw(ea(1, 2), A(0))
    elif m == 'sh':
        mem.sh(ea(1, 2), A(0))
    elif m == 'sb':
        mem.sb(ea(1, 2), A(0))
    elif m in ('lwl', 'lwr', 'swl', 'swr'):
        a = ea(1, 2)
        if m == 'lwl':
            r[reg(o[0])] = mem.lwl(a)
        elif m == 'lwr':
            r[reg(o[0])] = mem.lwr(a)
        elif m == 'swl':
            mem.swl(a, A(0))
        else:
            mem.swr(a, A(0))
    elif m == 'mult':
        p = sx32(A(0)) * sx32(A(1))
        r[LO], r[HI] = p & 0xFFFFFFFF, (p >> 32) & 0xFFFFFFFF
    elif m == 'multu':
        p = A(0) * A(1)
        r[LO], r[HI] = p & 0xFFFFFFFF, (p >> 32) & 0xFFFFFFFF
    elif m in ('div', 'divu'):
        a, b = (sx32(A(0)), sx32(A(1))) if m == 'div' else (A(0), A(1))
        if b == 0:
            r[LO], r[HI] = 0, 0
        else:
            q = int(a / b)
            r[LO], r[HI] = q & 0xFFFFFFFF, (a - q * b) & 0xFFFFFFFF
    elif m == 'rem':
        a, b = sx32(A(0)), sx32(A(1))
        r[LO] = ((a - int(a / b) * b) if b else 0) & 0xFFFFFFFF
    elif m == 'mflo':
        setd(r[LO])
    elif m == 'mfhi':
        setd(r[HI])
    elif m in BRANCH3:
        x, y = A(0), A(1)
        eq = x == y
        take = eq if m == 'beq' else not eq
        return 'taken' if take and ins.tgt is not None else 'fall'
    elif m in BRANCH2:
        x = A(0)
        if m == 'beqz':
            take = x == 0
        elif m == 'bnez':
            take = x != 0
        elif m == 'blez':
            take = sx32(x) <= 0
        elif m == 'bgtz':
            take = sx32(x) > 0
        elif m == 'bltz':
            take = sx32(x) < 0
        else:
            take = sx32(x) >= 0
        return 'taken' if take and ins.tgt is not None else 'fall'
    elif m == 'j':
        return 'jump' if ins.tgt is not None else 'fall'
    elif m == 'jr':
        if A(0) == SENTINEL_RA:
            return 'ret'
        raise Trap(f'indirect jr to {A(0):#x}')
    elif m in ('jal', 'jalr'):
        # The argument setup lives in the delay slot, so the call itself can
        # only be recorded once that slot has run. `run` does it.
        return 'jal'
    elif m in ('cop2', 'cfc2', 'ctc2', 'mfc2', 'mtc2', 'lwc2', 'swc2'):
        trace.append((m, tuple(o)))
        if m in ('mfc2', 'lwc2'):
            setd(stub(len(trace) - 1, [A(1)], []))
        elif m in ('mtc2', 'swc2'):
            r[reg(o[1])] = A(0)
    elif m == 'break':
        raise Trap('break')
    else:
        raise Unsupported(f'mnemonic {m!r}')
    return 'fall'


def run(prog, symtab, mem, args, stub, trace_limit=2000, arities=None,
        arity_list=None, names=None):
    r = [0] * 34
    for i in range(4):
        r[REGNUM['a%d' % i]] = args[i] & 0xFFFFFFFF
    r[REGNUM['sp']] = args[4] & 0xFFFFFFFF
    r[REGNUM['ra']] = SENTINEL_RA
    trace = []
    np = len(prog)
    pc = 0
    steps = 0
    while True:
        if steps > STEP_LIMIT:
            raise Trap('step limit')
        if pc < 0 or pc >= np:
            raise Trap(f'pc {pc} outside function')
        ins = prog[pc]
        action = step(ins, r, mem, symtab, stub, trace)
        steps += 1
        if len(trace) > trace_limit:
            raise Trap('trace limit')
        if action == 'taken' or action == 'jump':
            nxt = ins.tgt
            if pc + 1 >= np:
                raise Trap('branch delay slot past the end')
            step(prog[pc + 1], r, mem, symtab, stub, trace)
            steps += 1
            pc = nxt
            continue
        if action == 'jal':
            # delay slot first, then the call, then the fallthrough after it
            if pc + 1 < np:
                step(prog[pc + 1], r, mem, symtab, stub, trace)
                steps += 1
            sp = r[REGNUM['sp']]
            a = [r[REGNUM['a%d' % i]] for i in range(4)]
            st = [mem.lw(sp + 4 * k) for k in range(4)]
            # Registers the callee's prototype does not declare are don't-care
            # in o32: the original leaves whatever happened to be there, the
            # rebuild leaves something else, and neither callee can observe it.
            # The original's `jal` carries a bare address while the rebuild's
            # carries the callee's name, so the arity is taken from the rebuild
            # and applied to both sides positionally - masking one side only
            # would manufacture the very mismatch this is meant to ignore.
            if arity_list is not None:
                n = arity_list[len(trace)] if len(trace) < len(arity_list) else 4
            elif arities is not None:
                n = arities.get(ins.ops[-1], 4)
            else:
                n = 4
            if names is not None:
                names.append(ins.ops[-1])
            for i in range(min(max(n, 0), 4), 4):
                a[i] = 0
            # o32 spills the fifth argument onwards at sp+0x10, so they are
            # slots 4,5,... of the stack window, not slots 0,1,...
            count = max(n, 4)
            st = [mem.lw(sp + 4 * k) for k in range(count)]
            for k in range(4, count):
                st[k] = 0
            trace.append(('call', tuple(a), tuple(st)))
            r[REGNUM['v0']] = stub(len(trace) - 1, a, st)
            r[REGNUM['v1']] = stub(len(trace), a, st)
            if len(trace) > trace_limit:
                raise Trap('trace limit')
            pc += 2
            continue
        if action == 'ret':
            if pc + 1 < np:
                step(prog[pc + 1], r, mem, symtab, stub, trace)
            return r[REGNUM['v0']], trace, steps
        pc += 1


# --------------------------------------------------------------- driver


def load_symtab():
    tab = {}
    if os.path.exists(SYMTAB):
        try:
            tab = json.load(open(SYMTAB))
        except Exception:
            tab = {}
    return tab


def save_symtab(tab):
    json.dump(tab, open(SYMTAB, 'w'), indent=1, sort_keys=True)


def resolve_hexnames(tab, extra=()):
    """Fill in every `<prefix>_<8 hex digits>` name from the name itself."""
    import glob as _glob
    added = 0
    pat = re.compile(r'INCLUDE_ASM\("asm/[^"]*?/([A-Za-z_][\w]*)_'
                     r'([0-9A-Fa-f]{8})\.s"\)')
    for p in _glob.glob(os.path.join(ROOT, 'source', '**', '*.c'),
                        recursive=True):
        for name, hexpart in pat.findall(open(p, errors='ignore').read()):
            key = f'{name}_{hexpart}'
            addr = 0x80000000 | int(hexpart, 16)
            if key not in tab:
                tab[key] = addr
                added += 1
            elif tab[key] != addr:
                raise SystemExit(f'{key}: cached {tab[key]:#x}, name says {addr:#x}')
    return added


def learn_symbols(orig, gen, tab):
    """Zip the two streams and read off symbol -> address.

    Only valid when both have the same instruction count, where index
    alignment is exact by construction.

    `lui %hi(S)` on its own does not give S, only the top half; the low half
    arrives with the matching `addiu %lo(S)`. Learning S from the `lui` alone
    is how rank_dword_800E1998 once ended up cached as 0x800E0000 and every
    store through it landed 0x1998 bytes early.
    """
    learned = {}
    if len(orig) != len(gen):
        return learned
    pending = {}
    for a, b in zip(orig, gen):
        if b.m == 'lisym':
            # one instruction materialises the whole address, so the original
            # must be a single immediate load too
            if a.m != 'li32':
                continue
            addr = num(a.ops[1])
        elif b.m == 'hisym':
            if a.m != 'lui':
                continue
            pending[(b.ops[0], b.sym)] = 0x80000000 | (num(a.ops[1]) << 16)
            continue
        elif b.m == 'addiusym':
            base = pending.pop((b.ops[1], b.sym), None)
            if base is None or a.m not in ('addiu', 'addi'):
                continue
            addr = (base & 0xFFFF0000) + s16(num(a.ops[2]))
        else:
            continue
        if HEXNAME_RE.match(b.sym):
            continue        # the naming convention already answers this
        old = tab.get(b.sym)
        if old is not None and old != addr:
            raise SystemExit(f'symbol {b.sym}: cached {old:#x}, learned {addr:#x}')
        if old is None:
            learned[b.sym] = addr
            tab[b.sym] = addr
    return learned


def make_stub(seed):
    def stub(k, args, stack):
        h = (seed * 2654435761 + k * 40503) & 0xFFFFFFFF
        h ^= (args[0] * 31 + args[1] * 17 + args[2] * 7 + args[3]) & 0xFFFFFFFF
        h &= 0xFFFFFFFF
        pick = h % 6
        if pick == 0:
            return 0
        if pick == 1:
            return h & 0xFFFF
        if pick == 2:
            return h & 0xFFFF0000
        if pick == 3:
            return (h | 0x80000000) & 0xFFFFFFFF
        if pick == 4:
            return WORK_BASE + (h & 0x3FF) * 4
        return (WORK_BASE + (h & 0x3FF) * 4) | 1
    return stub


def make_memory(seed):
    mem, rnd = Mem(), random.Random(seed)
    for off in range(0, WORK_BYTES, 4):
        pick = rnd.randrange(8)
        if pick == 0:
            v = WORK_BASE + rnd.randrange(WORK_BYTES // 4) * 4
        elif pick == 1:
            v = rnd.randrange(-4, 5) & 0xFFFFFFFF
        else:
            v = rnd.randrange(0, 1 << 32)
        mem.w[WORK_BASE + off] = v
    for off in range(0, GLOBALS_BYTES, 4):
        mem.w[GLOBALS_BASE + off] = rnd.randrange(0, 1 << 32)
    for i in range(0, 0x400, 4):
        mem.w[STACK_TOP + i] = rnd.randrange(0, 1 << 32)
    return mem


def make_args(seed):
    rnd = random.Random(seed ^ 0x5EED)
    pool = [0, 1, 2, 3, 0xFFFFFFFF, 0x80000000,
            WORK_BASE, WORK_BASE + 0x40, WORK_BASE + 0x100]
    return [rnd.choice(pool) for _ in range(4)] + [STACK_TOP]


def load_pair(orig_path, obj_path, symbol):
    o_insns, base = parse_dump(orig_path)
    g_insns, labels = parse_obj(obj_path, symbol)
    o_prog = resolve_targets(expand(o_insns), {}, base)
    g_prog = resolve_targets(expand(g_insns), labels, None)
    return o_prog, g_prog


def build_arities():
    """How many parameters does each declared function take?

    The o32 contract only makes the declared arguments observable. Reading
    this out of the repo's own prototypes is what lets the trace ignore the
    register the original left behind.
    """
    if os.path.exists(ARITY):
        try:
            return json.load(open(ARITY))
        except Exception:
            pass
    out = {}
    src = os.path.join(ROOT, 'source')
    decl = re.compile(r'^[^#\n]*?\b([A-Za-z_]\w*)\s*\(([^;{()]*(?:\([^()]*\)[^;{()]*)*)\)'
                      r'\s*[;{]', re.M)
    for dirpath, _dirs, files in os.walk(src):
        for f in files:
            if not f.endswith(('.c', '.h')):
                continue
            text = open(os.path.join(dirpath, f), errors='ignore').read()
            for name, params in decl.findall(text):
                p = params.strip()
                if not p or p == 'void':
                    n = 0
                elif p == '...':
                    continue
                else:
                    # count top-level commas
                    depth, n = 0, 1
                    for ch in p:
                        if ch in '([':
                            depth += 1
                        elif ch in ')]':
                            depth -= 1
                        elif ch == ',' and depth == 0:
                            n += 1
                prev = out.get(name)
                if prev is None or (prev == 0 and n > 0):
                    out[name] = n
    json.dump(out, open(ARITY, 'w'), indent=0, sort_keys=True)
    return out


def returns_void(obj_path, symbol):
    """Is the function declared void? v0 is not observable for those, so a
    difference there is scratch, not behaviour."""
    rel = obj_path.replace('\\', '/')
    if '/obj/' in rel:
        rel = rel.split('/obj/', 1)[1]
    elif rel.startswith('obj/'):
        rel = rel[4:]
    base = re.sub(r'\.(c\.)?asm$', '.c', rel)
    src = os.path.join(ROOT, 'source', base)
    if not os.path.exists(src):
        return False
    pat = re.compile(r'^\s*(?:static\s+|extern\s+|inline\s+)*'
                     r'([A-Za-z_][\w \t*]*?)\s+' + re.escape(symbol) +
                     r'\s*\([^;{]*\)\s*[{;]', re.M)
    m = pat.search(open(src, errors='ignore').read())
    if not m:
        return False
    return m.group(1).strip() == 'void'


def compare(orig_path, obj_path, symbol, trials=120, verbose=False):
    tab = load_symtab()
    nhex = resolve_hexnames(tab)
    if nhex and verbose:
        print(f'  {nhex} simboli con suffisso esadecimale risolti dal nome')
    o_prog, g_prog = load_pair(orig_path, obj_path, symbol)
    learned = learn_symbols(o_prog, g_prog, tab)
    if learned or nhex:
        save_symtab(tab)
        if verbose:
            names = ', '.join(sorted(learned)[:5])
            print(f'  imparati {len(learned)} simboli ({names}'
                  f'{", ..." if len(learned) > 5 else ""})')
    void_ret = returns_void(obj_path, symbol)
    arities = build_arities()
    if verbose:
        print(f'  ritorno: {"void (v0 non osservabile)" if void_ret else "valore"};'
              f' {len(arities)} prototipi noti')
    bad = None
    used = 0
    for t in range(trials):
        args = make_args(0x2000 + t)
        stub = make_stub(0x3000 + t)
        mem_b = make_memory(0x1000 + t)
        names = []
        limit = 8000
        trap_b = None
        try:
            vb, tb, sb = run(g_prog, tab, mem_b, args, stub,
                              arities=arities, names=names,
                              trace_limit=limit)
        except Unsupported as e:
            return 'SKIP', f'generato: {e}', 0
        except Trap as e:
            vb, tb, trap_b = None, [], str(e)
        mem_a = make_memory(0x1000 + t)
        trap_a = None
        try:
            va, ta, sa = run(o_prog, tab, mem_a, args, stub,
                             arity_list=[arities.get(n, 4) for n in names],
                             trace_limit=limit)
        except Unsupported as e:
            return 'SKIP', f'originale: {e}', 0
        except Trap as e:
            va, ta, trap_a = None, [], str(e)
        if trap_a or trap_b:
            # A loop that runs past the trace limit tells us nothing: the two
            # sides may still agree, we simply did not let them finish. Only a
            # side that stops while the other one runs counts as a difference.
            if 'trace limit' in (trap_a or '') or 'trace limit' in (trap_b or ''):
                continue
            used += 1
            bad = bad or (f'trial {t}: originale {trap_a or "ok"}, '
                          f'generato {trap_b or "ok"}')
            continue
        used += 1
        if va != vb and not void_ret:
            bad = bad or f'trial {t}: v0 {va:#010x} != {vb:#010x}'
            continue
        if ta != tb:
            k = next((i for i in range(min(len(ta), len(tb))) if ta[i] != tb[i]),
                     None)
            if k is None:
                # same calls so far, but one side made more of them
                bad = bad or (f'trial {t}: numero di chiamate diverso '
                              f'({len(ta)} contro {len(tb)})')
            else:
                bad = bad or (f'trial {t}: chiamata {k} diversa\n'
                              f'      {ta[k]}\n      {tb[k]}')
            continue
        da, db = mem_a.snapshot(), mem_b.snapshot()
        # The stack frame is private scratch: a slot one side spills and the
        # other does not is invisible to the program, because the only way to
        # read it back is from inside the function, which both sides do. The
        # original often saves a register the rebuild never needed.
        floor = args[4]
        diff = [a for a in sorted(set(da) | set(db))
                if a >= floor and da.get(a) != db.get(a)]
        if diff:
            a = diff[0]
            bad = bad or (f'trial {t}: memoria diversa a {a:#010x}: '
                          f'{da.get(a, 0):#010x} != {db.get(a, 0):#010x}')
            continue
        if verbose and t < 3:
            print(f'  trial {t}: coincide ({sa} istruzioni)')
    if bad:
        return 'MISMATCH', bad, used
    return 'EQUIV', f'{used} prove, nessuna divergenza', used


def P(m, *ops):
    return Insn(m, [str(x) for x in ops])


def PS(m, *ops, sym=None):
    i = Insn(m, [str(x) for x in ops], 0x80000000)
    if sym is not None:
        i.sym = sym
    return i


# `lui %hi(S)` + `addiu %lo(S)` must land on the same address as one immediate
# load. Getting this wrong put rank_dword_800E1998 at 0x800E0000 and every
# store through it 0x1998 bytes early, so it gets its own case.
SYM_REF = [P('li32', 2, 0x800E1998), P('sw', 4, 0, 2), P('jr', 'ra'), P('nop')]
SYM_HILO = [PS('hisym', 2, 'rank_dword_800E1998', sym='rank_dword_800E1998'),
            PS('addiusym', 2, 2, 'rank_dword_800E1998',
               sym='rank_dword_800E1998'),
            P('sw', 4, 0, 2), P('jr', 'ra'), P('nop')]
# 0x800C94A8 has bit 15 set in its low half, so %hi rounds up to 0x800D and
# %lo is negative. Truncating %hi turns the address into 0x800B94A8.
SYM_NEG_LO = [P('li32', 2, 0x800C94A8), P('sw', 4, 0, 2), P('jr', 'ra'), P('nop')]
SYM_NEG_LO_HILO = [PS('hisym', 2, 's14e_800C94A8', sym='s14e_800C94A8'),
                   PS('addiusym', 2, 2, 's14e_800C94A8', sym='s14e_800C94A8'),
                   P('sw', 4, 0, 2), P('jr', 'ra'), P('nop')]
# `lh $5,%lo(name)($2)` and the flattened three-operand form must agree
SYM_LO_OFF = [P('li32', 2, 0x800E1998), P('lh', 4, 0, 2), P('jr', 'ra'), P('nop')]
SYM_LO_OFF_HILO = [P('li32', 2, 0x800E1998),
                   P('lh', 4, '%lo(rank_dword_800E1998)', 2),
                   P('jr', 'ra'), P('nop')]


def build(seq):
    """Make a program from a literal listing; branch operands are *indices*."""
    prog = []
    for i in seq:
        c = Insn(i.m, list(i.ops), 0x80000000 + 4 * len(prog))
        c.sym = i.sym          # symbol reference, if any
        prog.append(c)
    for ins in prog:
        if (ins.m in BRANCH2 or ins.m in BRANCH3 or ins.m == 'j') and ins.ops:
            ins.tgt = int(ins.ops[-1])
    return prog


# The exact shape cc1psx mis-compiles, straight out of probe_h: the conditional
# negate is pulled into the delay slot of the branch that skips it. For a >= 0
# the branch *is* taken, the delay slot still runs, and the function returns -a.
BROKEN_NEGATE = [P('addu', 2, 4, 'zero'), P('bgez', 2, 4),
                 P('subu', 2, 'zero', 4), P('nop'), P('jr', 'ra'), P('nop')]
# The same logic with the negate out of the delay slot (index 3 is the hole).
RIGHT_NEGATE = [P('addu', 2, 4, 'zero'), P('nop'), P('bgez', 2, 6), P('nop'),
                P('subu', 2, 'zero', 4), P('nop'), P('jr', 'ra'), P('nop')]
RETURN_A1 = [P('addiu', 2, 4, 1), P('jr', 'ra'), P('nop')]
RETURN_A2 = [P('addiu', 2, 4, 2), P('jr', 'ra'), P('nop')]
STORE_4 = [P('addiu', 2, 4, 1), P('sw', 4, 0x10, '$4'), P('jr', 'ra'), P('nop')]
STORE_5 = [P('addiu', 2, 4, 1), P('sw', 4, 0x10, '$4'), P('addiu', 2, 2, 1),
           P('jr', 'ra'), P('nop')]
CALL_A0 = [P('jal', 0x80000800), P('nop'), P('jr', 'ra'), P('nop')]
CALL_A1 = [P('addiu', 4, 4, 4), P('jal', 0x80000800), P('nop'), P('jr', 'ra'),
           P('nop')]
# arguments set up in the jal's own delay slot: the call can only be recorded
# after that slot has run, which is the whole point of the o32 convention
CALL_DS_7 = [P('addiu', 4, 4, 1), P('jal', 0x80000800), P('addiu', 5, 'zero', 7),
             P('jr', 'ra'), P('nop')]
CALL_DS_8 = [P('addiu', 4, 4, 1), P('jal', 0x80000800), P('addiu', 5, 'zero', 8),
             P('jr', 'ra'), P('nop')]
# every negative immediate has to survive sign extension
NEG_A = [P('addiu', 2, 'zero', -1), P('jr', 'ra'), P('nop')]
NEG_A2 = [P('addiu', 2, 'zero', -2), P('jr', 'ra'), P('nop')]
NEG_OFF = [P('addiu', 2, 4, 0), P('addiu', 2, 2, -1), P('jr', 'ra'), P('nop')]
NEG_OFF2 = [P('addiu', 2, 4, 0), P('addiu', 2, 2, -2), P('jr', 'ra'), P('nop')]
LOOP2 = [P('addiu', 2, 'zero', 0), P('addiu', 2, 2, 1), P('slti', 3, 2, 2),
         P('bnez', 3, 1), P('jr', 'ra'), P('nop')]


def one_trial(prog, seed):
    args = make_args(0x2000 + seed)
    stub = make_stub(0x3000 + seed)
    mem = make_memory(0x1000 + seed)
    v, t, s = run(prog, load_symtab(), mem, args, stub)
    return v, t, mem.snapshot()


def same(a, b, seed):
    va, ta, ma = one_trial(a, seed)
    vb, tb, mb = one_trial(b, seed)
    if va != vb or ta != tb:
        return False
    return all(ma.get(k) == mb.get(k) for k in set(ma) | set(mb))


def selftest():
    # a poisoned cache entry must not beat the naming convention
    poisoned = {'rank_dword_800E1998': 0x800E0000}
    if lookup(poisoned, 'rank_dword_800E1998') != 0x800E1998:
        print('  FAIL il nome col suffisso esadecimale perde contro la cache')
        return 1
    cases = [
        ('identici', RETURN_A1, RETURN_A1, True),
        ('ritorno diverso', RETURN_A1, RETURN_A2, False),
        ('memoria diversa', STORE_4, STORE_5, False),
        ('argomento di chiamata diverso', CALL_A0, CALL_A1, False),
        ('argomenti nel delay slot di jal', CALL_DS_7, CALL_DS_7, True),
        ('delay slot di jal, arg. diversi', CALL_DS_7, CALL_DS_8, False),
        ('stesso ciclo', LOOP2, LOOP2, True),
        ('negate nel delay slot', RIGHT_NEGATE, BROKEN_NEGATE, False),
        ('immediato -1', NEG_A, NEG_A, True),
        ('immediato -1 vs -2', NEG_A, NEG_A2, False),
        ('offset di memoria negativo', NEG_OFF, NEG_OFF, True),
        ('offset -1 vs -2', NEG_OFF, NEG_OFF2, False),
        ('%hi/%lo come immediato', SYM_REF, SYM_HILO, True),
        ('%hi arrotondato, %lo negativo', SYM_NEG_LO, SYM_NEG_LO_HILO, True),
        ('%lo come offset di memoria', SYM_LO_OFF, SYM_LO_OFF_HILO, True),
    ]
    ok = True
    for name, a, b, want_equal in cases:
        pa, pb = build(a), build(b)
        got = all(same(pa, pb, s) for s in range(6))
        if got != want_equal:
            ok = False
        mark = 'ok  ' if got == want_equal else 'FAIL'
        print(f'  {mark} {name:<32} atteso '
              f'{"uguale" if want_equal else "diverso"}')
    print('\nselftest:', 'passato' if ok else 'FALLITO')
    return 0 if ok else 1


def main():
    a = sys.argv[1:]
    if a and a[0] == '--selftest':
        return selftest()
    if len(a) < 3:
        raise SystemExit(__doc__)
    trials = 120
    if '--trials' in a:
        i = a.index('--trials')
        trials = int(a[i + 1])
        del a[i:i + 2]
    verdict, detail, used = compare(a[0], a[1], a[2], trials, verbose=True)
    print(f'{a[2]}: {verdict} - {detail}')
    return 0 if verdict != 'MISMATCH' else 1


if __name__ == '__main__':
    sys.exit(main())