#!/bin/sh
# Give cmp_func.py and mipsemu.py an original dump for every function that is
# still a pragma.
#
# The repo keeps the original assembly under asm/; those files are removed as
# each function gets decompiled, so a symlink going dangling is the signal that
# a function is done. Copies would go stale instead.
set -u

ROOT="/Users/fury/Desktop/Progetti OpenCode/Porting/work"
REPO="$ROOT/mgs_rev2"

cd "$ROOT" || exit 1

made=0
missing=0
grep -rhoE 'INCLUDE_ASM\("asm/[^"]+\.s"\)' mgs_rev2/source/ 2>/dev/null |
	sed -E 's/.*asm\///; s/"\)$//' | sort -u | while read -r rel; do
	base=$(basename "$rel" .s)
	src="$REPO/asm/$rel"
	dst="$ROOT/orig_$base.s"
	if [ -L "$dst" ]; then
		[ -e "$dst" ] && continue          # still fine
		rm -f "$dst"                        # function has been decompiled
		continue
	fi
	[ -f "$dst" ] && continue             # a real copy, leave it alone
	if [ -f "$src" ]; then
		ln -sf "$src" "$dst"
		echo "collegato $base"
	else
		echo "ASSENTE  $rel"
	fi
done | sort | uniq -c | sed 's/^ *//'

n=$(find "$ROOT" -maxdepth 1 -name "orig_*.s" | wc -l | tr -d ' ')
echo "dump disponibili in work/: $n"