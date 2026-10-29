#!/usr/bin/env python3
"""Rank INCLUDE_ASM functions by how likely cc1psx is to reproduce them exactly.

Empirical rule from the verified matches so far: every function we matched
bit-for-bit had no empty delay slot anywhere, and every function we failed on had
at least one. Konami's compiler left those slots empty; cc1psx fills them with
whatever it can hoist, so those functions are unreachable with this toolchain.

`delayslots.py` measures the 17.1% baseline of empty conditional-branch delay
slots in the original dumps. This tool scores each candidate on:

  holes   - delay slots that are `nop` in the original (lower is better, 0 ideal)
  calls   - `jal` sites, where cc1psx also tends to fill a slot the original left
  gaps    - struct offsets still sitting inside a `pad` region (needs struct work)
  insns   - raw size

usage: rank_safe.py [max_insns] [limit]
"""
import glob
import os
import re
import struct
import sys
from collections import defaultdict

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32

ROOT = os.environ.get('MGS_ROOT') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'mgs_rev2')
MD = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32)
MD.detail = True
from capstone.mips_const import MIPS_OP_MEM

PRAGMA = re.compile(r'#pragma INCLUDE_ASM\("([^"]+\.s)"\)')
PAD = re.compile(r'/\*\s*(0x[0-9A-Fa-f]+)\s*\*/\s*char\s+(pad\w*)\[')

COND_BRANCH = {'beq', 'bne', 'beqz', 'bnez', 'blez', 'bgtz', 'bltz', 'bgez',
               'slt', 'sltu'}
CALL = {'jal', 'jalr'}


def load_dump(path):
    code = b''
    addr = None
    try:
        for line in open(path, errors='ignore'):
            if line.strip().startswith('dw '):
                if addr is None:
                    m = re.search(r';\s*([0-9a-fA-F]+)\s*$', line)
                    if m:
                        addr = int(m.group(1), 16)
                code += struct.pack('<I', int(line.split()[1], 16))
    except OSError:
        return None, b''
    return addr, code


def pad_regions(text):
    regions = []
    for m in PAD.finditer(text):
        start = int(m.group(1), 16)
        sz = re.search(r'\[\s*(0x[0-9A-Fa-f]+|\d+)\s*\]',
                       text[m.start():m.start() + 120])
        if sz:
            regions.append((start, start + int(sz.group(1), 0)))
    return regions


def struct_offsets(addr, code):
    out = set()
    for ins in MD.disasm(code, addr):
        m = re.search(r'(-?0x[0-9a-fA-F]+|\d+)\(\$a0\)$', ins.op_str)
        if m:
            out.add(int(m.group(1), 0) & 0xFFFF)
    return out


def global_refs(insns):
    """Absolute addresses read through a `lui` pair: `lui r,H` + `lw r2,off(r)`.

    A function that touches an address the repo never declares would need a new
    global, which moves the overlay's data layout. Without a working linker that
    cannot be checked, so those functions are ranked last.
    """
    hi = {}
    out = set()
    for ins in insns:
        if ins.mnemonic == 'lui':
            hi[ins.reg_name(ins.operands[0].reg)] = ins.operands[1].imm << 16
            continue
        if ins.mnemonic not in ('lw', 'sw', 'lhu', 'lhu', 'sh', 'lb',
                                'lbu', 'sb', 'addiu', 'addi'):
            continue
        mem = ins.operands[-1]
        # `lui r,H` then `addiu r2,r,X` builds a constant address without ever
        # touching memory; missing that form hides undeclared globals
        if ins.mnemonic in ('addiu', 'addi') and len(ins.operands) == 3:
            base = ins.reg_name(ins.operands[1].reg)
            if base in hi:
                out.add((hi[base] + ins.operands[2].imm) & 0xFFFFFFFF)
            continue
        if mem.type != MIPS_OP_MEM:
            continue
        base = ins.reg_name(mem.mem.base)
        if base in hi:
            out.add((hi[base] + mem.mem.disp) & 0xFFFFFFFF)
    return out


KNOWN_GLOBALS = None


def known_globals():
    """address -> set of symbol-name prefixes declared for it.

    `800C3480` exists in the tree but only as `brf_dword_800C3480`; that does
    not help `s07b_800CC6CC`, which lives in a different overlay with its own
    data. So an address only counts as declared if some identifier carries the
    caller's overlay prefix.
    """
    global KNOWN_GLOBALS
    if KNOWN_GLOBALS is None:
        KNOWN_GLOBALS = {}
        ident = re.compile(r'\b([A-Za-z_]\w*?)_?([0-9A-Fa-f]{7,8})\b')
        for pat in ('source/**/*.c', 'source/**/*.h'):
            for f in glob.glob(os.path.join(ROOT, pat), recursive=True):
                text = open(f, errors='ignore').read()
                for m in ident.finditer(text):
                    pre = m.group(1).split('_')[0]
                    KNOWN_GLOBALS.setdefault(int(m.group(2), 16),
                                             set()).add(pre.lower())
    return KNOWN_GLOBALS


def prefix_of(sym):
    """`s07b_800CC6CC` -> `s07b`"""
    return sym.split('_')[0].lower()


def count_holes(insns):
    """Instructions `cc1psx` provably cannot reproduce.

    Two kinds, both measured against the original dumps rather than assumed:
    a `nop` sitting in a branch or call delay slot that the original leaves
    empty (only 17.1% of those are empty in the first place, and Konami's
    compiler never puts a conditional negate there), and the `nop` some
    epilogues carry between `lw $ra` and `jr $ra` - the PSX has no load delay
    slot, so cc1psx has no reason to emit one, while 7.2% of the original
    epilogues have it.
    """
    holes = 0
    for i, ins in enumerate(insns):
        nxt = insns[i + 1] if i + 1 < len(insns) else None
        if ins.mnemonic in COND_BRANCH or ins.mnemonic in CALL \
                or ins.mnemonic in ('j', 'jr'):
            if nxt is not None and nxt.mnemonic == 'nop':
                holes += 1
    for i, ins in enumerate(insns):
        if ins.mnemonic in ('lw', 'lh', 'lhu', 'lb', 'lbu') \
                and i + 2 < len(insns) \
                and insns[i + 1].mnemonic == 'nop' \
                and insns[i + 2].mnemonic == 'jr':
            holes += 1
    return holes


def is_artifact(insns):
    """Prologue-only or epilogue-only fragments left over by smart_split_s_file."""
    if not insns:
        return True
    body = [i.mnemonic for i in insns]
    if 'jr' not in body:
        return True
    # only stack traffic and the final jr
    real = [m for m in body
            if m not in ('nop', 'jr', 'addiu', 'lw', 'sw', 'move')]
    return not real


def main():
    max_insns = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    rows = []
    for cf in glob.glob(os.path.join(ROOT, 'source', 'overlays', '**', '*.c'),
                        recursive=True):
        text = open(cf, errors='ignore').read()
        pads = pad_regions(text)
        for m in PRAGMA.finditer(text):
            ap = os.path.join(ROOT, m.group(1))
            addr, code = load_dump(ap)
            if addr is None or not code:
                continue
            insns = list(MD.disasm(code, addr))
            if not insns or len(insns) > max_insns or is_artifact(insns):
                continue
            used = struct_offsets(addr, code)
            gaps = sorted(o for o in used
                          if any(lo <= o < hi for lo, hi in pads))
            calls = sum(1 for i in insns if i.mnemonic in CALL)
            kg = known_globals()
            me = prefix_of(os.path.basename(ap)[:-2])
            unknown = sorted(a for a in global_refs(insns)
                             if me not in kg.get(a, ()))
            rows.append((count_holes(insns), len(gaps), len(unknown), calls,
                         len(insns), os.path.basename(ap),
                         os.path.relpath(cf, ROOT),
                         'gaps: ' + ','.join(f'0x{o:X}' for o in gaps)
                         if gaps else 'struct complete'))

    rows.sort()
    clean = [r for r in rows if r[0] == 0 and r[1] == 0 and r[2] == 0]
    print(f'{len(rows)} candidates; {len(clean)} with no delay-slot hole, '
          f'no struct gap and no undeclared global\n')
    print(f'{"hole":>4} {"gap":>3} {"gld":>3} {"call":>4} {"insn":>4}  function')
    for holes, gaps, ung, calls, n, ap, cf, note in rows[:limit]:
        print(f'{holes:>4} {gaps:>3} {ung:>3} {calls:>4} {n:>4}  {ap}')
        print(f'                    {cf}')
        print(f'                    {note}')
    return 0


if __name__ == '__main__':
    sys.exit(main())