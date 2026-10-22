#!/usr/bin/env bash
# ============================================================================
# idle-chain-test.sh — runs the REAL idle chain of one profile in a live,
# headless sway session (debian:trixie container) and checks that it works:
#
#   idle warning (countdown popup) -> input during the countdown ("resume")
#   -> session reset -> screensaver
#
# with the files the device would get (rendered by tests/render.yml), only the
# timeouts shortened: warning 10 s, reset 20 s, screensaver 30 s.
#
#   tests/idle-chain-test.sh browser                 # demo site
#   tests/idle-chain-test.sh app /path/to/site       # your site
#
# Why this exists: tests/docker-test.sh only checks the deployed FILES. In
# Oct. 2026 a "pkill -f kiosk-idle-warn.sh" in the reset and resume commands
# matched swayidle's own command line and killed the idle watchdog — every
# file was correct, but after the first reset there was no reset, no
# screensaver and no screen-off until the next reboot. Only a running session
# shows that. Profiles without idle reset are skipped (exit 0).
#
# Needs Docker, ~2 minutes (installs sway, swayidle, mako in the container).
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
CORE="$PWD"
PROFILE="${1:-browser}"
SITE="${2:-examples/site-demo}"
SITE="$(cd "$SITE" && pwd)"
IMAGE="${IMAGE:-debian:trixie}"
[ -f "$SITE/profiles/$PROFILE.yml" ] || { echo "ERROR: $SITE/profiles/$PROFILE.yml not found" >&2; exit 1; }

sv() { tools/site-var.sh "$SITE" "profile:$PROFILE" "$@"; }
if [ "$(sv idle_reset_enabled true)" != true ]; then
    echo "SKIP: profile $PROFILE has no idle reset (idle_reset_enabled: false)"
    exit 0
fi
SAVER="$(sv screensaver_enabled false)"
KUSER="$(sv kiosk_user kiosk)"

echo ">> Rendering profile $PROFILE of $SITE ..."
ansible-playbook -i localhost, tests/render.yml -e site="$SITE" -e profile="$PROFILE" >/dev/null
OUT="$CORE/tests/out/$(basename "$SITE")/$PROFILE"

# Work directory under the checkout: Docker on macOS (colima) shares $HOME only.
WORK="$CORE/tests/out/idle-chain/$(basename "$SITE")-$PROFILE"
rm -rf "$WORK"; mkdir -p "$WORK/wallpaper"
cp "$OUT/etc-sway-config" "$OUT/kiosk-idle-warn.sh" "$OUT/kiosk-reset.sh" "$OUT/etc-xdg-mako-config" "$WORK/"
[ -f "$OUT/kiosk-screensaver.sh" ] && cp "$OUT/kiosk-screensaver.sh" "$WORK/"
if [ "$SAVER" = true ]; then   # two images per resolution are enough
    for d in "$SITE"/wallpaper/*/; do
        [ -d "$d" ] || continue
        mkdir -p "$WORK/wallpaper/$(basename "$d")"
        find "$d" -maxdepth 1 -type f \( -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' \) | sort | head -n 2 \
            | while read -r f; do cp "$f" "$WORK/wallpaper/$(basename "$d")/"; done
    done
fi

# Shortened timeouts, matched by the command each timeout runs (not by value).
# Everything that needs real hardware or Firefox is dropped from the session;
# the idle watchdog, mako and the Firefox start command stay.
sed -E \
    -e "s#timeout [0-9]+ ('/usr/local/bin/kiosk-idle-warn.sh &')#timeout 10 \\1#" \
    -e "s#timeout [0-9]+ ('/usr/local/bin/kiosk-reset.sh')#timeout 20 \\1#" \
    -e "s#timeout [0-9]+ ('/usr/local/bin/kiosk-screensaver.sh start')#timeout 30 \\1#" \
    -e "s#timeout [0-9]+ ('swaymsg \"output \\* power off\"')#timeout 600 \\1#" \
    -e '/^exec (waybar|udiskie|python3 |\/usr\/local\/bin\/kiosk-fullscreen-watch)/d' \
    "$WORK/etc-sway-config" > "$WORK/sway-config"
grep -q "timeout 20 '/usr/local/bin/kiosk-reset.sh'" "$WORK/sway-config" \
    || { echo "ERROR: could not find the reset timeout in the rendered sway config" >&2; exit 1; }
# The command swayidle runs on input after the warning (first resume line).
RESUME=$(grep -E "^[[:space:]]+resume '" "$WORK/sway-config" | head -n1 | sed -E "s/^[[:space:]]+resume '(.*)'[[:space:]]*\\\\?[[:space:]]*$/\\1/")
[ -n "$RESUME" ] || { echo "ERROR: no resume command in the rendered sway config" >&2; exit 1; }
printf '%s\n' "$RESUME" > "$WORK/resume.cmd"
echo ">> Resume command under test: $RESUME"

docker run --rm -i -v "$WORK:/in:ro" -e KUSER="$KUSER" -e SAVER="$SAVER" "$IMAGE" bash -s <<'INNER'
set -u
export DEBIAN_FRONTEND=noninteractive
echo ">> Installing sway, swayidle, mako ..."
apt-get update -qq
apt-get install -y -qq --no-install-recommends sway swayidle mako-notifier libnotify-bin python3 \
    dbus procps fonts-dejavu >/dev/null 2>&1 || { echo "ERROR: package installation failed"; exit 1; }

useradd -m -u 1001 "$KUSER"
RUN=/run/user/1001
mkdir -p "$RUN" /opt/kiosk/homepage/assets /opt/kiosk/wallpaper /etc/xdg/mako
chown "$KUSER" "$RUN"; chmod 700 "$RUN"
cp -r /in/wallpaper/. /opt/kiosk/wallpaper/
cp /in/etc-xdg-mako-config /etc/xdg/mako/config
install -m 755 /in/kiosk-idle-warn.sh /in/kiosk-reset.sh /usr/local/bin/
[ -f /in/kiosk-screensaver.sh ] && install -m 755 /in/kiosk-screensaver.sh /usr/local/bin/
# Firefox stand-in: a binary whose process name is "firefox-esr", so the
# reset's pkill/pgrep -x firefox-esr really find it.
cp /bin/sleep /usr/local/bin/firefox-esr
cat > /usr/local/bin/kiosk-firefox.sh <<'F'
#!/bin/bash
echo "$(date +%T) firefox started" >> /tmp/events.log
exec /usr/local/bin/firefox-esr infinity
F
chmod 755 /usr/local/bin/kiosk-firefox.sh
cat > /usr/local/bin/logger <<'F'
#!/bin/bash
shift 2 2>/dev/null; echo "$(date +%T) log: $*" >> /tmp/events.log
F
chmod 755 /usr/local/bin/logger
: > /tmp/events.log; chmod 666 /tmp/events.log
cp /in/sway-config /tmp/sway-config; chmod 644 /tmp/sway-config

as_kiosk() { su "$KUSER" -s /bin/bash -c "export XDG_RUNTIME_DIR=$RUN; $1"; }
alive()    { pgrep -u "$KUSER" -x swayidle >/dev/null; }
ff_count() { pgrep -u "$KUSER" -x firefox-esr | wc -l; }
focused()  { SWAYSOCK=$(ls $RUN/sway-ipc.*.sock 2>/dev/null | head -n1) as_kiosk "swaymsg -t get_workspaces -r" \
                 | python3 -c 'import json,sys; print(next((w["name"] for w in json.load(sys.stdin) if w["focused"]), "?"))' 2>/dev/null; }
fail=0
check() { if eval "$2"; then echo "   OK   $1"; else echo "   FAIL $1"; fail=1; fi; }

echo ">> Starting a headless sway session (warning 10 s, reset 20 s, screensaver 30 s) ..."
T0=$(date +%s)
as_kiosk "WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 LANG=C.UTF-8 dbus-run-session -- sway -c /tmp/sway-config > /tmp/sway.log 2>&1" &
at() { local w=$(( T0 + $1 - $(date +%s) )); [ "$w" -gt 0 ] && sleep "$w"; }

at 6
check "session up: swayidle and mako running, Firefox started" "alive && pgrep -u $KUSER -x mako >/dev/null && [ \$(ff_count) -eq 1 ]"

at 13
check "warning: countdown running (PID file present)" "[ -s $RUN/kiosk-idle-warn.pid ] || pgrep -u $KUSER -x kiosk-idle-warn >/dev/null"
echo ">> Input during the countdown: running the resume command ..."
as_kiosk "sh -c '$(cat /in/resume.cmd)'"
sleep 2
check "resume: swayidle still running" "alive"
check "resume: countdown ended" "! pgrep -u $KUSER -x kiosk-idle-warn >/dev/null"

at 25
check "reset: swayidle still running" "alive"
check "reset: Firefox restarted (2 starts, 1 running)" "[ \$(grep -c 'firefox started' /tmp/events.log) -ge 2 ] && [ \$(ff_count) -eq 1 ]"

if [ "$SAVER" = true ]; then
    at 36
    check "screensaver: started (log) and own workspace focused" "grep -q 'log: started' /tmp/events.log && [ \"\$(focused)\" != 1 ]"
    check "screensaver: swayidle still running" "alive"
fi

echo ">> Events:"; sed 's/^/   /' /tmp/events.log
if [ $fail -ne 0 ]; then
    echo ">> sway log (errors):"; grep -iE 'error|fail' /tmp/sway.log | grep -viE 'xwayland|d-bus|dbus|logo|icon' | head -10 | sed 's/^/   /'
    echo "=== idle-chain-test: FAILED ==="; exit 1
fi
echo "=== idle-chain-test: OK ==="
INNER
