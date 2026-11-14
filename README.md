# 🎮 MGS Reversing — Decompilation Progress

> Reverse-engineering **Metal Gear Solid (1998, PSX)**.  
> Goal: decompile every remaining overlay function from
> [`FoxdieTeam/mgs_reversing`](https://github.com/FoxdieTeam/mgs_reversing)
> into C, verified instruction-by-instruction with `cc1psx` (PSYQ 4.4).

---

## 📊 Progress

```
[███████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░]  23.7%
```

| Status | Count | Description |
|:---|---:|:---|
| ✅ **MATCH** | 52+ | Exact instruction-level match with `cc1psx` (my verified) |
| ⚠️ **NOPDIFF** | 24 | Decompiled, but original had `nop` in delay slots (MATCH impossible) |
| 🔶 **DIFF** | 34 | Decompiled, schedule/codegen differences only (semantically equivalent) |
| 📝 **C total** | **177** | Functions with a C implementation (incl. upstream merge) |
| 🔧 **TODO** | **570** | Still in assembly (`#pragma INCLUDE_ASM`) |
| **Total** | **747** | Functions in the dump |

> After the **Nov 8 upstream merge** (FoxdieTeam `master`), 177 functions are in C.
> My own verified contributions: **52 MATCH · 24 NOPDIFF · 34 DIFF**.

---

## 🧪 Methodology

1. **Compiler verification, not eyeballing** — every candidate is compiled with
   `cc1psx` and compared instruction-by-instruction against the original dump.
   Only exact matches enable the pragma substitution.
2. **Differential emulation** — `mipsemu.py` runs both original and rebuilt dumps
   on a MIPS emulator and compares effects (`EQUIV` ≠ `MATCH`: `EQUIV` alone
   does **not** enable the pragma).
3. **Find the twin** — the winning method: locate an already-decompiled function
   with the same idiom in another overlay/module and reuse its types/patterns.
4. **Measure, don't guess** — `variants.py` / `trybody.py` enumerate plausible
   forms and measure them.
5. **Quality before volume** — no pragma substitution unless exact.

### Key findings

- **95% of candidates are blocked on data symbols**, not code. The data segment
  is laid out by declaration order, so adding a symbol shifts everything after it.
- **Register allocation** is the real obstacle: correct logic, but `cc1psx`
  allocates registers differently from Konami's compiler.
- **Addresses are per-overlay**: the same address means different things in
  different overlays. Every comparison must be scoped.
- **`volatile` on structs**: approved but unused (single justified exception:
  `s14e_800C9A68`).

---

## 🛠️ Tools

| Tool | Purpose |
|:---|:---|
| `mipsemu.py` | Differential MIPS emulator (selftest before trusting verdicts) |
| `cmp_func.py` | Instruction-level comparison (normalizes notation, symbols, macros) |
| `ready.py` | Filters candidates whose external refs are all known symbols |
| `rank_match.py` | Ranks candidates by delay-slot risk |
| `audit.py` | Runs the emulator on all already-decompiled C functions |
| `match.sh` | Installs candidate → builds → compares → restores |
| `trybody.py` / `variants.py` | Enumerate and measure plausible implementations |
| `mkelf.py` | Rebuilds an overlay dump into an ELF32 MIPS image for REA/Ghidra |

### `cmp_func.py` normalizations (extended)

- `li` with zero low half
- `jal $31,reg` → `jalr`
- `lui`+`ori` / `lui`+`addiu` fold (incl. different destination register)
- `lui`+`lw` and `lui`+`addiu`/`ori` separated by up to 4 instructions
- Calls to overlay-local symbols `<overlay>_<address>` compared by encoded address

---

## 📁 Repository Structure

```
work/
├── mgs_rev2/              # Main repo (branch: decompile-overlays)
│   ├── source/
│   │   ├── overlays/      # s14e, s07b, s08b, rank, brf, s11g, s11d, ...
│   │   ├── libdg/         # DG (display) library
│   │   ├── libhzd/        # HZD (collision) library
│   │   └── ...
│   └── build/             # ninja build system
├── mipsemu.py             # Differential emulator
├── cmp_func.py            # Instruction comparison
├── ready.py               # Candidate filter
├── rank_match.py          # Candidate ranking
├── audit.py               # Equivalence audit
├── match.sh               # Build/compare/restore
├── WORKLOG.md             # Detailed work log
└── README.md              # This file
```

---

## 🏆 Decompiled Functions (MATCH)

| Function | Overlay | Notes |
|:---|:---|:---|
| `s14e_800C5960` | s14e | Twin of `FreeModel`. Inline object array at `DG_OBJS+0x48`. |
| `s14e_800C5958` | s14e | Empty (`nop`). |
| `rank_800CD1A8` | rank | TRadioFileModeFn. |
| `rank_800CCF64` | rank | Guard: `GV_DestroyActor` inside `rank_dword_800E1878 >= 0`. |
| `rank_800D12D0` | rank | Fills two menu primitives. |
| `s14e_800CD184` | s14e | HZD_GetAddress/ReachTo + `s14e_800CD0FC`. |
| `s08b_bunsin2_800D0784` | s08b | HZD re-seat + counter. |
| `s08b_bunsin2_800CEA34` | s08b | HZD_FarZoneNavigate + GetRoute. |
| `s07b_800CBAA4` | s07b | `GV_SubVec3` + `GV_VecDir2` angle classification. |
| `s07b_800CB224` | s07b | Yaw from global SVECTOR at `0x800ABA10`. |
| `s07b_800C4FB8` | s07b | `DG_OBJ[4]` + `DG_MovePos` + `ReadRotMatrix` + `AN_SocomFlash`. |
| `s07b_800C54C0` / `54E8` | s07b | Shadow enable/disable helpers. |
| `s14e_800CA2E4` | s14e | Blood splash matrix (twin of `s07b_800C4F50`). |
| `s08b_bunsin2_800CD614` | s08b | Muzzle flash on `DG_OBJ[4]`. |
| `s08b_bunsin2_800CD770` | s08b | `RotMatrix` for floor light prim. |
| `s14e_800C82D0` | s14e | Copies `data_800AB9F4[1]` into `control.turn`. |
| `s08b_bunsin2_800CE21C` | s08b | Direction table lookup by `GV_Clock % 4`. |
| `s08b_bunsin2_800CD808` | s08b | Counter 0x800C3438, re-arms the actor. |
| `s14e_800C6F94` | s14e | `ratan2` pitch + `GV_NearExp8` smoothing. |
| `s14e_800C6AC0` | s14e | `int m; m = work->target->damaged & TARGET_DOWN; return m != 0;` |
| `rank_800D0A24/0A70/0ABC/0B08` | rank | NOPDIFF family (all EQUIV). |
| `s07b_800CB068` | s07b | NOPDIFF (message_len in delay slot). |

> Full list with instruction counts in [`WORKLOG.md`](WORKLOG.md).

---

## 🚫 Blocked Examples

| Function | Blocker |
|:---|:---|
| `s14e_800CD6C4` | Global at 0x800DD7AC in unmodeled gap. |
| `s07b_800CDA04` | 7-byte copy via lwl/lwr, needs 7-byte type. |
| `rank_800CC19C` | Missing main-exe symbols. |
| `s11d_hind_800CAFEC` | `andi 0xffff` folded by cc1psx. |
| `brf_800DDC40` | Scheduler coin flip (lui in load-delay slot). |

## 🔧 NOPDIFF (MATCH impossible)

10 functions whose original has a `nop` in a delay slot — cc1psx fills those
slots, so the streams can never be identical, but the C is semantically
equivalent (`s07b_800C4F50`, `s07b_800C5514`, `s14e_800C6028`, …).

---

## 🔨 Build & Verify

```bash
# Build
cd work/mgs_rev2/build
/Users/fury/mgsvenv/bin/ninja ../obj/<path>.asm

# Instruction-level comparison
python cmp_func.py orig_<sym>.s mgs_rev2/obj/<path>.asm <sym> mgs_rev2/build/functions.txt

# Differential emulation
python mipsemu.py orig_<sym>.s mgs_rev2/obj/<path>.asm <sym> --trials 300

# Emulator selftest (run before trusting verdicts)
python mipsemu.py --selftest
```

---

## 📜 Work Log

Full detailed log in [`WORKLOG.md`](WORKLOG.md).

## 🔗 Upstream

Base: [`FoxdieTeam/mgs_reversing`](https://github.com/FoxdieTeam/mgs_reversing)  
Progress tracker: [`FuryKid78543/mgs-reversing-progress`](https://github.com/FuryKid78543/mgs-reversing-progress)
