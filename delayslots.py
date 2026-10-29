#!/usr/bin/env python3
"""Measure how the original dumps fill branch delay slots.

Two questions we need data for:

1. Does the original code ever put a *conditional-path* instruction in the delay
   slot of a conditional branch?  Doing so changes the meaning (delay slots always
   execute), so if Konami's compiler never does it and cc1psx does, a class of
   functions is simply unreachable with this toolchain.

2. How often is the delay slot left empty (`nop`) right after a conditional
   branch?  That is the baseline rate.

usage: delayslots.py [--detail]
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

COND_BRANCH = {'beq', 'bne', 'beqz', 'bnez', 'blez', 'bgtz', 'bltz', 'bgez',
               'slt', 'sltu', 'slti', 'sltiu'}
UNCOND = {'j', 'jr', 'jal', 'jalr', 'b'}
# `negu rd,rs` and `subu rd,$zero,rs` are the same encoding; both negate
# unconditionally, so they only make sense outside a conditional path.
NEGATE = {'negu'}


def negate_forms(ins):
    m, op = ins.mnemonic, ins.op_str
    if m in NEGATE:
        return True
    if m == 'subu':
        ops = [x.strip() for x in op.split(',')]
        return len(ops) == 3 and ops[1] == '$zero'
    return False


def main():
    detail = '--detail' in sys.argv
    stats = dict(cond=0, nop=0, filled=0, neg_fill=0, others=0)
    others = []
    files = sorted(glob.glob(os.path.join(ROOT, 'asm', '**', '*.s'),
                             recursive=True))
    for path in files:
        code = b''
        addr = None
        for line in open(path, errors='ignore'):
            if line.strip().startswith('dw '):
                if addr is None:
                    m = re.search(r';\s*(0x[0-9a-fA-F]+|[0-9a-fA-F]+)\s*$',
                                  line)
                    if m:
                        addr = int(m.group(1), 16)
                code += struct.pack('<I', int(line.split()[1], 16))
        if addr is None:
            continue
        ins = list(MD.disasm(code, addr))
        for i in range(len(ins) - 1):
            cur, nxt = ins[i], ins[i + 1]
            if cur.mnemonic not in COND_BRANCH:
                continue
            stats['cond'] += 1
            if nxt.mnemonic == 'nop':
                stats['nop'] += 1
            else:
                stats['filled'] += 1
                if negate_forms(nxt):
                    stats['neg_fill'] += 1
                    if len(others) < 20:
                        others.append((os.path.relpath(path, ROOT),
                                       cur.address, cur.mnemonic, cur.op_str,
                                       nxt.mnemonic, nxt.op_str))
                elif detail:
                    if len(others) < 20:
                        others.append((os.path.relpath(path, ROOT),
                                       cur.address, cur.mnemonic, cur.op_str,
                                       nxt.mnemonic, nxt.op_str))

    print(f'scanned {len(files)} dump files')
    print(f'  conditional branches      : {stats["cond"]}')
    print(f'  delay slot = nop          : {stats["nop"]} '
          f'({100.0 * stats["nop"] / max(stats["cond"], 1):.1f}%)')
    print(f'  delay slot filled         : {stats["filled"]} '
          f'({100.0 * stats["filled"] / max(stats["cond"], 1):.1f}%)')
    print(f'  ... filled with a negate  : {stats["neg_fill"]}')
    for o in others:
        print(f'      {o[0]} @{o[1]:08X}  {o[2]} {o[3]} / {o[4]} {o[5]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())