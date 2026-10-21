# Operations

## What runs when

| What | When | Variable |
|---|---|---|
| configuration pull | 3 min after boot, then every 30 min (+0–5 min random) | `pull_boot_delay`, `pull_interval`, `pull_random_delay` |
| security updates (OS + Firefox) | package lists 02:30, upgrades 03:00 (+15 min random) | `apt_update_lists_time`, `apt_upgrade_time` |
| nightly reboot | 04:00 local time, waits for a running upgrade | `nightly_reboot_time` |
| session reset | after `idle_seconds` idle, popup `idle_warn_seconds` before | profile |
| screensaver / screen off | after `screensaver_seconds` / `screen_off_seconds` | profile |
| display refresh | every `monitor_refresh_minutes`, page polls locally every 30 s | profile |

Rule of thumb: what is pushed before 04:00 is active on every device the next
morning. Check with `systemctl list-timers`.

## Changing things

Everything goes through Git. Never edit a device by hand — the next pull
overwrites it (intended: no drift). Exceptions: the per-device override files.

| Change | Where | Active |
|---|---|---|
| bookmarks, allowed URLs, policies | `site/profiles/<p>.yml` | next session after the pull |
| texts, colours, fonts | `site/site.yml` (bar: immediately after the pull, waybar reloads) | pull |
| home pages | `site/templates/` | next session |
| extensions | `firefox_extensions` in the profile (bump `version` to re-download) | next boot (profile template) |
| display URL / rotation of one device | `host.yml` / `kiosk.yml` on the device | pull (+ reboot for rotation) |
| admin keys | `site/files/authorized_keys` | pull |
| deploy token | `site/secrets.yml`; rebuild ISO and firstrun.sh | pull (the unit is rewritten) |
| screensaver images | `site/wallpaper/<WxH>/` (removed images disappear on the devices) | pull |
| timers | `site/site.yml` | pull |

Force a pull: `sudo systemctl start ansible-pull.service`. Test a branch on
one device: edit `--checkout` in `/etc/systemd/system/ansible-pull.service`
(the next pull from `main` reverts it) or set `kiosk_pull_branch` in a
per-device override.

## Testing a change before pushing

```bash
ansible-playbook --syntax-check -i localhost, core/local.yml
ansible-lint core/local.yml core/roles/ core/vars/
ansible-playbook -i localhost, core/tests/render.yml -e site=site -e profile=browser   # -> core/tests/out/site/browser/
core/tests/docker-test.sh browser site        # full run in debian:trixie, twice (idempotence)
```

## Troubleshooting

| Symptom | Cause | Check / fix |
|---|---|---|
| installation stops at late_command | token/URL wrong, no network, CA missing | installer console 4; `/var/log/kiosk-bootstrap.log` |
| device behaves like the wrong profile | marker wrong | `cat /etc/kiosk/profile`; fix, pull, reboot |
| text console instead of Firefox | autologin/sway broken | `journalctl -b -u getty@tty1`; `/etc/profile.d/zz-kiosk-sway.sh` |
| bookmarks missing / policies inactive | JSON error | browser mode: `about:policies`; `python3 -c 'import json;json.load(open("/etc/firefox-esr/policies/policies.json"))'` |
| address bar visible (app) | userChrome.css missing | `pgrep -a firefox-esr` must show `--profile /home/kiosk/.mozilla/firefox/kiosk`; `ls …/kiosk/chrome/` |
| Firefox covers the bar (app) | watchdog not running | `sudo -u kiosk pgrep -f fullscreen-watch`; `waybar_layer: overlay`? |
| login provider blocked (app) | host missing in WebsiteFilter | add to `Exceptions` |
| no popup before the reset | mako not running | `sudo -u kiosk pgrep -x mako`; `journalctl -t kiosk-idle-warn -b` |
| new session slow (white screen) | profile template rebuilt every time | `journalctl -t kiosk-firefox -b`: only the first start after boot may report "profile template created" |
| extension welcome page every session | first run not saved | set `verify_firstrun: true` for that extension |
| display shows "Connecting…" forever | service cannot reach the server | `curl -s http://127.0.0.1:8081/status.json`; `journalctl -u kiosk-monitor -n 20`; check `monitor_url` and `monitor_expect` |
| display content old (timestamp bottom right) | network/server down > 30 min | intended; check the server |
| display upside down | `output_transform` | `host.yml` / `kiosk.yml`, pull, reboot |
| Pi bootstrap hangs | no DNS, token, CA | `/boot/firmware/kiosk-bootstrap.log`; `sudo systemctl start kiosk-bootstrap` |
| data survives a reboot | tmpfs mount missing | `mount \| grep /home/kiosk`; `/etc/fstab` |
| ansible-pull fails | token expired, URL wrong, CA | `journalctl -u ansible-pull.service` |
| Firefox outdated | unattended-upgrades inactive | `systemctl status apt-daily-upgrade.timer`; `/etc/apt/apt.conf.d/20auto-upgrades` |

Useful commands:
```bash
cat /etc/kiosk/profile                   # which profile?
journalctl -u ansible-pull.service       # convergence
sudo -u kiosk swaymsg -t get_tree        # is sway/Firefox running?
hostnamectl                              # hostname <prefix>-<mac>?
swapon --show                            # must be empty
```

## Security notes

- The kiosk user has no password and no sudo; the browser is locked down by
  policy (no about:config, no add-ons by users, `file://` only where needed).
- Downloaded files land on a `noexec` tmpfs.
- SSH: key login only, no root. Admin keys are a single list in the site.
- The deploy token should be read-only and project-scoped; rotate it by
  changing `secrets.yml` and rebuilding the installer media.
- Optional hardening: forbid VT switching with `fbcon=vc:1-1` in
  `GRUB_CMDLINE_LINUX` (`update-grub`); the admin then only gets in via SSH.
- Network segmentation, firewall and backups of the Git server are outside
  the scope of this toolkit.

## Developer notes

- **Chroot safety**: the first run happens in the Debian installer without a
  running systemd. Services and timers are only `systemctl enable`d (symlinks)
  and started when `in_chroot` is false. Never add an unconditional
  `state: started`.
- Templates are rendered on the device; keep site templates free of secrets
  (they end up world-readable under `/opt/kiosk`).
- `tests/compare-render.sh` compares two renderings while ignoring comments;
  use it when refactoring templates or migrating a fleet.
