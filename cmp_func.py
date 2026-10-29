#!/usr/bin/env python3
"""Compare one function's original MIPS code against what PSYQ cc generates.

The repo stores original code as `dw 0x...`; PSYQ cc emits GNU-ish text. The two
dumps differ only in notation (`addiu $sp,$sp,-24` vs `subu $sp,$sp,24`,
`j $ra` vs `jr $ra`, `$4` vs `$a0`, `jal font_set_color` vs `jal 0x80044dc4`),
so both sides are normalised before comparison. What survives is a real
instruction-level mismatch.

usage: cmp_func.py <original .s> <generated .asm> <symbol> [functions.txt]
"""
import os
import re

import struct
import sys

from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32

REGS = ['zero', 'at', 'v0', 'v1', 'a0', 'a1', 'a2', 'a3',
        't0', 't1', 't2', 't3', 't4', 't5', 't6', 't7',
        's0', 's1', 's2', 's3', 's4', 's5', 's6', 's7',
        't8', 't9', 'k0', 'k1', 'gp', 'sp', 'fp', 'ra']

MD = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32)
MD.detail = True


def reg_name(n):
    if n == 0:
        return 'zero'
    if n < 32:
        return REGS[n]
    if n == 32:
        return 'fp'
    return str(n)


def canon_regs(text):
    """Rewrite every $N / $abi-name inside an operand string to the ABI name."""
    text = re.sub(r'\$(\d+)\b', lambda m: reg_name(int(m.group(1))), text)
    text = re.sub(r'\$(zero|at|v0|v1|a[0-3]|t[0-9]|s[0-7]|k[01]|gp|sp|fp|ra)\b',
                  lambda m: m.group(1), text)
    text = re.sub(r'\bzero\b', 'zero', text)
    return text


def canon_reloc(text):
    """`%hi(sym)` / `%lo(sym)` -> the half-word the instruction actually carries.

    Overlay globals are named `<prefix>_<hex address>` (e.g. rank_dword_800E189C),
    so the address can be recovered from the symbol name alone. Hex literals are
    folded to decimal so both dumps spell numbers the same way.
    """
    def repl(kind, name):
        m = re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*_([0-9A-Fa-f]{8})', name)
        if not m:
            return None
        addr = 0x80000000 | int(m.group(1), 16)
        return str((addr >> 16) & 0xFFFF) if kind == 'hi' else str(addr & 0xFFFF)

    def sub(kind):
        def f(mm):
            v = repl(kind, mm.group(1))
            return v if v is not None else mm.group(0)
        return f

    text = re.sub(r'%hi\(([^)]+)\)', sub('hi'), text)
    text = re.sub(r'%lo\(([^)]+)\)', sub('lo'), text)
    text = re.sub(r'-?0x([0-9a-fA-F]+)',
                  lambda mm: ('-' if mm.group(0).startswith('-') else '')
                             + str(int(mm.group(1), 16)), text)
    return text


def canon_insn(mnemonic, ops):
    m = mnemonic
    o = [canon_reloc(canon_regs(x)) for x in ops]

    # `j $ra` and `jr $ra` share the encoding 0x03e00008
    if m == 'j' and o and o[0] == 'ra':
        m = 'jr'

    # `jal rd, rs` is not a call to a label: it is the assembler's two-operand
    # spelling of JALR (SPECIAL funct 0x09). The dump side prints the
    # one-operand form when rd is $31, which is capstone's default. Fold both
    # onto `jalr` before the label pass below turns the second operand into `L`
    # -- that pass exists for `jal sym`, and it must not see an indirect call.
    if m == 'jal' and len(o) == 2:
        if o[0] == 'ra':
            m, o = 'jalr', o[1:]
        else:
            m = 'jalr'
    elif m == 'jalr' and len(o) == 2 and o[0] == 'ra':
        o = o[1:]

    # branch displacements are resolved by the assembler; compare the shape only
    if m in ('b', 'beq', 'bne', 'beqz', 'bnez', 'bgez', 'bgtz', 'blez', 'bltz',
             'beqzl', 'bnezl', 'bgezal', 'bltzal', 'jal', 'j') and o:
        if re.fullmatch(r'-?0x[0-9a-fA-F]+|\d+', o[-1]):
            o[-1] = 'L'

    # the assembler resolves a called symbol to a displacement
    if m == 'jal' and o and not re.fullmatch(r'-?0x[0-9a-fA-F]+|\d+', o[-1]):
        o[-1] = 'L'

    # PSYQ's dialect prints the immediate form as `sltu rd,rs,imm` / `slt rd,rs,imm`
    # while the binary carries `sltiu` / `slti`
    if m in ('sltu', 'slt') and len(o) == 3 and re.fullmatch(r'-?0x[0-9a-fA-F]+|\d+', o[2]):
        m = 'sltiu' if m == 'sltu' else 'slti'

    # `bne rt, zero, L` is printed as `bnez rt, L` by the other side
    if m == 'bne' and len(o) == 3 and o[1] == 'zero':
        m, o = 'bnez', [o[0], o[2]]
    if m == 'beq' and len(o) == 3 and o[1] == 'zero':
        m, o = 'beqz', [o[0], o[2]]
    # `beq rd,rs,L` and `beq rs,rd,L` are the same instruction, and likewise for
    # `bne`/`beqnz`. Only when neither operand is $zero: the compare-with-zero
    # forms are handled above and must keep their single operand.
    if m in ('beq', 'bne') and len(o) == 3 and 'zero' not in (o[0], o[1]):
        o = [o[1], o[0], o[2]] if o[1] < o[0] else o

    # `sllv rd, rt, rs` is the same encoding as `sll rd, rt, rs` once the shift
    # amount is a register: MIPS SLL takes a 5-bit immediate, and the assembler
    # expands the register form into SLLV.
    if m == 'sllv' and len(o) == 3:
        m = 'sll'

    # `negu rd, rs` is the same encoding as `subu rd, $zero, rs` (SPECIAL 0x23
    # with rt=zero); the two spellings are pure notation.
    if m == 'negu' and len(o) == 2:
        m, o = 'subu', [o[0], 'zero', o[1]]

    # `0(reg)` and `(reg)` are the same operand
    o = [re.sub(r'\b0\((\w+)\)', r'(\1)', x) for x in o]

    # Canonical form for the split-immediate idiom is always `addiu rd, rs, imm`.
    # `li` is a macro: it becomes `addiu` for anything that fits a signed 16-bit
    # immediate and `lui` for a value whose low half is zero. Normalising it to
    # `addiu` unconditionally would hide a real difference (`li $5,65536` is
    # `lui $5,1`, and `addiu $5,$0,65536` would assemble as `addiu $5,$0,0`).
    if m == 'li' and len(o) == 2:
        val = int(o[1], 0)
        if 0 <= val <= 0xFFFF:
            # `li rd,imm` with a non-negative 16-bit value is `ori rd,$zero,imm`;
            # the assembler only reaches for `addiu` on the negative half. Folding
            # both spellings onto `ori` is what lets a generated `li $2,65535`
            # match a dumped `ori $2,$zero,65535`.
            m, o = 'ori', [o[0], 'zero', o[1]]
        elif -0x8000 <= val <= 0x7FFF:
            m, o = 'addiu', [o[0], 'zero', o[1]]
        elif val != 0 and (val & 0xFFFF) == 0:
            # A low half of zero means the macro only needs the high half, so
            # the encoding is a single LUI whether the value is written as
            # 0x80800000 or as -2139095040. The dumps always show the real
            # instruction, and cc1psx prints either spelling for the same C.
            m, o = 'lui', [o[0], str((val >> 16) & 0xFFFF)]
    # `addiu rd,$zero,imm` and `ori rd,$zero,imm` are one encoding for every
    # non-negative 16-bit imm: ADDIU sign-extends, and for a positive value the
    # two coincide. Below 0x8000 only.
    if m == 'addiu' and len(o) == 3 and o[1] == 'zero' and re.fullmatch(r'\d+', o[2]):
        if 0 <= int(o[2]) <= 0x7FFF:
            m, o = 'ori', [o[0], 'zero', o[2]]
    if len(o) == 3 and re.fullmatch(r'-?\d+', o[2]):
        val = int(o[2])
        # MIPS has no `addu rd,rs,imm` / `subu rd,rs,imm` encoding: the third
        # operand of those instructions is a register. cc1psx still prints the
        # immediate in the addu/subu slot, so the assembler can only be emitting
        # addiu. `addu` keeps the sign it was given, `subu` flips it.
        if m == 'addu':
            m = 'addiu'
        elif m == 'subu':
            m, o[2] = 'addiu', str(-val)

    return (m + ' ' + ', '.join(o)).strip()


def load_original(path, symtab):
    code = b''
    addr = None
    for line in open(path):
        if line.strip().startswith('dw '):
            if addr is None:
                addr = int(line.split(';')[1].strip(), 16)
            code += struct.pack('<I', int(line.split(' ')[1], 16))
    out = []
    for ins in MD.disasm(code, addr):
        ops = []
        for part in ins.op_str.split(','):
            part = part.strip()
            if ins.mnemonic in ('j', 'jal') and symtab:
                try:
                    val = int(part, 0)
                    part = symtab.get(val, part)
                except ValueError:
                    pass
            part = re.sub(r'0x[0-9a-fA-F]+', lambda m: str(int(m.group(0), 16)), part)
            ops.append(part)
        out.append(canon_insn(ins.mnemonic, ops))
    return out


DIRECTIVE = re.compile(r'^\s*\.')


def load_generated(path, symbol, symtab):
    lines = open(path).read().split('\n')
    start = None
    for i, line in enumerate(lines):
        if line.strip() == symbol + ':':
            start = i
            break
    if start is None:
        raise SystemExit(f'symbol {symbol} not found in {path}')

    out = []
    i = start + 1
    n = len(lines)
    while i < n:
        raw = lines[i]
        stripped = raw.strip()
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*:\s*$', stripped):
            break
        if stripped.startswith(('.ident', '.size', '.section')):
            break
        # `#nop` is a real instruction in this dialect, not a comment
        if stripped == '#nop':
            out.append('nop')
            i += 1
            continue
        line = raw.split('#')[0].rstrip()
        if not line.strip() or DIRECTIVE.match(line):
            i += 1
            continue
        m = re.match(r'^\s*([a-z][a-z0-9.]*)\s*(.*)$', line)
        if not m:
            i += 1
            continue
        mnemonic, rest = m.group(1), m.group(2)
        ops = []
        if rest:
            for part in rest.split(','):
                part = part.strip()
                part = re.sub(r'\$L\d+', 'L', part)
                part = re.sub(r'\$-?0x([0-9a-fA-F]+)', lambda mm: str(int(mm.group(1), 16)), part)
                ops.append(part)
        out.append(canon_insn(mnemonic, ops))
        i += 1

    # PSYQ emits a trailing `#nop` filler just before the return jump; the original
    # binaries carry no instruction there. The jump sits two slots from the end
    # because the frame restore fills the delay slot.
    if len(out) >= 3 and out[-3] == 'nop' and out[-2] in ('j ra', 'jr ra'):
        del out[-3]
    return out


LUI_CONST = re.compile(r'^lui (\S+), (\d+)$')
ADD_SAME = re.compile(r'^(?:addiu|addu|addi) (\S+), \1, (-?\d+)$')
ORI_SAME = re.compile(r'^ori (\S+), \1, (\d+)$')


def fold_lui_pairs(seq):
    """Collapse `lui r,H` + `addiu r,r,X` into one `li32 r,(H<<16)+X`.

    The same 32-bit constant has several legal splits: 0x800CF938 is
    `lui $2,0x800d` + `addiu $2,$2,-0x6c8` in the original dump and
    `lui $2,0x800c` + `addiu $2,$2,0xf938` in cc1psx output. Only adjacent pairs
    are folded, so the `addiu` that sits in a branch delay slot stays separate -
    there it really is a different instruction.
    """
    out, i = [], 0
    while i < len(seq):
        m1 = LUI_CONST.match(seq[i])
        if m1 and i + 1 < len(seq):
            m2 = ADD_SAME.match(seq[i + 1])
            if m2 and m2.group(1) == m1.group(1):
                val = (int(m1.group(2)) << 16) + int(m2.group(2))
                out.append(f'li32 {m2.group(1)}, {val}')
                i += 2
                continue
            m2 = ORI_SAME.match(seq[i + 1])
            if m2 and m2.group(1) == m1.group(1):
                val = (int(m1.group(2)) << 16) | int(m2.group(2))
                out.append(f'li32 {m2.group(1)}, {val}')
                i += 2
                continue
        out.append(seq[i])
        i += 1
    return out


LUI_LW = re.compile(r'^lui (\S+), (\d+)$')
LW_BASE = re.compile(r'^lw (\S+), (-?\d+)\((\S+)\)$')


def fold_lui_lw_pairs(seq):
    """Collapse `lui r,H` + `lw rt,X(r)` into `lw rt,addr`.

    The same global address has several legal splits: 0x800AB2EC is
    `lui $2,0x800b` + `lw $5,-0x4d14($2)` in the original dump and
    `lui $2,0x800a` + `lw $5,0xb2ec($2)` in cc1psx output. Only adjacent
    pairs are folded.
    """
    out, i = [], 0
    while i < len(seq):
        m1 = LUI_LW.match(seq[i])
        if m1 and i + 1 < len(seq):
            m2 = LW_BASE.match(seq[i + 1])
            if m2 and m2.group(3) == m1.group(1):
                hi = int(m1.group(2))
                lo = int(m2.group(2))
                # No sign extension: canon_reloc already converted %lo to unsigned,
                # and the original's signed immediate is preserved as-is.
                # (hi << 16) + lo works for both signed (original) and unsigned
                # (generated) because of the carry from the high half.
                addr = (hi << 16) + lo
                out.append(f'lw {m2.group(1)}, {addr}')
                i += 2
                continue
        out.append(seq[i])
        i += 1
    return out


def load_symtab(path):
    if not path or not os.path.exists(path):
        return {}
    tab = {}
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) == 3:
                tab.setdefault(int(parts[0], 16), parts[2])
    return tab


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    orig_path, gen_path, symbol = sys.argv[1], sys.argv[2], sys.argv[3]
    symtab = load_symtab(sys.argv[4]) if len(sys.argv) > 4 else {}

    a = fold_lui_lw_pairs(fold_lui_pairs(load_original(orig_path, symtab)))
    b = fold_lui_lw_pairs(fold_lui_pairs(load_generated(gen_path, symbol, symtab)))

    n = max(len(a), len(b))
    print(f'original : {len(a)} instructions')
    print(f'generated: {len(b)} instructions')
    print()
    width = max((len(x) for x in a + b), default=0)
    diffs = 0
    for i in range(n):
        x = a[i] if i < len(a) else ''
        y = b[i] if i < len(b) else ''
        if x != y:
            diffs += 1
        print(f'{" " if x == y else "*"} {i:3d}  {x:<{width}}   |   {y}')
    print()

    if diffs == 0 and len(a) == len(b):
        print('MATCH')
        return 0

    # `#nop` in this dialect is sometimes a real instruction and sometimes a filler
    # marker, so also report the verdict with nops discounted.
    an = [x for x in a if x != 'nop']
    bn = [y for y in b if y != 'nop']
    if an == bn:
        extra = len(b) - len(a)
        print(f'MATCH ignoring {abs(extra)} NOP '
              f'({extra:+d} in generated)')
        return 0

    print(f'{diffs} differing instruction(s) of {n}')
    return 1


if __name__ == '__main__':
    sys.exit(main())