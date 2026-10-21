# Site files

| File | Deployed to | Required |
|---|---|---|
| `authorized_keys` | `/home/<admin_user>/.ssh/authorized_keys` | strongly recommended (SSH is key-only) |
| `ca.crt` | `/usr/local/share/ca-certificates/<kiosk_ca_name>.crt`, also embedded in the installer | only for a self-hosted Git server with an internal CA |
| `wpa-kiosk-wlan.conf` | `/etc/wpa_supplicant/kiosk-wlan.conf` (or a NetworkManager keyfile) | only with `wlan_fallback_enabled: true` |
