#!/usr/bin/env bash
# ============================================================================
# site-var.sh — read a flat scalar from a site (shell helper for the installer
# scripts, which cannot evaluate Ansible variables).
#
#   tools/site-var.sh <site-dir> <variable> [default]
#   tools/site-var.sh <site-dir> profile:<name> <variable> [default]
#
# Looks in <site>/profiles/<name>.yml (second form), then secrets.yml, then
# site.yml, then dacapo/vars/defaults.yml. Only "key: value" lines are
# understood (quoted or bare); the installer only needs such scalars.
# ============================================================================
set -euo pipefail
SITE="${1:?site dir}"; shift
CORE="$(cd "$(dirname "$0")/.." && pwd)"
FILES=()
if [[ "${1:-}" == profile:* ]]; then
    FILES+=("$SITE/profiles/${1#profile:}.yml"); shift
fi
KEY="${1:?variable}"; DEFAULT="${2-}"
FILES+=("$SITE/secrets.yml" "$SITE/site.yml" "$CORE/vars/defaults.yml")
for f in "${FILES[@]}"; do
    [ -f "$f" ] || continue
    if line=$(grep -E "^${KEY}:" "$f" | head -n1) && [ -n "$line" ]; then
        val=$(printf '%s' "$line" | sed -E 's/^[^:]+:[[:space:]]*//; s/[[:space:]]+#.*$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/')
        printf '%s\n' "$val"
        exit 0
    fi
done
[ -n "$DEFAULT" ] && { printf '%s\n' "$DEFAULT"; exit 0; }
echo "site-var: '$KEY' not found in ${FILES[*]}" >&2
exit 1
