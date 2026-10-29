#!/bin/sh
# Does the candidate .c compute the same thing as the original dump?
#
# tryfn.sh restores the repo and rebuilds, which leaves obj/ holding the
# pristine version. This one installs the candidate, builds, runs mipsemu.py
# against that build, and then puts everything back.
#
# usage: equiv.sh <candidate.c> <source/rel/path.c> <obj/rel/path.asm> \
#                 <orig_dump.s> <symbol> [--trials N]
set -u

ROOT="/Users/fury/Desktop/Progetti OpenCode/Porting/work"
REPO="$ROOT/mgs_rev2"
SCRATCH="${TMPDIR:-/tmp}/opencode"
OBJ_DIR="${MGS_OBJ_DIR:-obj}"
NINJA=/Users/fury/mgsvenv/bin/ninja

die() { echo "equiv: $*" >&2; exit 1; }

[ -f "$1" ] || die "candidate $1 missing"
[ -f "$4" ] || die "original dump $4 missing"
mkdir -p "$SCRATCH" || die "cannot create $SCRATCH"

CAND=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
SRCPATH="$2"
OBJREL="$3"
ORIG="$4"
SYM="$5"
shift 5
EXTRA="$*"

case "$OBJREL" in
obj/*) [ "$OBJ_DIR" = obj ] || OBJREL="$OBJ_DIR/${OBJREL#obj/}" ;;
esac

DEST="$REPO/source/$SRCPATH"
[ -f "$DEST" ] || die "missing $DEST"

SAVE=$(mktemp "$SCRATCH/equiv.XXXXXX") || die "mktemp failed"
cp "$DEST" "$SAVE" || die "cannot back up $DEST"
cmp -s "$DEST" "$SAVE" || die "backup does not match"

restore() {
	cp "$SAVE" "$DEST" 2>/dev/null || echo "equiv: FAILED to restore $DEST" >&2
	rm -f "$SAVE"
	cd "$REPO/build" 2>/dev/null &&
		"$NINJA" "../$OBJREL" >/dev/null 2>&1
}
trap restore EXIT INT TERM

cp "$CAND" "$DEST" || die "cannot install candidate"

cd "$REPO/build" || die "no build dir"
if ! "$NINJA" "../$OBJREL" >"$SCRATCH/equiv.log" 2>&1; then
	echo "BUILD FAIL"
	grep -E "rror" "$SCRATCH/equiv.log" | head -4
	exit 1
fi

cd "$ROOT" || exit 1
"$ROOT/.venv/bin/python" "$ROOT/mipsemu.py" "$ORIG" "$REPO/$OBJREL" "$SYM" $EXTRA