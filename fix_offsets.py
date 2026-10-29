#!/usr/bin/env python3
"""Rewrite `/* 0xNNN */` offset comments to match what cc1psx actually emits.

Only comments are touched. The declarations are the ground truth for the layout,
so changing a size or a type here would silently move every field after it; the
comments are the part that drifts, and a wrong comment is worse than none
because it is trusted.

Dry run by default; pass -w to write.

usage: fix_offsets.py -h <header.h> -s <StructName> -c <probe_host.c> \
                      -o <obj/path.asm> [-w]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import struct_probe as sp


PROBE_MARK = '/* --- struct_probe --- */'


def strip_probes():
    """Remove probe blocks left behind by an interrupted run.

    The probes are appended to a real translation unit and removed again in a
    `finally`, but a kill -9 or a machine restart skips that, and the repo would
    be left with 135 lines of junk in it. Cheap to make self-healing.
    """
    import glob
    fixed = []
    for path in (glob.glob(os.path.join(sp.REPO, 'source', '**', '*.c'),
                           recursive=True)
                 + glob.glob(os.path.join(sp.REPO, 'source', '**', '*.h'),
                             recursive=True)):
        try:
            text = open(path, errors='ignore').read()
        except OSError:
            continue
        i = text.find(PROBE_MARK)
        if i == -1:
            continue
        open(path, 'w').write(text[:i].rstrip() + '\n')
        fixed.append(os.path.relpath(path, sp.REPO))
    return fixed


def measure(header, struct, host_c, obj_asm):
    """Reuse struct_probe's machinery; returns (fields, reals)."""
    structs = sp.parse_structs(open(header, errors='ignore').read())
    fields = sp.walk(structs, struct)
    keep = open(host_c, errors='ignore').read()
    probes = [f'\n{PROBE_MARK}\n']
    for i, (path, _, _) in enumerate(fields):
        probes.append(f'char *sp{i}( {struct} *p )'
                      f' {{ return (char *)&p->{path}; }}\n')
    try:
        open(host_c, 'w').write(keep + ''.join(probes))
        import subprocess
        subprocess.run(['/Users/fury/mgsvenv/bin/ninja',
                        '../' + os.path.relpath(obj_asm, sp.REPO)],
                       cwd=os.path.join(sp.REPO, 'build'), capture_output=True)
    finally:
        open(host_c, 'w').write(keep)

    asm = open(obj_asm, errors='ignore').read()
    reals = {}
    for i, (path, _, _) in enumerate(fields):
        m = re.search(r'^sp%d:$(.*?)^\t\.end\s+sp%d$' % (i, i), asm,
                      re.S | re.M)
        if not m:
            continue
        am = re.search(r'\b(?:addiu|addu|addi)\s+\$2,\$4,'
                       r'(0x[0-9a-fA-F]+|\d+)', m.group(1))
        if am:
            reals[path] = int(am.group(1), 0)
        elif re.search(r'move\s+\$2,\$4', m.group(1)):
            reals[path] = 0
    return fields, reals


def apply(header, struct, fields, reals, write):
    """Point the comment of each field we measured at the measured offset."""
    text = open(header, errors='ignore').read()
    lines = text.split('\n')

    # Which struct block does each path belong to? The parent of the first path
    # component is the struct we descended into.
    owners = {}
    for path, _, _ in fields:
        owner, leaf = (path.split('.') if '.' in path
                       else (struct, path))
        owners.setdefault(leaf, []).append((owner, path))

    changed = []
    for leaf, entries in owners.items():
        for owner, path in entries:
            real = reals.get(path)
            if real is None:
                continue
            # find the declaration inside `owner`'s block
            blocks = list(re.finditer(
                r'typedef\s+struct\s+(?:_(\w+)\s*)?\{(.*?)\}\s*(\w+)\s*;',
                text, re.S))
            for b in blocks:
                # the path component is the field name in lower case, the
                # typedef is usually upper case
                if (b.group(1) or b.group(3)).lower() != owner.lower():
                    continue
                start = text[:b.start(2)].count('\n')
                end = start + b.group(2).count('\n')
                for i in range(start, min(end + 1, len(lines))):
                    lm = re.match(
                        r'^(\s*/\*\s*)(0x[0-9A-Fa-f]+)(\s*\*/\s*)(.*\b'
                        + re.escape(leaf) + r'\b.*)$', lines[i])
                    if not lm:
                        continue
                    # keep the original zero padding so the diff stays minimal
                    width = len(lm.group(2)) - 2
                    newlit = f'0x{real:0{width}X}'
                    want = (f'{lm.group(1)}{newlit}'
                            f'{lm.group(3)}{lm.group(4)}')
                    if want != lines[i]:
                        changed.append((i + 1, lines[i].strip(), want.strip()))
                        lines[i] = want
                    break
    if changed and write:
        open(header, 'w').write('\n'.join(lines))
    return changed


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument('--header', required=True)
    ap.add_argument('--struct', required=True)
    ap.add_argument('--host', required=True)
    ap.add_argument('--obj', required=True)
    ap.add_argument('-w', '--write', action='store_true')
    a = ap.parse_args()

    def abs_(p):
        return p if p.startswith('/') else os.path.join(sp.REPO, p)

    fields, reals = measure(abs_(a.header), a.struct, abs_(a.host),
                            abs_(a.obj))
    bad = [(p, c, reals[p]) for p, c, _ in fields
           if p in reals and c is not None and c != reals[p]]
    changed = apply(abs_(a.header), a.struct, fields, reals, a.write)
    for p, claimed, real in bad:
        print(f'  {p:<32} comment 0x{claimed:X} -> 0x{real:X}')
    print(f'{len(changed)} comment(s) {"rewritten" if a.write else "to rewrite"}'
          f' in {a.header}')
    return 0


if __name__ == '__main__':
    sys.exit(main())