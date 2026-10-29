#!/usr/bin/env python3
"""Try several phrasings of one function and report which comes closest.

The original has an `andi a3,a2,0xffff` that GCC 2.x keeps folding away. That
instruction says the source held the loaded short in two registers at once -
one zero-extended for the high masks, one raw for the low ones - so the
question is which declaration makes the optimiser keep both. Rather than
reason about it, try the plausible forms and let cmp_func.py count.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))


SCRATCH = os.environ.get('SCRATCH', '/private/tmp/opencode/v')


def attempt(srcpath, pragma, body, objasm, orig, sym):
    src = open(os.path.join(ROOT, 'mgs_rev2/source', srcpath)).read()
    assert pragma in src, f'pragma not found: {pragma}'
    os.makedirs(SCRATCH, exist_ok=True)
    fd, cand = tempfile.mkstemp(suffix='.c', dir=SCRATCH)
    with os.fdopen(fd, 'w') as f:
        f.write(src.replace(pragma, body))
    try:
        out = subprocess.run(
            [os.path.join(ROOT, 'match.sh'), cand, srcpath, objasm, orig, sym],
            capture_output=True, text=True, timeout=1800).stdout
    finally:
        os.unlink(cand)
    lines = [x for x in out.splitlines() if x.strip()]
    counts = [x for x in lines if 'instructions' in x]
    diff = [x for x in lines if 'differing instruction' in x]
    verdict = [x for x in lines if x.strip() in ('MATCH', 'NODIFF')]
    n = 0
    if diff:
        n = int(diff[0].split()[0])
    return (counts, n, verdict, lines)


def main():
    srcpath, pragma, objasm, orig, sym = sys.argv[1:6]
    variants = open(sys.argv[6]).read().split('\n@@@\n')
    best = None
    for i, body in enumerate(variants):
        body = body.strip('\n')
        if not body.strip():
            continue
        counts, n, verdict, lines = attempt(srcpath, pragma, body, objasm,
                                            orig, sym)
        tag = 'MATCH' if verdict else (f'{n} diff' if n else '?')
        mark = ''
        if best is None or n < best[0]:
            best = (n, i)
            mark = '  <-- migliore'
        counts_s = ' '.join(c.replace('original : ', 'orig ').replace(
            'generated: ', 'gen ').replace(' instructions', '') for c in counts)
        print(f'  [{i}] {tag:<10} {counts_s}{mark}')
        if not n:
            for x in lines:
                print(f'       {x}')
    print(f'\nmigliore: variante {best[1]} con {best[0]} differenze')
    return 0


if __name__ == '__main__':
    sys.exit(main())