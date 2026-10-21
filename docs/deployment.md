# Deployment

From an empty machine to a running kiosk: PCs via an unattended Debian
installer (part A), Raspberry Pi via a prepared SD card (part B). Both end in
the same state: the device pulls its configuration from the site repository
every 30 minutes.

## Prerequisites

- [ ] a site repository (private) with `core/` submodule and `site/`
      directory, pushed — devices pull exactly that state
- [ ] `site/secrets.yml`: `kiosk_pull_url` with a **read-only deploy token**,
      `install_admin_password_hash` (`mkpasswd --method=sha-512` or
      `openssl passwd -6`)
- [ ] `site/files/authorized_keys` up to date — after the first pull SSH is
      key-only
- [ ] `site/files/ca.crt` if the Git server uses an internal CA

The build scripts refuse to run while a `CHANGE_ME` remains:
```bash
grep -rn CHANGE_ME site/
```

---

# Part A — PCs (installer ISO)

## 1. Build the ISO

**With Docker (macOS or Linux, no Debian machine needed):**
```bash
cd core/preseed && ./build-iso-docker.sh all ../../site      # one ISO, boot menu with every profile
./build-iso-docker.sh browser ../../site                      # dedicated stick for one profile, starts after 5 s
```
Downloads the Debian netinst ISO once into `preseed/cache/` (checksum
verified), builds inside `debian:trixie` and writes
`preseed/kiosk-<all|profile>-installer.iso`. New Debian point release:
`DEBIAN_VERSION=13.7.0 ./build-iso-docker.sh all ../../site`.

**On a Debian/Ubuntu machine:**
```bash
sudo apt install xorriso isolinux
wget https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/debian-13.6.0-amd64-netinst.iso
./build-iso.sh all debian-13.6.0-amd64-netinst.iso ../../site
```

The script fills `preseed.cfg` from the site (locale, keymap, time zone,
admin user and hash, pull URL, branch, playbook, state directory, hostname
prefixes of all profiles, optional CA), embeds it into the installer initrd,
writes the boot menus (`kiosk.profile=<name>` as kernel parameter) and
verifies the result.

Write to a stick — Linux (`/dev/sdX` = the stick, check with `lsblk`):
```bash
sudo dd if=kiosk-all-installer.iso of=/dev/sdX bs=4M status=progress oflag=sync
```
macOS (`/dev/diskN` = the stick, check with `diskutil list`):
```bash
diskutil unmountDisk /dev/diskN && sudo dd if=kiosk-all-installer.iso of=/dev/rdiskN bs=4m status=progress && diskutil eject /dev/diskN
```

## 2. Install a device

> **WARNING:** the stick wipes the machine's disk without asking.

1. Boot from the stick; pick the profile in the menu (dedicated sticks start
   on their own after 5 s).
2. Wait 15–30 minutes: installation and the first `ansible-pull` run
   unattended, then the machine reboots into the kiosk. If the pull fails the
   installer shows an error — usually token or URL; log on installer console 4
   (`Ctrl+Alt+F4`), later in `/var/log/kiosk-bootstrap.log`.
3. **Harden the BIOS** (otherwise zero persistence can be defeated with a
   live stick): BIOS admin password, boot order fixed to the internal disk,
   USB boot off, Secure Boot on, "power on after power loss" on for displays.

The device is named `<hostname_prefix>-<MAC suffix>` and reachable with
`ssh <admin_user>@<ip>` (key only). The profile is in the marker file
(`/etc/kiosk/profile` by default).

### Manual bootstrap (fallback)

If a device ends in a text login instead of the kiosk, the bootstrap at the
end of the installation failed (most often: ISO built from an outdated site).
Finish by hand, on the device:

```bash
sudo mkdir -p /etc/kiosk && echo <profile> | sudo tee /etc/kiosk/profile
sudo ansible-pull -U '<kiosk_pull_url>' -i localhost, --checkout main core/local.yml
sudo reboot
```
(Copy `site/files/ca.crt` to `/usr/local/share/ca-certificates/` and run
`update-ca-certificates` first if your Git server needs it.)

## 3. Acceptance test (first device of every profile)

All profiles:
- [ ] marker file shows the right profile
- [ ] a commit to the site + `sudo systemctl start ansible-pull.service` arrives
- [ ] after a reboot `/home/kiosk` is empty, `swapon --show` is empty
- [ ] `systemctl list-timers` shows `ansible-pull`, `nightly-reboot`, `apt-daily*`
- [ ] booting from a foreign USB stick is impossible

browser / app:
- [ ] home page below the bar, "end session" button resets immediately
- [ ] countdown popup `idle_warn_seconds` before the reset, any input closes it
- [ ] browser: bookmark bar with the managed folder, `about:policies` active,
      USB drive appears on the "USB drive" page, screensaver after
      `screensaver_seconds`
- [ ] app: no address bar/tabs, F11 does not hide the bar, foreign URLs blocked

display:
- [ ] brief status page, then the content full screen, no pointer
- [ ] `curl -s http://127.0.0.1:8081/status.json` → `"ok": true`
- [ ] unplug the network → content stays; replug → updates
- [ ] `host.yml` with another URL or `output_transform: "90"` → applied after pull/reboot

---

# Part B — Raspberry Pi (SD card)

Hardware: Raspberry Pi 4 or 5 with ≥ 2 GB RAM, microSD ≥ 16 GB, Ethernet for
the setup. Set `kiosk_home_tmpfs_size: 1g` in the profile.

## 1. Generate firstrun.sh

```bash
cd core/raspberry && ./make-firstrun.sh display ../../site
```
Produces `raspberry/firstrun.sh` with profile, pull URL + branch + playbook,
state directory and embedded CA. It contains the deploy token — do not
commit or share it (git-ignored).

## 2. Flash Raspberry Pi OS Lite

Raspberry Pi Imager: *Raspberry Pi OS (other) → Raspberry Pi OS Lite
(64-bit)* — **Lite**, not Desktop. Settings: hostname `<prefix>-<place>`
(e.g. `display-foyer`), username = your `admin_user`, a console password,
time zone and keyboard, SSH enabled. Wi-Fi may stay empty (Ethernet) or a
simple WPA2 network; the fleet Wi-Fi comes from the playbook.

## 3. Files onto the boot partition

After flashing the card appears as `bootfs`. Copy `firstrun.sh` to its root,
optionally `kiosk.yml` with per-device settings:
```yaml
monitor_url: "https://signage.example.org/board/2"
output_transform: "90"
```
Then append to the single line of `cmdline.txt` (space before, no newline):
```
 systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.run_failure_action=reboot
```

## 4. Boot

```
Boot 1  firstrun.sh writes marker, CA and the bootstrap service (no network) → reboot
Boot 2  kiosk-bootstrap.service waits for network and time, installs git +
        ansible, runs ansible-pull (sway, Firefox ESR, configuration) → reboot
Boot 3  autologin → sway → Firefox → kiosk ✓
```
Logs live on the boot partition (`firstrun.log`, `kiosk-bootstrap.log`) and
are readable on any computer. A failed stage 2 retries at every boot;
immediately: `sudo systemctl start kiosk-bootstrap`.

> The Pi path shares the playbook with the PC path (tested in the container);
> the Pi-specific parts are the two-stage bootstrap and the NetworkManager
> Wi-Fi. Run the acceptance test on the first Pi before writing more cards.
