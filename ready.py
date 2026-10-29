#!/usr/bin/env python
"""Which INCLUDE_ASM functions can be written without inventing symbols?

A function is "ready" when every external address it touches is either a
function in functions.txt, a symbol already declared somewhere in the tree,
or a small offset into one of those. Everything else would need a new name
in the data segment, and the data segment is laid out by declaration order,
so adding one moves every symbol after it -- see WORKLOG, 23/10 09:52.

usage: ready.py [-v] [-n N]
"""
import glob
import os
import re
import sys

import mipsemu as M

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(ROOT, 'mgs_rev2')


def collect_known():
    """address -> set of names, from every symbol the repo already spells."""
    known = {}
    pat = re.compile(r'\b([A-Za-z_]\w*?)_([0-9A-Fa-f]{8})\b')
    files = []
    for sub in ('source', 'include'):
        files += glob.glob(os.path.join(REPO, sub, '**', '*.c'), recursive=True)
        files += glob.glob(os.path.join(REPO, sub, '**', '*.h'), recursive=True)
    for p in files:
        try:
            txt = open(p, errors='ignore').read()
        except OSError:
            continue
        for m in pat.finditer(txt):
            name = f'{m.group(1)}_{m.group(2)}'
            if re.fullmatch(r'[0-9A-Fa-f]{8}', m.group(2)):
                known.setdefault(0x80000000 | int(m.group(2), 16),
                                 set()).add((name, p))
    fn = os.path.join(REPO, 'build', 'functions.txt')
    if os.path.exists(fn):
        for line in open(fn):
            parts = line.split()
            if parts and re.fullmatch(r'[0-9A-F]{8}', parts[0]):
                known.setdefault(int(parts[0], 16), set()).add(
                    (parts[2] if len(parts) > 2 else parts[0], 'main'))
    for k, v in M.load_symtab().items():
        known.setdefault(v, set()).add((k, 'main'))
    return known


def visible(entry, overlay, addr):
    """A symbol is usable from `overlay` if it lives in that overlay or in the
    main executable. Addresses are per-overlay: 0x800DD798 means one thing in
    s01a and another in s14e, so a name from another overlay is not evidence
    that an address is already accounted for. A shared module (animal/,
    libgv/, ...) linked into another overlay is not evidence either, unless it
    sits in the main executable's own range."""
    name, path = entry
    if addr < 0x800C0000:
        return True                     # main executable: same for everyone
    if path == 'main':
        return True
    if 'overlays' not in path:
        return False                    # linked into some other overlay
    return f'overlays/{overlay}/' in path.replace(os.sep, '/')


def fold(ins, prog, i, hi):
    """Address built by `lui rd,hi` -- look for the low half nearby.

    `%lo` is sign-extended by every instruction that uses it as a displacement
    (lw/sw/addiu) and zero-extended by the logical ones (ori). Getting this
    wrong invents addresses 0x10000 too high: `lui 0x800e` + `lw -0x2854` is
    0x800dd7ac, not 0x800ed7ac.
    """
    rd = ins.ops[0]

    def make(lo, mnemonic):
        lo &= 0xFFFF
        if mnemonic != 'ori' and lo >= 0x8000:
            lo -= 0x10000
        return (hi << 16) + lo

    for j in range(i + 1, min(i + 7, len(prog))):
        n = prog[j]
        if n.m in ('addiu', 'ori') and len(n.ops) == 3 \
                and n.ops[1] == rd and re.fullmatch(r'-?0x[0-9a-fA-F]+|-?\d+',
                                                    n.ops[2]):
            return make(int(n.ops[2], 0), n.m), n.m
        for op in n.ops:
            m = re.fullmatch(r'(-?0x[0-9a-fA-F]+|-?\d+)\((\w+)\)', op)
            if m and m.group(2) == rd:
                return make(int(m.group(1), 0), n.m), n.m
        # parse_dump flattens an addressing mode into `offset, base`: look for
        # a number immediately followed by the high-half register.
        for k in range(len(n.ops) - 1):
            if n.ops[k + 1] == rd and re.fullmatch(
                    r'-?0x[0-9a-fA-F]+|-?\d+', n.ops[k]):
                return make(int(n.ops[k], 0), n.m), n.m
        if rd in n.ops[1:] and n.m not in ('addu', 'sll', 'srl'):
            # register reused for something else: the pair is not an address
            if j > i + 3:
                return None, None
    return None, None


def refs(dump):
    """Every address the function reads, calls or stores a pointer to."""
    o, b = M.parse_dump(dump)
    prog = M.resolve_targets(M.expand(o), {}, b)
    out = []
    for i, ins in enumerate(prog):
        if ins.m == 'jal' and ins.ops and re.fullmatch(r'0x[0-9a-fA-F]+',
                                                       ins.ops[0]):
            out.append((int(ins.ops[0], 16), 'jal'))
        elif ins.m == 'li32' and len(ins.ops) == 2:
            out.append((int(ins.ops[1], 0) & 0xFFFFFFFF, 'li32'))
        elif ins.m == 'lui' and len(ins.ops) == 2:
            hi = int(ins.ops[1], 0)
            if hi < 0x100:
                continue                      # 0x10000 << n, not an address
            a, how = fold(ins, prog, i, hi)
            if a is not None and a >= 0x80000000:
                out.append((a, 'lui/' + how))
    return prog, out


def nearest(addr, known, overlay):
    """Closest usable symbol at or below `addr`."""
    best = None
    for a in known:
        if a > addr:
            continue
        names = [e for e in known[a] if visible(e, overlay, a)]
        if names and (best is None or a > best):
            best = a
    if best is None:
        return None, None
    return best, addr - best


def pragmas():
    """symbol -> (source path, asm path) for everything still in assembly."""
    pat = re.compile(r'#pragma\s+INCLUDE_ASM\("(asm/[^"]*?/([A-Za-z_]\w*)_'
                     r'([0-9A-Fa-f]{8})\.s)"\)')
    for p in glob.glob(os.path.join(REPO, 'source', '**', '*.c'),
                       recursive=True):
        try:
            txt = open(p, errors='ignore').read()
        except OSError:
            continue
        for m in pat.finditer(txt):
            yield m.group(2) + '_' + m.group(3), p, m.group(1)


def main():
    verbose = '-v' in sys.argv
    n = 25
    if '-n' in sys.argv:
        n = int(sys.argv[sys.argv.index('-n') + 1])
    all_ready = '--all' in sys.argv
    import rank_match as R
    known = collect_known()
    ready = []
    blocked = 0
    frag = 0
    risky = 0
    for sym, src, asm in pragmas():
        dump = os.path.join(ROOT, f'orig_{sym}.s')
        if not os.path.exists(dump):
            continue
        try:
            prog, rs = refs(dump)
        except Exception as e:
            if verbose:
                print(f'  ! {sym}: {e}')
            continue
        if len(prog) < 4:
            frag += 1
            continue
        ovl = asm.split('/')[2] if asm.count('/') >= 2 else ''
        THRESHOLD = 0x400
        bad = []
        for a, how in rs:
            if a < 0x800C0000:
                # Main executable: address can be used as a raw constant
                # in C (e.g. (void *)0x800AB2EC). No new symbol needed.
                continue
            if any(visible(e, ovl, a) for e in known.get(a, ())):
                continue
            base, d = nearest(a, known, ovl)
            if base is not None and d <= THRESHOLD:
                continue
            bad.append((a, how, base, d))
        if bad:
            blocked += 1
            if verbose:
                print(f'{sym}  ({len(prog)} insn)')
                for a, how, base, d in bad:
                    if base is None:
                        near = '?'
                    else:
                        nm = [e[0] for e in known[base]
                              if visible(e, ovl, base)][0]
                        near = f'{nm}+{d:#x}' if d else nm
                    print(f'    {a:#010x} via {how:<14} vicino a {near}')
            continue
        sc = R.score(dump)
        if sc is None:
            continue
        if sc['frammento']:
            frag += 1
            continue
        # Un epilogo con un solo ripristino porta sempre il `nop` che il
        # compilatore originale lasciava al posto del secondo: MATCH
        # irraggiungibile per costruzione.
        if sc['epilogo'] in ('lw', 'lw+nop'):
            risky += 1
            continue
        # Uno slot vuoto dopo un ramo condizionale non e' riproducibile: in
        # `.set macro` il ramo espande a ramo+nop. Dopo una `jal` invece il
        # `nop` e' quello che cc1psx stesso emette quando non ha nulla da
        # mettere, e infatti molte MATCH ce l'hanno. Uno store nello slot
        # resta un problema in entrambi i casi.
        bad_slot = False
        for k, ins in enumerate(prog):
            if k + 1 >= len(prog) or ins.m not in R.BRANCH:
                continue
            nxt = prog[k + 1]
            if nxt.m == 'nop' and ins.m != 'jal':
                bad_slot = True
            elif nxt.m in ('sw', 'sh', 'sb', 'swl', 'swr'):
                bad_slot = True
        if bad_slot:
            risky += 1
            continue
        ready.append((sc['branches'], sc['store_slot'], len(prog), sym, src,
                      sc['epilogo']))
    ready.sort(key=lambda r: (r[0], r[2]))
    print(f'pronte: {len(ready)}   bloccate sui dati: {blocked}   '
          f'frammenti: {frag}   rischio slot/epilogo: {risky}\n')
    show = ready if all_ready else ready[:n]
    print(f'{"ins":>4} {"ram":>4}  {"epilogo":<12} simbolo')
    for branches, _, size, sym, src, epi in show:
        print(f'{size:>4} {branches:>4}  {epi:<12} {sym}')


if __name__ == '__main__':
    main()
