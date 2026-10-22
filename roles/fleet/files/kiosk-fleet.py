#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dennis Bormann (HerrBarmann) and Hochschule für Musik und
# Theater Hamburg, ITSC (see NOTICE)
"""kiosk-fleet — reports this device to a management server and runs its actions.

Name and profile come from the server, the device reports every few minutes
and carries out the actions an admin requested there (pull now, install
updates now, reboot). Reference server: Partitur; the HTTP contract is in
docs/fleet.md, so any server speaking it works.

One run (systemd timer, see kiosk-fleet.timer):
  1. identity: serial number, MAC addresses, model
  2. name: GET /fleet/hostname?serial=&mac= → hostname + profile.
     Unknown device: POST /fleet/enroll (X-Enroll-Key, with the current profile
     and hostname). With a self-adopting key the server creates the device at
     once and answers with its name; otherwise it waits on the server's list
     until an admin creates it. The hostname is set with hostnamectl, the
     profile marker is written — the next ansible-pull converges the device.
     Both endpoints sit behind the repository credentials, so the device can
     report from any network (public Wi-Fi included).
  3. actions: GET /clients/<HOST>.json (repo credentials + per-device token)
  4. run the actions that do not reboot (pull-now, update-now)
  5. report: POST /report with the device state and the finished action tokens
  6. reboot if requested

Standard library only; runs as root (hostnamectl, apt, systemctl). Config in
FLEET_CONFIG (JSON, written by the ansible role), state in state_dir.
"""
import fcntl
import json
import os
import platform
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from datetime import datetime, timedelta

VERSION = "0.2.0"
CONFIG = os.environ.get("FLEET_CONFIG", "/etc/kiosk/fleet.json")
TIMEOUT = 20


def log(msg):
    print(msg, flush=True)


def read_config():
    with open(CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)
    cfg.setdefault("state_dir", "/var/lib/kiosk-fleet")
    cfg["url"] = cfg["url"].rstrip("/")
    return cfg


def read_state(cfg):
    try:
        with open(os.path.join(cfg["state_dir"], "state.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_state(cfg, state):
    os.makedirs(cfg["state_dir"], exist_ok=True)
    tmp = os.path.join(cfg["state_dir"], "state.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, os.path.join(cfg["state_dir"], "state.json"))


def read_first(*paths):
    for p in paths:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                v = fh.read().strip().strip("\x00")
            if v and v.lower() not in ("none", "to be filled by o.e.m.", "default string", "system serial number"):
                return v
        except OSError:
            continue
    return ""


def run(cmd, timeout=60, check=False):
    """stdout of a command, "" on failure (never raises unless check)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if check:
            raise
        log(f"{cmd[0]}: {exc}")
        return ""
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: rc={r.returncode} {r.stderr.strip()[:200]}")
    return r.stdout


# ---------- identity ----------
def identity():
    serial = read_first("/sys/class/dmi/id/product_serial", "/sys/firmware/devicetree/base/serial-number")
    if not serial:
        m = re.search(r"^Serial\s*:\s*(\S+)", read_first("/proc/cpuinfo"), re.M)
        serial = m.group(1) if m else ""
    serial = re.sub(r"[^A-Za-z0-9-]", "", serial)[:32]
    macs = []
    for dev in sorted(os.listdir("/sys/class/net")):
        if dev == "lo" or not os.path.exists(f"/sys/class/net/{dev}/device"):
            continue           # virtual interfaces (bridges, tunnels) have no device link
        mac = read_first(f"/sys/class/net/{dev}/address").lower()
        if re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", mac) and mac != "00:00:00:00:00:00":
            macs.append(mac)
    model = (read_first("/sys/class/dmi/id/sys_vendor") + " " + read_first("/sys/class/dmi/id/product_name")).strip() \
        or read_first("/proc/device-tree/model")
    return {"serial": serial if len(serial) >= 4 else "", "macs": macs, "model": model[:80]}


# ---------- http ----------
def http(cfg, method, path, body=None, headers=None, auth=False, timeout=TIMEOUT):
    """(status, json-or-text). Network errors come back as (0, message)."""
    url = cfg["url"] + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", f"kiosk-fleet/{VERSION}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if auth:
        cred = b64encode(f"{cfg['repo_user']}:{cfg['repo_password']}".encode()).decode()
        req.add_header("Authorization", f"Basic {cred}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read().decode("utf-8", "replace"), exc.code
    except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
        return 0, str(exc)
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw


# ---------- name and profile ----------
def set_hostname(name):
    run(["hostnamectl", "set-hostname", name], check=True)
    try:
        with open("/etc/hosts", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    out, done = [], False
    for line in lines:
        if line.startswith("127.0.1.1"):
            out.append(f"127.0.1.1\t{name}")
            done = True
        else:
            out.append(line)
    if not done:
        out.append(f"127.0.1.1\t{name}")
    with open("/etc/hosts", "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def apply_name(cfg, data, state):
    """Take name and profile from a server answer: set the hostname, write the profile marker."""
    name = str(data["hostname"]).lower()
    if socket.gethostname().lower() != name:
        log(f"hostname {socket.gethostname()} -> {name} (Partitur)")
        set_hostname(name)
    profile = str(data.get("profile") or "")
    if profile and cfg.get("profile_file"):
        current = read_first(cfg["profile_file"])
        if current != profile:
            log(f"profile {current or '-'} -> {profile} (Partitur); the next ansible-pull converges the device")
            os.makedirs(os.path.dirname(cfg["profile_file"]), exist_ok=True)
            with open(cfg["profile_file"], "w", encoding="utf-8") as fh:
                fh.write(profile + "\n")
    state.update(hostname=name, profile=profile, waiting=None)
    return name


def enroll(cfg, ident, state):
    """Register with the enrolment key. Returns the server answer (dict) or None. A known device (reinstalled) gets its new
    secret accepted for a day; with a self-adopting key an unknown device is created at once."""
    if not cfg.get("enroll_key"):
        log("device unknown to Partitur and no enrolment key configured")
        return None
    body = {"serial": ident["serial"], "macs": ident["macs"], "model": ident["model"],
            "profile": read_first(cfg.get("profile_file") or "/nonexistent"), "old_hostname": socket.gethostname()}
    status, data = http(cfg, "POST", "/fleet/enroll", body, headers={"X-Enroll-Key": cfg["enroll_key"]}, auth=True)
    if status == 200 and isinstance(data, dict) and data.get("ok"):
        if data.get("state") == "waiting" and data.get("code") != state.get("waiting"):
            log(f"waiting for the admin: code {data.get('code')} at {data.get('url')}")
            state["waiting"] = data.get("code")
        return data
    log(f"enrolment refused: {status} {str(data)[:200]}")
    return None


def ensure_name(cfg, ident, state):
    """Hostname of this device as Partitur knows it (lower case) or None while the device waits to be created."""
    q = urllib.parse.urlencode([("serial", ident["serial"])] + [("mac", m) for m in ident["macs"]])
    status, data = http(cfg, "GET", "/fleet/hostname?" + q, auth=True)
    if status == 200 and isinstance(data, dict) and data.get("hostname"):
        return apply_name(cfg, data, state)
    if status != 404:
        log(f"hostname lookup failed: {status} {str(data)[:200]}")
        return None
    data = enroll(cfg, ident, state)
    if data and data.get("state") == "approved" and data.get("hostname"):
        state["enrolled"] = True              # just created or re-admitted: the new secret is accepted
        return apply_name(cfg, data, state)
    return None


# ---------- device state ----------
def token(cfg):
    path = os.path.join(cfg["state_dir"], "token")
    try:
        with open(path, encoding="utf-8") as fh:
            t = fh.read().strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{32,128}", t):
            return t
    except OSError:
        pass
    os.makedirs(cfg["state_dir"], exist_ok=True)
    t = secrets.token_urlsafe(48)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(t + "\n")
    return t


def iso(ts):
    return ts.replace(microsecond=0).isoformat()


def pull_state():
    """ansible-pull.service: last result and end time, checkout commit and branch."""
    out = {}
    show = run(["systemctl", "show", "ansible-pull.service", "-p", "Result", "-p", "ExecMainExitTimestamp", "-p", "ExecMainStatus"])
    props = dict(line.split("=", 1) for line in show.splitlines() if "=" in line)
    if props.get("ExecMainExitTimestamp"):
        stamp = run(["date", "-d", props["ExecMainExitTimestamp"], "+%Y-%m-%dT%H:%M:%S"]).strip()
        if stamp:
            out["pull_last"] = stamp
            out["pull_result"] = props.get("Result") or ("success" if props.get("ExecMainStatus") == "0" else "exit-code")
    # ansible-pull checks out into ~/.ansible/pull/<hostname>; after a rename the old checkout stays next to the new one, so
    # take the one fetched most recently
    best, best_time = None, -1.0
    base = "/root/.ansible/pull"
    for sub in (os.listdir(base) if os.path.isdir(base) else []):
        git = os.path.join(base, sub)
        stamps = [os.path.getmtime(os.path.join(git, ".git", f)) for f in ("FETCH_HEAD", "index", "HEAD")
                  if os.path.exists(os.path.join(git, ".git", f))]
        if stamps and max(stamps) > best_time:
            best, best_time = git, max(stamps)
    if best:
        commit = run(["git", "-C", best, "rev-parse", "--short=10", "HEAD"]).strip()
        branch = run(["git", "-C", best, "rev-parse", "--abbrev-ref", "HEAD"]).strip()
        if commit:
            out["pull_commit"], out["pull_branch"] = commit, branch
    return out


def updates_pending():
    text = run(["apt-get", "-s", "-o", "Debug::NoLocking=true", "upgrade"], timeout=120)
    lines = [ln for ln in text.splitlines() if ln.startswith("Inst ")]
    return len(lines), sum(1 for ln in lines if "security" in ln.lower())


def firefox_version():
    m = re.search(r"Firefox\s+([\w.]+)", run(["firefox-esr", "--version"], timeout=30) or run(["firefox", "--version"], timeout=30))
    return m.group(1) if m else ""


def monitor_state(cfg):
    port = int(cfg.get("monitor_port") or 0)
    if not port:
        return {}
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status.json", timeout=5) as resp:
            data = json.loads(resp.read().decode())
        return {"monitor_url": str(data.get("url") or "")[:200], "monitor_ok": bool(data.get("ok"))}
    except (OSError, ValueError):
        return {"monitor_url": str(cfg.get("monitor_url") or "")[:200], "monitor_ok": False}


def local_ip(cfg):
    try:
        host = urllib.parse.urlsplit(cfg["url"]).hostname
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 443))
            return s.getsockname()[0]
    except OSError:
        return ""


def os_release():
    m = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', read_first("/etc/os-release"), re.M)
    return m.group(1) if m else platform.platform()


def gather(cfg, name, ident, actions_done, clients_fetched):
    now = datetime.now()
    try:
        uptime = float(read_first("/proc/uptime").split()[0])
    except (ValueError, IndexError):
        uptime = 0.0
    pending, security = updates_pending()
    usage = shutil.disk_usage("/")
    kiosk = {"last_boot": iso(now - timedelta(seconds=uptime)), "nightly_reboot": str(cfg.get("nightly_reboot") or ""),
             "updates_pending": pending, "updates_security": security,
             "disk_size_gb": round(usage.total / 1e9, 1), "disk_free_gb": round(usage.free / 1e9, 1),
             "load": round(os.getloadavg()[0], 2), "firefox_version": firefox_version(), "core_version": str(cfg.get("core_version") or "")}
    kiosk.update(pull_state())
    kiosk.update(monitor_state(cfg))
    install_date = ""
    try:
        install_date = iso(datetime.fromtimestamp(os.stat("/etc/machine-id").st_mtime))
    except OSError:
        pass
    return {"hostname": name.upper(), "serial": ident["serial"], "macs": ident["macs"], "model": ident["model"],
            "os_version": os_release(), "os_build": platform.release(), "ip": local_ip(cfg), "uptime_hours": round(uptime / 3600, 1),
            "agent_version": VERSION, "last_run": iso(now), "install_date": install_date, "kind": "kiosk",
            "kiosk": kiosk, "actions_done": actions_done, "clients_fetched": clients_fetched, "errors": []}


# ---------- actions ----------
def fetch_actions(cfg, name, tok, ident):
    status, data = http(cfg, "GET", f"/clients/{name.upper()}.json", auth=True,
                        headers={"X-Client-Token": tok, "X-Client-Serial": ident["serial"], "X-Client-Macs": ",".join(ident["macs"])})
    if status == 200 and isinstance(data, dict):
        return [a for a in data.get("actions") or [] if isinstance(a, dict) and a.get("token")], True, status
    log(f"actions not fetched: {status} {str(data)[:200]}")
    return [], False, status


def run_actions(actions):
    """Carry out the actions; returns (finished tokens, reboot requested, error messages)."""
    done, reboot, errors = [], False, []
    for a in actions:
        kind = a.get("type")
        try:
            if kind == "pull-now":
                log("action: ansible-pull now")
                run(["systemctl", "start", "ansible-pull.service"], timeout=1800, check=True)
            elif kind == "update-now":
                log("action: install updates now")
                run(["apt-get", "-q", "update"], timeout=600, check=True)
                run(["apt-get", "-q", "-y", "-o", "Dpkg::Options::=--force-confold", "upgrade"], timeout=2400, check=True)
            elif kind == "reboot":
                reboot = True
            else:
                log(f"action {kind}: not supported on a kiosk, acknowledged")
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{kind}: {str(exc)[:200]}")
            log(f"action {kind} failed: {exc}")
        done.append(a["token"])
    return done, reboot, errors


def main():
    cfg = read_config()
    os.makedirs(cfg["state_dir"], exist_ok=True)
    lock = open(os.path.join(cfg["state_dir"], "lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another run is still active")
        return 0
    state = read_state(cfg)
    ident = identity()
    if not ident["macs"] and not ident["serial"]:
        log("neither serial number nor MAC address found")
        return 1
    name = ensure_name(cfg, ident, state)
    write_state(cfg, state)
    if not name:
        return 0
    tok = token(cfg)
    actions, fetched, status = fetch_actions(cfg, name, tok, ident)
    if status == 403 and enroll(cfg, ident, state):
        # reinstalled device with a new secret: the enrolment key re-admits it for a day, then try once more
        actions, fetched, status = fetch_actions(cfg, name, tok, ident)
    done, reboot, errors = run_actions(actions)
    payload = gather(cfg, name, ident, done, fetched)
    payload["errors"] = errors
    status, data = http(cfg, "POST", "/report", payload, headers={"X-Client-Token": tok}, auth=True, timeout=60)
    if status != 200:
        log(f"report refused: {status} {str(data)[:200]}")
        state["last_report_error"] = f"{status} {str(data)[:200]}"
    else:
        state["last_report"] = iso(datetime.now())
        state.pop("last_report_error", None)
    write_state(cfg, state)
    if reboot:
        log("action: reboot")
        time.sleep(2)
        run(["systemctl", "reboot"])
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
