#!/usr/bin/env python3
"""Rank the remaining candidates by how likely they are to reach MATCH.

What the last sessions showed: the logic is usually right and the instruction
count usually matches, and what blocks the MATCH is the *delay slot*. Two
things in the original's delay slot are trouble:

- an empty slot (a `nop`). `cc1psx` always fills delay slots, so a `nop`
  there can never be reproduced from C.
- a store. If the original's slot holds a store, the statement it belongs to
  runs **unconditionally**, even when the branch skips it. Putting that
  statement inside the `if` in C changes the behaviour, not just the layout.

So a candidate with few branches, no empty delay slot and no store in one is
a candidate whose remaining differences are unlikely to be about delay slots
at all.

usage: rank_match.py [max]
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mipsemu as M   # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))

BRANCH = set(M.BRANCH2) | set(M.BRANCH3) | {'j', 'jr', 'jal', 'jalr'}


def is_branch(ins):
    return ins.m in BRANCH


def fragment(prog):
    """'prologo', 'epilogo' or None: the dump is only half a function."""
    if not prog:
        return 'vuoto'
    primi = prog[:12]
    da_sp = prog[0].m in ('lw', 'sw') and 'sp' in str(prog[0].ops)
    ha_jr = any(i.m in ('jr', 'jalr') for i in primi)
    prologo = prog[0].m in ('addiu', 'move', 'or') and 'sp' in str(prog[0].ops)
    fine = any(i.m in ('jr', 'jalr') for i in prog[-4:]) or \
        any(i.m == 'j' for i in prog[-4:])
    if da_sp and ha_jr:
        return 'epilogo'
    if prologo and not fine:
        return 'prologo'
    return None


def score(path):
    try:
        orig, base = M.parse_dump(path)
    except Exception:
        return None
    prog = M.resolve_targets(M.expand(orig), {}, base)
    n = len(prog)
    empty_slot = 0
    store_slot = 0
    load_slot = 0
    branches = 0
    for k, ins in enumerate(prog):
        if not is_branch(ins):
            continue
        branches += 1
        if k + 1 >= n:
            continue
        nxt = prog[k + 1]
        if nxt.m == 'nop':
            empty_slot += 1
        elif nxt.m in ('sw', 'sh', 'sb', 'swl', 'swr'):
            store_slot += 1
        elif nxt.m in ('lw', 'lh', 'lhu', 'lb', 'lbu', 'lwl', 'lwr'):
            load_slot += 1
    calls = sum(1 for i in prog if i.m in ('jal', 'jalr'))
    # epilogo: quante righe tra l'ultimo ripristino e il jr. Il compilatore
    # originale mette un nop dove ci sarebbe un secondo ripristino, e quel
    # nop non e' riproducibile da sorgente.
    j = None
    for k in range(len(prog) - 1, -1, -1):
        if prog[k].m == 'jr':
            j = k
            break
    epilogo = '?'
    if j is not None and j >= 2:
        i = j - 1
        while i >= 0 and (prog[i].m == 'lw' and 'sp' in str(prog[i].ops)
                          or prog[i].m == 'nop'):
            i -= 1
        righe = prog[i + 1:j]
        if righe:
            epilogo = '+'.join(x.m for x in righe)
    return {
        'epilogo': epilogo,
        'frammento': fragment(prog),
        'n': n,
        'branches': branches,
        'empty_slot': empty_slot,
        'store_slot': store_slot,
        'load_slot': load_slot,
        'calls': calls,
    }


PRAGMA_RE = re.compile(r'INCLUDE_ASM\("asm/[^"]*/([A-Za-z_][\w]*)\.s"\)')


def still_pragma():
    """Names of the functions that are still an INCLUDE_ASM pragma."""
    src = os.path.join(ROOT, 'mgs_rev2', 'source')
    out = set()
    for dirpath, _dirs, files in os.walk(src):
        for f in files:
            if not f.endswith(('.c', '.h')):
                continue
            try:
                text = open(os.path.join(dirpath, f), errors='ignore').read()
            except OSError:
                continue
            out.update(PRAGMA_RE.findall(text))
    return sorted(out)


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    # Only the functions that are still a pragma: a dump can outlive the
    # pragma it belongs to, and a function already in C is not a candidate.
    names = still_pragma()
    if not names:
        names = sorted(f[5:-2] for f in os.listdir(ROOT)
                       if f.startswith('orig_') and f.endswith('.s'))
    rows = []
    for name in names:
        p = os.path.join(ROOT, f'orig_{name}.s')
        if not os.path.exists(p):
            continue
        s = score(p)
        if s is None:
            continue
        # fewer branches and no empty/store slot is better
        s['name'] = name
        s['risk'] = s['empty_slot'] * 3 + s['store_slot'] * 2 + s['load_slot']
        if s['frammento']:
            s['risk'] += 100       # non decompilabile cosi' com'e'
        if s['epilogo'] == 'lw+nop':
            s['risk'] += 50        # MATCH esatto irraggiungibile: il nop
                                   # dell'originale non e' da sorgente
        rows.append(s)
    rows.sort(key=lambda r: (r['risk'], r['branches'], r['n']))
    print(f"{'funzione':<26} {'ist':>4} {'rami':>5} {'slot vuoti':>10} "
          f"{'store':>6} {'load':>5} {'chiam':>6} {'note':>9}")
    mostrati = 0
    for r in rows:
        if mostrati >= limit:
            break
        if r['frammento']:
            continue
        mostrati += 1
        print(f"{r['name']:<26} {r['n']:>4} {r['branches']:>5} "
              f"{r['empty_slot']:>10} {r['store_slot']:>6} {r['load_slot']:>5} "
              f"{r['calls']:>6} {r['epilogo']:>13} {r['risk']:>8}")
    print(f"\n{len(rows)} candidate valutate")
    return 0


if __name__ == '__main__':
    sys.exit(main())