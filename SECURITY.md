# Security

## Reporting a vulnerability

Please report security issues privately via GitHub's "Report a vulnerability"
(Security → Advisories) on the repository, not as a public issue. You will
get an answer within a few days.

## Scope and model

Da capo locks down a Debian machine for unattended public use:

- the session user has no password and no sudo; the browser is restricted by
  Firefox enterprise policies (no about:config, no add-on installs, file://
  only where needed, permanent private browsing);
- `/home/kiosk` is a tmpfs with `noexec,nosuid,nodev`; there is no swap;
- the device is reset after idle time and rebooted nightly;
- SSH is key-only, root login disabled;
- the local USB page and the display service bind to 127.0.0.1 and verify
  token, Host and Origin.

Out of scope: physical access (BIOS lock and USB boot are the operator's job,
see docs/deployment.md), the network, the Git server, and anything in a site
repository.

## Secrets

The deploy token is written to `/etc/systemd/system/ansible-pull.service`
(mode 0600) on every device. Use a read-only, project-scoped token and rotate
it by changing `site/secrets.yml` and rebuilding the installer media. Never
commit a site's `secrets.yml` to a public repository.
