#!/usr/bin/env python3
"""compose.py — composes full kiosk screens from rendered demo files and
screenshots them with headless Chromium (runs inside the renderer image).

  compose.py <rendered-site-dir> <site-dir> <mockup-dir> <out-dir>

Produces in <out-dir>: browser-home.png, browser-idle-popup.png,
browser-usb-manager.png, app-home.png, display-board.png (1920x1080).
"""
import datetime
import html
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

RENDERED, SITE, MOCK, OUT = (os.path.abspath(a) for a in sys.argv[1:5])
W, H = 1920, 1080
BAR_H = 43           # waybar 40 px + 3 px accent line
FF_TOOLBAR_H = 112   # simulated Firefox tab strip + nav bar + bookmarks bar
CLOCK = datetime.datetime(2026, 10, 21, 14, 32)


def load_vars(profile):
    """Colours, texts and fonts as the templates rendered them (parsed back
    from the rendered files — no Ansible needed in the container)."""
    d = os.path.join(RENDERED, profile)
    v = {"profile": profile}
    css = open(os.path.join(d, "etc-xdg-waybar-style.css"), encoding="utf-8").read()
    v["font"] = re.search(r"font-family:\s*(.+?);", css).group(1).replace(', "FontAwesome"', "")
    v["bar_bg"] = re.search(r"window#waybar\s*{[^}]*background-color:\s*(#\w+)", css).group(1)
    v["bar_fg"] = re.search(r"window#waybar\s*{[^}]*\n\s*color:\s*(#\w+)", css).group(1)
    v["accent"] = re.search(r"border-bottom:\s*3px solid (#\w+)", css).group(1)
    v["divider"] = re.search(r"border-right:\s*1px solid (#\w+)", css).group(1)
    v["muted"] = re.search(r"#custom-hint\s*{[^}]*color:\s*(#\w+)", css).group(1)
    v["dim"] = re.search(r"#custom-host\s*{[^}]*color:\s*(#\w+)", css).group(1)
    v["on_accent"] = re.search(r"#custom-usb\s*{[^}]*\n\s*color:\s*(#\w+)", css).group(1)
    v["danger"] = re.search(r"#custom-reset\s*{[^}]*background-color:\s*(#\w+)", css).group(1)
    wb = json.load(open(os.path.join(d, "etc-xdg-waybar-config.json"), encoding="utf-8"))
    v["logo_light"] = os.path.basename(wb["image#logo"]["path"])
    v["brand"] = wb["custom/brand"]["format"]
    v["hint"] = re.sub(r"<span[^>]*>.*?</span>\s*", "", wb["custom/hint"]["format"]).strip()
    v["clock_fmt"] = re.search(r"{:(.+)}", wb["clock"]["format"]).group(1)
    v["host_fmt"] = wb["custom/host"]["format"]
    v["usb"] = wb["custom/usb"]["format"].split("  ", 1)[-1] if "custom/usb" in wb else None
    v["reset"] = wb["custom/reset"]["format"].split("  ", 1)[-1]
    v["layer"] = wb["layer"]
    mako = open(os.path.join(d, "etc-xdg-mako-config"), encoding="utf-8").read()
    v["popup_bg"] = re.search(r"^background-color=(\S+)", mako, re.M).group(1)
    v["popup_fg"] = re.search(r"^text-color=(\S+)", mako, re.M).group(1)
    v["popup_w"] = int(re.search(r"^width=(\d+)", mako, re.M).group(1))
    v["popup_h"] = int(re.search(r"^height=(\d+)", mako, re.M).group(1))
    v["popup_font"] = re.search(r"^font=(.+)$", mako, re.M).group(1)
    warn = open(os.path.join(d, "kiosk-idle-warn.sh"), encoding="utf-8").read()
    g = lambda k: re.search(r"^%s='(.*)'$" % k, warn, re.M).group(1).replace("'\\''", "'")
    v["idle_title"], v["idle_body"], v["idle_hint"] = g("TITLE"), g("BODY"), g("HINT")
    v["unit_many"] = g("UNIT_MANY")
    v["warn_secs"] = int(re.search(r"^SECS=(\d+)", warn, re.M).group(1))
    return v


def rgba(hex8):
    """#RRGGBBAA -> rgba() for CSS (mako notation)."""
    h = hex8.lstrip("#")
    if len(h) == 8:
        r, g, b, a = (int(h[i:i + 2], 16) for i in (0, 2, 4, 6))
        return f"rgba({r},{g},{b},{a / 255:.2f})"
    return "#" + h


ICON_CLOCK = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="{c}" stroke-width="2.4"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>'
ICON_USB = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><rect x="7" y="9" width="10" height="13"/><path d="M9 9V3h6v6"/></svg>'
ICON_POWER = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"><path d="M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0"/></svg>'


def bar_html(v):
    usb = (f'<span class="btn usb">{ICON_USB}&nbsp;&nbsp;{html.escape(v["usb"])}</span>' if v["usb"] else "")
    return f'''
<div class="bar">
  <img src="assets/{v["logo_light"]}" alt="">
  <span class="brand">{html.escape(v["brand"])}</span>
  <span class="hint">{ICON_CLOCK.format(c=v["accent"])}&nbsp;&nbsp;{html.escape(v["hint"])}</span>
  <span class="spacer"></span>
  <span class="clock">{html.escape(CLOCK.strftime(v["clock_fmt"]))}</span>
  <span class="host">{html.escape(v["host_fmt"].replace("{}", "kiosk-3a9f2c"))}</span>
  {usb}
  <span class="btn reset">{ICON_POWER}&nbsp;&nbsp;{html.escape(v["reset"])}</span>
</div>'''


def firefox_toolbar(title, url="file:///opt/kiosk/homepage/index.html", bookmarks=()):
    bm = "".join(f'<span class="bm">{"&#128193; " if folder else ""}{html.escape(b)}</span>' for b, folder in bookmarks)
    return f'''
<div class="ff">
  <div class="tabs"><span class="tab">{html.escape(title)}</span><span class="newtab">+</span></div>
  <div class="nav"><span class="nb">&#8592;</span><span class="nb dim">&#8594;</span><span class="nb">&#8635;</span>
    <span class="url"><span class="lock">&#128274;</span>{html.escape(url)}</span><span class="nb">&#9776;</span></div>
  <div class="bms">{bm}</div>
</div>'''


def popup_html(v, secs):
    body = v["idle_body"].replace("{seconds}", str(secs)).replace("{unit}", v["unit_many"])
    hint = v["idle_hint"].replace("{seconds}", str(secs)).replace("{unit}", v["unit_many"])
    return f'''
<div class="popup">
  <img src="assets/{v["logo_light"]}" alt="">
  <div><div class="ptitle">{html.escape(v["idle_title"])}</div>
    <div class="pbody">{body}</div>
    <div class="phint">{hint}</div></div>
</div>'''


def css(v, top):
    pf = re.match(r"(.+?)\s+(\d+)$", v["popup_font"])
    popup_family, popup_size = pf.group(1), int(pf.group(2))
    return f'''
<style>
  html, body {{ margin: 0; width: {W}px; height: {H}px; overflow: hidden; background: #fff; }}
  body {{ font-family: {v["font"]}; }}
  .bar {{ position: absolute; left: 0; top: 0; width: {W}px; height: 40px; box-sizing: content-box; display: flex; align-items: center;
         background: {v["bar_bg"]}; color: {v["bar_fg"]}; border-bottom: 3px solid {v["accent"]}; font-size: 15px; z-index: 10; }}
  .bar img {{ height: 28px; margin: 0 10px 0 14px; }}
  .bar .brand {{ font-weight: bold; font-size: 16px; padding: 0 16px 0 0; margin: 8px 0; line-height: 24px; border-right: 1px solid {v["divider"]}; }}
  .bar .hint {{ color: {v["muted"]}; padding: 0 16px; display: flex; align-items: center; }}
  .bar .spacer {{ flex: 1; }}
  .bar .clock {{ font-weight: bold; padding: 0 6px 0 12px; }}
  .bar .host {{ color: {v["dim"]}; font-size: 12px; padding: 0 16px 0 6px; }}
  .bar .btn {{ display: inline-flex; align-items: center; height: 32px; padding: 0 16px; margin: 4px 0 4px 6px; font-weight: bold; }}
  .bar .usb {{ background: {v["accent"]}; color: {v["on_accent"]}; }}
  .bar .reset {{ background: {v["danger"]}; color: #fff; margin-right: 8px; }}
  .ff {{ position: absolute; left: 0; top: {BAR_H}px; width: {W}px; height: {FF_TOOLBAR_H}px; background: #f9f9fb; color: #15141a;
        font-family: "DejaVu Sans", sans-serif; font-size: 14px; border-bottom: 1px solid #cfcfd8; box-sizing: border-box; }}
  .ff .tabs {{ height: 36px; background: #f0f0f4; display: flex; align-items: flex-end; padding: 0 8px; gap: 6px; }}
  .ff .tab {{ background: #fff; border-radius: 6px 6px 0 0; padding: 8px 16px; min-width: 240px; box-shadow: 0 -1px 0 #cfcfd8 inset; }}
  .ff .newtab {{ padding: 6px 10px; color: #5b5b66; }}
  .ff .nav {{ height: 44px; display: flex; align-items: center; gap: 10px; padding: 0 12px; }}
  .ff .nb {{ width: 28px; text-align: center; font-size: 18px; color: #5b5b66; }}
  .ff .nb.dim {{ color: #b0b0b8; }}
  .ff .url {{ flex: 1; height: 32px; background: #fff; border: 1px solid #cfcfd8; border-radius: 6px; display: flex; align-items: center; padding: 0 12px; color: #5b5b66; }}
  .ff .lock {{ margin-right: 8px; font-size: 12px; }}
  .ff .bms {{ height: 30px; display: flex; align-items: center; gap: 18px; padding: 0 14px; font-size: 13px; }}
  .content {{ position: absolute; left: 0; top: {top}px; width: {W}px; height: {H - top}px; border: 0; }}
  .popup {{ position: absolute; left: {(W - v["popup_w"]) // 2}px; top: {(H - v["popup_h"]) // 2}px; width: {v["popup_w"]}px; height: {v["popup_h"]}px;
           box-sizing: border-box; padding: 30px 40px; border: 4px solid {v["accent"]}; background: {rgba(v["popup_bg"])}; color: {v["popup_fg"]};
           font-family: "{popup_family}", {v["font"]}; font-size: {popup_size}px; display: flex; gap: 40px; align-items: center; z-index: 20; }}
  .popup img {{ width: 110px; height: 110px; flex: none; }}
  .popup .ptitle {{ font-weight: bold; margin-bottom: 14px; }}
  .popup .pbody {{ font-size: {int(popup_size * 1.2)}px; line-height: 1.3; }}
  .popup .phint {{ font-size: {int(popup_size * 0.83)}px; color: {v["muted"]}; margin-top: 14px; }}
</style>'''


def page(v, parts, top):
    return f'<!DOCTYPE html><html><head><meta charset="utf-8">{css(v, top)}</head><body>{"".join(parts)}</body></html>'


def write(name, text):
    with open(os.path.join(MOCK, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def shoot(name, out):
    url = "file://" + os.path.join(MOCK, name)
    subprocess.run(["chromium", "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                    "--allow-file-access-from-files", "--force-device-scale-factor=1",
                    f"--window-size={W},{H}", "--virtual-time-budget=3000",
                    f"--screenshot={os.path.join(OUT, out)}", url],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("   wrote", out)


def main():
    os.makedirs(MOCK, exist_ok=True)
    # assets next to the mockups (home pages reference assets/<file>)
    if os.path.isdir(os.path.join(MOCK, "assets")):
        shutil.rmtree(os.path.join(MOCK, "assets"))
    shutil.copytree(os.path.join(SITE, "assets"), os.path.join(MOCK, "assets"))

    # --- browser profile ------------------------------------------------------
    v = load_vars("browser")
    shutil.copy(os.path.join(RENDERED, "browser", "homepage-index.html"), os.path.join(MOCK, "home-browser.html"))
    pol = json.load(open(os.path.join(RENDERED, "browser", "policies.json"), encoding="utf-8"))["policies"]
    bms = [(pol["ManagedBookmarks"][0]["toplevel_name"], True)] + [(b["Title"], False) for b in pol.get("Bookmarks", [])]
    top = BAR_H + FF_TOOLBAR_H
    title = re.search(r"<title>(.*?)</title>", open(os.path.join(MOCK, "home-browser.html"), encoding="utf-8").read()).group(1)
    frame = f'<iframe class="content" src="home-browser.html"></iframe>'
    write("browser-home.html", page(v, [bar_html(v), firefox_toolbar(html.unescape(title), bookmarks=bms), frame], top))
    shoot("browser-home.html", "browser-home.png")
    write("browser-idle-popup.html", page(v, [bar_html(v), firefox_toolbar(html.unescape(title), bookmarks=bms), frame,
                                              popup_html(v, v["warn_secs"] - 13)], top))
    shoot("browser-idle-popup.html", "browser-idle-popup.png")

    # USB manager page rendered by the real script with sample data
    media = os.path.join(MOCK, "media"); dl = os.path.join(MOCK, "downloads")
    for d in (media, dl):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(media, "MY_STICK", "Theses"), exist_ok=True); os.makedirs(dl)
    for name, size in (("MY_STICK/Score - Bach BWV 1007.pdf", 180_000), ("MY_STICK/Essay draft.docx", 42_000),
                       ("MY_STICK/Theses/.keep", 0), ("MY_STICK/holiday.jpg", 2_400_000)):
        with open(os.path.join(media, name), "wb") as fh:
            fh.write(b"%PDF-" if name.endswith(".pdf") else b"x")
            fh.truncate(size)
    for name, size in (("Reading list.pdf", 96_000), ("Lecture notes.epub", 1_200_000)):
        with open(os.path.join(dl, name), "wb") as fh:
            fh.write(b"%PDF-" if name.endswith(".pdf") else b"PK\x03\x04"); fh.truncate(size)
    os.environ.update(KIOSK_MEDIA_ROOT=media, KIOSK_DOWNLOADS=dl,
                      KIOSK_USB_CONFIG=os.path.join(RENDERED, "browser", "usbmanager.json"))
    spec = importlib.util.spec_from_file_location("usbmanager", "/work/roles/usbmanager/files/usbmanager.py")
    usb = importlib.util.module_from_spec(spec); spec.loader.exec_module(usb)
    # the sample directory is not a mount point: present it as one drive
    stick = os.path.join(media, "MY_STICK")
    usb.mounted_sticks = lambda: [(stick, "/dev/sdb1", "MY_STICK")]
    usb_html = usb.render("MY_STICK", "", "", False).replace('src="/assets/logo"', f'src="assets/{usb.STYLE["logo"]}"')
    usb_html = re.sub(r"<script>.*?</script>", "", usb_html, flags=re.S)
    write("usb.html", usb_html)
    write("browser-usb-manager.html", page(v, [bar_html(v), firefox_toolbar("USB drive — Demo Kiosk", "http://127.0.0.1:8080/", bms),
                                               '<iframe class="content" src="usb.html"></iframe>'], top))
    shoot("browser-usb-manager.html", "browser-usb-manager.png")

    # --- app profile -----------------------------------------------------------
    v = load_vars("app")
    shutil.copy(os.path.join(RENDERED, "app", "homepage-index.html"), os.path.join(MOCK, "home-app.html"))
    write("app-home.html", page(v, [bar_html(v), '<iframe class="content" src="home-app.html"></iframe>'], BAR_H))
    shoot("app-home.html", "app-home.png")

    # --- display profile: the real page with a sample board injected -----------
    v = load_vars("display")
    board = sample_board()
    disp = open(os.path.join(RENDERED, "display", "homepage-index.html"), encoding="utf-8").read()
    disp = disp.replace("poll();", 'show("image/svg+xml", ' + json.dumps(board) + '); document.getElementById("status").classList.add("hidden");')
    write("home-display.html", disp)
    write("display-board.html", f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden}}'
          f'iframe{{border:0;width:{W}px;height:{H}px}}</style></head><body><iframe src="home-display.html"></iframe></body></html>')
    shoot("display-board.html", "display-board.png")

    # --- screensaver: wallpaper with the real letterbox colour ------------------
    wp = sorted(os.listdir(os.path.join(SITE, "wallpaper", "1920x1080")))[0]
    shutil.copy(os.path.join(SITE, "wallpaper", "1920x1080", wp), os.path.join(OUT, "screensaver.png"))
    print("   wrote screensaver.png (copy of", wp + ")")


def sample_board():
    """A neutral signage board (events of the day) as SVG — what a signage
    server would deliver."""
    rows = [("09:00", "Room 101", "Chamber music rehearsal"), ("11:30", "Hall", "Open lecture: Baroque ornamentation"),
            ("14:00", "Studio B", "Workshop: Stage lighting"), ("17:00", "Room 203", "Composition seminar"),
            ("19:30", "Hall", "Evening concert — student ensembles")]
    y = 300
    items = ""
    for t, room, title in rows:
        items += (f'<text x="120" y="{y}" font-size="44" font-weight="700" fill="#0E7C86">{t}</text>'
                  f'<text x="330" y="{y}" font-size="40" fill="#111">{html.escape(title)}</text>'
                  f'<text x="1560" y="{y}" font-size="36" fill="#5c5c5c">{room}</text>'
                  f'<line x1="120" y1="{y + 34}" x2="1800" y2="{y + 34}" stroke="#e0e0dc" stroke-width="2"/>')
        y += 120
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080" font-family="URW Gothic, DejaVu Sans, sans-serif">
<rect width="1920" height="1080" fill="#ffffff"/>
<rect x="0" y="0" width="1920" height="150" fill="#0E7C86"/>
<text x="120" y="98" font-size="64" font-weight="700" fill="#ffffff">Today at the Demo Academy</text>
<text x="1800" y="98" font-size="44" fill="#ffffff" text-anchor="end">Wed 21 Oct 2026</text>
{items}
<text x="120" y="1010" font-size="28" fill="#5c5c5c">Changes and room swaps are shown here as soon as they are entered.</text>
</svg>'''


if __name__ == "__main__":
    main()
