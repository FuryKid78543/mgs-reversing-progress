#!/usr/bin/env python3
"""Disassemble one or more of the repo's `dw` dumps the way cc1psx spells them.

usage: disasm.py <dump.s> [dump.s ...]
"""
import re
import struct
import sys

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32

MD = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32)


def load(path):
    code = b''
    addr = None
    for line in open(path, errors='ignore'):
        if line.strip().startswith('dw '):
            if addr is None:
                m = re.search(r';\s*([0-9a-fA-F]+)\s*$', line)
                if m:
                    addr = int(m.group(1), 16)
            code += struct.pack('<I', int(line.split()[1], 16))
    return addr, code


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    first = True
    for path in sys.argv[1:]:
        addr, code = load(path)
        if addr is None or not code:
            print(f'; {path}: no instructions')
            continue
        insns = list(MD.disasm(code, addr))
        if not first:
            print()
        first = False
        end = addr + len(code) - 4
        print(f'; {path}  (0x{addr:X} - 0x{end:X}, {len(insns)} insns)')
        for ins in insns:
            print(f'  {ins.address:08X}: {ins.mnemonic:<9} {ins.op_str}')
    return 0


if __name__ == '__main__':
    sys.exit(main())