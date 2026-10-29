#!/bin/sh
# Does the candidate .c reproduce the original instruction for instruction?
#
# Installs the candidate, builds, runs cmp_func.py, then puts the repo back.
# obj/ has to hold the *variant*, and the pristine rebuild is what tryfn.sh
# leaves behind, so this has to do its own install/restore cycle.
#
# usage: match.sh <candidate.c> <source/rel/path.c> <obj/rel/path.asm> \
#                 <orig_dump.s> <symbol>
set -u

ROOT="/Users/fury/Desktop/Progetti OpenCode/Porting/work"
REPO="$ROOT/mgs_rev2"
SCRATCH="${TMPDIR:-/tmp}/opencode"
NINJA=/Users/fury/mgsvenv/bin/ninja
OBJ_DIR="${MGS_OBJ_DIR:-obj}"

die() { echo "match: $*" >&2; exit 1; }

[ -f "$1" ] || die "candidate $1 missing"
[ -f "$4" ] || die "original dump $4 missing"
mkdir -p "$SCRATCH" || die "cannot create $SCRATCH"

CAND=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
SRCPATH="$2"
OBJREL="$3"
ORIG="$4"
SYM="$5"

case "$OBJREL" in
obj/*) [ "$OBJ_DIR" = obj ] || OBJREL="$OBJ_DIR/${OBJREL#obj/}" ;;
esac

DEST="$REPO/source/$SRCPATH"
[ -f "$DEST" ] || die "missing $DEST"

SAVE=$(mktemp "$SCRATCH/match.XXXXXX") || die "mktemp failed"
cp "$DEST" "$SAVE" || die "cannot back up $DEST"

restore() {
	cp "$SAVE" "$DEST" 2>/dev/null || echo "match: FAILED to restore $DEST" >&2
	rm -f "$SAVE"
	cd "$REPO/build" 2>/dev/null && "$NINJA" "../$OBJREL" >/dev/null 2>&1
}
trap restore EXIT INT TERM

cp "$CAND" "$DEST" || die "cannot install candidate"

cd "$REPO/build" || die "no build dir"
if ! "$NINJA" "../$OBJREL" >"$SCRATCH/match.log" 2>&1; then
	echo "BUILD FAIL"
	grep -iE "error" "$SCRATCH/match.log" | head -4
	exit 1
fi

cd "$ROOT" || exit 1
# cmp_func marks every differing line with `*`; keep those plus the counts
# and the verdict, so a clean run is two lines of output.
"$ROOT/.venv/bin/python" "$ROOT/cmp_func.py" "$ROOT/$ORIG" \
	"$REPO/$OBJREL" "$SYM" | grep -E "instructions|^\*|^(MATCH|NODIFF|[0-9]+ diff)"