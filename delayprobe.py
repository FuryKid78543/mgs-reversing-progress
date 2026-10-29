#!/usr/bin/env python3
"""Can cc1psx leave a branch delay slot empty?

The project has been treating "the original has a `nop` in a delay slot" as an
unreachable class, on the assumption that cc1psx always fills the slot. That
assumption has never been tested. This script tests it directly against the
compiled output: it walks every function in obj/ and counts the delay slots of
every conditional branch, in the generated code and in the original dump.

usage: delayprobe.py [--detail N]
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

COND_BRANCH = {'beq', 'bne', 'beqz', 'bnez', 'blez', 'bgtz', 'bltz', 'bgez',
               'slt', 'sltu', 'slti', 'sltiu'}


def obj_delay_slots():
    """Yield (file, symbol, filled, empty) for every conditional branch."""
    stats = dict(cond=0, empty=0, filled=0)
    samples = []
    for path in glob.glob(os.path.join(ROOT, 'obj', '**', '*.asm'),
                          recursive=True):
        if '.preproc' in path or '.eucjp' in path:
            continue
        lines = open(path, errors='ignore').read().splitlines()
        sym = None
        body = []
        for line in lines:
            s = line.strip()
            if s.startswith('.ent'):
                sym = s.split()[-1]
                body = []
                continue
            if s.startswith('.end'):
                if sym and body:
                    for i in range(len(body) - 1):
                        mn = body[i].lstrip()
                        if mn.split(' ')[0] not in COND_BRANCH and \
                                not re.match(r'\w+\t', mn):
                            pass
                        op, _, rest = mn.partition('\t')
                        if op.strip() not in COND_BRANCH:
                            continue
                        stats['cond'] += 1
                        nxt = body[i + 1].split('\t')[0].strip()
                        if nxt == 'nop':
                            stats['empty'] += 1
                            if len(samples) < 12:
                                samples.append((os.path.relpath(path, ROOT),
                                                sym, body[i].strip()[:40]))
                        else:
                            stats['filled'] += 1
                sym = None
                continue
            if sym is not None and line and '\t' in line and \
                    not line.strip().startswith('.'):
                body.append(line)
    return stats, samples


def dump_delay_slots():
    stats = dict(cond=0, empty=0, filled=0)
    files = sorted(glob.glob(os.path.join(ROOT, 'asm', '**', '*.s'),
                             recursive=True))
    for path in files:
        code, addr = b'', None
        for line in open(path, errors='ignore'):
            if line.strip().startswith('dw '):
                if addr is None:
                    m = re.search(r';\s*([0-9a-fA-F]+)\s*$', line)
                    if m:
                        addr = int(m.group(1), 16)
                code += struct.pack('<I', int(line.split()[1], 16))
        if addr is None:
            continue
        ins = list(MD.disasm(code, addr))
        for i in range(len(ins) - 1):
            if ins[i].mnemonic not in COND_BRANCH:
                continue
            stats['cond'] += 1
            if ins[i + 1].mnemonic == 'nop':
                stats['empty'] += 1
            else:
                stats['filled'] += 1
    return stats


def main():
    detail = '--detail' in sys.argv
    gen, samples = obj_delay_slots()
    orig = dump_delay_slots()
    print('                    generato da cc1psx      originale Konami')
    for key, label in (('cond', 'branch condizionali'),
                       ('empty', 'delay slot vuoto   '),
                       ('filled', 'delay slot riempito')):
        g, o = gen[key], orig[key]
        ge = 100.0 * g / max(gen['cond'], 1)
        oe = 100.0 * o / max(orig['cond'], 1)
        print(f'  {label}: {g:>6} ({ge:5.1f}%)      {o:>6} ({oe:5.1f}%)')
    if detail:
        print('\nprimi esempi di delay slot vuoto nel codice generato:')
        for s in samples:
            print(f'   {s[0]}  {s[1]}  {s[2]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())