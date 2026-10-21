#!/usr/bin/env bash
# ============================================================================
# make-firstrun.sh — generates firstrun.sh for the SD card of a Raspberry Pi.
#
#   ./make-firstrun.sh <profile> [site-dir]
#   -> raspberry/firstrun.sh  (copy to the bootfs partition, docs/deployment.md)
#
# Fills into firstrun.sh.in: profile, pull URL + branch + playbook, state
# directory and (if the site has one) the CA certificate — all from the site.
# Runs on macOS and Linux (bash/sed/awk only).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"
CORE="$(cd .. && pwd)"
PROFILE="${1:?usage: $0 <profile> [site-dir]}"
SITE="${2:-${KIOSK_SITE_DIR:-}}"
if [ -z "$SITE" ]; then
    if [ -f "$CORE/../site/site.yml" ]; then SITE="$CORE/../site"; else SITE="$CORE/examples/site-demo"; fi
fi
SITE="$(cd "$SITE" && pwd)"
sv() { "$CORE/tools/site-var.sh" "$SITE" "$@"; }
[ -f "$SITE/profiles/$PROFILE.yml" ] || { echo "ERROR: unknown profile '$PROFILE' ($SITE/profiles/$PROFILE.yml missing)." >&2; exit 1; }

PULL_URL="$(sv kiosk_pull_url)"
BRANCH="$(sv kiosk_pull_branch main)"
PLAYBOOK="$(sv kiosk_pull_playbook core/local.yml)"
STATE_DIR="$(sv kiosk_state_dir /etc/kiosk)"
PROFILE_FILE="$(sv kiosk_profile_file "$STATE_DIR/profile")"; PROFILE_FILE="${PROFILE_FILE//\{\{ kiosk_state_dir \}\}/$STATE_DIR}"
CA_NAME="$(sv kiosk_ca_name kiosk-site)"
case "$PULL_URL" in *CHANGE_ME*) echo "ERROR: kiosk_pull_url still contains CHANGE_ME ($SITE/secrets.yml)." >&2; exit 1 ;; esac
CA="$SITE/files/ca.crt"
CA_TMP="$(mktemp)"; trap 'rm -f "$CA_TMP"' EXIT
if [ -f "$CA" ]; then
    grep -q "BEGIN CERTIFICATE" "$CA" || { echo "ERROR: $CA is not a PEM certificate." >&2; exit 1; }
    {
        echo "# Site CA — without it the clone from an internally signed Git server fails"
        echo "mkdir -p /usr/local/share/ca-certificates"
        echo "cat > /usr/local/share/ca-certificates/$CA_NAME.crt <<'CA'"
        cat "$CA"
        echo "CA"
    } > "$CA_TMP"
else
    echo "# (no site CA)" > "$CA_TMP"
fi

OUT=firstrun.sh
awk -v profile="$PROFILE" -v url="$PULL_URL" -v branch="$BRANCH" -v playbook="$PLAYBOOK" \
    -v statedir="$STATE_DIR" -v profilefile="$PROFILE_FILE" -v cafile="$CA_TMP" '
    /@@CA_BLOCK@@/ { while ((getline line < cafile) > 0) print line; close(cafile); next }
    { gsub(/@@PROFILE@@/, profile); gsub(/@@PULL_URL@@/, url); gsub(/@@BRANCH@@/, branch); gsub(/@@PLAYBOOK@@/, playbook);
      gsub(/@@STATE_DIR@@/, statedir); gsub(/@@PROFILE_FILE@@/, profilefile); print }
' firstrun.sh.in > "$OUT"
chmod 0755 "$OUT"
if grep -q "@@" "$OUT"; then echo "ERROR: placeholders not replaced:" >&2; grep -n "@@" "$OUT" >&2; exit 1; fi
bash -n "$OUT"
echo "Done: $(pwd)/$OUT  (profile $PROFILE, branch $BRANCH, site $SITE)"
echo "Copy to the bootfs partition of the SD card and extend cmdline.txt (docs/deployment.md, Raspberry Pi)."
