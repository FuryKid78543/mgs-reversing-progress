#!/usr/bin/env python3
"""Audit the functions that are already in C against their original dump.

`checkall.py` compares the *text* of the instructions, so a body that produces
the right code is accepted, and a body that produces the wrong code is caught
only if the text differs. A body can be textually different and still be
right (the delay-slot cases), but it can also be textually close and wrong.

This runs the differential emulator over every decompiled function and reports
the ones whose behaviour differs from the original's. Those are real bugs in
the decompilation, not in the comparison.

usage: audit.py [max]
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mipsemu as M   # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(ROOT, 'mgs_rev2')

PRAGMA_RE = re.compile(r'INCLUDE_ASM\("asm/[^"]*/([A-Za-z_]\w*)\.s"\)')


def still_pragma():
    out = set()
    for dp, _d, fs in os.walk(os.path.join(REPO, 'source')):
        for f in fs:
            if f.endswith(('.c', '.h')):
                t = open(os.path.join(dp, f), errors='ignore').read()
                out.update(PRAGMA_RE.findall(t))
    return out


def symbols_in_obj():
    """symbol -> obj path, for every function in the built objects."""
    out = {}
    for dp, _d, fs in os.walk(os.path.join(REPO, 'obj')):
        for f in fs:
            if not f.endswith('.asm'):
                continue
            p = os.path.join(dp, f)
            try:
                t = open(p, errors='ignore').read()
            except OSError:
                continue
            for m in re.finditer(r'^([A-Za-z_]\w*):\s*$', t, re.M):
                out.setdefault(m.group(1), p)
    return out


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    pragma = still_pragma()
    syms = symbols_in_obj()
    tab = M.load_symtab()
    M.resolve_hexnames(tab)
    arities = M.build_arities()
    bad = []
    checked = 0
    for name, obj in sorted(syms.items()):
        if name in pragma:
            continue
        dump = os.path.join(ROOT, f'orig_{name}.s')
        if not os.path.exists(dump):
            continue
        checked += 1
        try:
            verdict, detail, _ = M.compare(dump, obj, name, trials=40,
                                          verbose=False)
        except Exception as e:
            verdict, detail = 'ERR', f'{type(e).__name__}: {e}'
        if verdict not in ('EQUIV',):
            bad.append((name, verdict, detail))
    print(f'{checked} funzioni gia decompilate, {len(bad)} non equivalenti')
    for name, verdict, detail in bad[:limit]:
        print(f'  {verdict:<8} {name:<26} {detail}')
    return 0


if __name__ == '__main__':
    sys.exit(main())