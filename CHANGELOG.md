# Changelog

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
