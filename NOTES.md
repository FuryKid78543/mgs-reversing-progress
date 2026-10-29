# MGS overlay decompilation — session notes

Target: `FoxdieTeam/mgs_reversing` (MGS Integral, `SLPM_862.47/48/49`), cloned at
`work/mgs_reversing` (master @ `f54dbb2`).

> The discs in `Porting/` are **MGS USA 1998** (`SLUS_005.94` / `SLUS_007.76`), a
> different build from the Integrity executables this repo targets. They are not
> usable as build inputs, but they were useful as ground truth (see "addu/addiu").

## Toolchain: working (macOS x86_64)

`wine` is not an option on this machine: Homebrew disabled the `wine-stable` cask
on 2026-09-01 (Gatekeeper), and the Wine builds already present (Wineskin's
WineCX 11.0, Game Porting Toolkit 7.7) are 64-bit only — they cannot load
`kernel32.dll` for a PE32 binary, which is what every PSYQ tool is.

The working path is **wibo**, a native Win32 loader for these tools:

- `work/wibo` — clone of `decompals/wibo`, built with
  `cmake --preset release-macos` (cmake 4.1.1 unpacked in `/private/tmp/opencode`)
- two local patches were needed to make `asmpsx.exe`/`aspsx.exe` run:
  - `dll/kernel32/wincon.{h,cpp}`: added `WriteConsoleA` (was missing, so the
    assembler segfaulted on its first output call)
  - `src/heap.cpp`: pre-create the first guest arena in the constructor
- result installed at `/usr/local/bin/wibo`
- `psyq_sdk` cloned at `work/psyq_sdk` (default `--psyq_path ../../psyq_sdk` resolves there)
- venv at `/Users/fury/mgsvenv` — **must not contain spaces**: `build.py` interpolates
  `sys.executable` into ninja rules without quoting, so a venv under
  `.../Progetti OpenCode/...` breaks every rule

Two local edits to `build/build.py` (kept out of the decompilation commits):
- `prefix()` now falls back to `wibo` when `wine` is absent
- the `cpe2exe` rule drops `cmd /c ... > NUL` when running under wibo

`aspsx`/`asmpsx` still crash (mimalloc arena allocation needs a host mutex, and the
guest runs with its own `fs` segment on macOS). That only blocks *assembly and
linking*, so a full `build.py` run cannot finish here.

## Verification without the assembler

Assembling is not needed to check a decompilation: `cc1psx` already emits the
final assembly, so a single function can be compared instruction by instruction.
This is the same loop decomp.me drives.

- `work/cmp_func.py` — diffs one function: the repo's `dw` dump (disassembled with
  capstone) against the `.asm` `cc1psx` produced, normalising notation
- `work/checkall.py` — MATCH/DIFF summary for every function in the working
  branch; it discovers the symbols itself from `orig_<sym>.s` + `obj/**/*.asm`,
  and separates "still INCLUDE_ASM" (TODO) from real failures
- `work/rank_safe.py` — ranks the remaining pragmas by how likely an exact match
  is: delay-slot holes, struct gaps, call sites, size
- `work/delayslots.py` — measures delay-slot filling across every dump
- `work/twins.py` — pairs undecopiled dumps with already-compiled C of the same
  shape (the stand-in for `build/find_duplicates.py`, which needs `.obj` files
  we cannot produce)
- `work/tryfn.sh <candidate.c> <source path> <obj path> <orig .s> <symbol>` —
  swaps a candidate file in, builds that one object, reports the verdict, and
  always restores the repo file
- iterate with `ninja ../obj/<path>.asm` for a single translation unit; build all
  of them at once with `ninja -t targets all | grep '\.asm:' | xargs ninja`

Normalisations that are safe (each backed by evidence, not guesswork):
- `$4` / `a0`, `j $ra` / `jr $ra`
- `%hi(sym)` / `%lo(sym)` resolved from the hex address embedded in overlay symbol
  names (`rank_dword_800E189C` → `0x800E189C`)
- branch targets compared by shape, not displacement
- `li rd,N` expands to `addiu` when `N` fits a signed 16-bit immediate and to
  `lui` when the low half is zero. Normalising `li` to `addiu` unconditionally
  is wrong: `li $5,65536` is `lui $5,1`, while `addiu $5,$0,65536` assembles as
  `addiu $5,$0,0`. This one bug was masking a real difference in `s14e_800CD514`.
- `negu rd,rs` ≡ `subu rd,$zero,rs` (SPECIAL 0x23 with `rt=zero`)
- `addu rd,rs,imm` / `subu rd,rs,imm`: MIPS has no immediate form of either —
  their third operand is a register — so the assembler can only be emitting
  `addiu`. `addu` keeps the sign it was given, `subu` flips it.
- an **adjacent** `lui r,H` + `addiu r,r,X` pair is folded to one `li32`, because
  the same constant has several legal splits: `0x800CF938` is `lui $2,0x800d` +
  `addiu $2,$2,-0x6c8` in the dump and `lui $2,0x800c` + `addiu $2,$2,0xf938`
  in cc1psx output. Only adjacent pairs fold, so the `addiu` that fills a branch
  delay slot stays a separate instruction — there it really is different.

## The toolchain ceiling: empty delay slots

`work/delayslots.py` over all 831 dumps: 9611 conditional branches, **82.9% of
delay slots filled**, 17.1% left as `nop`. Konami's compiler fills them
aggressively, and in the whole corpus it never puts the negation of the register
a branch tests into that branch's delay slot (that would execute unconditionally
and change the meaning).

`cc1psx` has no such restraint. Given `if (x < 0) { x = -x; }` it emits

    bgez  $2,$L
    subu  $2,$0,$2     # delay slot: runs even when the branch is taken
    $L:

which negates unconditionally. That is why `s14e_800C6ADC` cannot be matched:
the code is structurally identical to the original except that the three
`nop`s the original needs are filled. The same shows up in `s11g_hind_800DA534`
(one `nop` in the delay slot of a `jal`) and `s14e_800C609C`.

Practical consequence: **an empty delay slot in the original is a reliable
predictor that the function is unreachable with this toolchain**, which is the
first column of `work/rank_safe.py`. Of 107 candidates under 30 instructions,
61 have neither a delay-slot hole nor a struct gap.

## Struct offsets are the other half of the work

`rank_safe.py` flags functions whose offsets land in a `pad` region. Those need
the `Work` struct extended before any C can be written. Worse, a struct can be
wrong in a way the comments hide: in `animal/zako14/zako.h`, `vision` is
commented `0xB94` but `cc1psx` places `vision.length` at **0xB90**, while
`s14e_800CA96C` reads 0xB8C/0xB8E. `PARAM`'s own comments disagree with each
other (`/* 0xB82 */ short unknown;` and `/* 0xB82 */ short damage;`). Every
offset in that struct has to be confirmed against the compiler before it can be
trusted, and a wrong offset silently breaks every function in the overlay.

`work/fix_all_structs.py` answers this without guessing. For each `Work` struct
it emits a probe per field that returns the field's address, reads back the
displacement cc1psx produced, and rewrites the `/* 0xNNN */` comment to match.
Only comments change — the declarations are what define the layout, so editing a
size would move every field after it.

The result across the whole tree: of ~70 `Work` structs, **only zako11a/zako14
were stale**, and the cause was a single duplicated `/* 0xB82 */` on `PARAM`
pushing everything from `damage` up to `field_C44` two bytes low. psyco, meryl,
hind, snake, torture, rope and jeep are all clean.

## Where cc1psx simply cannot follow

Two classes are unreachable, and knowing them saves a lot of wasted iterations:

- **Empty delay slots.** `delayslots.py` over all 831 dumps: 9611 conditional
  branches, 82.9% of delay slots filled. Konami's compiler never puts the
  negation of the register a branch tests into that branch's delay slot, because
  the slot always executes and it would change the meaning. `cc1psx` has no such
  restraint — given `if (x < 0) { x = -x; }` it emits the `subu` in the delay
  slot and negates unconditionally. That is why `s14e_800C6ADC` cannot be
  matched: the code is structurally identical except that the three `nop`s the
  original needs are filled.
- **The epilogue `nop`.** Only 7.2% of the 826 dump epilogues put a `nop`
  between `lw $ra,off($sp)` and `jr $ra`, but `cc1psx` never emits one there
  (the PSX has no load delay slot, so the compiler has no reason to). This caps
  `rank_800D0A24` and `rank_800D0ABC` at NOPDIFF.

A third pattern is a redundant zero-extension that GCC always folds:
`andi $r,$r,0xffff` applied to the result of an `lhu`. It shows up in
`rank_800CF2F0` and `s11d_hind_800CAFEC` and resisted every phrasing tried.

A fourth, found later on `rank_800D1A64`: **`lh` where `cc1psx` emits `lhu`**.
The SGI compiler loaded every signed short with `lh`; `cc1psx` looks at how the
value is used and drops the sign extension when only the low 16 bits survive
(typically because the result goes straight into an `sh`). The two are
equivalent for `a - b` modulo 2^16 as long as both operands are extended the
same way, so the original's choice was not wrong — just conservative. Forcing
`lh` does not work: declaring the locals `int`, or casting to `(signed short *)`,
both leave `cc1psx`'s decision unchanged, because the analysis follows the use
and not the declared type. This one is deliberately **not** normalised in
`cmp_func.py`: the two mnemonics are not the same instruction, and folding them
would hide real differences.

## The toolchain is not trustworthy in one spot

`delayprobe.py` walks the compiled tree and every dump with the same code:

| | cc1psx output | Konami's dumps |
|---|---|---|
| conditional branches | 37379 | 9585 |
| delay slot left `nop` | **0** | 1643 (17.1%) |

cc1psx fills every single delay slot it emits. Probing seven source shapes
(`if (a==0) return 1;`, loops, `for` with `break`, an empty guarded statement)
never produced an empty one, and it is a GCC 2.x, so the filling comes from the
scheduler rather than a switch.

Worse, in one shape it is **wrong**, not merely different:

```c
int probe_h(int a){ if (a < 0) a = -a; return a; }
/* move $2,$4 ; bgez $2,L ; subu $2,$0,$2 ; j $31   -> returns -a for a >= 0 */
```

The negate sits in the delay slot of the branch that skips it, and a delay slot
always runs. So the delay-slot differences are **not** cosmetic, and relaxing
the comparison to accept them would admit broken code. That is also why
`cmp_func.py` refuses to fold `lh` into `lhu`: same offset, but the question of
whether the discarded high half mattered is exactly what the miscompilation is
made of.

## Working inside the sandbox

Two things bit during the fourth session and are worth knowing:

- The build output directory is overridable with `MGS_OBJ_DIR`, because the
  checked-in `obj/` may be owned by another user and then is neither writable
  nor replaceable. `checkall.py`, `twins.py` and `tryfn.sh` all honour it.
- `checkall.py` reads `obj/**/*.asm`, so after `tryfn.sh` has tried a candidate
  it will report the *candidate's* assembly until the object is rebuilt.
  `tryfn.sh` now rebuilds in its restore trap.

## Result

| function | status |
|---|---|
| `s14e_800CCC70` | **MATCH** (7/7) |
| `rank_800D1988` | **MATCH** (20/20) |
| `rank_800D51EC` | **MATCH** (20/20) |
| `rank_800CD4E4` | **MATCH** (23/23) |
| `s14e_800CA890` | **MATCH** (18/18) — zako14 twin of `ZAKO11A_SetPutChar` |
| `s14e_800CA8D8` | **MATCH** (13/13) — zako14 twin of `ZAKO11A_ClearPutChar` |
| `s14e_800CD514` | **MATCH** (18/18) |
| `s14e_800CA96C` | **MATCH** (11/11) — needs the corrected zako14 offsets |
| `s14e_800CD55C` | **MATCH** (18/18) |
| `rank_800CCF10` | **MATCH** (19/19) — statement order decides whether the `lui` is hoisted |
| `s07b_800D0F94` | **MATCH** (11/11) |
| `s07b_800CB264` | **MATCH** (16/16) — naming `control.turn.vy` confirms the CONTROL layout |
| `s14e_800C5DAC` | **MATCH** (23/23) — needs `&work->act` in a local to get `s1` and a 0x20 frame |
| `rank_800CC738` | **MATCH** (24/24) — after the `sllv` normalisation |
| `rank_800D19E4` | **MATCH** (23/23) |
| `s14e_800CCC04` | **MATCH** (27/27) |
| `s14e_800CD0FC` | **MATCH** (34/34) — shape lifted from the s08b ninja twin |
| `rank_800D21DC` | **MATCH** (15/15) |
| `s07b_800CAF54` | **MATCH** (29/29) — SVECTOR passed by value becomes the `sll`/`or` pair |
| `s07b_800CBD4C` | **MATCH** (21/21) — the flag store is inside the guard |
| `rank_800D8E64` | **MATCH** (32/32) |
| `s07b_800CC514` | **MATCH** (25/25) — after the `li`/`ori` normalisation |
| `s14e_800CD024` | **MATCH** (26/26) — the bound needs an explicit unsigned cast |
| `rank_800CF2F0` | **MATCH** (14/14) — the `sllv` normalisation also cleared this one |
| `rank_800D1A64` | 36/36 — eight `lh` where `cc1psx` emits `lhu`, provably equivalent |
| `s14e_800C6A34` | 34/34 — the original keeps `work` in `a2`, the rebuild in `s0` |
| `s14e_800CAA1C` | 22/24 — the original burns a third callee-saved register |
| `s07b_800CF684` | **MATCH** (27/27) |
| `s07b_800CDA90` | 27/27 — the original hoists the flag store into the delay slot, cc1psx hoists the call setup |
| `rank_800CEE0C` | **MATCH** (33/33) |
| `rank_800D1AF4` | **MATCH** (33/33) |
| `s07b_800CBAA4` | 37 insns vs 36 — the original has a dead `r = 3` that cc1psx folds away |
| `s14e_800C86D4`, `s14e_800C6D20` | blocked by a `short` global at `0x800C339C`/`0x800C339E`, declared nowhere |
| `s11d_hind_800CAFEC` | 26/26, loop identical — the redundant `andi` plus the prologue order |
| `rank_800D0A24` | 19/19 — one `nop` the epilogue needs and `cc1psx` never emits |
| `rank_800D0ABC` | 19/19 — same |
| `s14e_800CCBA8` | 23/23 — identical instruction set, two independent chains loaded in opposite order |
| `rank_800D128C` | 16/17 — `cc1psx` CSEs the packing shared by the two branches, the original duplicates it |
| `s14e_800C6AC0` | semantically correct; 5 insns vs 7 — original keeps two `nop` |
| `s14e_800C609C` | semantically correct; 6 vs 8 — original keeps two `nop` |
| `s14e_800C6ADC` | semantically correct; `cc1psx` fills delay slots with a conditional negate |
| `s07b_800CB068` | correct logic; scheduler/`ori` differences |
| `s11g_11g_demo_800CB878` | correct logic; different block layout and register allocation |
| `s11g_hind_800DA534` | 55/56; one `nop` in the delay slot of a `jal` |

The strongest predictor of a match turned out to be **the same function already
decompiled elsewhere in the tree** — zako11a's `put.c` has the exact source for
both `s14e_800CA890` and `s14e_800CA8D8`, and both matched first try. The second
predictor is **a function whose shape mirrors one already matched**: `800CD55C`
matched first try because it is `800CD514` with the condition flipped. That is
also what `twins.py` automates.

## How to continue

1. pick a target with `work/rank_safe.py <max_insns> <limit>` — it sorts out
   delay-slot holes, struct gaps and undeclared globals before you start
2. check for a twin first with `work/twins.py`, and look for a function of the
   same shape that already matched
3. disassemble with `work/disasm.py <file>.s`
4. write the C, replacing the `#pragma INCLUDE_ASM` + prototype
5. `cp` the dump to `work/orig_<symbol>.s` **before** `git rm`-ing it, or
   `checkall.py` has nothing to compare against
6. `git rm` the `.s` — `build/post_build_checkup.py` fails the CI on orphans
7. `work/tryfn.sh` to iterate quickly, then `work/checkall.py` for the branch
8. commit the `.c` and the `.s` deletion together

Two things to get right when reading a diff:

- **when the instruction counts differ the comparison table misaligns** and shows
  phantom differences. Dump the generated `.asm` and read it directly.
- when two names are easy to swap, the diff will look identical either way.
  `RADAR_SIGHT_PARAM` is `dir(0) dis(2) range(4) r(6)` — writing `r = 0` where
  the original meant `range = 0` still gave "7 differences".

Skip anything that needs a global the repo has not declared yet (e.g.
`s07b_800CC6CC` writes six undeclared dwords at `0x800C3480`..`0x800C3494`):
adding a declaration shifts the overlay's data layout, and without a working
linker there is no way to check that the rest still lines up.

## Verifica funzionale: `mipsemu.py`

Secondo livello, **non** sostituisce `cmp_func.py`. Se `cmp_func.py` risponde
"il testo è lo stesso", `mipsemu.py` risponde "fa la stessa cosa". I due
verdetti si tengono separati: `MATCH` (28 funzioni) abilita la sostituzione del
pragma, `EQUIV` no.

```
.venv/bin/python mipsemu.py <dump.s> <obj.asm> <simbolo> [--trials N]
./equiv.sh <candidato.c> <sorgente/rel.c> <obj/rel.asm> <dump.s> <simbolo>
```

`equiv.sh` installa il candidato, compila, esegue il confronto e ripristina il
repo. Senza di esso non si può confrontare: `obj/` deve contenere la *variante*,
e `tryfn.sh` ricostruisce alla versione pulita.

Prima di fidarsi: `mipsemu.py --selftest`, 12 casi incluso il bug di delay slot
di `cc1psx`. Se il selftest non passa, i risultati non valgono niente.

`symtab.json` (imparato) e `arity.json` (prototipi, 6734 funzioni) sono cache:
si possono cancellare, vengono ricalcolate.
