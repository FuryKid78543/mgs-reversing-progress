#!/bin/sh
# Recompile one translation unit and diff one function against its original dump.
# usage: try.sh <relative .c path> <function symbol> [<original .s>]
set -e
ROOT="/Users/fury/Desktop/Progetti OpenCode/Porting/work"
REPO="$ROOT/mgs_reversing"
CFILE="$1"
SYMBOL="$2"
ORIG="${3:-}"

cd "$REPO/build"
/Users/fury/mgsvenv/bin/python build.py >/tmp/build_one.log 2>&1 || true
# build.py always runs a full ninja pass; ask ninja for just the one .asm instead
/Users/fury/mgsvenv/bin/ninja "../${CFILE%.c}.asm" 2>&1 | tail -3

ASM="$REPO/obj/${CFILE%.c}.asm"
if [ ! -f "$ASM" ]; then
    echo "no generated asm at $ASM"
    exit 1
fi

if [ -z "$ORIG" ]; then
    # recover the original dump from git history: the .s was deleted in our branch
    BASE=$(git -C "$REPO" rev-list --max-parents=0 HEAD 2>/dev/null | head -1)
    ORIG=$(git -C "$REPO" show "master:asm/overlays/${SYMBOL%_*}/$SYMBOL.s" 2>/dev/null || true)
    if [ -z "$ORIG" ]; then
        ORIG=$(git -C "$REPO" show "origin/master:$(cd "$REPO" && git show --name-only --format= HEAD~1 2>/dev/null | grep "$SYMBOL.s$" | head -1)" 2>/dev/null || true)
    fi
    if [ ! -f "$ORIG" ]; then
        echo "could not locate original dump for $SYMBOL; pass it as argument 3"
        exit 1
    fi
fi

exec "$ROOT/.venv/bin/python" "$ROOT/cmp_func.py" "$ORIG" "$ASM" "$SYMBOL"