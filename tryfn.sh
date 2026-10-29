#!/bin/sh
# Swap in one candidate .c file, build just that object, and report the
# instruction-level verdict against the original dump. The repo file is always
# restored, so a bad variant costs nothing.
#
# usage: tryfn.sh <candidate.c> <source/rel/path.c> <obj/rel/path.asm> \
#                 <orig_dump.s> <symbol>
set -u

ROOT="/Users/fury/Desktop/Progetti OpenCode/Porting/work"
REPO="$ROOT/mgs_rev2"
SCRATCH="${TMPDIR:-/tmp}/opencode"
OBJ_DIR="${MGS_OBJ_DIR:-obj}"

die() { echo "tryfn: $*" >&2; exit 1; }

# Everything that touches the repo is checked up front. The first version of
# this script ended with `rm -f "$DEST"` for the case where the backup could
# not be taken, and that silently deleted source/overlays/rank/menu/pocket.c
# once the scratch directory had disappeared. Fail loudly instead.
[ -f "$1" ] || die "candidate $1 does not exist"
[ -f "$4" ] || die "original dump $4 does not exist"
mkdir -p "$SCRATCH" || die "cannot create scratch dir $SCRATCH"

CAND=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
SRCPATH="$2"
OBJREL="$3"
# Callers keep writing obj/... paths; point them at whatever MGS_OBJ_DIR says so
# the invocation does not have to change when the build moves.
case "$OBJREL" in
obj/*) [ "$OBJ_DIR" = "obj" ] || OBJREL="$OBJ_DIR/${OBJREL#obj/}" ;;
esac
OBJ="$REPO/$OBJREL"
case "$4" in /*) ORIG="$4" ;; *) ORIG="$(pwd)/$4" ;; esac
SYM="$5"

DEST="$REPO/source/$SRCPATH"
[ -f "$DEST" ] || die "refusing to touch missing $DEST"

SAVE=$(mktemp "$SCRATCH/cand.XXXXXX") || die "mktemp failed"
cp "$DEST" "$SAVE" || die "could not back up $DEST"
cmp -s "$DEST" "$SAVE" || die "backup of $DEST does not match"

restore() {
	cp "$SAVE" "$DEST" || echo "tryfn: FAILED to restore $DEST" >&2
	rm -f "$SAVE"
	# Rebuild after restoring, so obj/ always mirrors the repo. Otherwise the
	# next checkall.py run reads the candidate's assembly and reports a
	# difference that no longer exists in the source.
	cd "$REPO/build" 2>/dev/null &&
		MGS_OBJ_DIR="$OBJ_DIR" /Users/fury/mgsvenv/bin/ninja "../$OBJREL" \
			>/dev/null 2>&1
}
trap restore EXIT INT TERM

cp "$CAND" "$DEST" || die "could not install candidate"

cd "$REPO/build" || die "no build dir"
if MGS_OBJ_DIR="$OBJ_DIR" /Users/fury/mgsvenv/bin/ninja "../$OBJREL" >"$SCRATCH/tryfn.log" 2>&1; then
	"$ROOT/.venv/bin/python" "$ROOT/cmp_func.py" "$ORIG" \
		"$OBJ" "$SYM" "$REPO/build/functions.txt" |
		grep -E "^\*|MATCH|NOPDIFF|differing|instructions"
else
	echo "BUILD FAIL"
	grep -E "rror" "$SCRATCH/tryfn.log" | head -4
fi