#!/usr/bin/env python3
"""List undecopiled overlay functions in mgs_reversing, ranked by size.

For every `#pragma INCLUDE_ASM("...s")` found under source/overlays we recover the
function symbol from the .s `xdef` line, disassemble it, reject pure-epilogue
artifacts (functions the compiler merged with the preceding one) and look up the
declaration/definition of that symbol in the sources.

Usage: rank_targets.py [max_insns] [limit]
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


def load(path):
    code = b''
    addr = None
    sym = None
    with open(path) as f:
        for line in f:
            m = re.match(r'\s*xdef\s+(\S+)', line)
            if m and sym is None:
                sym = m.group(1)
            if line.strip().startswith('dw '):
                if addr is None:
                    addr = int(line.split(';')[1].strip(), 16)
                code += struct.pack('<I', int(line.split(' ')[1], 16))
    return sym, addr, code


def instructions(code, addr):
    out = []
    for ins in MD.disasm(code, addr):
        out.append((ins.address, ins.mnemonic, ins.op_str))
    return out


# pure epilogue artifacts: only register restores + jr/addiu sp, no prologue
RESTORE = re.compile(r'^(lw|addiu|move|jr|nop|sll)\b')
def is_epilogue_only(insns):
    if not insns:
        return False
    seen_jr = False
    for _, mn, op in insns:
        if mn == 'jr' and op == '$ra':
            seen_jr = True
        elif seen_jr:
            continue
        elif mn == 'nop':
            continue
        elif mn == 'jr':
            return False
        elif mn in ('lw', 'addiu') and ('($sp)' in op or op.startswith('$sp,')):
            continue
        else:
            return False
    return seen_jr


def main():
    max_insns = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 40

    sources = {}
    for cf in glob.glob(os.path.join(ROOT, 'source', '**', '*.c'), recursive=True):
        sources[cf] = open(cf).read()
    headers = ''
    for hf in glob.glob(os.path.join(ROOT, 'source', '**', '*.h'), recursive=True):
        headers += open(hf).read()

    rows = []
    for cf, text in sources.items():
        for m in re.finditer(r'#pragma INCLUDE_ASM\("([^"]+\.s)"\)', text):
            apath = os.path.join(ROOT, m.group(1))
            if not os.path.exists(apath):
                continue
            sym, addr, code = load(apath)
            if not sym:
                continue
            insns = instructions(code, addr)
            if not insns or len(insns) > max_insns:
                continue
            if is_epilogue_only(insns):
                continue
            decl = ''
            for pat in (rf'^[^\n;]*\b{re.escape(sym)}\s*\([^;]*\)\s*;',
                        rf'^[^\n;]*\b{re.escape(sym)}\s*\([^;{{]*\)\s*\{{'):
                dm = re.search(pat, text, re.M)
                if dm:
                    decl = ' '.join(dm.group(0).split())
                    break
            if not decl:
                for pat in (rf'^[^\n;]*\b{re.escape(sym)}\s*\([^;]*\)\s*;',):
                    dm = re.search(pat, headers, re.M)
                    if dm:
                        decl = ' '.join(dm.group(0).split()) + '   [header]'
                        break
            if not decl:
                decl = '*** NO DECL FOUND ***'
            rows.append((len(insns), m.group(1), sym, decl, os.path.relpath(cf, ROOT)))

    rows.sort()
    print(f'{len(rows)} candidate functions (epilogue artifacts excluded)\n')
    for n, ap, sym, decl, cf in rows[:limit]:
        print(f'{n:4d} insn  {sym}')
        print(f'        asm: {ap}')
        print(f'        src: {cf}')
        print(f'        decl: {decl}')
    return 0


if __name__ == '__main__':
    sys.exit(main())