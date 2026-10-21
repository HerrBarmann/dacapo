# Demo site

A complete, neutral site for dacapo: three profiles, free font (URW
Gothic from `fonts-urw-base35`), generated logo and wallpapers, placeholder
secrets. The core falls back to this directory when no `../site` exists, so
`tests/render.yml` and `tests/docker-test.sh` work out of the box.

| Path | Purpose |
|---|---|
| `site.yml` | fleet-wide settings: brand, language, colours, texts, timers |
| `secrets.yml` | pull URL with deploy token, admin password hash (placeholders) |
| `profiles/browser.yml` | public computer: visible browser, bookmarks, USB, screensaver |
| `profiles/app.yml` | web-app terminal: hidden chrome, one allowed application |
| `profiles/display.yml` | digital signage: fullscreen, display service |
| `templates/homepage-*.html.j2` | local home pages (Jinja2) |
| `assets/` | `logo.png`, `logo-light.png` (+ SVG sources) → `/opt/kiosk/homepage/assets/` |
| `wallpaper/1920x1080/` | screensaver images |
| `files/authorized_keys` | admin SSH keys (placeholder key) |
| `files/wpa-kiosk-wlan.conf.example` | Wi-Fi fallback template |

Use it as the starting point of your own site: copy, replace logo and
wallpapers, edit texts, add profiles — see `docs/site-layout.md`.
