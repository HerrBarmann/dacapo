# Changelog

## 0.3.2 — unreleased

- fleet: after a successful report the reporter sends the files the screen
  is built from (bar configuration and style, start page with its assets,
  Firefox policies, Firefox mode) to `POST /fleet/preview`, only when their
  hash changed. A management server can rebuild a picture of the screen from
  them, like `tools/screenshots/compose.py`. A server without the endpoint
  (404) is fine; the reporter tries again when the files change.
- `dacapo_version` 0.3.2

## 0.3.1 — unreleased

- fleet: `fleet_enabled: false` now switches the reporter off on the devices:
  the role stops and removes `kiosk-fleet.timer`, the service, the
  configuration and the server's settings file. Before, the role was just
  skipped and the timer kept running. The device secret stays, so switching
  back on needs no new admission. The role therefore runs on every device.
- `dacapo_version` 0.3.1 (matches the reporter version)
- bar: the host name is re-read every 5 minutes instead of once, so a device
  renamed by the management server shows its new name without a restart

## 0.3.0 — unreleased

- fleet: a pull going on right now is reported as `pull_running` (start
  time); `pull_last`/`pull_result` keep the last finished run instead of
  disappearing while systemd clears the end time.

- fleet: the server may set variables listed in `fleet_settable_vars`
  (bookmarks, display URL, rotation …). The reporter writes them to
  `fleet_vars_file`, loaded after the profile and before `host.yml`, and runs
  `ansible-pull` when they changed. Values with Jinja braces or outside the
  list are refused and leave the file untouched. Values in `host.yml` equal to
  the server's are removed there (backup `*.before-fleet`). Reports `settings_hash` and
  `local_overrides`.

## 0.2.0 — unreleased

- new role `fleet` (optional, `fleet_enabled`): the device reports to a
  Partitur server, takes its name and profile from there and runs the
  actions requested there (pull now, install updates now, reboot). Script
  `kiosk-fleet.py`, timer every `fleet_report_interval`; credentials in
  `secrets.yml` (`fleet_enroll_key`, `fleet_repo_user`, `fleet_repo_password`);
  the role stays idle while one of them is empty. `dacapo_version` in
  `vars/defaults.yml`. Self-adoption of unknown devices and re-admission
  after a reinstall via the enrolment key. HTTP contract in `docs/fleet.md`,
  so any server speaking it can manage a fleet.

## 0.1.1 — 2026-10-22

- **fix:** the idle reset and input during the countdown killed `swayidle`
  itself (`pkill -f kiosk-idle-warn.sh` matched swayidle's own command line).
  After the first reset a device had no further reset, no screensaver and no
  screen-off until the next reboot. `kiosk-idle-warn.sh stop` now ends exactly
  the countdown process via a PID file; sway resume and `kiosk-reset.sh` use it.
  Devices need a reboot (or a new session) to get the fixed swayidle line.
- new `tests/idle-chain-test.sh`: runs warning, input, reset and screensaver
  in a live headless sway session; part of CI
- build-iso: the placeholder check ignores comment lines
- base: enable `non-free-firmware` before installing firmware on x86_64

## 0.1.0 — 2026-10-21

First public release, extracted from the kiosk fleet of the Hochschule für
Musik und Theater Hamburg (library computers, booking terminals, event
displays).

- one `ansible-pull` playbook for every profile; site directory found next to
  the core or given explicitly
- roles `base`, `session`, `firefox`, `usbmanager`, `monitor`
- three Firefox modes: `browser`, `app`, `display`
- every brand element and text as a variable (`vars/defaults.yml`)
- extensions downloaded at pull time from a URL list, policy generated
- demo site with three profiles, free fonts and generated assets
- preseed ISO build (one ISO with boot menu or dedicated sticks), Docker wrapper
- Raspberry Pi two-stage bootstrap
- render test, container test, render comparison, GitHub Actions workflow
- English documentation and screenshots
