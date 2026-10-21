# Profiles

A profile describes one kind of kiosk. It is a YAML file in
`site/profiles/<name>.yml`; the file name is the profile name. Every device
runs exactly one profile, chosen in this order:

1. `-e profile=<name>` (the installer passes it)
2. the marker file `kiosk_profile_file` (default `/etc/kiosk/profile`), written
   by the installer and rewritten on every run
3. the hostname prefix: a host `terminal-3a9f2c` runs the profile whose
   `hostname_prefix` is `terminal` (or the profile named `terminal`)
4. `kiosk_default_profile` from `site.yml`

Changing a device's profile: write the new name into the marker file, run
`sudo systemctl start ansible-pull.service`, reboot.

## The three Firefox modes

| `firefox_mode` | Firefox start | Chrome | Bar | Reset |
|---|---|---|---|---|
| `browser` | `--private-window` | address + bookmark bar visible | yes | idle + button |
| `app` | `--private-window` | hidden with `userChrome.css` (not `--kiosk`, which would cover the bar) | yes, `waybar_layer: overlay` recommended | idle + button |
| `display` | `--kiosk` in a restart loop | none | no | none (restart loop) |

## Switches

Defaults in `core/vars/defaults.yml`; everything can also be set site-wide.

| Variable | Meaning |
|---|---|
| `kiosk_profile_title` | human-readable name (boot menu, sway config header) |
| `hostname_prefix` | installer names devices `<prefix>-<MAC suffix>`; used for detection |
| `idle_reset_enabled`, `idle_seconds`, `idle_warn_seconds` | reset after inactivity; countdown popup `idle_warn_seconds` before |
| `screensaver_enabled`, `screensaver_seconds` | random image from `site/wallpaper/` on an empty workspace |
| `screen_off_enabled`, `screen_off_seconds` | DPMS off |
| `hide_cursor` | hide the pointer after 1 s (displays) |
| `output_transform` | `normal`, `90`, `180`, `270` |
| `waybar_enabled`, `waybar_layer` (`top`/`overlay`), `waybar_usb_button` | the bar |
| `text_bar_brand`, `text_bar_hint` | texts in the bar (may use `{{ idle_seconds }}` etc.) |
| `usb_enabled` | udiskie automount + local "USB drive" page on 127.0.0.1:8080 |
| `fullscreen_guard_enabled` | sway rule + IPC watchdog against F11/JS fullscreen |
| `firefox_mode`, `homepage_template`, `kiosk_homepage` | see above; the template is in `site/templates/` |
| `firefox_extensions` | list of extensions, see below |
| `firefox_policies_profile` | Firefox policies merged last (bookmarks, WebsiteFilter, handlers, preferences) |
| `monitor_enabled`, `monitor_url`, `monitor_refresh_minutes`, `monitor_port`, `monitor_expect` | display service (type `display`) |
| `kiosk_home_tmpfs_size` | tmpfs limit, e.g. `1g` on a Raspberry Pi |
| `kiosk_packages_extra` | additional Debian packages |

## Extensions

Extensions are never committed. The site lists them; the device downloads
each one once at pull time to `/opt/kiosk/<name>[-<version>].xpi` and Firefox
force-installs it from there (every session starts from a fresh profile, so a
network download per session would be pointless traffic).

```yaml
firefox_extensions:
  - name: ublock-origin
    id: "uBlock0@raymondhill.net"
    url: "https://addons.mozilla.org/firefox/downloads/latest/ublock-origin/latest.xpi"
    version: ""              # optional; part of the file name -> forces a new download
    checksum: ""             # optional "sha256:<hex>"
    private_browsing: true   # needed because of permanent private browsing
    verify_firstrun: false   # true: wait until the extension saved its first-run storage
```

The core generates the matching `ExtensionSettings` policy
(`force_installed`, `updates_disabled`) and removes XPI files that are no
longer listed. `verify_firstrun` is for extensions that otherwise show a
welcome page in every session: the profile template is only accepted when the
extension's storage exists.

## Policies

`policies.json` = core common policies (lockdown, privacy, permanent private
browsing, no about:config, language) ⊕ `firefox_policies_site` ⊕ generated
extension settings ⊕ `firefox_policies_profile`, deep-merged in that order.
Reference: https://mozilla.github.io/policy-templates/

Typical profile policies:

```yaml
firefox_policies_profile:
  DisplayBookmarksToolbar: "always"
  ManagedBookmarks:
    - { toplevel_name: "Library" }
    - { name: "Catalogue", url: "https://example.org/catalogue" }
  WebsiteFilter:                       # app/display: allow only what is needed
    Block: ["<all_urls>"]
    Exceptions: ["https://app.example.org/*", "file:///opt/kiosk/*"]
  Handlers:
    mimeTypes:
      application/pdf: { action: handleInternally, ask: false }
```

## Per-device overrides

Two YAML files are loaded after the profile when they exist:
`<kiosk_state_dir>/host.yml` (any device, written by root) and
`/boot/firmware/kiosk.yml` (Raspberry Pi boot partition, editable on any
computer). Typical content for a signage display:

```yaml
monitor_url: "https://signage.example.org/board/2"
output_transform: "90"
```

## Adding a profile

Copy the closest demo profile, adjust the switches, add a home page template
if needed. The installer picks it up automatically (boot menu entry with
`kiosk_profile_title`), the tests as well:

```bash
ansible-playbook -i localhost, tests/render.yml -e profile=<name> -e site=../site
tests/docker-test.sh <name> ../site
```
