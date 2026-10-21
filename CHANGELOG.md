# Changelog

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
