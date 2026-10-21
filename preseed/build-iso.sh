#!/usr/bin/env bash
# ============================================================================
# build-iso.sh — builds a kiosk installer ISO from the official Debian netinst
# ISO for a site:
#   - preseed.cfg (placeholders filled from the site) is embedded into the
#     installer initrd (d-i loads it automatically)
#   - the profile arrives as kernel parameter kiosk.profile=<name> from the
#     boot menu (BIOS/isolinux + UEFI/grub)
#
# Run on a Debian/Ubuntu machine (not macOS) — or use build-iso-docker.sh:
#   sudo apt install xorriso isolinux
#   ./build-iso.sh all     debian-13.x.y-amd64-netinst.iso [site-dir]   # ONE ISO,
#                                                           # menu with all profiles,
#                                                           # waits for a choice
#   ./build-iso.sh browser debian-13.x.y-amd64-netinst.iso [site-dir]   # dedicated
#                                                           # stick: starts after 5 s
#
# Result: kiosk-<all|profile>-installer.iso — onto the stick:
#   sudo dd if=kiosk-all-installer.iso of=/dev/sdX bs=4M status=progress oflag=sync
#
# Pull URL (incl. deploy token), branch, playbook path, locale, admin
# password hash and the optional CA come from the site (site.yml,
# secrets.yml, files/ca.crt); profiles and their titles from profiles/*.yml.
#
# WARNING: a computer booted from this stick is wiped and installed WITHOUT
# confirmation.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"
CORE="$(cd .. && pwd)"

PROFILE="${1:?usage: $0 <all|profile> <debian-netinst.iso> [site-dir]}"
ISO_IN="${2:?usage: $0 <all|profile> <debian-netinst.iso> [site-dir]}"
SITE="${3:-${KIOSK_SITE_DIR:-}}"
if [ -z "$SITE" ]; then
    if [ -f "$CORE/../site/site.yml" ]; then SITE="$CORE/../site"; else SITE="$CORE/examples/site-demo"; fi
fi
SITE="$(cd "$SITE" && pwd)"
sv() { "$CORE/tools/site-var.sh" "$SITE" "$@"; }

ALL_PROFILES="$(for f in "$SITE"/profiles/*.yml; do [ -f "$f" ] && basename "$f" .yml; done | tr '\n' ' ' | sed 's/ $//')"
[ -n "$ALL_PROFILES" ] || { echo "ERROR: no profiles in $SITE/profiles/" >&2; exit 1; }
if [ "$PROFILE" != all ] && [ ! -f "$SITE/profiles/$PROFILE.yml" ]; then
    echo "ERROR: unknown profile '$PROFILE' (all, $ALL_PROFILES)." >&2; exit 1
fi
ISO_OUT="kiosk-${PROFILE}-installer.iso"
WORK="$(mktemp -d)"
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

PULL_URL="$(sv kiosk_pull_url)"
BRANCH="$(sv kiosk_pull_branch main)"
PLAYBOOK="$(sv kiosk_pull_playbook core/local.yml)"
LOCALE="$(sv install_locale en_US.UTF-8)"
KEYMAP="$(sv install_keymap us)"
TIMEZONE="$(sv install_timezone UTC)"
ADMIN_USER="$(sv admin_user kioskadmin)"
ADMIN_HASH="$(sv install_admin_password_hash CHANGE_ME)"
STATE_DIR="$(sv kiosk_state_dir /etc/kiosk)"
PROFILE_FILE="$(sv kiosk_profile_file "$STATE_DIR/profile")"
PROFILE_FILE="${PROFILE_FILE//\{\{ kiosk_state_dir \}\}/$STATE_DIR}"
CA_NAME="$(sv kiosk_ca_name kiosk-site)"
DEFAULT_PROFILE="$PROFILE"; [ "$PROFILE" = all ] && DEFAULT_PROFILE="$(sv kiosk_default_profile "${ALL_PROFILES%% *}")"
case "$PULL_URL$ADMIN_HASH" in *CHANGE_ME*) echo "ERROR: kiosk_pull_url or install_admin_password_hash still contains CHANGE_ME ($SITE/secrets.yml)." >&2; exit 1 ;; esac
case "$ADMIN_HASH" in '$6$'*|'$y$'*|'$5$'*) ;; *) echo "ERROR: install_admin_password_hash is not a crypt hash (\$6\$...)." >&2; exit 1 ;; esac

# hostname prefix per profile for the late_command case statement
PREFIX_CASES=""
for p in $ALL_PROFILES; do
    pre="$(sv "profile:$p" hostname_prefix "$p")"; pre="${pre//\{\{ kiosk_profile \}\}/$p}"
    PREFIX_CASES+="$p) P=$pre;; "
done

CA="$SITE/files/ca.crt"
if [ -f "$CA" ]; then
    grep -q "BEGIN CERTIFICATE" "$CA" || { echo "ERROR: $CA is not a PEM certificate." >&2; exit 1; }
    CA_COMMANDS="mkdir -p /target/usr/local/share/ca-certificates; cp /site-ca.crt /target/usr/local/share/ca-certificates/$CA_NAME.crt; in-target update-ca-certificates;"
else
    CA_COMMANDS="true;"
fi

echo ">> ISO '$PROFILE' from site $SITE (branch $BRANCH, pull URL $(printf '%s' "$PULL_URL" | sed -E 's#//[^@]*@#//***@#'))"
sed -e "s|@@PROFILE@@|$DEFAULT_PROFILE|g" \
    -e "s|@@BRANCH@@|$BRANCH|g" \
    -e "s|@@PULL_URL@@|$PULL_URL|g" \
    -e "s|@@PLAYBOOK@@|$PLAYBOOK|g" \
    -e "s|@@LOCALE@@|$LOCALE|g" \
    -e "s|@@KEYMAP@@|$KEYMAP|g" \
    -e "s|@@TIMEZONE@@|$TIMEZONE|g" \
    -e "s|@@ADMIN_USER@@|$ADMIN_USER|g" \
    -e "s|@@ADMIN_PASSWORD_HASH@@|$(printf '%s' "$ADMIN_HASH" | sed 's/[&|\\]/\\&/g')|g" \
    -e "s|@@STATE_DIR@@|$STATE_DIR|g" \
    -e "s|@@PROFILE_FILE@@|$PROFILE_FILE|g" \
    -e "s|@@PREFIX_CASES@@|$PREFIX_CASES|g" \
    -e "s|@@CA_COMMANDS@@|$CA_COMMANDS|g" \
    preseed.cfg > "$WORK/preseed.cfg"
if grep -q "CHANGE_ME\|@@" "$WORK/preseed.cfg"; then
    echo "ERROR: preseed.cfg still contains placeholders:" >&2
    grep -n "CHANGE_ME\|@@" "$WORK/preseed.cfg" >&2
    exit 1
fi

echo ">> Extracting ISO ..."
xorriso -osirrox on -indev "$ISO_IN" -extract / "$WORK/iso"
chmod -R u+w "$WORK/iso"

echo ">> Embedding preseed.cfg${CA:+ + site CA} into the initrd ..."
gunzip "$WORK/iso/install.amd/initrd.gz"
FILES="preseed.cfg"
if [ -f "$CA" ]; then cp "$CA" "$WORK/site-ca.crt"; FILES="$FILES site-ca.crt"; fi
( cd "$WORK" && printf '%s\n' $FILES | cpio -H newc -o -A -F iso/install.amd/initrd )
gzip "$WORK/iso/install.amd/initrd"

echo ">> Writing boot menus ..."
KARGS="auto=true priority=critical vga=788"
label_for() { sv "profile:$1" kiosk_profile_title "$1" | sed 's/[{}]//g'; }
if [ "$PROFILE" = all ]; then
    MENU_PROFILES="$ALL_PROFILES"; ISOLINUX_TIMEOUT=0; GRUB_TIMEOUT=-1   # waits for a choice
else
    MENU_PROFILES="$PROFILE";      ISOLINUX_TIMEOUT=50; GRUB_TIMEOUT=5   # 5 s, then go
fi
# BIOS (isolinux)
{
    first=1
    for p in $MENU_PROFILES; do
        [ $first = 1 ] && { echo "default install-$p"; first=0; }
        printf 'label install-%s\n\tmenu label ^Install %s (%s) - WIPES THE DISK\n\tkernel /install.amd/vmlinuz\n\tappend %s kiosk.profile=%s initrd=/install.amd/initrd.gz ---\n' \
            "$p" "$(label_for "$p")" "$p" "$KARGS" "$p"
    done
} > "$WORK/iso/isolinux/txt.cfg"
sed -i "s/^timeout .*/timeout $ISOLINUX_TIMEOUT/" "$WORK/iso/isolinux/isolinux.cfg"
# UEFI (grub)
{
    echo "set default=0"
    echo "set timeout=$GRUB_TIMEOUT"
    for p in $MENU_PROFILES; do
        printf 'menuentry "Install %s (%s) - WIPES THE DISK" {\n    linux /install.amd/vmlinuz %s kiosk.profile=%s ---\n    initrd /install.amd/initrd.gz\n}\n' \
            "$(label_for "$p")" "$p" "$KARGS" "$p"
    done
} > "$WORK/iso/boot/grub/grub.cfg"

echo ">> Building hybrid ISO (BIOS + UEFI) ..."
xorriso -as mkisofs -r -V "KIOSK_$(echo "$PROFILE" | tr a-z A-Z | tr -c 'A-Z0-9\n' _)" \
    -o "$ISO_OUT" \
    -J -joliet-long \
    -b isolinux/isolinux.bin -c isolinux/boot.cat \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
    -eltorito-alt-boot -e boot/grub/efi.img -no-emul-boot \
    -isohybrid-mbr /usr/lib/ISOLINUX/isohdpfx.bin \
    -isohybrid-gpt-basdat \
    "$WORK/iso"

echo ">> Self-check: is the current state really inside the ISO? ..."
rm -f "$WORK/verify-initrd.gz"
xorriso -osirrox on -indev "$ISO_OUT" -extract /install.amd/initrd.gz "$WORK/verify-initrd.gz" 2>&1 | grep -iE 'failure|error' || true
[ -s "$WORK/verify-initrd.gz" ] || { echo "ERROR: initrd.gz could not be read from the ISO." >&2; exit 1; }
# Decompress first, then search: "zcat | grep -q" would break the pipe on the
# first hit and kill zcat with SIGPIPE (pipefail -> false alarm).
zcat "$WORK/verify-initrd.gz" > "$WORK/verify-initrd"
if [ -f "$CA" ]; then
    CA_NEEDLE="$(grep -v '^#' "$CA" | sed -n '2p')"
    grep -aqF -- "$CA_NEEDLE" "$WORK/verify-initrd" || { echo "ERROR: site CA missing in the initrd of the built ISO!" >&2; exit 1; }
fi
grep -aqF -- "echo \"\$V\" > $PROFILE_FILE" "$WORK/verify-initrd" || { echo "ERROR: preseed.cfg in the ISO does not write the profile marker (old state?)!" >&2; exit 1; }
rm -f "$WORK/verify-initrd"
for p in $MENU_PROFILES; do
    rm -f "$WORK/verify-grub.cfg"; xorriso -osirrox on -indev "$ISO_OUT" -extract /boot/grub/grub.cfg "$WORK/verify-grub.cfg" 2>/dev/null
    grep -q "kiosk.profile=$p" "$WORK/verify-grub.cfg" || { echo "ERROR: boot menu without entry for $p!" >&2; exit 1; }
done
echo "   OK: preseed, profile bootstrap and boot menu ($MENU_PROFILES) are in the ISO."

echo ""
echo "Done: $(pwd)/$ISO_OUT"
echo "Write to a stick:  sudo dd if=$ISO_OUT of=/dev/sdX bs=4M status=progress oflag=sync"
