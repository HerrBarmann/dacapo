#!/usr/bin/env python3
"""kiosk-monitor.py — local display service for digital signage.

Fetches the signage page (SVG, HTML or an image) from the server at a fixed
interval, keeps the last good copy in memory and serves it to the local home
page on http://127.0.0.1:<port>/. Python standard library only.

Why a service in between:
  - The home page is a local file (file://) and may not load from the server
    directly because of CORS; this service answers with
    Access-Control-Allow-Origin.
  - Network/server outage: the last copy stays on screen, the service keeps
    retrying in the background (quickly as long as nothing was ever loaded).
  - Firefox may crash/restart, the content survives.

Endpoints (127.0.0.1 only):
  /display      last copy (Content-Type of the server, ETag = hash,
                X-Fetched-At = Unix time of the fetch, X-Upstream-URL);
                503 while nothing is available
  /status.json  {"ok", "fetched_at", "age_seconds", "last_error", "url"}

Configuration via environment (set by kiosk-monitor.service):
  MONITOR_URL, MONITOR_REFRESH_SECONDS (default 300), MONITOR_PORT (8081),
  MONITOR_EXPECT (svg | html | any — sanity check of the response body)
"""
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

URL = os.environ.get("MONITOR_URL", "")
REFRESH = int(os.environ.get("MONITOR_REFRESH_SECONDS", "300"))
PORT = int(os.environ.get("MONITOR_PORT", "8081"))
EXPECT = os.environ.get("MONITOR_EXPECT", "any").lower()
HOST = "127.0.0.1"
RETRY_INITIAL = 10      # seconds while nothing was ever loaded
RETRY_LATER = 60        # seconds when an old copy exists
TIMEOUT = 25

state = {
    "body": None,            # bytes
    "content_type": "application/octet-stream",
    "etag": None,
    "fetched_at": None,      # float (Unix time)
    "last_error": None,
    "last_attempt": None,
}
lock = threading.Lock()


def log(msg):
    print(msg, flush=True)


def check_body(body, ctype):
    head = body[:4096].lower()
    if EXPECT == "svg" and b"<svg" not in head:
        raise ValueError("unexpected response (no SVG)")
    if EXPECT == "html" and b"<html" not in head and b"<!doctype html" not in head:
        raise ValueError("unexpected response (no HTML)")
    if not body:
        raise ValueError("empty response")


def fetch_once():
    req = urllib.request.Request(URL, headers={
        "User-Agent": "Kiosk-Monitor/1.0",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = resp.read()
        ctype = resp.headers.get("Content-Type", "application/octet-stream")
    check_body(body, ctype)
    return body, ctype


def fetch_loop():
    while True:
        try:
            body, ctype = fetch_once()
            with lock:
                changed = state["etag"] != hashlib.sha256(body).hexdigest()
                state["body"] = body
                state["content_type"] = ctype.split(";")[0].strip() or "application/octet-stream"
                state["etag"] = hashlib.sha256(body).hexdigest()
                state["fetched_at"] = time.time()
                state["last_error"] = None
                state["last_attempt"] = time.time()
            if changed:
                log(f"display updated ({len(body)} bytes)")
            delay = REFRESH
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
            with lock:
                state["last_error"] = str(exc)
                state["last_attempt"] = time.time()
                have = state["body"] is not None
            log(f"fetch failed: {exc}")
            delay = RETRY_LATER if have else RETRY_INITIAL
        time.sleep(delay)


class Handler(BaseHTTPRequestHandler):
    server_version = "Kiosk-Monitor/1.0"

    def log_message(self, fmt, *args):  # do not spam the journal with accesses
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "ETag, X-Fetched-At, X-Upstream-URL")
        self.send_header("Cache-Control", "no-store")

    def _json(self, code, obj):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        with lock:
            snap = dict(state)
        if path == "/display":
            if snap["body"] is None:
                self._json(503, {"ok": False, "error": snap["last_error"] or "no data yet"})
                return
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", snap["content_type"])
            self.send_header("Content-Length", str(len(snap["body"])))
            self.send_header("ETag", f'"{snap["etag"]}"')
            self.send_header("X-Fetched-At", str(int(snap["fetched_at"])))
            self.send_header("X-Upstream-URL", URL)
            self.end_headers()
            self.wfile.write(snap["body"])
        elif path in ("/", "/status.json"):
            now = time.time()
            self._json(200, {
                "ok": snap["body"] is not None,
                "fetched_at": snap["fetched_at"],
                "age_seconds": (now - snap["fetched_at"]) if snap["fetched_at"] else None,
                "last_error": snap["last_error"],
                "last_attempt": snap["last_attempt"],
                "url": URL,
                "refresh_seconds": REFRESH,
            })
        else:
            self._json(404, {"ok": False, "error": "unknown path"})


def main():
    if not URL:
        log("ERROR: MONITOR_URL is not set")
        sys.exit(2)
    log(f"kiosk-monitor: {URL} every {REFRESH} s, local on http://{HOST}:{PORT}/")
    threading.Thread(target=fetch_loop, daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
