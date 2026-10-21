#!/usr/bin/env python3
"""usbmanager.py — manage USB drives from the kiosk browser.

Small local web service (127.0.0.1:8080 only), runs as the kiosk user inside
the sway session. Python standard library only.

Features:
  - show and delete files on connected drives (/media/<user>/*)
  - copy downloads of this session (~/Downloads) to the drive
  - eject the drive safely (udisksctl unmount + power-off)
  - open PDF/EPUB files from the drive in the browser

Security:
  - every path is canonicalised and must stay below MEDIA_ROOT or DOWNLOADS
  - every action needs a token only the page knows plus a matching
    Host/Origin — foreign web pages cannot send delete requests to localhost

Texts, colours and fonts come from a JSON file written by Ansible
(KIOSK_USB_CONFIG, default /opt/kiosk/usbmanager.json): {"texts": {...},
"style": {...}}. Missing keys fall back to the English defaults below.
"""
import getpass
import html
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST, PORT = "127.0.0.1", 8080
MEDIA_ROOT = os.environ.get("KIOSK_MEDIA_ROOT", f"/media/{getpass.getuser()}")
DOWNLOADS = os.environ.get("KIOSK_DOWNLOADS", os.path.expanduser("~/Downloads"))
CONFIG_FILE = os.environ.get("KIOSK_USB_CONFIG", "/opt/kiosk/usbmanager.json")
TOKEN = secrets.token_urlsafe(24)
ALLOWED_HOSTS = {f"{HOST}:{PORT}", f"localhost:{PORT}"}
ALLOWED_ORIGINS = {f"http://{h}" for h in ALLOWED_HOSTS}

# ------------------------------------------------------------ Configuration --

TEXTS = {
    "page_title": "USB drive", "title_a": "USB drive", "title_b": "manager",
    "hint": "Shared USB drives are used by many people: please delete your files after printing.",
    "no_stick_title": "No USB drive connected",
    "no_stick_text": "Plug in your drive — it appears here automatically after a few seconds.",
    "stick_panel": "USB drive", "eject": "Eject safely", "eject_confirm": "Eject drive “{label}” now?",
    "free_of": "{free} free of {total}", "folder": "Folder:", "select_all": "Select all",
    "size": "Size", "modified": "Modified", "open": "open", "open_title": "Open in the browser",
    "delete_selected": "Delete selected", "delete_selected_confirm": "Permanently delete the selected entries?",
    "delete_all": "Delete everything in this folder", "delete_all_confirm": "Really delete ALL files in this folder?",
    "folder_empty_title": "This folder is empty", "folder_empty_text": "Copy downloads from the right-hand column here.",
    "downloads_panel": "Downloads of this session", "downloads_empty_title": "No downloads yet",
    "downloads_empty_text": "Downloaded files appear here and can be copied to the drive.",
    "downloads_deleted_note": "Downloads are deleted when the session ends.",
    "copy_to_stick": "Copy to the drive →", "delete": "Delete",
    "delete_downloads_confirm": "Delete the selected downloads?", "target": "Target: {target}",
    "plug_in_first": "Plug in a USB drive first to copy files.",
    "msg_no_downloads_selected": "No downloads selected.", "msg_downloads_deleted": "{n} download(s) deleted.",
    "msg_no_stick": "No USB drive connected (any more).", "msg_invalid_path": "Invalid path.",
    "msg_nothing_selected": "Nothing selected.", "msg_delete_error": "Error deleting {name}: {error}",
    "msg_deleted": "{n} entry/entries deleted.", "msg_copy_error": "Error copying {name}: {error}",
    "msg_copied": "{n} file(s) copied to the drive.",
    "msg_renamed": " {n} file name(s) adjusted so they work on all devices (special characters replaced by _).",
    "msg_ejected": "“{label}” was ejected safely — the drive can be removed now.",
    "msg_eject_error": "The drive could not be ejected: {error}", "msg_unknown_action": "Unknown action.",
    "msg_device_unknown": "Device unknown", "msg_bad_link": "Invalid link — please reload the page.",
    "msg_cannot_open": "This file cannot be opened in the browser. Only PDF documents and e-books (EPUB) can be opened.",
    "back": "Back to the overview", "forbidden": "Not allowed.", "not_found": "Not found.",
    "bad_token": "Invalid token — please reload the page.", "file_fallback_name": "File",
    "date_format": "%Y-%m-%d %H:%M", "lang": "en",
}
STYLE = {
    "font_family": '"URW Gothic", "DejaVu Sans", sans-serif', "accent": "#0E7C86", "ink": "#111111",
    "muted": "#5c5c5c", "paper": "#f4f4f2", "line": "#e0e0dc", "danger": "#B3261E",
    "secondary": "#0E7C86", "logo": "logo.png", "decoration": "",
}
try:
    with open(CONFIG_FILE, encoding="utf-8") as _fh:
        _cfg = json.load(_fh)
    TEXTS.update(_cfg.get("texts", {}))
    STYLE.update(_cfg.get("style", {}))
except (OSError, ValueError) as _exc:
    sys.stderr.write(f"usbmanager: config {CONFIG_FILE} not usable ({_exc}), using defaults\n")


def t(key, **kw):
    """Text by key with {placeholders} filled in."""
    s = TEXTS.get(key, key)
    try:
        return s.format(**kw) if kw else s
    except (KeyError, IndexError, ValueError):
        return s


# ---------------------------------------------------------------- Helpers ---

def mounted_sticks():
    """[(mountpoint, device, label)] of all mounts below MEDIA_ROOT."""
    sticks = []
    try:
        with open("/proc/mounts", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 2:
                    continue
                dev, mnt = parts[0], parts[1].encode().decode("unicode_escape")
                if mnt.startswith(MEDIA_ROOT + os.sep) and os.path.isdir(mnt):
                    sticks.append((mnt, dev, os.path.basename(mnt)))
    except OSError:
        # No /proc/mounts (local tests outside Linux): treat sub-folders as
        # "drives", ejecting is not possible then.
        try:
            for name in sorted(os.listdir(MEDIA_ROOT)):
                p = os.path.join(MEDIA_ROOT, name)
                if os.path.isdir(p):
                    sticks.append((p, None, name))
        except OSError:
            pass
    return sorted(sticks)


def safe_join(base, rel):
    """Resolve a path below base; None if it leaves base."""
    base_real = os.path.realpath(base)
    target = os.path.realpath(os.path.join(base_real, rel or ""))
    if target == base_real or target.startswith(base_real + os.sep):
        return target
    return None


def listing(directory):
    entries = []
    try:
        for name in sorted(os.listdir(directory), key=str.lower):
            if name.startswith("."):
                continue
            full = os.path.join(directory, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            entries.append({
                "name": name,
                "is_dir": os.path.isdir(full),
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
    except OSError:
        pass
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


# Characters FAT32/exFAT/NTFS or Windows forbid in file names.
FORBIDDEN_CHARS = set('<>:"/\\|?*')
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                  *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_NAME_BYTES = 200  # headroom below 255 for suffixes like " (2)"


def sanitize_name(name):
    """Clean a file name so it works on Windows, macOS, copiers and every
    drive format. The extension is kept."""
    cleaned = "".join("_" if c in FORBIDDEN_CHARS or ord(c) < 32 or ord(c) == 127 else c
                      for c in name)
    cleaned = cleaned.strip().rstrip(". ")
    stem, ext = os.path.splitext(cleaned)
    if len(ext.encode("utf-8")) > 20:      # not a real extension but part of the name
        stem, ext = cleaned, ""
    if stem.split(".")[0].upper() in RESERVED_NAMES:
        stem = "_" + stem
    while stem and len((stem + ext).encode("utf-8")) > MAX_NAME_BYTES:
        stem = stem[:-1]
    stem = stem.rstrip(". ") or t("file_fallback_name")
    return stem + ext


def copy_to_stick(src, dest):
    """Copy content only: FAT drives have no permissions, transferring them
    (shutil.copy2) can fail with EPERM there."""
    shutil.copyfile(src, dest)
    try:
        st = os.stat(src)
        os.utime(dest, (st.st_atime, st.st_mtime))
    except OSError:
        pass


# Files Firefox can display itself. Extension AND file header are checked —
# a renamed HTML file "x.pdf" is not served.
OPENABLE = {
    ".pdf": ("application/pdf", lambda head: b"%PDF-" in head[:1024]),
    ".epub": ("application/epub+zip", lambda head: head.startswith(b"PK\x03\x04")),
}


def openable_type(path):
    """Content-Type if the file may be opened in the browser, else None."""
    kind = OPENABLE.get(os.path.splitext(path)[1].lower())
    if not kind or not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            head = fh.read(1024)
    except OSError:
        return None
    return kind[0] if kind[1](head) else None


def unique_dest(directory, name):
    dest = os.path.join(directory, name)
    stem, ext = os.path.splitext(name)
    i = 2
    while os.path.exists(dest):
        dest = os.path.join(directory, f"{stem} ({i}){ext}")
        i += 1
    return dest


def eject(device):
    if not device:
        return False, t("msg_device_unknown")
    os.sync()
    for cmd in (["udisksctl", "unmount", "-b", device],
                ["udisksctl", "power-off", "-b", device]):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout).strip()
    return True, ""


# -------------------------------------------------------------- Rendering ---

# Flat layout, no rounded corners. Fits the kiosk area without scrolling
# (about 1920x925 to 1366x613); long file lists scroll inside their column.
DECORATION_CSS = (
    'h1 span::after{content:"";position:absolute;left:-3%;right:-4%;bottom:-.06em;height:.66em;'
    'background:url(/assets/decoration) center/100% 100% no-repeat;z-index:-1}'
    if STYLE.get("decoration") else ""
)
CSS = f"""
:root{{--accent:{STYLE['accent']};--ink:{STYLE['ink']};--muted:{STYLE['muted']};--paper:{STYLE['paper']};--line:{STYLE['line']};
      --red:{STYLE['danger']};--second:{STYLE['secondary']}}}
*{{box-sizing:border-box}}
html,body{{height:100%;margin:0}}
body{{font-family:{STYLE['font_family']};color:var(--ink);
     background:#fff;display:flex;flex-direction:column}}
.wrap{{width:min(1500px,100% - 2*clamp(24px,4vw,80px));margin:0 auto}}
header{{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;padding-top:clamp(16px,3.2vh,40px)}}
header .brand{{display:flex;align-items:center;gap:clamp(16px,2vw,32px)}}
header img{{width:clamp(56px,8vh,96px)}}
h1{{margin:0;font-size:clamp(30px,5.4vh,60px);font-weight:700;line-height:1}}
h1 span{{position:relative;white-space:nowrap}}
{DECORATION_CSS}
.hint{{max-width:30rem;margin:0;font-size:clamp(14px,1.9vh,18px);line-height:1.35;border-left:4px solid var(--accent);padding:4px 0 4px 14px}}
.msg{{margin:clamp(10px,2vh,20px) 0 0;padding:10px 16px;font-weight:700;background:#e6f2f1;border-left:6px solid var(--second)}}
.msg.err{{background:#f7e3e2;border-color:var(--red)}}
main.wrap{{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1.65fr) minmax(0,1fr);
  gap:clamp(16px,2vw,32px);padding:clamp(14px,3vh,32px) 0 clamp(14px,3vh,32px)}}
.panel{{display:flex;flex-direction:column;min-height:0;background:var(--paper);border-top:8px solid var(--c,var(--accent))}}
.panel.dl{{--c:var(--second)}}
.phead{{padding:clamp(12px,2.2vh,22px) clamp(14px,1.6vw,24px) 10px}}
.ptitle{{display:flex;align-items:center;justify-content:space-between;gap:16px}}
.ptitle h2{{margin:0;font-size:clamp(20px,3vh,30px);font-weight:700;display:flex;align-items:center;gap:12px;min-width:0}}
.ptitle h2 svg{{flex:none;color:var(--c,var(--accent))}}
.ptitle h2 .n{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.cap{{display:flex;align-items:center;gap:12px;margin-top:10px;font-size:clamp(13px,1.7vh,16px);color:var(--muted)}}
.bar{{flex:1;max-width:360px;height:8px;background:#e4e4de}}
.bar i{{display:block;height:100%;background:var(--accent)}}
.tabs{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}}
.tabs a{{padding:6px 12px;border:2px solid var(--ink);color:var(--ink);text-decoration:none;font-weight:700;font-size:.9em}}
.tabs a.on{{background:var(--ink);color:#fff}}
.crumbs{{margin-top:10px;font-size:clamp(14px,1.8vh,17px);color:var(--muted)}}
.crumbs a{{color:var(--ink);font-weight:700}}
form.pf{{display:flex;flex-direction:column;flex:1;min-height:0}}
.list{{flex:1;min-height:0;overflow-y:auto;background:#fff;margin:0 clamp(14px,1.6vw,24px);border:1px solid var(--line)}}
.row{{display:grid;grid-template-columns:28px 26px minmax(0,1fr) auto auto;gap:12px;align-items:center;
     padding:clamp(7px,1.3vh,12px) 14px;border-bottom:1px solid var(--line);cursor:pointer;font-size:clamp(14px,1.9vh,18px)}}
.row:hover{{background:#f7f7f4}}
.row.head{{position:sticky;top:0;background:#fff;font-weight:700;font-size:clamp(13px,1.6vh,15px);color:var(--muted);z-index:1}}
.row input{{width:20px;height:20px;margin:0;accent-color:var(--ink);cursor:pointer}}
.row .nm{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.row .nm a{{color:var(--ink);font-weight:700}}
.row .meta{{color:var(--muted);font-size:.85em;white-space:nowrap;text-align:right}}
.row svg{{color:var(--muted)}}
.badge{{font-size:.72em;font-weight:700;background:var(--accent);color:#fff;padding:2px 7px;margin-left:8px;text-decoration:none}}
.empty{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:10px;
       margin:0 clamp(14px,1.6vw,24px);border:2px dashed #d0d0c8;padding:20px;color:var(--muted);font-size:clamp(15px,2vh,19px)}}
.empty strong{{color:var(--ink);font-size:1.15em}}
.actions{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;padding:clamp(10px,2vh,18px) clamp(14px,1.6vw,24px)}}
.btn{{border:2px solid transparent;font:inherit;font-weight:700;font-size:clamp(14px,1.9vh,18px);padding:9px 16px;cursor:pointer;
     background:var(--ink);color:#fff;display:inline-flex;align-items:center;gap:8px}}
.btn:disabled{{opacity:.35;cursor:default}}
.btn.accent{{background:var(--accent);color:#fff}}
.btn.red{{background:var(--red)}}
.btn.outline{{background:transparent;color:var(--ink);border-color:var(--ink)}}
.btn.outline.red{{color:var(--red);border-color:var(--red)}}
.btn.second{{background:var(--second);color:#fff}}
.spacer{{flex:1}}
.note{{color:var(--muted);font-size:clamp(13px,1.6vh,15px);padding:0 clamp(14px,1.6vw,24px) clamp(10px,1.8vh,16px);margin:0}}
.note a{{color:var(--ink);font-weight:700}}
"""

ICON_USB = ('<svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">'
            '<rect x="7" y="9" width="10" height="13"/><path d="M9 9V3h6v6M10.5 5.5h.01M13.5 5.5h.01"/></svg>')
ICON_DL = ('<svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">'
           '<path d="M12 3v12M7 10l5 5 5-5M4 21h16"/></svg>')
ICON_DIR = ('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">'
            '<path d="M3 6h7l2 2h9v11H3z"/></svg>')
ICON_FILE = ('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">'
             '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/></svg>')
ICON_EJECT = ('<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M12 5l7 8H5z"/>'
              '<rect x="5" y="16" width="14" height="3"/></svg>')

# Assets served by this service (an http page may not embed file:// images).
# Fixed mapping — a path from the request is never used.
ASSET_DIR = os.environ.get("KIOSK_ASSET_DIR", "/opt/kiosk/homepage/assets")
ASSETS = {"/assets/logo": STYLE.get("logo") or "logo.png"}
if STYLE.get("decoration"):
    ASSETS["/assets/decoration"] = STYLE["decoration"]

# Instead of reloading the page blindly (which closed open confirm dialogs):
# check every 3 s whether the set of drives changed and only then reload —
# never while checkboxes are ticked. While a confirm() is open no JavaScript
# runs anyway. Also: buttons are enabled only with a selection (with
# counter), plus "select all".
PAGE_JS = """<script>
(function () {
  var known = __STATE__;
  setInterval(function () {
    if (document.querySelector('input[type=checkbox]:checked')) return;
    fetch('/state', {cache: 'no-store'}).then(function (r) { return r.json(); })
      .then(function (s) {
        if (JSON.stringify(s) === known) return;
        var q = new URLSearchParams(location.search);
        q.delete('msg'); q.delete('err');
        location.replace('/?' + q.toString());
      }).catch(function () {});
  }, 3000);

  document.querySelectorAll('form.pf').forEach(function (f) {
    var boxes = f.querySelectorAll('input.pick');
    var all = f.querySelector('input.all');
    function update() {
      var n = f.querySelectorAll('input.pick:checked').length;
      f.querySelectorAll('[data-needs-sel]').forEach(function (b) {
        b.disabled = n === 0 || b.hasAttribute('data-blocked');
        var t = b.querySelector('.cnt'); if (t) t.textContent = n ? n + ' ' : '';
      });
      if (all) { all.checked = n > 0 && n === boxes.length; all.indeterminate = n > 0 && n < boxes.length; }
    }
    boxes.forEach(function (b) { b.addEventListener('change', update); });
    if (all) all.addEventListener('change', function () { boxes.forEach(function (b) { b.checked = all.checked; }); update(); });
    update();
  });
})();
</script>"""


def stick_state():
    return [s[2] for s in mounted_sticks()]


def page(body):
    # Double-encoded = JS string literal; escape "<" because drive labels can
    # contain arbitrary text (e.g. "</script>").
    state = json.dumps(json.dumps(stick_state())).replace("<", "\\u003c")
    js = PAGE_JS.replace("__STATE__", state)
    return f"""<!DOCTYPE html><html lang="{esc(t('lang'))}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(t('page_title'))}</title><style>{CSS}</style></head>
<body>{body}{js}</body></html>"""


def esc(s):
    return html.escape(str(s), quote=True)


def js_str(s):
    """Text inside a single-quoted JavaScript string inside an HTML attribute."""
    return esc(str(s).replace("\\", "\\\\").replace("'", "\\'"))


def head_html(msg, err):
    out = ['<div class="wrap"><header><div class="brand">'
           f'<img src="/assets/logo" alt="">'
           f'<h1><span>{esc(t("title_a"))}</span> {esc(t("title_b"))}</h1></div>'
           f'<p class="hint">{esc(t("hint"))}</p></header>']
    if msg:
        out.append(f'<div class="msg{" err" if err else ""}" role="status">{esc(msg)}</div>')
    out.append('</div>')
    return "".join(out)


def hidden_fields(label, rel_dir):
    return (f'<input type="hidden" name="token" value="{TOKEN}">'
            f'<input type="hidden" name="stick" value="{esc(label or "")}">'
            f'<input type="hidden" name="dir" value="{esc(rel_dir)}">')


def render(stick_name, rel_dir, msg, err):
    sticks = mounted_sticks()
    out = [head_html(msg, err), '<main class="wrap">']

    if not sticks:
        out.append('<section class="panel"><div class="phead"><div class="ptitle"><h2>'
                   f'{ICON_USB}<span class="n">{esc(t("stick_panel"))}</span></h2></div></div>'
                   '<div class="empty" style="margin-bottom:clamp(14px,1.6vw,24px)">'
                   f'{ICON_USB.replace("30", "64", 2)}<strong>{esc(t("no_stick_title"))}</strong>'
                   f'{esc(t("no_stick_text"))}</div></section>')
        out.append(render_downloads(None, "", ""))
        out.append('</main>')
        return page("".join(out))

    current = next((s for s in sticks if s[2] == stick_name), sticks[0])
    mnt, dev, label = current
    cur_dir = safe_join(mnt, rel_dir)
    if cur_dir is None or not os.path.isdir(cur_dir):
        cur_dir, rel_dir = mnt, ""
    q = lambda **kw: "/?" + urllib.parse.urlencode({"stick": label, **kw})

    try:
        du = shutil.disk_usage(mnt)
        used = 100 * (du.total - du.free) / du.total if du.total else 0
        cap = (f'<div class="cap"><span class="bar"><i style="width:{used:.0f}%"></i></span>'
               f'<span>{esc(t("free_of", free=human(du.free), total=human(du.total)))}</span></div>')
    except OSError:
        cap = ""

    tabs = ""
    if len(sticks) > 1:
        tabs = '<div class="tabs">' + "".join(
            f'<a class="{"on" if s[2] == label else ""}" href="/?stick={urllib.parse.quote(s[2])}">{esc(s[2])}</a>'
            for s in sticks) + "</div>"

    crumbs = [f'<a href="{q()}">{esc(label)}</a>']
    acc = ""
    for part in [p for p in rel_dir.split("/") if p]:
        acc = f"{acc}/{part}" if acc else part
        crumbs.append(f'<a href="{q(dir=acc)}">{esc(part)}</a>')
    shown_path = " / ".join(crumbs)

    out.append('<section class="panel">'
               '<div class="phead"><div class="ptitle">'
               f'<h2>{ICON_USB}<span class="n">{esc(label)}</span></h2>'
               '<form method="post" action="/action">' + hidden_fields(label, rel_dir) +
               '<button class="btn accent" name="action" value="eject" '
               f'onclick="return confirm(\'{js_str(t("eject_confirm", label=label))}\')">{ICON_EJECT}{esc(t("eject"))}</button>'
               '</form></div>' + cap + tabs + f'<div class="crumbs">{esc(t("folder"))} {shown_path}</div></div>')

    entries = listing(cur_dir)
    out.append('<form class="pf" method="post" action="/action">' + hidden_fields(label, rel_dir))
    if entries:
        out.append(f'<div class="list"><label class="row head"><input type="checkbox" class="all" aria-label="{esc(t("select_all"))}">'
                   f'<span></span><span>{esc(t("select_all"))}</span><span class="meta">{esc(t("size"))}</span><span class="meta">{esc(t("modified"))}</span></label>')
        for e in entries:
            sub = (rel_dir + "/" + e["name"]).strip("/")
            if e["is_dir"]:
                icon, name = ICON_DIR, f'<a href="{q(dir=sub)}">{esc(e["name"])}</a>'
            elif os.path.splitext(e["name"])[1].lower() in OPENABLE:
                link = "/open?" + urllib.parse.urlencode({"token": TOKEN, "stick": label, "path": sub})
                icon = ICON_FILE
                name = (f'{esc(e["name"])}<a class="badge" href="{esc(link)}" target="_blank" '
                        f'title="{esc(t("open_title"))}">{esc(t("open"))}</a>')
            else:
                icon, name = ICON_FILE, esc(e["name"])
            size = "" if e["is_dir"] else human(e["size"])
            when = time.strftime(t("date_format"), time.localtime(e["mtime"]))
            out.append(f'<label class="row"><input type="checkbox" class="pick" name="sel" value="{esc(e["name"])}">'
                       f'{icon}<span class="nm">{name}</span><span class="meta">{size}</span>'
                       f'<span class="meta">{when}</span></label>')
        out.append('</div><div class="actions">'
                   '<button class="btn red" data-needs-sel name="action" value="delete" '
                   f'onclick="return confirm(\'{js_str(t("delete_selected_confirm"))}\')"><span class="cnt"></span>{esc(t("delete_selected"))}</button>'
                   '<span class="spacer"></span>'
                   '<button class="btn outline red" name="action" value="delete_all" '
                   f'onclick="return confirm(\'{js_str(t("delete_all_confirm"))}\')">{esc(t("delete_all"))}</button>'
                   '</div>')
    else:
        out.append(f'<div class="empty" style="margin-bottom:clamp(14px,1.6vw,24px)"><strong>{esc(t("folder_empty_title"))}</strong>'
                   f'{esc(t("folder_empty_text"))}</div>')
    out.append('</form></section>')

    out.append(render_downloads(label, rel_dir, shown_path))
    out.append('</main>')
    return page("".join(out))


def render_downloads(label, rel_dir, target_html):
    entries = [e for e in listing(DOWNLOADS) if not e["is_dir"]]
    out = ['<section class="panel dl"><div class="phead"><div class="ptitle">'
           f'<h2>{ICON_DL}<span class="n">{esc(t("downloads_panel"))}</span></h2></div></div>']
    if not entries:
        out.append(f'<div class="empty"><strong>{esc(t("downloads_empty_title"))}</strong>'
                   f'{esc(t("downloads_empty_text"))}</div>'
                   f'<p class="note" style="padding-top:12px">{esc(t("downloads_deleted_note"))}</p></section>')
        return "".join(out)
    out.append('<form class="pf" method="post" action="/action">' + hidden_fields(label, rel_dir))
    out.append(f'<div class="list"><label class="row head"><input type="checkbox" class="all" aria-label="{esc(t("select_all"))}">'
               f'<span></span><span>{esc(t("select_all"))}</span><span class="meta">{esc(t("size"))}</span><span></span></label>')
    for e in entries:
        out.append(f'<label class="row"><input type="checkbox" class="pick" name="dl" value="{esc(e["name"])}">'
                   f'{ICON_FILE}<span class="nm">{esc(e["name"])}</span><span class="meta">{human(e["size"])}</span><span></span></label>')
    out.append('</div><div class="actions">')
    if label:
        out.append('<button class="btn second" data-needs-sel name="action" value="copy">'
                   f'<span class="cnt"></span>{esc(t("copy_to_stick"))}</button>')
    else:
        out.append(f'<button class="btn second" data-needs-sel data-blocked disabled>{esc(t("copy_to_stick"))}</button>')
    out.append('<button class="btn outline" data-needs-sel name="action" value="delete_dl" '
               f'onclick="return confirm(\'{js_str(t("delete_downloads_confirm"))}\')"><span class="cnt"></span>{esc(t("delete"))}</button></div>')
    # target_html already contains markup (links); the surrounding text is escaped.
    note = (esc(t("target", target="\0")).replace("\0", target_html) if label else esc(t("plug_in_first")))
    out.append(f'<p class="note">{note} · {esc(t("downloads_deleted_note"))}</p></form></section>')
    return "".join(out)


# ---------------------------------------------------------------- Actions ---

def do_action(form):
    """Execute an action; returns (stick, dir, message, is_error)."""
    action = form.get("action", [""])[0]
    label = form.get("stick", [""])[0]
    rel_dir = form.get("dir", [""])[0]

    # Deleting downloads needs no drive — therefore BEFORE the drive check.
    if action == "delete_dl":
        names = form.get("dl", [])
        if not names:
            return label, rel_dir, t("msg_no_downloads_selected"), True
        n = 0
        for name in names:
            src = safe_join(DOWNLOADS, name)
            if src and os.path.isfile(src) and os.path.dirname(src) == os.path.realpath(DOWNLOADS):
                try:
                    os.remove(src)
                    n += 1
                except OSError:
                    pass
        return label, rel_dir, t("msg_downloads_deleted", n=n), False

    sticks = {s[2]: s for s in mounted_sticks()}
    if label not in sticks:
        return label, rel_dir, t("msg_no_stick"), True
    mnt, dev, _ = sticks[label]
    cur_dir = safe_join(mnt, rel_dir)
    if cur_dir is None:
        return label, "", t("msg_invalid_path"), True

    if action in ("delete", "delete_all"):
        names = form.get("sel", []) if action == "delete" else [e["name"] for e in listing(cur_dir)]
        if not names:
            return label, rel_dir, t("msg_nothing_selected"), True
        n = 0
        for name in names:
            p = safe_join(cur_dir, name)
            if not p or p == os.path.realpath(mnt) or os.path.dirname(p) != os.path.realpath(cur_dir):
                continue
            try:
                shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
                n += 1
            except OSError as exc:
                return label, rel_dir, t("msg_delete_error", name=name, error=exc.strerror), True
        os.sync()
        return label, rel_dir, t("msg_deleted", n=n), False

    if action == "copy":
        names = form.get("dl", [])
        if not names:
            return label, rel_dir, t("msg_no_downloads_selected"), True
        n = renamed = 0
        for name in names:
            src = safe_join(DOWNLOADS, name)
            if not src or not os.path.isfile(src) or os.path.dirname(src) != os.path.realpath(DOWNLOADS):
                continue
            clean = sanitize_name(name)
            renamed += clean != name
            try:
                copy_to_stick(src, unique_dest(cur_dir, clean))
                n += 1
            except OSError as exc:
                return label, rel_dir, t("msg_copy_error", name=name, error=exc.strerror), True
        os.sync()
        msg = t("msg_copied", n=n)
        if renamed:
            msg += t("msg_renamed", n=renamed)
        return label, rel_dir, msg, False

    if action == "eject":
        ok, err = eject(dev)
        if ok:
            return "", "", t("msg_ejected", label=label), False
        return label, rel_dir, t("msg_eject_error", error=err), True

    return label, rel_dir, t("msg_unknown_action"), True


# ----------------------------------------------------------------- Server ---

class Handler(BaseHTTPRequestHandler):
    server_version = "KioskUSB/1.0"

    def _origin_ok(self):
        if self.headers.get("Host", "") not in ALLOWED_HOSTS:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in ALLOWED_ORIGINS

    def _send_html(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, stick, rel_dir, msg, err):
        params = {"msg": msg, "err": "1" if err else "0"}
        if stick:
            params["stick"] = stick
        if rel_dir:
            params["dir"] = rel_dir
        self.send_response(303)
        self.send_header("Location", "/?" + urllib.parse.urlencode(params))
        self.end_headers()

    def do_GET(self):
        if not self._origin_ok():
            return self._send_html(page(f"<p>{esc(t('forbidden'))}</p>"), 403)
        url = urllib.parse.urlparse(self.path)
        if url.path in ASSETS:
            try:
                with open(os.path.join(ASSET_DIR, os.path.basename(ASSETS[url.path])), "rb") as fh:
                    data = fh.read()
            except OSError:
                return self._send_html(page(f"<p>{esc(t('not_found'))}</p>"), 404)
            self.send_response(200)
            ext = os.path.splitext(ASSETS[url.path])[1].lower()
            self.send_header("Content-Type", {"png": "image/png", "svg": "image/svg+xml", "jpg": "image/jpeg",
                                              "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext.lstrip("."), "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            return self.wfile.write(data)
        if url.path == "/open":
            return self._open_file(urllib.parse.parse_qs(url.query))
        if url.path == "/state":
            data = json.dumps(stick_state()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return self.wfile.write(data)
        if url.path != "/":
            return self._send_html(page(f"<p>{esc(t('not_found'))}</p>"), 404)
        qs = urllib.parse.parse_qs(url.query)
        body = render(qs.get("stick", [""])[0], qs.get("dir", [""])[0],
                      qs.get("msg", [""])[0], qs.get("err", ["0"])[0] == "1")
        self._send_html(body)

    def _open_file(self, qs):
        """Serve a PDF/EPUB from the drive for display in Firefox — nothing else."""
        def refuse(text):
            self._send_html(page(head_html(text, True) +
                                 f'<div class="wrap"><p><a href="/">{esc(t("back"))}</a></p></div>'), 403)
        if qs.get("token", [""])[0] != TOKEN:
            return refuse(t("msg_bad_link"))
        sticks = {s[2]: s for s in mounted_sticks()}
        stick = sticks.get(qs.get("stick", [""])[0])
        path = safe_join(stick[0], qs.get("path", [""])[0]) if stick else None
        ctype = openable_type(path) if path else None
        if not ctype:
            return refuse(t("msg_cannot_open"))
        name = os.path.basename(path)
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition",
                                 "inline; filename*=UTF-8''" + urllib.parse.quote(name))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                shutil.copyfileobj(fh, self.wfile)
        except OSError:
            pass  # drive removed while serving

    def do_POST(self):
        if not self._origin_ok() or self.path != "/action":
            return self._send_html(page(f"<p>{esc(t('forbidden'))}</p>"), 403)
        length = int(self.headers.get("Content-Length", "0") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(min(length, 1_000_000)).decode("utf-8"))
        if form.get("token", [""])[0] != TOKEN:
            return self._send_html(page(f"<p>{esc(t('bad_token'))}</p>"), 403)
        self._redirect(*do_action(form))

    def log_message(self, fmt, *args):
        sys.stderr.write("usbmanager: " + fmt % args + "\n")


if __name__ == "__main__":
    os.makedirs(DOWNLOADS, exist_ok=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
