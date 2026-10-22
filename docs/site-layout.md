# The site directory

A *site* is everything that distinguishes your fleet from any other: name,
colours, fonts, logo, texts, home pages, profiles, URLs, admin keys and
secrets. It is a plain directory; the recommended layout is a private Git
repository that carries dacapo as a submodule named `core`:

```
my-kiosk-site/
├── core/                      git submodule -> dacapo
├── site/
│   ├── site.yml               fleet-wide variables (required)
│   ├── secrets.yml            pull URL with token, admin hash (required for the installer)
│   ├── profiles/<name>.yml    one file per profile (at least one)
│   ├── templates/             home pages, referenced by homepage_template
│   ├── assets/                logo and decorations -> /opt/kiosk/homepage/assets/
│   ├── fonts/                 optional, -> /usr/local/share/fonts/<kiosk_fonts_dir_name>/
│   ├── wallpaper/<WxH>/       optional, screensaver images per resolution
│   └── files/
│       ├── authorized_keys    admin SSH keys (strongly recommended)
│       ├── ca.crt             optional internal CA (self-hosted Git server)
│       └── wpa-kiosk-wlan.conf  optional Wi-Fi fallback
└── README.md
```

Devices pull the site repository and run `core/local.yml`
(`kiosk_pull_playbook`). The playbook looks for the site in this order:

1. `-e kiosk_site_dir=/path` on the command line
2. the environment variable `KIOSK_SITE_DIR`
3. `../site` relative to the playbook (the layout above)
4. `examples/site-demo` inside the core (fallback for tests)

## Variable layering

Later layers win:

```
core/vars/defaults.yml        every variable, documented default
site/site.yml                 your fleet
site/secrets.yml              pull URL, admin hash (loaded with no_log)
site/profiles/<profile>.yml   the kiosk type of this device
/etc/kiosk/fleet-vars.json    settings from a management server (role fleet, fleet_settable_vars only; docs/fleet.md)
/etc/kiosk/host.yml           optional per-device override (root writes it)
/boot/firmware/kiosk.yml      optional per-device override on a Raspberry Pi boot partition
-e key=value                  command line (installer, tests)
```

`kiosk_state_dir`, `kiosk_profile_file` and `kiosk_host_vars_files` change
the device paths; sites migrating from an older setup keep their old paths.

## site.yml — what belongs here

| Area | Variables |
|---|---|
| Branding | `kiosk_brand_name`, `kiosk_language`, `kiosk_locale`, `keyboard_layout`, `kiosk_font_family`, `kiosk_font_stack`, `kiosk_logo`, `kiosk_logo_light`, `kiosk_fonts_dir_name` |
| Colours | `color_accent`, `color_danger`, `color_bar_*`, `color_popup_*`, `color_desktop_bg`, `color_screensaver_bg` (+ `_hover` variants) |
| Texts | `text_bar_*` (brand, hint, clock format, host, USB, reset), `text_idle_*` (title, body, hint, second/seconds), `popup_*` |
| Devices | `kiosk_default_profile`, `kiosk_state_dir`, `admin_user`, `kiosk_user`, `kiosk_home_tmpfs_size` |
| Convergence | `pull_interval`, `pull_boot_delay`, `pull_random_delay`, `nightly_reboot_time`, `apt_update_lists_time`, `apt_upgrade_time` |
| Network | `wlan_fallback_enabled`, `kiosk_ca_name` |
| Installer | `install_locale`, `install_keymap`, `install_timezone` |
| Firefox | `firefox_policies_site` (merged over the core policies, below the profile's) |
| USB page | `usbmanager_texts`, `usbmanager_style` (partial overrides of the English defaults) |

Texts may contain Jinja2 (`{{ idle_seconds }}`); the idle popup texts use
`{seconds}` and `{unit}` placeholders which the script fills at runtime.

## secrets.yml

```yaml
kiosk_pull_url: "https://gitlab+deploy-token-1:gldt-xxxx@git.example.org/org/kiosk-site.git"
kiosk_pull_branch: main
kiosk_pull_playbook: core/local.yml
install_admin_password_hash: "$6$..."     # mkpasswd --method=sha-512
fleet_enroll_key: ""                      # optional, role fleet: enrolment key issued in Partitur
fleet_repo_user: ""                       # optional, role fleet: repository credentials of the Partitur server
fleet_repo_password: ""
```

The pull URL ends up in `/etc/systemd/system/ansible-pull.service` (mode
0600) on every device, so a read-only deploy token is the right credential.
`preseed/build-iso.sh` and `raspberry/make-firstrun.sh` read these values
with `tools/site-var.sh` and refuse to build while a `CHANGE_ME` is left.

## templates/

Home pages are Jinja2 templates rendered with the full variable set. Useful
variables: `kiosk_brand_name`, `kiosk_css_font_family` (ready-made CSS
`font-family` list), `color_*`, `kiosk_logo`, `idle_seconds`, `kiosk_profile`,
`kiosk_language`, anything you define in the profile (`app_url`, …). The
rendered page is `/opt/kiosk/homepage/index.html`; assets are reachable as
`assets/<file>`. The page must fit the screen without scrolling below the bar
(and the Firefox toolbars in browser mode).

## assets/, fonts/, wallpaper/

- `assets/` is copied as a whole. The bar and the popup use
  `kiosk_logo_light` (light mark on dark background), home pages typically
  `kiosk_logo`. Any image format GTK and Firefox can read (PNG, SVG).
- `fonts/` (optional): installed system-wide and picked up by fontconfig; put
  licensed fonts here, never in the core. `kiosk_font_family` must match the
  font's family name.
- `wallpaper/<WIDTHxHEIGHT>/*.png|jpg` (optional): the screensaver picks the
  folder with the closest aspect ratio and a random image. Images removed from
  the site are removed from the devices as well. Required when a profile sets
  `screensaver_enabled: true`.

## files/

| File | Deployed to | Notes |
|---|---|---|
| `authorized_keys` | `~<admin_user>/.ssh/authorized_keys` | replaced as a whole; SSH is key-only after the first pull |
| `ca.crt` | `/usr/local/share/ca-certificates/<kiosk_ca_name>.crt` | also embedded in the installer initrd and the Pi firstrun |
| `wpa-kiosk-wlan.conf` | `/etc/wpa_supplicant/kiosk-wlan.conf` (Debian) or a NetworkManager keyfile (Pi OS) | PSK or 802.1X; see `examples/site-demo/files/wpa-kiosk-wlan.conf.example` |
