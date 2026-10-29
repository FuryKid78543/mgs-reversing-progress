#!/usr/bin/env python3
"""Run the offset check over every Work struct the overlays depend on.

For each `typedef struct _Work` in the tree, find a translation unit that can be
built on its own (an overlay .c that includes the header, or the file itself when
the struct is declared locally), ask cc1psx for every field's real offset, and
report the ones whose `/* 0xNNN */` comment disagrees.

Dry run by default; -w rewrites the comments.

usage: fix_all_structs.py [-w] [name-filter]
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fix_offsets as fo
import struct_probe as sp

REPO = sp.REPO
OBJ_EXT = '.asm'


def headers_with_work():
    """header path -> True for every file declaring a _Work struct."""
    out = []
    for pat in ('source/overlays/**/*.h', 'source/overlays/**/*.c',
                'source/animal/**/*.h', 'source/chara/**/*.h',
                'source/chara/**/*.c'):
        for p in sorted(glob.glob(os.path.join(REPO, pat), recursive=True)):
            text = open(p, errors='ignore').read()
            if re.search(r'typedef\s+struct\s+_?Work\s*\{', text):
                out.append(p)
    return out


def obj_for(rel_c):
    """`source/overlays/a/b.c` compiles to `obj/overlays/a/b.asm`."""
    r = os.path.relpath(rel_c, REPO)
    if r.startswith('source' + os.sep):
        r = r[len('source') + 1:]
    return os.path.join(REPO, 'obj', r[:-2] + OBJ_EXT)


def find_host(path):
    """A buildable .c for this struct, and the obj path it compiles to.

    The include path has to match exactly: several overlays each ship their own
    `zako.h`, and a basename match would compile zako11a's fields against s03d's
    Work struct and report nonsense offsets.
    """
    rel = os.path.relpath(path, REPO)
    if rel.endswith('.c'):
        return path, obj_for(path)
    want = rel[len('source/'):] if rel.startswith('source/') else rel
    hdir = os.path.dirname(path)
    candidates = sorted(glob.glob(os.path.join(REPO, 'source/overlays/**/*.c'),
                                  recursive=True))
    # a sibling .c of the header itself works too - animal/zako11a has its own
    candidates += sorted(glob.glob(os.path.join(hdir, '*.c')))
    for c in candidates:
        ctext = open(c, errors='ignore').read()
        incs = re.findall(r'#include\s+"([^"]+)"', ctext)
        if want in incs:
            return c, obj_for(c)
        # `#include "zako.h"` from a file next to zako.h
        if os.path.dirname(c) == hdir and os.path.basename(path) in incs:
            return c, obj_for(c)
    return None, None


def main():
    write = '-w' in sys.argv
    flt = [a for a in sys.argv[1:] if not a.startswith('-')]
    leftovers = fo.strip_probes()
    if leftovers:
        print('cleaned up probe blocks left by an interrupted run: '
              + ', '.join(leftovers))
    total_bad = 0
    for path in headers_with_work():
        rel = os.path.relpath(path, REPO)
        if flt and not any(f in rel for f in flt):
            continue
        host, obj = find_host(path)
        if not host:
            print(f'-- {rel}: no buildable translation unit, skipped')
            continue
        try:
            fields, reals = fo.measure(path, 'Work', host, obj)
        except Exception as exc:                      # noqa: BLE001
            print(f'-- {rel}: {exc}')
            continue
        if not reals:
            print(f'-- {rel}: no probe compiled, skipped')
            continue
        bad = [(p, c, reals[p]) for p, c, _ in fields
               if p in reals and c is not None and c != reals[p]]
        if not bad:
            print(f'ok {rel} ({len(fields)} fields, no comment drift)')
            continue
        total_bad += len(bad)
        print(f'!! {rel}: {len(bad)} stale comment(s)')
        for p, claimed, real in bad:
            print(f'     {p:<32} comment 0x{claimed:X} -> 0x{real:X}')
        if write:
            fo.apply(path, 'Work', fields, reals, True)
    print(f'\n{total_bad} stale comment(s) in total')
    return 0


if __name__ == '__main__':
    sys.exit(main())