#!/usr/bin/env python3
"""Put a verified decompilation into the repo.

Only call this after cmp_func.py has printed MATCH: `compare.py` checks the
generated assembly against the original, and an EQUIV-only body would change
the bytes it compares.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(ROOT, 'mgs_rev2')
NINJA = '/Users/fury/mgsvenv/bin/ninja'


def main():
    srcpath, pragma, bodyfile = sys.argv[1:4]
    body = open(bodyfile).read().rstrip('\n')
    full = os.path.join(REPO, 'source', srcpath)
    text = open(full).read()
    if text.count(pragma) != 1:
        raise SystemExit(f'{srcpath}: pragma found {text.count(pragma)} times')
    open(full, 'w').write(text.replace(pragma, body))
    objrel = srcpath[:-2] + '.asm'
    objrel = f'obj/{objrel}' if not objrel.startswith('obj/') else objrel
    r = subprocess.run([NINJA, f'../{objrel}'], cwd=os.path.join(REPO, 'build'),
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('BUILD FAIL\n' + r.stdout[-800:] + r.stderr[-400:])
    print('applicato e compilato', objrel)


if __name__ == '__main__':
    sys.exit(main())