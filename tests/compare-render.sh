#!/usr/bin/env bash
# ============================================================================
# compare-render.sh — compares two directories of rendered kiosk files
# (e.g. a legacy deployment vs. the same site rendered through dacapo).
#
#   tests/compare-render.sh <expected-dir> <actual-dir> [rename-map]
#
# Rules:
#   - comment lines (#, <!-- ... -->), blank lines and trailing whitespace are
#     ignored in text files; translating comments is never a difference
#   - *.json files are compared structurally
#   - an optional rename map (one "old<TAB>new" per line) is applied to the
#     EXPECTED side first: documented, intentional renames (e.g. a waybar
#     module id) count as identical
#   - every remaining difference is written to <actual-dir>/../diff-<file>.txt
#     and reported; the exit code is 1 if any file differs
# ============================================================================
set -euo pipefail
EXPECTED="${1:?expected dir}"; ACTUAL="${2:?actual dir}"; MAP="${3:-}"
DIFFDIR="$(dirname "$ACTUAL")/diff-$(basename "$ACTUAL")"
rm -rf "$DIFFDIR"; mkdir -p "$DIFFDIR"
rc=0; n_ok=0; n_diff=0; n_missing=0

norm() {   # strip comments/blank lines/trailing whitespace; apply rename map
    local f="$1" apply_map="$2"
    local body
    body=$(grep -vE '^[[:space:]]*#' "$f" | grep -vE '^[[:space:]]*<!--.*-->[[:space:]]*$' \
           | sed -E '/^[[:space:]]*<!--/,/-->[[:space:]]*$/d' \
           | grep -vE '^[[:space:]]*/\*.*\*/[[:space:]]*$' | sed -E '/^[[:space:]]*\/\*/,/\*\/[[:space:]]*$/d' \
           | grep -vE '^[[:space:]]*$' | sed -E 's/[[:space:]]+$//')
    if [ "$apply_map" = 1 ] && [ -n "$MAP" ] && [ -f "$MAP" ]; then
        while IFS=$'\t' read -r old new; do
            [ -n "$old" ] || continue
            body=${body//"$old"/"$new"}
        done < "$MAP"
    fi
    printf '%s\n' "$body"
}

json_cmp() {
    python3 - "$1" "$2" "$MAP" <<'PY'
import json, sys
a, b, mapfile = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(a, encoding="utf-8").read()
if mapfile:
    for line in open(mapfile, encoding="utf-8"):
        if "\t" in line:
            old, new = line.rstrip("\n").split("\t", 1)
            text = text.replace(old, new)
x, y = json.loads(text), json.load(open(b, encoding="utf-8"))
def walk(p, u, v, out):
    if isinstance(u, dict) and isinstance(v, dict):
        for k in sorted(set(u) | set(v)):
            if k not in u: out.append(f"{p}/{k}: only in actual = {json.dumps(v[k], ensure_ascii=False)}")
            elif k not in v: out.append(f"{p}/{k}: only in expected = {json.dumps(u[k], ensure_ascii=False)}")
            else: walk(f"{p}/{k}", u[k], v[k], out)
    elif u != v:
        out.append(f"{p}: expected={json.dumps(u, ensure_ascii=False)} actual={json.dumps(v, ensure_ascii=False)}")
out = []
walk("", x, y, out)
print("\n".join(out))
sys.exit(1 if out else 0)
PY
}

for f in $(cd "$EXPECTED" && ls); do
    if [ ! -f "$ACTUAL/$f" ]; then echo "MISSING $f (not rendered)"; n_missing=$((n_missing+1)); rc=1; continue; fi
    case "$f" in
        *.json)
            if out=$(json_cmp "$EXPECTED/$f" "$ACTUAL/$f"); then echo "OK      $f  (JSON identical)"; n_ok=$((n_ok+1))
            else echo "DIFF    $f  -> $DIFFDIR/diff-$f.txt"; printf '%s\n' "$out" > "$DIFFDIR/diff-$f.txt"; n_diff=$((n_diff+1)); rc=1; fi ;;
        *)
            if diff -u <(norm "$EXPECTED/$f" 1) <(norm "$ACTUAL/$f" 0) > "$DIFFDIR/diff-$f.txt"; then
                echo "OK      $f  (identical without comments)"; rm -f "$DIFFDIR/diff-$f.txt"; n_ok=$((n_ok+1))
            else echo "DIFF    $f  -> $DIFFDIR/diff-$f.txt"; n_diff=$((n_diff+1)); rc=1; fi ;;
    esac
done
echo "--- $n_ok identical, $n_diff different, $n_missing missing"
exit $rc
