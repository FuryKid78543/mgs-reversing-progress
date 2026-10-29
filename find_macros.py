#!/usr/bin/env python3
"""Find which branch mnemonics are assembler macros in this dialect.

PSYQ's assembler has two modes. With `.set macro` (the default) a branch
pseudo-instruction expands to the branch *plus* a nop delay slot, so the next
line of the file is a separate instruction. With `.set nomacro` the branch is
plain and the next line really is the delay slot. `cc1psx` wraps volatile
accesses and calls in nomacro blocks and leaves the rest in macro mode.

The original dumps are already-assembled words, so they show what the macro
expansion produced. The 32 functions that are a textual MATCH are the ground
truth: parse their generated .asm, expand the macros, and the word sequence
must come out identical to the original's.

usage: find_macros.py
"""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cmp_func as C   # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(ROOT, 'mgs_rev2')

DIRECTIVE = re.compile(r'^\s*\.')

CANDIDATES = [
    ('beq', 'bne', 'beqz', 'bnez', 'bgez', 'bgtz', 'bltz', 'blez', 'j'),
    ('beq', 'bne', 'beqz', 'bnez', 'bgez', 'bgtz', 'bltz', 'blez', 'j', 'jal'),
    ('beq', 'bne', 'beqz', 'bnez', 'bgez', 'bgtz', 'bltz', 'blez', 'j', 'jal', 'jr'),
    ('b', 'beq', 'bne', 'beqz', 'bnez', 'bgez', 'bgtz', 'bltz', 'blez', 'j',
     'jal', 'jr'),
    ('beq', 'bne', 'beqz', 'bnez', 'bgez', 'bgtz', 'bltz', 'blez'),
]


def load_generated_macroaware(path, symbol, symtab):
    """Like cmp_func.load_generated, but records the macro mode of each line."""
    lines = open(path).read().split('\n')
    start = None
    for i, line in enumerate(lines):
        if line.strip() == symbol + ':':
            start = i
            break
    if start is None:
        raise SystemExit(f'{symbol} not found in {path}')
    out = []
    macro = True
    i = start + 1
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*:\s*$', stripped):
            break
        if stripped.startswith(('.ident', '.size', '.section')):
            break
        if stripped == '#nop':
            out.append(('nop', True))
            i += 1
            continue
        line = lines[i].split('#')[0].rstrip()
        if not line.strip() or DIRECTIVE.match(line):
            if stripped.startswith('.set'):
                if 'nomacro' in stripped:
                    macro = False
                elif re.search(r'\bmacro\b', stripped) and 'nomacro' not in stripped:
                    macro = True
            i += 1
            continue
        m = re.match(r'^\s*([a-z][a-z0-9.]*)\s*(.*)$', line)
        if not m:
            i += 1
            continue
        mnemonic, rest = m.group(1), m.group(2)
        ops = []
        if rest:
            for part in rest.split(','):
                part = part.strip()
                part = re.sub(r'\$L\d+', 'L', part)
                part = re.sub(r'\$-?0x([0-9a-fA-F]+)',
                              lambda mm: str(int(mm.group(1), 16)), part)
                ops.append(part)
        out.append((C.canon_insn(mnemonic, ops), macro))
        i += 1
    return out


def expand(seq, macroset):
    out = []
    for ins, macro in seq:
        out.append(ins)
        if macro and ins.split(' ')[0] in macroset:
            out.append('nop')
    return out


def main():
    # the functions that are a textual MATCH
    out = subprocess.run([os.path.join(ROOT, '.venv/bin/python'),
                          os.path.join(ROOT, 'checkall.py'), '-v'],
                         capture_output=True, text=True, cwd=ROOT).stdout
    matches = []
    for line in out.splitlines():
        if line.startswith('MATCH '):
            matches.append(line.split()[1])
    print(f'{len(matches)} funzioni MATCH')
    # where is each one?
    loc = {}
    for dp, _d, fs in os.walk(os.path.join(REPO, 'source')):
        for f in fs:
            if not f.endswith('.c'):
                continue
            t = open(os.path.join(dp, f), errors='ignore').read()
            for m in re.finditer(r'INCLUDE_ASM\("asm/([^"]+)\.s"\)', t):
                loc[m.group(1).split('/')[-1]] = (f, os.path.join(dp, f))
    symtab = {}
    for name in matches:
        # find the symbol in the sources
        pass
    # simpler: take the names straight from the .asm files
    hits = []
    for dp, _d, fs in os.walk(os.path.join(REPO, 'obj')):
        for f in fs:
            if not f.endswith('.asm'):
                continue
            p = os.path.join(dp, f)
            txt = open(p, errors='ignore').read()
            for name in matches:
                if re.search(r'^' + re.escape(name) + r':', txt, re.M):
                    hits.append((name, p))
    print(f'{len(hits)} trovate nelle obj')
    for macroset in CANDIDATES:
        ok = 0
        for name, p in hits:
            dump = os.path.join(ROOT, f'orig_{name}.s')
            if not os.path.exists(dump):
                continue
            try:
                a = C.load_original(dump, symtab)
                b = expand(load_generated_macroaware(p, name, symtab), macroset)
                if len(b) >= 3 and b[-3] == 'nop' and b[-2] in ('j ra', 'jr ra'):
                    del b[-3]
                if a == b:
                    ok += 1
            except Exception:
                pass
        print(f'  macro={",".join(macroset):<50} {ok}/{len(hits)} combaciano')
    return 0


if __name__ == '__main__':
    sys.exit(main())