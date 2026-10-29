#!/usr/bin/env python3
"""Find already-decompiled twins for the functions still behind INCLUDE_ASM.

`build/find_duplicates.py` compares obj/*.obj, which we cannot produce (aspsx
crashes under wibo), and it only knows about asm/*.s dumps. This version works
from what we do have:

  * left-hand side  - the original dumps under asm/**, i.e. everything that is
    still `INCLUDE_ASM`;
  * right-hand side - the *compiled* C under obj/**, skipping every function that
    still has a pragma (the build inlines those dumps verbatim, so they would
    otherwise compare as perfect matches against themselves).

The signature is the mnemonic sequence with register operands kept and every
immediate / load-store displacement dropped, so two functions match even when
they address different struct fields. An exact signature hit is a strong hint
that the C for one side can be adapted verbatim for the other.

usage: twins.py [max_insns] [limit]
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
OBJ_DIR = os.environ.get('MGS_OBJ_DIR', 'obj')
MD = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32)
MD.detail = True

PRAGMA = re.compile(r'#pragma INCLUDE_ASM\("([^"]+\.s)"\)')
ENT = re.compile(r'^\s*\.ent\s+(\S+)')
END = re.compile(r'^\s*\.end\s+(\S+)')
# directives and assembler bookkeeping that are not instructions
DIRECTIVE = re.compile(r'^\s*[.#]|\.set\b|\.frame\b|\.mask\b|\.fmask\b|'
                       r'\.align\b|\.globl\b|\.ent\b|\.end\b|\.size\b|'
                       r'\.type\b|\.data\b|\.section\b|\.lcomm\b|\.comm\b|'
                       r'\.extern\b|\.space\b|\.byte\b|\.word\b|\.half\b')

# registers are $0..$31; keeping them lets us distinguish e.g. `sw v0,8(a0)`
# from `sw a1,8(a0)`, while dropping immediates keeps struct offsets irrelevant
MEM = re.compile(r'(-?0x[0-9a-fA-F]+|\d+)?\s*\(\s*(\$\w+)\s*\)')
BRANCH_TARGET = re.compile(r'^0x[0-9a-fA-F]+$')
REG = re.compile(r'\$\w+')


def signature(insns):
    """Mnemonics plus register operands, with every immediate dropped."""
    out = []
    for ins in insns:
        if DIRECTIVE.match(ins) or ins.strip().startswith('#'):
            continue
        parts = ins.strip().split(None, 1)
        if len(parts) != 2 or not parts[0]:
            if parts:
                out.append(parts[0])
            continue
        mn, op = parts

        # Registers first: `\$2` would otherwise be eaten by the numeric
        # immediate mask below, since `$` is a non-word character.
        regs = [r[1:] for r in REG.findall(op)]
        op = REG.sub('R', op)
        op = MEM.sub(lambda m: '(' + m.group(2) + ')', op)
        op = re.sub(r'\b-?0x[0-9a-fA-F]+\b|\b\d+\b', 'I', op)
        op = re.sub(r'<I(\d)>', lambda m: regs[int(m.group(1))], op)
        out.append(mn + ' ' + op)
    return tuple(out)


def dump_sig(path):
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
        return None
    if addr is None or not code:
        return None
    return tuple(f'{i.mnemonic} {i.op_str}' for i in MD.disasm(code, addr))


def compiled_sigs():
    """Signatures of every function the C compiler produced, keyed by name."""
    sigs = {}
    for path in glob.glob(os.path.join(ROOT, OBJ_DIR, '**', '*.asm'),
                          recursive=True):
        name = None
        buf = []
        try:
            fh = open(path, errors='ignore')
        except OSError:
            continue
        with fh:
            for line in fh:
                m = ENT.match(line)
                if m:
                    name, buf = m.group(1), []
                    continue
                m = END.match(line)
                if m and name:
                    sigs[name] = signature(buf)
                    name = None
                    continue
                if name is not None:
                    buf.append(line)
    return sigs


def main():
    max_insns = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    pragma_files = set()
    for cf in glob.glob(os.path.join(ROOT, 'source', '**', '*.c'),
                        recursive=True):
        text = open(cf, errors='ignore').read()
        for m in PRAGMA.finditer(text):
            pragma_files.add(os.path.basename(m.group(1))[:-2])

    done = compiled_sigs()
    by_sig = defaultdict(list)
    for name, sig in done.items():
        by_sig[sig].append(name)
    print(f'{len(done)} functions compiled from C')
    todo = defaultdict(list)
    skipped = 0
    for ap in sorted(glob.glob(os.path.join(ROOT, 'asm', '**', '*.s'),
                               recursive=True)):
        sym = os.path.basename(ap)[:-2]
        if sym not in pragma_files:
            skipped += 1
            continue
        raw = dump_sig(ap)
        if not raw or len(raw) > max_insns:
            continue
        todo[signature(raw)].append(sym)

    hits = []
    for sig, syms in todo.items():
        twins = by_sig.get(sig)
        if twins:
            hits.append((len(sig), syms, twins))
    hits.sort(key=lambda t: t[0])

    print(f'{len(todo)} distinct shapes among the '
          f'{len(pragma_files)} INCLUDE_ASM functions, '
          f'{skipped} dump files are orphans\n')
    if not hits:
        print('no exact twin for any of them')
        return 1
    print(f'{len(hits)} undecopiled functions have a compiled twin:\n')
    for n, syms, twins in hits[:limit]:
        print(f'  {n:>3} insn  {", ".join(syms[:4])}'
              + (f' (+{len(syms) - 4})' if len(syms) > 4 else '')
              + f'\n          -> {", ".join(twins[:4])}'
              + (f' (+{len(twins) - 4})' if len(twins) > 4 else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())