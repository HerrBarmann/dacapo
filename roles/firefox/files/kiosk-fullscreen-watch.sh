#!/bin/bash
# kiosk-fullscreen-watch.sh — runs inside the sway session (exec in
# /etc/sway/config). A fullscreen window (F11, JavaScript fullscreen API,
# video fullscreen) would cover the waybar with the "end session" button.
# The static for_window rule only applies when the window is mapped — a
# later fullscreen request wins. Therefore: subscribe to sway IPC and take
# fullscreen away from EVERY window immediately.
# (The hidden browser chrome is NOT handled here — that is userChrome.css,
# see kiosk-firefox.sh.)
set -u

# Clean up once at start (in case a window is already fullscreen).
swaymsg '[app_id=".*"] fullscreen disable' >/dev/null 2>&1

# "change":"fullscreen_mode" fires on every fullscreen toggle (on AND off);
# fullscreen disable is idempotent, so the off case is harmless.
# (Regex instead of a fixed string: swaymsg prints compact JSON in a pipe and
# spaced JSON on a TTY — cover both.)
swaymsg -t subscribe -m '["window"]' 2>/dev/null \
  | grep --line-buffered -E '"change": ?"fullscreen_mode"' \
  | while read -r _; do
        swaymsg '[app_id=".*"] fullscreen disable' >/dev/null 2>&1
    done
