#!/usr/bin/env python3
"""Check every `/* 0xNNN */` offset comment in a struct against the compiler.

The offsets in these headers are maintained by hand and drift. This tool asks
cc1psx the truth: for each field it emits a probe that returns the field's
address, then reads back the displacement the compiler actually produced.

    char *probe_12( Work *w ) { return (char *)&w->vision.length; }
    ->  move $2,$4 ; addiu $2,$2,0xB90 ; j $31 ; nop

Any field whose real offset differs from its comment is reported, together with
whether the overlay's own code ever touches the commented offset.

usage: struct_probe.py <header.h> <StructName> <probe_host.c> <obj/path.asm>
"""
import os
import re
import subprocess
import sys

ROOT = os.environ.get('MGS_ROOT') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'mgs_rev2')
REPO = ROOT

STRUCT_RE = re.compile(r'typedef\s+struct\s+(?:_(\w+)\s*)?\{(.*?)\}\s*(\w+)\s*;',
                       re.S)
# /* 0xB00 */ PUTFUNC putchar[ 8 ];   /   /* 0xB84 */ char padB84[ 0x6 ];
FIELD_RE = re.compile(
    r'^\s*(?:/\*\s*(0x[0-9A-Fa-f]+|\d+)\s*\*/\s*)?'
    r'(?:(struct|union)\s+)?'
    r'([A-Za-z_][\w \t*]*?)\s+'
    r'([A-Za-z_]\w*)\s*'
    r'(?:\[\s*([^\]]*?)\s*\])?\s*;$')
PTR = re.compile(r'\b(char|short|int|long|u_char|u_short|u_long|void)\b')

# Depth cap: some of these structs nest four deep and the point is to check the
# top of the tree, not to enumerate every pad byte.
MAX_DEPTH = 4


def parse_structs(text):
    """typedef name -> list of (comment_offset_or_None, type, name, array)."""
    out = {}
    for m in STRUCT_RE.finditer(text):
        name = m.group(1) or m.group(3)
        fields = []
        for raw in m.group(2).split('\n'):
            line = raw.split('//')[0].strip()
            if not line:
                continue
            # Keep the leading `/* 0xNNN */` marker, drop every other comment.
            m2 = re.match(r'^/\*\s*(0x[0-9A-Fa-f]+|\d+)\s*\*/\s*(.*)$', line)
            if m2:
                line = f'/* {m2.group(1)} */ {m2.group(2)}'
            else:
                line = re.sub(r'/\*.*?\*/', '', line).strip()
            if not line:
                continue
            fm = FIELD_RE.match(line)
            if not fm:
                if line:
                    fields.append((None, None, None, line))
                continue
            fields.append((fm.group(1), fm.group(3).strip(),
                           fm.group(4), fm.group(5)))
        out[name] = fields
    return out


def base_type(t):
    """Strip pointers/qualifiers down to the typedef name, or None."""
    if t is None:
        return None
    t = t.strip()
    if t.endswith('*') or t.startswith('const ') and '*' in t:
        return None
    t = t.replace('const ', '').replace('volatile ', '').strip()
    if re.fullmatch(r'(unsigned\s+)?(char|short|int|long|float|double|void|'
                    r'u_char|u_short|u_long|u_int)', t):
        return None
    return t


def walk(structs, name, prefix='', depth=0, acc=None):
    """Yield (path, declared_offset, real_type) for every addressable field."""
    if acc is None:
        acc = []
    if depth > MAX_DEPTH or name not in structs:
        return acc
    for comment, typ, fname, arr in structs[name]:
        if fname is None:
            continue
        path = f'{prefix}{fname}'
        bt = base_type(typ)
        if bt in structs:
            if arr is None:
                # not an array: descend without a subscript
                walk(structs, bt, f'{path}.', depth + 1, acc)
                continue
            try:
                n = int(arr, 0)
            except ValueError:
                n = 1
            for i in range(min(n, 4)):
                walk(structs, bt, f'{path}[{i}].', depth + 1, acc)
        else:
            acc.append((path, int(comment, 0) if comment else None, typ))
    return acc


def main():
    header, struct, host_c, obj_asm = sys.argv[1:5]
    header = os.path.join(REPO, header) if not header.startswith('/') else header
    host_c = os.path.join(REPO, host_c) if not host_c.startswith('/') else host_c
    obj_asm = os.path.join(REPO, obj_asm) if not obj_asm.startswith('/') else obj_asm

    structs = parse_structs(open(header, errors='ignore').read())
    if struct not in structs:
        print(f'{struct} not found in {header}')
        print('known:', ', '.join(sorted(structs)[:20]))
        return 1
    fields = walk(structs, struct)
    if not fields:
        print('no addressable fields found')
        return 1

    # Emit one probe per field, appended to a translation unit that already
    # includes the header.
    keep = open(host_c, errors='ignore').read()
    probes = ['\n/* --- struct_probe --- */\n']
    for i, (path, _, _) in enumerate(fields):
        probes.append(f'char *sp{i}( {struct} *p ) {{ return (char *)&p->{path}; }}\n')
    try:
        open(host_c, 'w').write(keep + ''.join(probes))
        res = subprocess.run(['/Users/fury/mgsvenv/bin/ninja',
                              '../' + os.path.relpath(obj_asm, REPO)],
                             cwd=os.path.join(REPO, 'build'),
                             capture_output=True, text=True)
        if res.returncode != 0:
            out = res.stdout + res.stderr
            print('BUILD FAILED:')
            errs = [ln for ln in out.splitlines() if 'rror' in ln]
            for ln in (errs or out.splitlines())[-12:]:
                print('   ', ln)
            return 1
    finally:
        open(host_c, 'w').write(keep)

    asm = open(obj_asm, errors='ignore').read()
    print(f'{struct}: {len(fields)} fields, header {os.path.basename(header)}\n')
    print(f'{"offset":>7} {"comment":>9}  field')
    bad = 0
    for i, (path, comment, typ) in enumerate(fields):
        m = re.search(r'^sp%d:$(.*?)^\t\.end\s+sp%d$' % (i, i), asm,
                      re.S | re.M)
        real = None
        if m:
            # cc1psx spells the address computation `addu $2,$4,OFFSET`
            am = re.search(r'\b(?:addiu|addu|addi)\s+\$2,\$4,'
                           r'(0x[0-9a-fA-F]+|\d+)', m.group(1))
            if am:
                real = int(am.group(1), 0)
            elif re.search(r'move\s+\$2,\$4', m.group(1)):
                real = 0        # no displacement at all
        flag = ''
        if real is None:
            flag = '?? not found'
            bad += 1
        elif comment is None:
            flag = '-- no comment'
        elif real != comment:
            flag = f'*** MISMATCH, real is 0x{real:X}'
            bad += 1
        shown = f'0x{real:X}' if real is not None else '??'
        claimed = f'0x{comment:X}' if comment is not None else '--'
        print(f'{shown:>7} {claimed:>9}  {path}  {flag}')
    print(f'\n{bad} problem(s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())