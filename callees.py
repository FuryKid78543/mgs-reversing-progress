#!/usr/bin/env python3
"""Which candidates have every callee nameable?

`rank_safe.py` flags functions that touch undeclared *globals*, but a function
can also be blocked by a `jal` to an address that has no symbol anywhere: main
exe calls resolve through build/functions.txt, overlay calls through the
`#pragma INCLUDE_ASM` list or an already-decompiled definition.

    callees.py [--limit N] [--max-insns N]

Prints the candidates whose every callee resolves, smallest first. Those are the
ones worth a build.
"""
import glob
import os
import re
import struct
import subprocess
import sys

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32

ROOT = os.environ.get('MGS_ROOT') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'mgs_rev2')
MD = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32)


def load_functions_txt():
    """Address -> name, for the main executable."""
    path = os.path.join(ROOT, 'build', 'functions.txt')
    out = {}
    if not os.path.exists(path):
        return out
    for line in open(path, errors='ignore'):
        parts = line.split()
        if len(parts) >= 3 and re.fullmatch(r'[0-9A-Fa-f]{8}', parts[0]):
            out[int(parts[0], 16)] = parts[2]
    return out


def overlay_symbols():
    """Every symbol named by a pragma or a definition in the overlay sources."""
    known = set()
    pat = re.compile(r'#pragma\s+INCLUDE_ASM\("asm/[^"]*?/([A-Za-z_]\w*)\.s"\)')
    for p in glob.glob(os.path.join(ROOT, 'source', '**', '*.c'),
                       recursive=True):
        text = open(p, errors='ignore').read()
        known.update(pat.findall(text))
        for m in re.finditer(r'^\s*(?:static\s+)?[A-Za-z_][\w \t*]*?'
                             r'([A-Za-z_]\w*)\s*\([^;]*\)\s*\{',
                             text, re.M):
            known.add(m.group(1))
    return known


def dump_path(name):
    hits = glob.glob(os.path.join(ROOT, 'asm', '**', f'{name}.s'), recursive=True)
    return hits[0] if hits else None


def callees(path):
    code, addr = b'', None
    for line in open(path, errors='ignore'):
        if line.strip().startswith('dw '):
            if addr is None:
                m = re.search(r';\s*([0-9a-fA-F]+)\s*$', line)
                if m:
                    addr = int(m.group(1), 16)
            code += struct.pack('<I', int(line.split()[1], 16))
    if addr is None:
        return []
    out = []
    for ins in MD.disasm(code, addr):
        if ins.mnemonic == 'jal' and ins.op_str.startswith('0x'):
            out.append(int(ins.op_str, 16))
    return out


def main():
    args = sys.argv[1:]
    limit = 30
    max_insns = 9999
    if '--limit' in args:
        limit = int(args[args.index('--limit') + 1])
    if '--max-insns' in args:
        max_insns = int(args[args.index('--max-insns') + 1])

    funcs = load_functions_txt()
    # overlay dumps that are still present, by address, so a jal into another
    # unresolved dump counts as resolved-by-address.
    dump_by_addr = {}
    for p in glob.glob(os.path.join(ROOT, 'asm', '**', '*.s'), recursive=True):
        b = os.path.basename(p)[:-2]
        try:
            dump_by_addr[int(b.split('_')[-1], 16)] = b
        except ValueError:
            pass

    clean = []
    rank = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'rank_safe.py')
    res = subprocess.run(['/Users/fury/mgsvenv/bin/python', rank,
                          str(max_insns), '900'], capture_output=True, text=True)
    for line in res.stdout.splitlines():
        m = re.match(r'\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+\.s)\s*$',
                     line)
        if not m:
            continue
        if m.group(1) == '0' and m.group(2) == '0' and m.group(3) == '0':
            clean.append((int(m.group(5)), m.group(6)))

    clean.sort()
    ok, blocked = [], []
    for insns, dump in clean:
        name = dump[:-2]
        path = dump_path(name)
        if path is None:
            continue
        missing = []
        for tgt in callees(path):
            if tgt in funcs or tgt in dump_by_addr:
                continue
            missing.append(tgt)
        if missing:
            blocked.append((insns, name,
                            ', '.join(f'0x{t:X}' for t in sorted(set(missing)))))
        else:
            ok.append((insns, name))

    print(f'{len(clean)} candidate "pulite"; '
          f'{len(ok)} con tutti i callee risolti, {len(blocked)} con almeno '
          f'un callee senza nome\n')
    print(f'{"istruzioni":>10}  funzione')
    for insns, name in ok[:limit]:
        print(f'{insns:>10}  {name}')
    if blocked:
        print(f'\nbloccate da callee senza nome ({len(blocked)}):')
        for insns, name, miss in blocked[:limit]:
            print(f'{insns:>10}  {name:<26} {miss}')
    return 0


if __name__ == '__main__':
    sys.exit(main())