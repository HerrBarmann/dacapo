# Fleet reporting

The optional role `fleet` connects every device to a management server: the
device takes its **name and profile** from the server, **reports its state**
every few minutes and **runs actions** an admin requested there (pull the
configuration now, install updates now, reboot). It also sends the files its
screen is built from, so the server can show a **preview** of the screen. The configuration itself
still comes from the site repository via `ansible-pull`; the server only
names, watches and nudges the devices.

The reference server is Partitur, the computer management of the Hochschule
für Musik und Theater Hamburg (to be published by the same author). Any server
that speaks the small HTTP contract below works as well.

## Switching it on

In `site.yml`:

```yaml
fleet_enabled: true
fleet_url: "https://manage.example.org"   # no path
fleet_report_interval: 15min
```

In `secrets.yml`:

```yaml
fleet_enroll_key: "…"      # enrolment key issued by the server
fleet_repo_user: "…"       # HTTP basic auth for all fleet endpoints
fleet_repo_password: "…"
```

While one of the four values is empty the role only prints a warning and
leaves the device alone, so a half-filled site never breaks the pull of a
fleet. Keep the profiles' `hostname_prefix` equal to the server's naming
scheme (lower case): the server renames devices to `<prefix>-NNN`, and the
playbook recognises the profile by that prefix.

## Settings from the server

The server may also set variables on the devices, for example the bookmarks
of a library profile or the page a signage display shows. Which variables it
may set is the site's decision:

```yaml
fleet_settable_vars:
  - firefox_managed_bookmarks
  - monitor_url
  - output_transform
```

The server sends the merged values with the actions (`settings`, plus a short
`settings_hash`). The script writes them to `fleet_vars_file`
(`<kiosk_state_dir>/fleet-vars.json`) and starts `ansible-pull` when they
changed. The playbook loads that file after the profile and before the
per-device overrides (`kiosk_host_vars_files`), so the order is: core
defaults, `site.yml`, profile, server, `host.yml`. A local `host.yml` stays an
emergency switch on the device.

Safety: a value for a variable outside `fleet_settable_vars`, or any value
containing Jinja braces (`{{`, `{%`, `{#`), is refused. Ansible would template
such a value, so a server could otherwise run commands on the device. If
anything is refused, the file is left as it was and the device reports
`partial:<hash>`, so the server sees the settings as not arrived.

Taking over local values: the device reports which settable variables its
`host.yml` sets (`local_overrides`). When the server holds exactly the same
value for one of them, the script removes it from `host.yml` (once, with a
backup `host.yml.before-fleet` next to it); from then on the server manages
it.

## What a run does

`/opt/kiosk/kiosk-fleet.py`, started by `kiosk-fleet.timer` one minute after
it is set up, two minutes after boot and then every `fleet_report_interval`:

1. Identity: serial number (DMI, or the device tree on a Raspberry Pi), MAC
   addresses of the physical interfaces, model.
2. Name: `GET /fleet/hostname`. Known device → set the hostname
   (`hostnamectl`, `/etc/hosts`) and write the profile marker; the next
   `ansible-pull` converges the device to that profile.
3. Unknown device: `POST /fleet/enroll` with the enrolment key. The server
   either creates the device at once ("self-adoption") and answers with its
   name, or puts it on a waiting list for an admin.
4. Actions: `GET /clients/<HOSTNAME>.json`; run `pull-now`
   (`systemctl start ansible-pull.service`) and `update-now`
   (`apt-get update && apt-get upgrade`); remember `reboot` for the end.
5. Report: `POST /report` with the state and the tokens of the finished
   actions; then reboot if requested.
6. Preview: if the screen files changed since the last accepted upload,
   `POST /fleet/preview` (see below). Failures here never fail the run.

Each device creates its own random secret on first contact
(`/var/lib/kiosk-fleet/token`, 0600) and sends it as `X-Client-Token`; the
server keeps only a hash. After a reinstall the server rejects the new secret
once; the script registers with the enrolment key again, and the server
accepts the new secret if serial number or MAC match. Logs:
`journalctl -u kiosk-fleet`; run now: `sudo systemctl start kiosk-fleet.service`.

## HTTP contract

All endpoints use HTTP basic auth (`fleet_repo_user`, `fleet_repo_password`)
over HTTPS. Host names travel in upper case; the device uses lower case.

| Request | Body / parameters | Answer |
|---|---|---|
| `GET /fleet/hostname?serial=…&mac=…&mac=…` | serial number and MAC addresses | `200 {"hostname", "profile"}` for a known device, else `404` |
| `POST /fleet/enroll`, header `X-Enroll-Key` | `{"serial", "macs", "model", "profile", "old_hostname"}` | `200 {"state": "approved", "hostname", "profile"}` (created or re-admitted) or `{"state": "waiting", "code", "url"}`; `403` for a wrong key |
| `GET /clients/<HOSTNAME>.json`, headers `X-Client-Token`, `X-Client-Serial`, `X-Client-Macs` | – | `200 {"actions": [{"type", "token"}, …], "settings": {variable: value}, "settings_hash"}`; `403` if the secret does not match |
| `POST /report`, header `X-Client-Token` | see below | `200`; `403` if serial/MAC or secret do not match |
| `POST /fleet/preview`, header `X-Client-Token` | `{"hostname", "serial", "macs", "hash", "files": {"waybar_config", "waybar_style", "homepage", "policies"}, "assets": {name: base64}, "meta": {"firefox_mode", "waybar_enabled"}}` | `200`; `403` if the secret does not match; `404` from servers without previews; after `200`, `400`, `404` or `413` the reporter sends again only when the files change |

Report body: `hostname`, `serial`, `macs`, `model`, `os_version`, `os_build`,
`ip`, `uptime_hours`, `agent_version`, `last_run`, `install_date`,
`kind: "kiosk"`, `actions_done` (tokens), `clients_fetched`, `errors` and the
block `kiosk`:

| Field | Meaning |
|---|---|
| `pull_last`, `pull_result`, `pull_commit`, `pull_branch` | last finished run of `ansible-pull.service` (systemd result) and the checked-out commit |
| `pull_running` | start time of a run going on right now (absent otherwise) |
| `last_boot`, `nightly_reboot` | last boot, scheduled reboot time |
| `updates_pending`, `updates_security` | packages waiting according to `apt-get -s upgrade`, of which security updates |
| `disk_size_gb`, `disk_free_gb`, `load` | root file system, 1-minute load |
| `firefox_version`, `core_version` | Firefox ESR, `dacapo_version` |
| `monitor_url`, `monitor_ok` | display profiles: the local display service delivers a page |
| `settings_hash` | hash of the settings in `fleet_vars_file` (`partial:…` if some were refused) |
| `local_overrides` | settable variables that a per-device override file sets |

Unknown action types are acknowledged and ignored, so a server can add new
ones without breaking older devices.
