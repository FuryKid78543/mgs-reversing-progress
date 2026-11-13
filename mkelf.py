#!/usr/bin/env python3
"""Build a loadable ELF32 MIPS-LE image from the split overlay dumps.

The mgs_reversing tree stores each overlay function as a `dw` dump with its
virtual address in a trailing comment.  REA/Ghidra cannot open raw PSX data,
so this reassembles those words into a flat .text section at the original
addresses and wraps it in a minimal ET_EXEC ELF32 MIPS32r2 image with a
function symbol table and a valid .MIPS.abiflags record.

usage: mkelf.py <overlay> [<out.elf>]
"""
import glob
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(ROOT, 'mgs_rev2')
ASM = os.path.join(REPO, 'asm', 'overlays')
OUTDIR = os.path.join(ROOT, 'elf')

WORD = re.compile(r'dw\s+0x([0-9A-Fa-f]{8})\s*;\s*([0-9A-Fa-f]{8})')
XDEF = re.compile(r'^\s*xdef\s+(\S+)', re.M)


def collect(overlay):
    words = {}
    funcs = []
    for path in glob.glob(os.path.join(ASM, overlay, '*.s')):
        text = open(path, errors='ignore').read()
        m = XDEF.search(text)
        name = m.group(1) if m else None
        first = None
        for mm in WORD.finditer(text):
            val, addr = int(mm.group(1), 16), int(mm.group(2), 16)
            if first is None:
                first = addr
            if addr in words and words[addr] != val:
                raise SystemExit(f'conflict at {addr:08X} in {path}')
            words[addr] = val
        if name and first is not None:
            funcs.append((first, name))
    return words, funcs


def build(overlay, out):
    words, funcs = collect(overlay)
    if not words:
        raise SystemExit(f'no words for overlay {overlay}')
    base = min(words)
    end = max(words) + 4
    size = end - base
    text = bytearray(b'\0' * size)
    for addr, val in words.items():
        struct.pack_into('<I', text, addr - base, val)

    funcs.sort()
    sized = []
    for i, (addr, name) in enumerate(funcs):
        nxt = funcs[i + 1][0] if i + 1 < len(funcs) else end
        sized.append((addr, max(nxt - addr, 4), name))

    ehsize, phentsize, phnum = 52, 32, 1
    shentsize = 40
    text_off = 0x1000
    sym_off = (text_off + size + 3) & ~3

    strtab = bytearray(b'\0')
    syms = [(0, 0, 0, 0, 0, 0)]
    str_off = {}
    for addr, fsize, name in sized:
        if name not in str_off:
            str_off[name] = len(strtab)
            strtab += name.encode() + b'\0'
        syms.append((str_off[name], addr, fsize, (1 << 4) | 2, 0, 1))
    sym_size = 16 * len(syms)
    strtab_off = sym_off + sym_size

    # .MIPS.abiflags: version 0, ISA 32.2, 32-bit GPR/CPR1, double FP ABI
    abi = struct.pack('<HBBBBBBIIII', 0, 32, 2, 1, 1, 0, 1, 0, 0, 0, 0)
    assert len(abi) == 24
    abi_off = (strtab_off + len(strtab) + 7) & ~7

    shstr = bytearray(b'\0')
    shnames = {}
    for nm in ('.text', '.MIPS.abiflags', '.symtab', '.strtab', '.shstrtab'):
        shnames[nm] = len(shstr)
        shstr += nm.encode() + b'\0'
    shstr_off = abi_off + len(abi)
    sh_off = (shstr_off + len(shstr) + 3) & ~3

    ehdr = struct.pack(
        '<16sHHIIIIIHHHHHH',
        b'\x7fELF' + bytes([1, 1, 1, 0]) + bytes(8),
        2, 8, 1, base, ehsize, sh_off, 0x70001000,
        ehsize, phentsize, phnum, shentsize, 6, 5)
    phdr = struct.pack('<IIIIIIII', 1, text_off, base, base, size, size, 5, 4)
    shdrs = [b'\0' * shentsize]
    shdrs.append(struct.pack('<IIIIIIIIII', shnames['.text'], 1, 6, base,
                             text_off, size, 0, 0, 4, 0))
    shdrs.append(struct.pack('<IIIIIIIIII', shnames['.MIPS.abiflags'],
                             0x7000002A, 0, 0, abi_off, len(abi), 0, 0, 8, 24))
    shdrs.append(struct.pack('<IIIIIIIIII', shnames['.symtab'], 2, 0, 0,
                             sym_off, sym_size, 4, 1, 4, 16))
    shdrs.append(struct.pack('<IIIIIIIIII', shnames['.strtab'], 3, 0, 0,
                             strtab_off, len(strtab), 0, 0, 1, 0))
    shdrs.append(struct.pack('<IIIIIIIIII', shnames['.shstrtab'], 3, 0, 0,
                             shstr_off, len(shstr), 0, 0, 1, 0))
    symtab = b''.join(struct.pack('<IIIBBH', *s) for s in syms)

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'wb') as f:
        f.write(ehdr)
        f.write(phdr)
        f.write(b'\0' * (text_off - ehsize - phentsize))
        f.write(text)
        f.write(b'\0' * (sym_off - text_off - size))
        f.write(symtab)
        f.write(strtab)
        f.write(b'\0' * (abi_off - strtab_off - len(strtab)))
        f.write(abi)
        f.write(shstr)
        f.write(b'\0' * (sh_off - shstr_off - len(shstr)))
        f.write(b''.join(shdrs))
    print(f'{overlay}: {len(funcs)} funcs, {size} bytes, base {base:08X} -> {out}')


if __name__ == '__main__':
    ov = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(OUTDIR, ov + '.elf')
    build(ov, out)
