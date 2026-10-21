#!/usr/bin/env bash
# ============================================================================
# docker-test.sh — runs local.yml for ONE profile of a site in a fresh
# debian:trixie container (like the first run inside the installer chroot:
# in_chroot=true, no running systemd) and checks the result.
#
#   tests/docker-test.sh browser                      # demo site
#   tests/docker-test.sh app   /path/to/site          # your site
#   SKIP_PACKAGES=1 tests/docker-test.sh display      # no package installs (fast)
#
# Needs Docker. With packages: several minutes (sway, firefox-esr ...).
# The expectations are derived from the profile's switches, so the script
# works for any site.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")/.."
PROFILE="${1:-browser}"
SITE="${2:-examples/site-demo}"
SITE="$(cd "$SITE" && pwd)"
IMAGE="${IMAGE:-debian:trixie}"
SKIP="${SKIP_PACKAGES:-0}"
[ -f "$SITE/profiles/$PROFILE.yml" ] || { echo "ERROR: $SITE/profiles/$PROFILE.yml not found" >&2; exit 1; }

docker run --rm -i -v "$PWD:/repo:ro" -v "$SITE:/site:ro" -e PROFILE="$PROFILE" -e SKIP_PACKAGES="$SKIP" "$IMAGE" bash -s <<'INNER'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
echo ">> Test environment: installing ansible + systemd (for systemctl enable) ..."
apt-get update -qq
apt-get install -y -qq --no-install-recommends ansible systemd python3 ca-certificates git fontconfig >/dev/null   # fontconfig: fc-cache handler even without package tasks
# admin user as created by the installer
useradd -m -s /bin/bash kioskadmin
cp -r /repo /work && cp -r /site /work-site && cd /work
EXTRA=""
if [ "$SKIP_PACKAGES" = "1" ]; then
    # skip package tasks: replace the apt module with a dummy
    mkdir -p /work/library
    cat > /work/library/apt.py <<'PY'
from ansible.module_utils.basic import AnsibleModule
m = AnsibleModule(argument_spec=dict(name=dict(type='list'), state=dict(), update_cache=dict(type='bool')))
m.exit_json(changed=False, msg="apt skipped (SKIP_PACKAGES=1)")
PY
    EXTRA="-e ansible_library=/work/library"
    sed -i 's/ansible.builtin.apt:/apt:/' roles/*/tasks/*.yml
fi
echo ">> ansible-playbook local.yml (profile=$PROFILE, in_chroot=true) ..."
ansible-playbook -i localhost, -c local local.yml -e kiosk_site_dir=/work-site -e profile="$PROFILE" -e in_chroot=true $EXTRA
echo ">> Second run must be idempotent (changed=0) ..."
ansible-playbook -i localhost, -c local local.yml -e kiosk_site_dir=/work-site -e profile="$PROFILE" -e in_chroot=true $EXTRA | tee /tmp/run2.log | tail -n 3
grep -E 'changed=0 ' /tmp/run2.log || { echo "ERROR: second run not idempotent"; exit 1; }

# ---- expectations from the profile and site -----------------------------------
# Flat "key: value" lookups (profile overrides site overrides core default).
pget() {   # <key> <default>
    local v
    v=$(grep -hE "^$1:" /work-site/profiles/"$PROFILE".yml /work-site/site.yml /work/vars/defaults.yml 2>/dev/null | head -n1 | sed -E 's/^[^:]+:[[:space:]]*//; s/[[:space:]]+#.*$//; s/^"(.*)"$/\1/; s/^'"'"'(.*)'"'"'$/\1/')
    echo "${v:-$2}"
}
yes_() { [ "$1" = true ] || [ "$1" = True ] || [ "$1" = yes ]; }
USB=$(pget usb_enabled false); WAYBAR=$(pget waybar_enabled true); IDLE=$(pget idle_reset_enabled true)
SAVER=$(pget screensaver_enabled false); GUARD=$(pget fullscreen_guard_enabled false); MODE=$(pget firefox_mode browser)
CURSOR=$(pget hide_cursor false); MON=$(pget monitor_enabled false); WLAN=$(pget wlan_fallback_enabled false)
STATE_DIR=$(pget kiosk_state_dir /etc/kiosk); KUSER=$(pget kiosk_user kiosk); KHOME=$(pget kiosk_home /home/kiosk); ADMIN=$(pget admin_user kioskadmin)
N_EXT=$(awk '/^firefox_extensions:/{f=1;next} f&&/^[^ -]/{f=0} f&&/^  - name:/{n++} END{print n+0}' /work-site/profiles/"$PROFILE".yml)
echo ">> Expectations: usb=$USB waybar=$WAYBAR idle=$IDLE screensaver=$SAVER guard=$GUARD mode=$MODE cursor=$CURSOR monitor=$MON wlan=$WLAN extensions=$N_EXT"

echo ">> Checking the result ..."
fail=0
chk() { if eval "$2"; then echo "   OK   $1"; else echo "   FAIL $1"; fail=1; fi; }
chk "profile marker"            "[ \"\$(cat $STATE_DIR/profile)\" = \"$PROFILE\" ]"
chk "policies.json valid"       "python3 -c 'import json;json.load(open(\"/etc/firefox-esr/policies/policies.json\"))'"
chk "ExtensionSettings is an object" "python3 -c 'import json;d=json.load(open(\"/etc/firefox-esr/policies/policies.json\"));assert isinstance(d[\"policies\"][\"ExtensionSettings\"],dict)'"
chk "sway config present"       "[ -s /etc/sway/config ]"
chk "kiosk-firefox.sh executable" "[ -x /usr/local/bin/kiosk-firefox.sh ] && bash -n /usr/local/bin/kiosk-firefox.sh"
chk "kiosk-reset.sh executable"  "[ -x /usr/local/bin/kiosk-reset.sh ] && bash -n /usr/local/bin/kiosk-reset.sh"
chk "home page present"         "[ -s /opt/kiosk/homepage/index.html ]"
chk "assets deployed"           "ls /opt/kiosk/homepage/assets/* >/dev/null"
chk "timers enabled"            "[ -L /etc/systemd/system/timers.target.wants/ansible-pull.timer ] && [ -L /etc/systemd/system/timers.target.wants/nightly-reboot.timer ]"
chk "ansible-pull unit (0600)"  "[ \"\$(stat -c %a /etc/systemd/system/ansible-pull.service)\" = 600 ] && grep -q 'ansible-pull' /etc/systemd/system/ansible-pull.service"
chk "fstab tmpfs"               "grep -q '$KHOME.*tmpfs' /etc/fstab"
chk "autologin"                 "grep -q 'autologin $KUSER' /etc/systemd/system/getty@tty1.service.d/override.conf"
if [ -f /work-site/files/authorized_keys ]; then
chk "admin keys"                "[ -s /home/$ADMIN/.ssh/authorized_keys ]"; fi
if yes_ "$WLAN"; then
chk "Wi-Fi (ifupdown)"          "[ -s /etc/wpa_supplicant/kiosk-wlan.conf ] && [ -s /etc/network/interfaces.d/wlan0 ]"; fi
if yes_ "$USB"; then
chk "USB manager + config"      "[ -x /opt/kiosk/usbmanager.py ] && python3 -m py_compile /opt/kiosk/usbmanager.py && python3 -c 'import json;json.load(open(\"/opt/kiosk/usbmanager.json\"))'"
chk "USB manager in sway"       "grep -q 'usbmanager.py' /etc/sway/config"
else
chk "no USB manager"            "[ ! -e /opt/kiosk/usbmanager.py ] && ! grep -q usbmanager /etc/sway/config"; fi
if [ "$N_EXT" -gt 0 ]; then
chk "$N_EXT extension(s) downloaded" "[ \$(ls /opt/kiosk/*.xpi 2>/dev/null | wc -l) -eq $N_EXT ] && for f in /opt/kiosk/*.xpi; do python3 -c \"import zipfile,sys;zipfile.ZipFile(sys.argv[1]).testzip()\" \$f; done"
chk "extensions in policy"      "grep -q 'force_installed' /etc/firefox-esr/policies/policies.json"
else
chk "no extensions"             "! ls /opt/kiosk/*.xpi >/dev/null 2>&1"; fi
if yes_ "$SAVER"; then
chk "screensaver"               "[ -x /usr/local/bin/kiosk-screensaver.sh ] && bash -n /usr/local/bin/kiosk-screensaver.sh && ls /opt/kiosk/wallpaper/*/* >/dev/null"
else
chk "no screensaver"            "[ ! -e /usr/local/bin/kiosk-screensaver.sh ] && [ ! -e /opt/kiosk/wallpaper ]"; fi
if yes_ "$WAYBAR"; then
chk "waybar config"             "python3 -c 'import json;json.load(open(\"/etc/xdg/waybar/config\"))' && [ -s /etc/xdg/waybar/style.css ] && grep -q '^exec waybar' /etc/sway/config"
else
chk "no waybar in sway"         "! grep -q '^exec waybar' /etc/sway/config"; fi
if yes_ "$IDLE"; then
chk "idle popup (mako)"         "[ -x /usr/local/bin/kiosk-idle-warn.sh ] && bash -n /usr/local/bin/kiosk-idle-warn.sh && [ -s /etc/xdg/mako/config ] && grep -q 'exec mako' /etc/sway/config && grep -q swayidle /etc/sway/config"
else
chk "no idle popup"             "[ ! -e /usr/local/bin/kiosk-idle-warn.sh ] && [ ! -e /etc/xdg/mako/config ] && ! grep -q 'kiosk-idle-warn' /etc/sway/config"; fi
if yes_ "$GUARD"; then
chk "fullscreen watchdog"       "[ -x /usr/local/bin/kiosk-fullscreen-watch.sh ] && grep -q 'fullscreen disable' /etc/sway/config"
else
chk "no fullscreen watchdog"    "[ ! -e /usr/local/bin/kiosk-fullscreen-watch.sh ]"; fi
case "$MODE" in
  display) chk "--kiosk in wrapper"  "grep -q -- '--kiosk' /usr/local/bin/kiosk-firefox.sh"
           chk "restart loop"        "grep -q 'while true' /etc/sway/config" ;;
  app)     chk "userChrome in wrapper" "grep -q 'userChrome.css' /usr/local/bin/kiosk-firefox.sh"
           chk "hide-chrome pref locked" "grep -q 'toolkit.legacyUserProfileCustomizations.stylesheets' /etc/firefox-esr/policies/policies.json" ;;
  browser) chk "private window, no userChrome" "grep -q -- '--private-window' /usr/local/bin/kiosk-firefox.sh && ! grep -q 'chrome/userChrome.css' /usr/local/bin/kiosk-firefox.sh" ;;
esac
if yes_ "$CURSOR"; then chk "pointer hidden" "grep -q 'hide_cursor' /etc/sway/config"; fi
if yes_ "$MON"; then
chk "kiosk-monitor.py"          "[ -x /opt/kiosk/kiosk-monitor.py ] && python3 -m py_compile /opt/kiosk/kiosk-monitor.py"
chk "kiosk-monitor.service"     "[ -L /etc/systemd/system/multi-user.target.wants/kiosk-monitor.service ] && grep -q MONITOR_URL /etc/systemd/system/kiosk-monitor.service"
else
chk "no kiosk-monitor"          "[ ! -e /etc/systemd/system/kiosk-monitor.service ]"; fi
if [ "$SKIP_PACKAGES" != "1" ]; then
    chk "firefox-esr installed" "command -v firefox-esr >/dev/null"
    chk "sway installed"        "command -v sway >/dev/null"
    chk "waybar installed"      "command -v waybar >/dev/null"
fi
echo ">> Files under /etc/sway /etc/xdg/waybar /opt/kiosk /usr/local/bin:"
find /etc/sway /etc/xdg/waybar /opt/kiosk /usr/local/bin -maxdepth 1 -type f 2>/dev/null | sort | sed 's/^/   /'
[ $fail -eq 0 ] && echo "=== docker-test $PROFILE: OK ===" || { echo "=== docker-test $PROFILE: FAILED ==="; exit 1; }
INNER
