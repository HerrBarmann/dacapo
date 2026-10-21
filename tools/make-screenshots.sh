#!/usr/bin/env bash
# ============================================================================
# make-screenshots.sh — renders the documentation screenshots (docs/images/)
# from the demo site: bar, home pages, idle popup, USB page, signage display.
#
#   tools/make-screenshots.sh [site-dir]        (default: examples/site-demo)
#
# Needs Docker and ansible on the host. The profiles are rendered with
# tests/render.yml, then a container with Chromium and the free fonts composes
# full 1920x1080 kiosk screens (tools/screenshots/compose.py) and screenshots
# them. Fully reproducible; the clock in the mockups is fixed.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
SITE="${1:-examples/site-demo}"
SITE="$(cd "$SITE" && pwd)"
command -v docker >/dev/null || { echo "ERROR: docker not found" >&2; exit 1; }

echo ">> Rendering profiles of $SITE ..."
for p in "$SITE"/profiles/*.yml; do
    ansible-playbook -i localhost, tests/render.yml -e site="$SITE" -e profile="$(basename "$p" .yml)" >/dev/null
done
OUT_SITE="tests/out/$(basename "$SITE")"

echo ">> Building renderer image ..."
docker build -q -t dacapo-screenshots tools/screenshots >/dev/null

echo ">> Composing and screenshotting ..."
mkdir -p docs/images tests/out/mockups
docker run --rm \
    -v "$PWD:/work:ro" \
    -v "$SITE:/site:ro" \
    -v "$PWD/tests/out/mockups:/mockups" \
    -v "$PWD/docs/images:/out" \
    dacapo-screenshots "/work/$OUT_SITE" /site /mockups /out
ls -la docs/images/*.png
