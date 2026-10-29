#!/usr/bin/env python3
"""Rank undecopiled overlay functions by how easy they are to express correctly.

For every function still behind an INCLUDE_ASM pragma we disassemble the dump and
collect the struct offsets it touches, then check those offsets against the `Work`
struct declared in the same .c file. Offsets that fall inside a `pad` region mean
the struct still needs reconstruction, which is the expensive part of writing the
C; functions whose offsets are all already named are the quick, high-confidence
wins.

usage: rank_easy.py [max_insns] [limit]
"""
import glob
import os
import re
import struct
import sys

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32

ROOT = os.environ.get('MGS_ROOT') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'mgs_rev2')
MD = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32)
MD.detail = True

PRAGMA = re.compile(r'#pragma INCLUDE_ASM\("([^"]+\.s)"\)')
PAD = re.compile(r'/\*\s*(0x[0-9A-Fa-f]+)\s*\*/\s*char\s+(pad\w*)\[', re.I)
OFF = re.compile(r'/\*\s*(0x[0-9A-Fa-f]+)\s*\*/')


def load_dump(path):
    code = b''
    addr = None
    for line in open(path):
        if line.strip().startswith('dw '):
            if addr is None:
                addr = int(line.split(';')[1].strip(), 16)
            code += struct.pack('<I', int(line.split(' ')[1], 16))
    return addr, code


def struct_regions(text):
    """Offsets covered by `char padX[...]` declarations, as (start, end) pairs."""
    regions = []
    for m in PAD.finditer(text):
        start = int(m.group(1), 16)
        # find the matching size in the same declaration
        tail = text[m.start():m.start() + 120]
        sz = re.search(r'\[\s*(0x[0-9A-Fa-f]+|\d+)\s*\]', tail)
        if sz:
            n = int(sz.group(1), 0)
            regions.append((start, start + n))
    return regions


def offsets_used(addr, code):
    """Memory offsets referenced with a0 as base (i.e. struct fields)."""
    out = set()
    for ins in MD.disasm(code, addr):
        op = ins.op_str
        m = re.search(r'(-?0x[0-9a-fA-F]+|\d+)\(\$a0\)$', op)
        if m:
            out.add(int(m.group(1), 0) & 0xFFFF)
    return out


def is_epilogue_only(insns):
    seen_jr = False
    for ins in insns:
        mn, op = ins.mnemonic, ins.op_str
        if mn == 'jr' and op == '$ra':
            seen_jr = True
        elif seen_jr or mn == 'nop':
            continue
        elif mn in ('lw', 'addiu') and ('($sp)' in op or op.startswith('$sp,')):
            continue
        else:
            return False
    return seen_jr


def main():
    max_insns = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 25

    rows = []
    for cf in glob.glob(os.path.join(ROOT, 'source', 'overlays', '**', '*.c'), recursive=True):
        text = open(cf).read()
        pads = struct_regions(text)
        for m in PRAGMA.finditer(text):
            ap = os.path.join(ROOT, m.group(1))
            if not os.path.exists(ap):
                continue
            addr, code = load_dump(ap)
            if addr is None:
                continue
            insns = list(MD.disasm(code, addr))
            if not insns or len(insns) > max_insns or is_epilogue_only(insns):
                continue
            used = offsets_used(addr, code)
            in_pad = sorted(o for o in used
                            if any(lo <= o < hi for lo, hi in pads))
            calls = sum(1 for ins in insns
                        if ins.mnemonic in ("jal", "jalr") or (ins.mnemonic == "j" and ins.op_str != "$ra"))
            rows.append((len(in_pad), calls, len(insns),
                         m.group(1), os.path.relpath(cf, ROOT),
                         ','.join(f'0x{o:X}' for o in sorted(used)),
                         ','.join(f'0x{o:X}' for o in in_pad)))

    rows.sort()
    print(f'{len(rows)} candidates (struct gaps first, then no calls)\n')
    print(f'{"pad":>3} {"call":>4} {"insn":>4}  function')
    for n_pad, calls, n, ap, cf, used, inp in rows[:limit]:
        print(f'{n_pad:>3} {calls:>4} {n:>4}  {os.path.basename(ap)}')
        print(f'          file: {cf}')
        print(f'          uses {used}' + (f'   IN PAD: {inp}' if inp else '   (all named)'))
    return 0


if __name__ == '__main__':
    sys.exit(main())