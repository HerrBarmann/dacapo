#!/usr/bin/env bash
# ============================================================================
# build-iso-docker.sh — builds the kiosk installer ISO inside a debian:trixie
# container. Needs only Docker (macOS with colima/Docker Desktop or Linux), NO
# Debian machine and no root: xorriso works without loop mounts, the isolinux
# files are architecture independent (an amd64 ISO is produced on Apple
# Silicon as well).
#
#   ./build-iso-docker.sh all [site-dir]                  # ONE ISO with boot menu
#   ./build-iso-docker.sh <profile> [site-dir]            # dedicated auto-install stick
#
# The netinst ISO is downloaded once into preseed/cache/ and reused
# (DEBIAN_VERSION below or via environment when cdimage.debian.org has a new
# point release). Result: preseed/kiosk-<profile>-installer.iso
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"
PROFILE="${1:-all}"
SITE="${2:-${KIOSK_SITE_DIR:-}}"
DEBIAN_VERSION="${DEBIAN_VERSION:-13.6.0}"
IMAGE="${IMAGE:-debian:trixie}"
ISO="debian-${DEBIAN_VERSION}-amd64-netinst.iso"
CORE="$(cd .. && pwd)"
if [ -z "$SITE" ]; then
    if [ -f "$CORE/../site/site.yml" ]; then SITE="$CORE/../site"; else SITE="$CORE/examples/site-demo"; fi
fi
SITE="$(cd "$SITE" && pwd)"

command -v docker >/dev/null || { echo "ERROR: docker not found (macOS: brew install colima docker && colima start)." >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "ERROR: Docker is not running (macOS: colima start)." >&2; exit 1; }

mkdir -p cache
if [ ! -f "cache/$ISO" ]; then
    echo ">> Downloading netinst ISO $ISO (once, ~700 MB) ..."
    curl -fL --progress-bar -o "cache/$ISO.part" \
        "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/$ISO" \
     || curl -fL --progress-bar -o "cache/$ISO.part" \
        "https://cdimage.debian.org/cdimage/archive/${DEBIAN_VERSION}/amd64/iso-cd/$ISO" \
     || { echo "ERROR: netinst ISO not found — check the version number:" >&2
          echo "       https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/" >&2
          echo "       and run DEBIAN_VERSION=13.x.y $0 $PROFILE again." >&2; exit 1; }
    mv "cache/$ISO.part" "cache/$ISO"
fi
echo ">> Checksum of the netinst ISO against cdimage.debian.org ..."
if curl -fsSL "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/SHA256SUMS" 2>/dev/null \
     | grep " $ISO\$" > cache/SHA256SUMS.tmp 2>/dev/null && [ -s cache/SHA256SUMS.tmp ]; then
    ( cd cache && shasum -a 256 -c SHA256SUMS.tmp ) || { echo "ERROR: checksum mismatch — delete cache/$ISO and download again." >&2; exit 1; }
else
    echo "   (no checksum for $ISO under current/ — older point release, check skipped)"
fi
rm -f cache/SHA256SUMS.tmp

echo ">> Building '$PROFILE' from site $SITE in container $IMAGE ..."
docker run --rm -i \
    -v "$CORE:/core:ro" \
    -v "$SITE:/site:ro" \
    -v "$(pwd)/cache:/cache:ro" \
    -v "$(pwd):/out" \
    -e PROFILE="$PROFILE" -e ISO="$ISO" \
    "$IMAGE" bash -s <<'INNER'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends xorriso isolinux cpio gzip >/dev/null
# core is mounted read-only: work on a copy (build-iso.sh writes into preseed/)
cp -r /core /work && cd /work/preseed
./build-iso.sh "$PROFILE" "/cache/$ISO" /site
cp "kiosk-${PROFILE}-installer.iso" /out/
chown "$(stat -c %u:%g /out)" "/out/kiosk-${PROFILE}-installer.iso" 2>/dev/null || true
INNER

echo ""
echo "Done: $(pwd)/kiosk-${PROFILE}-installer.iso"
echo "Write to a stick (macOS; /dev/diskN = the stick, check with: diskutil list):"
echo "  diskutil unmountDisk /dev/diskN && sudo dd if=kiosk-${PROFILE}-installer.iso of=/dev/rdiskN bs=4m status=progress && diskutil eject /dev/diskN"
