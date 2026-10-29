#!/usr/bin/env python3
"""Verify every function this branch has decompiled against its original dump.

Each verified function keeps a copy of its dump in the work directory as
`orig_<symbol>.s`. This walks those copies, finds the compiled symbol in
obj/**/*.asm, and runs the instruction-level comparison.

usage: checkall.py [-v]
"""
import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
OBJ_DIR = os.environ.get('MGS_OBJ_DIR', 'obj')

REPO = os.path.join(ROOT, 'mgs_rev2')
PY = os.path.join(ROOT, '.venv', 'bin', 'python')
CMP = os.path.join(ROOT, 'cmp_func.py')
SYMTAB = os.path.join(REPO, 'build', 'functions.txt')
ENT = re.compile(r'^\s*\.ent\s+(\S+)')
PRAGMA = re.compile(r'#pragma INCLUDE_ASM\("([^"]+\.s)"\)')


def still_a_pragma():
    """Symbols whose dump is still wired up by an INCLUDE_ASM pragma."""
    out = set()
    for path in glob.glob(os.path.join(REPO, 'source', '**', '*.c'),
                          recursive=True):
        try:
            text = open(path, errors='ignore').read()
        except OSError:
            continue
        for m in PRAGMA.finditer(text):
            out.add(os.path.basename(m.group(1))[:-2])
    return out

# Verdict text -> short tag used in the report.
TAGS = (('MATCH ignoring', 'NOPDIFF'), ('MATCH', 'MATCH'))


def symbol_locations():
    """symbol -> path of the .asm that defines it."""
    out = {}
    for path in glob.glob(os.path.join(REPO, OBJ_DIR, '**', '*.asm'),
                          recursive=True):
        try:
            fh = open(path, errors='ignore')
        except OSError:
            continue
        with fh:
            for line in fh:
                m = ENT.match(line)
                if m:
                    out.setdefault(m.group(1), path)
    return out


def main():
    verbose = '-v' in sys.argv
    locs = symbol_locations()
    pragmas = still_a_pragma()

    dumps = sorted(glob.glob(os.path.join(ROOT, 'orig_*.s')))
    rows = []
    for dump in dumps:
        sym = os.path.basename(dump)[len('orig_'):-len('.s')]
        asm = locs.get(sym)
        if asm is None:
            if sym in pragmas:
                rows.append(('TODO', sym, 'still INCLUDE_ASM', '', ''))
            else:
                rows.append(('STALE', sym, 'not compiled and not a pragma', '', ''))
            continue
        res = subprocess.run([PY, CMP, dump, asm, sym, SYMTAB],
                             capture_output=True, text=True)
        lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
        if len(lines) < 2:
            rows.append(('ERR', sym, (res.stdout + res.stderr).strip()[-70:], '', ''))
            continue
        o = lines[0].split(':', 1)[1].strip()
        g = lines[1].split(':', 1)[1].strip()
        verdict = lines[-1].strip()
        tag = 'DIFF'
        for prefix, name in TAGS:
            if verdict.startswith(prefix):
                tag = name
                break
        rows.append((tag, sym, f'{o}/{g}', verdict, asm))

    order = {'MATCH': 0, 'NOPDIFF': 1, 'DIFF': 2, 'ERR': 3,
             'TODO': 4, 'STALE': 5}
    rows.sort(key=lambda r: (order[r[0]], r[1]))

    counts = {}
    for tag, sym, ratio, verdict, _ in rows:
        counts[tag] = counts.get(tag, 0) + 1
        if tag == 'MATCH' and not verbose:
            continue
        print(f'{tag:<8} {sym:<26} {ratio:<22} {verdict}')

    print()
    print('  '.join(f'{k}={v}' for k, v in sorted(counts.items())))
    return 0 if counts.get('MATCH') else 1


if __name__ == '__main__':
    sys.exit(main())