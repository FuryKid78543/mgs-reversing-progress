# MGS Reversing — Decompilation Progress

Reverse-engineering project for **Metal Gear Solid (1998, PSX)**.  
Goal: decompile the remaining overlay functions from `FoxdieTeam/mgs_reversing` into C, verified instruction-by-instruction with `cc1psx`.

## Progress

| Metric | Count | Description |
|---|---|---|
| **MATCH** | **52** | Decompiled and verified exact with `cc1psx` (instruction-level) |
| NOPDIFF | 23 | Decompiled, but original had `nop` in delay slots (MATCH impossible) |
| DIFF | 22 | Decompiled, schedule/codegen differences only (no semantic mismatch) |
| TODO | 650 | Still in assembly (`INCLUDE_ASM`) |
| **Total** | **747** | Functions in the dump |

Progress: **52/747 (7.0%)** decompiled with exact MATCH; **97/747 (13.0%)** with a C implementation (MATCH + NOPDIFF + DIFF).

## Methodology

1. **Compiler verification, not eyeballing**: every candidate is compiled with `cc1psx` (PSYQ 4.4) and compared instruction-by-instruction against the original dump. Only exact matches enable the pragma substitution.
2. **Differential emulation**: `mipsemu.py` executes both original and rebuilt dumps on a MIPS emulator and compares effects. Verdict `EQUIV` is separate from `MATCH` — `EQUIV` alone does NOT enable the pragma.
3. **Find the twin**: the winning method is locating an already-decompiled function with the same idioma in another overlay/module and reusing its type and pattern.
4. **Measure, don't guess**: `variants.py` / `trybody.py` enumerate plausible forms and measure them.
5. **Quality before volume**: no pragma substitution unless exact.

## Key Findings

- **95% of candidates are blocked on data symbols**, not code. The data segment is laid out by declaration order (psylink `org $80010000`), so adding a symbol moves everything after it.
- **31 functions have a single register restore** → `nop` between `lw ra` and `jr ra` → MATCH unreachable by construction.
- **Register allocation is the real obstacle**: several functions have correct logic but cc1psx allocates registers differently from Konami's compiler.
- **Addresses are per-overlay**: the same address means different things in different overlays. Every comparison must be scoped.
- **`volatile` on structs**: approved but unused in the repo (single justified exception: `s14e_800C9A68`).

## Tools

| Tool | Purpose |
|---|---|
| `mipsemu.py` | Differential MIPS emulator. Selftest required before trusting verdicts. |
| `cmp_func.py` | Instruction-level comparison (normalizes notation, symbols, macros). |
| `ready.py` | Filters candidates whose external references are all known symbols. |
| `rank_match.py` | Ranks candidates by delay-slot risk (empty slots, stores in slots, epilogue). |
| `audit.py` | Runs emulator on all already-decompiled C functions. |
| `match.sh` | Installs candidate, builds, compares, restores. |
| `trybody.py` / `variants.py` | Enumerate and measure plausible implementations. |
| `mkelf.py` | Rebuilds an overlay dump into an ELF32 MIPS image for REA/Ghidra analysis. |

### Known `cmp_func.py` limitations (fixed)

- `li` with zero low half: fixed (`0 <= val` → `val != 0 and (val & 0xFFFF) == 0`)
- `jal $31,reg` → `jalr`: fixed (was printed as `jal ra, L`, indistinguishable from direct calls)
- `lui`+`ori` fold: fixed (was only folding `lui`+`addiu`)
- `lui`+`addiu`/`ori` with a different destination register: folded when the base
  register is dead afterwards (added 13/11)
- `lui; jal; addiu` delay-slot split: folded for `jal` only, never for
  conditional branches (added 13/11)
- `lui` + `lw` and `lui` + `addiu`/`ori` separated by up to four independent
  instructions: folded with a base-register liveness check (added 13/11)
- Calls to overlay-local symbols named `<overlay>_<address>` are compared by
  the address encoded in the name, so `functions.txt` stays main-exe-only
  (added 13/11)

## Repository Structure

```
work/
├── mgs_rev2/              # Main repo (branch: decompile-overlays)
│   ├── source/
│   │   ├── overlays/      # Overlay source (s14e, s07b, s08b, rank, brf, s15c, s11d, ...)
│   │   ├── libdg/         # DG (display) library
│   │   ├── libhzd/        # HZD (hazard/collision) library
│   │   └── ...
│   └── build/             # Build system (ninja, compare.py)
├── mipsemu.py             # Differential emulator
├── cmp_func.py            # Instruction comparison
├── ready.py               # Candidate filter
├── rank_match.py          # Candidate ranking
├── audit.py               # Equivalence audit
├── match.sh               # Build/compare/restore
├── WORKLOG.md             # Detailed work log (1640+ lines)
└── README.md              # This file
```

## Decompiled Functions (MATCH)

| Function | Overlay | Instructions | Notes |
|---|---|---|---|
| `s14e_800C5960` | s14e | 31 | Twin of FreeModel (meryl3.c). Inline object array at DG_OBJS+0x48. |
| `s14e_800C5958` | s14e | 1 | Empty (nop). |
| `rank_800CD1A8` | rank | 34 | TRadioFileModeFn: builds TextConfig, calls `_menu_number_draw_string2`. |
| `rank_800CCF64` | rank | — | Guard: `GV_DestroyActor` inside `rank_dword_800E1878 >= 0` check. |
| `rank_800D12D0` | rank | 400+ | Fills two menu primitives. |
| `rank_800CD1A8` | rank | 34 | (see above) |
| `s14e_800CD184` | s14e | 37 | HZD_GetAddress/ReachTo + s14e_800CD0FC. |
| `s08b_bunsin2_800D0784` | s08b | 36 | HZD re-seat + counter. |
| `s08b_bunsin2_800CEA34` | s08b | 67 | HZD_FarZoneNavigate + GetRoute, HZD_ZON → SVECTOR. |
| `s07b_800CBAA4` | s07b | 37 | GV_SubVec3 + GV_VecDir2 angle classification. |
| `s07b_800CB224` | s07b | 16 | Yaw from global SVECTOR at 0x800ABA10. |
| `s07b_800CB068` | s07b | 19 | NOPDIFF (message_len in delay slot). |
| `s14e_800C6AC0` | s14e | — | `int m; m = work->target->damaged & TARGET_DOWN; return m != 0;` |
| `rank_800D0A24/0A70/0ABC/0B08` | rank | 18 each | NOPDIFF (family, all EQUIV). |
| `s07b_800C4FB8` | s07b | 21 | `DG_OBJ[4]` + `DG_MovePos` + `ReadRotMatrix` + `AN_SocomFlash`. |
| `s07b_800C54C0` | s07b | 10 | Shadow enable/disable helper (`field_8C2`, `field_84C`). |
| `s07b_800C54E8` | s07b | 11 | Undo of `800C54C0`, clears `field_8C2` and re-enables shadow bits. |
| `s14e_800CA2E4` | s14e | 28 | Blood splash matrix on `DG_OBJ[idx]` (twin of `s07b_800C4F50`). |
| `s08b_bunsin2_800CD614` | s08b | 31 | Muzzle flash on `DG_OBJ[4]`, `GM_SeSet` + `AN_SocomFlash`, re-arms itself. |
| `s08b_bunsin2_800CD770` | s08b | 38 | `RotMatrix` for the floor light prim, then copies the body translation. |
| `s14e_800C82D0` | s14e | 29 | Copies `data_800AB9F4[1]` (SVECTOR) into `control.turn`, sets the position, then `GV_SubVec3` into `control.step`. |
| `s08b_bunsin2_800CE21C` | s08b | 44 | Direction table lookup by `GV_Clock % 4`, HZD address probe; keeps the table expression rematerialized instead of cached. |
| `s08b_bunsin2_800CD808` | s08b | 28 | Counter 0x800C3438: every other frame calls `s08b_800C5D48`, every 8th re-arms the actor. |
| `s14e_800C6F94` | s14e | 33 | `ratan2` pitch to the player, `GV_NearExp8` smoothing, negative/positive `*3` or `*3/2` store. |

## Blocked Functions (examples)

| Function | Blocker |
|---|---|
| `s14e_800CD6C4` | Global at 0x800DD7AC in unmodeled gap (0x3B8 bytes before bss). |
| `s07b_800CDA04` | 7-byte copy ("MANTIS") via lwl/lwr, needs 7-byte type. |
| `rank_800CC19C` | Missing main-exe symbols (0x8008E768, 0x800AB2EC). |
| `s07b_800CB224` | Missing main-exe symbol (0x800AB2EC) — resolved via raw address. |
| `s11d_hind_800CAFEC` | `andi 0xffff` folded by cc1psx + register assignment. |
| `brf_800DDC40` | Scheduler coin flip (lui position in load-delay slot). |
| `s14e_800C6208` | Register allocation differs (counter in a1 vs v1). |

## NOPDIFF Functions (MATCH impossible)

10 functions whose original has a `nop` in a delay slot: a single register
restore before `jr ra`, or an unfilled `jal`/`jr` slot. cc1psx fills those
slots, so the instruction streams can never be identical, but the C is
semantically equivalent.

Examples: `s07b_800C4F50`, `s07b_800C5514`, `s14e_800C6028`,
`s14e_800C6E08`, `s14e_800CA49C`, `s14e_800CADD0`, and the `rank_800D0A24`
family.

## Work Log

Full detailed log in `WORKLOG.md` (2200+ lines, last entry 13/11 21:00 CET).

## Build

```bash
cd work/mgs_rev2/build
/Users/fury/mgsvenv/bin/ninja ../obj/<path>.asm
```

## Verification

```bash
# Instruction-level comparison
python cmp_func.py orig_<sym>.s mgs_rev2/obj/<path>.asm <sym> mgs_rev2/build/functions.txt

# Differential emulation
python mipsemu.py orig_<sym>.s mgs_rev2/obj/<path>.asm <sym> --trials 300

# Selftest (run before trusting emulator)
python mipsemu.py --selftest
```
