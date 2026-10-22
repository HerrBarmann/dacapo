# Fleet reporting

The optional role `fleet` connects every device to a management server: the
device takes its **name and profile** from the server, **reports its state**
every few minutes and **runs actions** an admin requested there (pull the
configuration now, install updates now, reboot). The configuration itself
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
| `GET /clients/<HOSTNAME>.json`, headers `X-Client-Token`, `X-Client-Serial`, `X-Client-Macs` | – | `200 {"actions": [{"type", "token"}, …]}`; `403` if the secret does not match |
| `POST /report`, header `X-Client-Token` | see below | `200`; `403` if serial/MAC or secret do not match |

Report body: `hostname`, `serial`, `macs`, `model`, `os_version`, `os_build`,
`ip`, `uptime_hours`, `agent_version`, `last_run`, `install_date`,
`kind: "kiosk"`, `actions_done` (tokens), `clients_fetched`, `errors` and the
block `kiosk`:

| Field | Meaning |
|---|---|
| `pull_last`, `pull_result`, `pull_commit`, `pull_branch` | last run of `ansible-pull.service` (systemd result) and the checked-out commit |
| `last_boot`, `nightly_reboot` | last boot, scheduled reboot time |
| `updates_pending`, `updates_security` | packages waiting according to `apt-get -s upgrade`, of which security updates |
| `disk_size_gb`, `disk_free_gb`, `load` | root file system, 1-minute load |
| `firefox_version`, `core_version` | Firefox ESR, `dacapo_version` |
| `monitor_url`, `monitor_ok` | display profiles: the local display service delivers a page |

Unknown action types are acknowledged and ignored, so a server can add new
ones without breaking older devices.
