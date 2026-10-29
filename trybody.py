#!/usr/bin/env python3
"""Try several bodies for a function that is already decompiled.

`variants.py` swaps a pragma; this one swaps a function body that is already
in C, which is the case for a function that is decompiled but does not match
(`DIFF` in checkall.py).

usage: trybody.py <source/rel.c> <obj/rel.asm> <orig.s> <symbol> <bodies.txt>
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(ROOT, 'mgs_rev2')
NINJA = '/Users/fury/mgsvenv/bin/ninja'


def build(srcpath, objrel, text):
    full = os.path.join(REPO, 'source', srcpath)
    with open(full, 'w') as f:
        f.write(text)
    r = subprocess.run([NINJA, '../' + objrel], cwd=os.path.join(REPO, 'build'),
                       capture_output=True, text=True)
    if r.returncode:
        for line in (r.stdout + r.stderr).splitlines():
            if 'rror' in line or 'parse error' in line:
                return None, line
        return None, 'build fallito'
    return True, None


def main():
    srcpath, objrel, orig, sym, bodiesfile = sys.argv[1:6]
    full = os.path.join(REPO, 'source', srcpath)
    original = open(full).read()
    bodies = open(bodiesfile).read().split('\n@@@\n')
    # the body of the function, as it stands in the file
    pat = re.compile(r'(^[^\n]*\b' + re.escape(sym) + r'\s*\([^)]*\)\s*\n?\{)',
                     re.M)
    m = pat.search(original)
    if not m:
        raise SystemExit(f'{sym}: non trovata in {srcpath}')
    # find the matching closing brace
    start = m.end() - 1
    depth = 0
    for i in range(start, len(original)):
        if original[i] == '{':
            depth += 1
        elif original[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    else:
        raise SystemExit('graffe non bilanciate')
    testa = original[:m.end()]
    coda = original[end - 1:]      # end-1: the closing brace belongs to us

    migliore = None
    for k, body in enumerate(bodies):
        body = body.strip('\n')
        if not body.strip():
            continue
        # the body may be written as a whole function: keep only what is
        # inside the braces, the signature is already in the file
        if sym in body:
            b = body.find('{')
            if b < 0:
                continue
            e = body.rfind('}')
            body = body[b + 1:e] if e > b else body[b + 1:]
        ok, err = build(srcpath, objrel, testa + '\n' + body + '\n' + coda)
        if not ok:
            print(f'  [{k}] BUILD FAIL: {err}')
            continue
        out = subprocess.run(
            [os.path.join(ROOT, '.venv/bin/python'),
             os.path.join(ROOT, 'cmp_func.py'),
             os.path.join(ROOT, orig),
             os.path.join(REPO, objrel), sym],
            capture_output=True, text=True).stdout
        n = 0
        verdict = ''
        for line in out.splitlines():
            if 'differing instruction' in line:
                n = int(line.split()[0])
            if line.startswith(('MATCH', 'NODIFF')):
                verdict = line
        if n == 0 and verdict:
            print(f'  [{k}] {verdict}  <== MATCH')
        else:
            print(f'  [{k}] {n} diff' + (f'  ({verdict})' if verdict else ''))
        if migliore is None or n < migliore[0]:
            migliore = (n, k, body)
    build(srcpath, objrel, original)
    if migliore:
        print(f'\nmigliore: [{migliore[1]}] con {migliore[0]} differenze')
        open('/private/tmp/opencode/v/trybody_best.c', 'w').write(
            testa + '\n' + migliore[2] + '\n' + coda)
    return 0


if __name__ == '__main__':
    sys.exit(main())