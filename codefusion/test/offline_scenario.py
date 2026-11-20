"""Hostel Wi-Fi scenario for DRAGON, driven through the editor UI in real Chrome.

Four independent sessions (separate browser profiles, like four laptops). Dev reaches the
server through test/netproxy.js, so the test can break Dev's network for real:
drop (Wi-Fi off), blackhole (Wi-Fi "connected" but dead) and holddown (server replies held).

Start first:
  node server.js                                  # sync server on 8787
  node test/netproxy.js 8790 8787 8791            # Dev's Wi-Fi
Run:
  python test/offline_scenario.py [http://localhost:8787] [chrome] [http://localhost:8790] [http://127.0.0.1:8791]
"""
import sys, time, urllib.request
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from accounts_helper import make_account, login_script   # every test user signs in with a real account
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

arg = lambda i, d: sys.argv[i] if len(sys.argv) > i and sys.argv[i] else d
BASE = arg(1, "http://localhost:8787")
CHROME = arg(2, None)
PROXY = arg(3, "http://localhost:8790")
CONTROL = arg(4, "http://127.0.0.1:8791")
T0 = time.time()
results = []

def log(msg):
    print(f"   [{time.time() - T0:6.1f}s] {msg}")

def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(detail)) if detail and not cond else ""))
    return bool(cond)

def net(mode):
    urllib.request.urlopen(f"{CONTROL}/mode/{mode}", timeout=5).read()
    log(f"Dev's network: {mode}")

# Records every change of the status message and every message from the server, with
# the page's own clock, so the test can prove "Synced" only follows a server reply.
INIT = r"""
(() => {
  window.__statusLog = []; window.__wsLog = [];
  const hook = () => {
    const el = document.getElementById('syncBadge');
    if (!el || el.__hooked) return; el.__hooked = true;
    const rec = () => window.__statusLog.push([performance.now(), el.textContent]);
    rec();
    new MutationObserver(rec).observe(el, { childList: true, characterData: true, subtree: true });
  };
  document.addEventListener('readystatechange', hook);
  document.addEventListener('DOMContentLoaded', hook);
  const Native = window.WebSocket;
  function Logged(url, protocols) {
    const s = protocols === undefined ? new Native(url) : new Native(url, protocols);
    s.addEventListener('message', e => { try { window.__wsLog.push([performance.now(), JSON.parse(e.data).t]); } catch (err) {} });
    return s;
  }
  Logged.prototype = Native.prototype;
  Logged.CONNECTING = 0; Logged.OPEN = 1; Logged.CLOSING = 2; Logged.CLOSED = 3;
  window.WebSocket = Logged;
})();
"""

def text(pg): return pg.locator("#code").input_value()
def badge(pg): return pg.locator("#syncBadge").inner_text()
def conn(pg): return pg.locator("#connBadge").inner_text()
def people(pg): return pg.locator("#collabPeople .person-chip span").all_inner_texts()
def carets(pg): return pg.locator(".remote-caret span").all_inner_texts()
def now_ms(pg): return pg.evaluate("performance.now()")
def status_log(pg): return pg.evaluate("window.__statusLog")
def ws_log(pg): return pg.evaluate("window.__wsLog")

def wait_until(fn, timeout=10.0, step=0.15):
    end = time.time() + timeout
    while True:
        try:
            v = fn()
            if v: return v
        except Exception:
            v = None
        if time.time() > end: return v
        time.sleep(step)

def wait_same(pages, timeout=20.0):
    ok = wait_until(lambda: len({text(p) for p in pages}) == 1, timeout)
    return bool(ok), text(pages[0])

def type_at(pg, s, where="End"):
    pg.locator("#code").click()          # a click also starts a new undo step
    pg.keyboard.press("Control+" + where)
    pg.keyboard.type(s)

def synced(pg): return badge(pg) == "Synced" and conn(pg) == "Online"

def synced_violations(statuses, messages):
    """Every switch to "Synced" must follow a server reply (ack/welcome/resync)
    received after the previous status change."""
    bad = []
    for i in range(1, len(statuses)):
        t, s = statuses[i]
        if s != "Synced" or statuses[i - 1][1] == "Synced": continue
        prev = statuses[i - 1][0]
        if not any(prev <= wt <= t and kind in ("ack", "welcome", "resync") for wt, kind in messages):
            bad.append((round(t), statuses[i - 1][1]))
    return bad

with sync_playwright() as p:
    launch = {"args": ["--no-sandbox"]}
    if CHROME: launch["executable_path"] = CHROME
    browser = p.chromium.launch(**launch)
    errors, logs_before_reload = [], []

    def session(name):
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        ctx.add_init_script(login_script(make_account(BASE, name)))
        ctx.add_init_script(INIT)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(name + ": " + str(e)))
        prompts = []
        pg.on("dialog", lambda d: (prompts.append(d.default_value), d.accept()))
        pg.prompts = prompts
        return ctx, pg

    # ------------------------------------------------------------------ 1. room link
    print("\n== 1. Asha creates a project and shares its room link")
    actx, asha = session("Asha")
    actx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
    asha.goto(f"{BASE}/?name=Asha")
    wait_until(lambda: synced(asha), 10)
    room = parse_qs(urlparse(asha.url).query).get("room", [""])[0]
    check("opening the app gives the project a room ID in the address bar", room.startswith("p-"), asha.url)
    type_at(asha, " @@ASHA-1@@")
    wait_until(lambda: synced(asha), 5)
    asha.reload()
    wait_until(lambda: "@@ASHA-1@@" in text(asha) and synced(asha), 10)
    check("room link is stable: a refresh stays in the same project", parse_qs(urlparse(asha.url).query).get("room", [""])[0] == room and text(asha).count("@@ASHA-1@@") == 1, asha.url)
    asha.locator("#shareRoom").click()
    time.sleep(0.4)
    toasts = asha.locator(".toast").all_inner_texts()
    shared = next((t.split("copied: ", 1)[1].strip() for t in toasts if "copied: " in t), asha.prompts[-1] if asha.prompts else "")
    check("Share link gives the stable room link", shared == f"{BASE}/?room={room}", shared)
    log(f"room link: {shared}")

    # ------------------------------------------------------------------ 2. four sessions
    print("\n== 2. Four independent sessions join through that link")
    bctx, bilal = session("Bilal"); bilal.goto(shared + "&name=Bilal")
    cctx, chen = session("Chen"); chen.goto(shared + "&name=Chen")
    dctx, dev = session("Dev"); dev.goto(shared.replace(BASE, PROXY) + "&name=Dev")
    everyone = [asha, bilal, chen, dev]
    check("all 4 sessions show Synced and Online", wait_until(lambda: all(synced(pg) for pg in everyone), 15), [(badge(pg), conn(pg)) for pg in everyone])
    check("all 4 see the same project", wait_same(everyone)[0])
    for pg in everyone: pg.locator("#code").click(); pg.keyboard.press("Control+End")
    check("each person sees the 3 others by name", wait_until(lambda: all(len(people(pg)) == 3 for pg in everyone), 8), [people(pg) for pg in everyone])
    check("Bilal sees named live cursors for Asha, Chen and Dev", wait_until(lambda: sorted(carets(bilal)) == ["Asha", "Chen", "Dev"], 8), carets(bilal))
    sw_ready = dev.evaluate("""async () => { await navigator.serviceWorker.ready;
        for (let i = 0; i < 50; i++) { if (await caches.match('./')) return true; await new Promise(r => setTimeout(r, 100)); }
        return false; }""")
    check("Dev's browser has cached the app for offline use", sw_ready)
    bilal.locator("#code").click(); bilal.keyboard.press("Control+Home"); bilal.keyboard.press("Shift+End")
    check("Bilal's selection is shown to the others", wait_until(lambda: chen.locator(".remote-sel").count() > 0, 5))

    # ------------------------------------------------------------------ 3. creator leaves
    print("\n== 3. Asha (the creator) closes her tab")
    asha.close()
    actx.close()
    rest = [bilal, chen, dev]
    check("Asha's name and cursor disappear for everyone else", wait_until(lambda: all("Asha" not in people(pg) and "Asha" not in carets(pg) for pg in rest), 8), [(people(pg), carets(pg)) for pg in rest])
    type_at(bilal, " @@B-AFTER-CLOSE@@"); type_at(chen, " @@C-AFTER-CLOSE@@"); type_at(dev, " @@D-AFTER-CLOSE@@")
    same, t = wait_same(rest)
    check("the other 3 keep editing and syncing after the creator left", same and all(t.count(m) == 1 for m in ["@@B-AFTER-CLOSE@@", "@@C-AFTER-CLOSE@@", "@@D-AFTER-CLOSE@@", "@@ASHA-1@@"]), t[-200:])
    check("their status reaches Synced", wait_until(lambda: all(synced(pg) for pg in rest), 8), [badge(pg) for pg in rest])

    # ------------------------------------------------------------------ 4. Dev's Wi-Fi drops
    print("\n== 4. Dev's Wi-Fi drops; Dev and Bilal edit the same file")
    net("drop"); dctx.set_offline(True)
    off_at = now_ms(dev)
    check("Dev sees that the connection is gone", wait_until(lambda: conn(dev).startswith("Offline"), 6), conn(dev))
    check("Dev's stale cursor is removed for Bilal and Chen", wait_until(lambda: all("Dev" not in people(pg) and "Dev" not in carets(pg) for pg in [bilal, chen]), 8), [(people(pg), carets(pg)) for pg in [bilal, chen]])
    check("Dev no longer shows teammates' cursors (they would be stale)", wait_until(lambda: carets(dev) == [] and people(dev) == [], 5), carets(dev))
    type_at(dev, " @@D-OFF-1@@")
    time.sleep(0.7)
    type_at(dev, "@@D-OFF-HOME@@", "Home")
    type_at(bilal, " @@B-ON-1@@")
    check("Dev's status: Saved on this device", wait_until(lambda: badge(dev) == "Saved on this device", 4), badge(dev))
    st = [s for t, s in status_log(dev) if t >= off_at]
    check("Dev saw \"Saving on this device\" before \"Saved on this device\"", "Saving on this device" in st and st[-1] == "Saved on this device", st)
    check("Dev never showed Synced while offline", "Synced" not in st, st)
    time.sleep(1)
    check("the offline edit has not reached Bilal/Chen yet", all("@@D-OFF-1@@" not in text(pg) for pg in [bilal, chen]))
    check("Bilal's online edit has not reached Dev yet", "@@B-ON-1@@" not in text(dev))

    # ------------------------------------------------------------------ 5. offline refresh
    print("\n== 5. Dev refreshes the page while still offline")
    logs_before_reload.append((status_log(dev), ws_log(dev)))
    dev.reload()
    reopened = wait_until(lambda: "@@D-OFF-1@@" in text(dev), 10)
    check("the editor reopens with no network (service worker)", reopened)
    check("Dev's offline edits survived the refresh (IndexedDB)", text(dev).count("@@D-OFF-1@@") == 1 and text(dev).count("@@D-OFF-HOME@@") == 1)
    check("the file Dev had open is open again", "index.html" in dev.locator("#fileName").inner_text() if dev.locator("#fileName").count() else True)
    check("after the refresh: Saved on this device / Offline", wait_until(lambda: badge(dev) == "Saved on this device" and conn(dev).startswith("Offline"), 5), (badge(dev), conn(dev)))
    type_at(dev, " @@D-OFF-2@@")
    type_at(bilal, " @@B-ON-2@@")
    check("Dev can keep typing after the offline refresh", wait_until(lambda: "@@D-OFF-2@@" in text(dev) and badge(dev) == "Saved on this device", 4), badge(dev))

    # ------------------------------------------------------------------ 6. reconnect
    print("\n== 6. Dev's Wi-Fi comes back")
    net("pass"); dctx.set_offline(False)
    back_at = now_ms(dev)
    same, t = wait_same(rest, 20)
    log("converged" if same else "NOT converged")
    check("all connected editors show identical text", same, [text(pg)[-120:] for pg in rest])
    markers = ["@@ASHA-1@@", "@@B-AFTER-CLOSE@@", "@@C-AFTER-CLOSE@@", "@@D-AFTER-CLOSE@@", "@@D-OFF-1@@", "@@D-OFF-HOME@@", "@@D-OFF-2@@", "@@B-ON-1@@", "@@B-ON-2@@"]
    check("online and offline edits each appear exactly once", all(t.count(m) == 1 for m in markers), {m: t.count(m) for m in markers})
    check("the rest of the file is intact (no duplicated text)", t.count("<body>") == 1 and t.count("</html>") == 1 and t.count("<!DOCTYPE html>") == 1)
    check("everyone shows Synced and Online", wait_until(lambda: all(synced(pg) for pg in rest), 10), [(badge(pg), conn(pg)) for pg in rest])
    st = [(tt, s) for tt, s in status_log(dev) if tt >= back_at]
    first_synced = next((tt for tt, s in st if s == "Synced"), None)
    first_ack = next((wt for wt, k in ws_log(dev) if wt >= back_at and k == "ack"), None)
    check("after reconnecting, Dev shows Synced only once the server acknowledged the offline edits",
          first_synced is not None and first_ack is not None and first_ack <= first_synced and st[-1][1] == "Synced", [s for _, s in st])
    check("Bilal and Chen see Dev again", wait_until(lambda: all("Dev" in people(pg) for pg in [bilal, chen]), 8))

    # ------------------------------------------------------------------ 7. undo offline edits after reconnect
    print("\n== 7. Dev undoes the edits made offline (one before the refresh), after reconnecting")
    dev.locator("#code").focus()
    order = ["@@D-OFF-2@@", "@@D-OFF-HOME@@", "@@D-OFF-1@@"]
    ok = True
    for i, m in enumerate(order):
        dev.keyboard.press("Control+z")
        same, t = wait_same(rest, 8)
        gone = all(t.count(x) == 0 for x in order[: i + 1]) and all(t.count(x) == 1 for x in order[i + 1:])
        keep = t.count("@@B-ON-1@@") == 1 and t.count("@@B-ON-2@@") == 1
        ok = ok and same and gone and keep
    check("Ctrl+Z removes Dev's offline edits one by one on every screen, even the one from before the refresh", ok)
    check("Bilal's online edits were never touched by Dev's undo", t.count("@@B-ON-1@@") == 1 and t.count("@@B-ON-2@@") == 1)
    for _ in order: dev.keyboard.press("Control+Shift+z")
    same, t = wait_same(rest, 8)
    check("Ctrl+Shift+Z brings all three back, once each", same and all(t.count(m) == 1 for m in markers), {m: t.count(m) for m in markers})

    # ------------------------------------------------------------------ 8. Synced waits for the ack
    print("\n== 8. Synced only after the server acknowledges")
    wait_until(lambda: synced(dev), 8)
    net("holddown")
    type_at(dev, " @@HOLD@@")
    reached = wait_until(lambda: "@@HOLD@@" in text(bilal), 5)
    time.sleep(1.0)
    check("server already has Dev's edit (Bilal sees it) but Dev still shows Syncing", reached and badge(dev) == "Syncing", badge(dev))
    net("pass")
    check("Dev shows Synced once the acknowledgement arrives", wait_until(lambda: badge(dev) == "Synced", 4), badge(dev))

    # ------------------------------------------------------------------ 9. repeated disconnects and retries
    print("\n== 9. Repeated disconnects and retries")
    # a) the acknowledgement is lost: the server has the edit, Dev never hears back
    net("holddown"); type_at(dev, " @@R1@@")
    wait_until(lambda: "@@R1@@" in text(bilal), 5)
    net("drop"); dctx.set_offline(True)
    wait_until(lambda: conn(dev).startswith("Offline"), 6)
    type_at(dev, " @@R1-OFF@@"); type_at(bilal, " @@R1-B@@")
    net("pass"); dctx.set_offline(False)
    same, t = wait_same(rest, 20)
    check("lost acknowledgement: no edit duplicated after the retry", same and all(t.count(m) == 1 for m in ["@@R1@@", "@@R1-OFF@@", "@@R1-B@@"]), {m: t.count(m) for m in ["@@R1@@", "@@R1-OFF@@", "@@R1-B@@"]})
    # b) the connection breaks right after typing, while the browser still thinks it is online
    type_at(dev, " @@R2@@"); net("drop")
    type_at(bilal, " @@R2-B@@"); type_at(dev, " @@R2-D@@")
    time.sleep(2); net("pass")
    same, t = wait_same(rest, 25)
    check("connection cut mid-send: everything arrives exactly once", same and all(t.count(m) == 1 for m in ["@@R2@@", "@@R2-B@@", "@@R2-D@@"]), {m: t.count(m) for m in ["@@R2@@", "@@R2-B@@", "@@R2-D@@"]})
    # c) silent dead Wi-Fi: no close, no error, just nothing
    wait_until(lambda: synced(dev), 10)
    net("blackhole")
    type_at(dev, " @@R3@@"); type_at(bilal, " @@R3-B@@")
    check("dead Wi-Fi: Dev shows Syncing while waiting for an acknowledgement", wait_until(lambda: badge(dev) == "Syncing", 3), badge(dev))
    gave_up = wait_until(lambda: badge(dev) == "Saved on this device", 20)
    log("Dev detected the dead connection" if gave_up else "Dev did NOT detect the dead connection")
    check("dead Wi-Fi is detected and Dev falls back to Saved on this device", gave_up, (badge(dev), conn(dev)))
    check("the server drops Dev's dead connection and Bilal stops seeing Dev's cursor", wait_until(lambda: "Dev" not in people(bilal) and "Dev" not in carets(bilal), 35), people(bilal))
    net("pass")
    same, t = wait_same(rest, 30)
    check("after dead Wi-Fi recovers: identical, each edit once", same and all(t.count(m) == 1 for m in ["@@R3@@", "@@R3-B@@"]), {m: t.count(m) for m in ["@@R3@@", "@@R3-B@@"]})
    # d) flapping: drop and restore several times while typing
    flap = []
    for i in range(4):
        m = f"@@F{i}@@"; flap.append(m)
        type_at(dev, " " + m); net("drop"); time.sleep(0.4); net("pass"); time.sleep(0.6)
    same, t = wait_same(rest, 30)
    check("flapping connection (4 quick drops): each edit exactly once", same and all(t.count(m) == 1 for m in flap), {m: t.count(m) for m in flap})
    check("all markers so far still exactly once", all(t.count(m) == 1 for m in markers + ["@@HOLD@@"]))
    check("everyone back to Synced and Online", wait_until(lambda: all(synced(pg) for pg in rest), 20), [(badge(pg), conn(pg)) for pg in rest])

    # ------------------------------------------------------------------ 10. per-user undo after reconnect
    print("\n== 10. Per-user undo after reconnect (separate test)")
    net("drop"); dctx.set_offline(True)
    wait_until(lambda: conn(dev).startswith("Offline"), 6)
    type_at(dev, " @@UNDO-D-OFFLINE@@")
    type_at(bilal, " @@KEEP-B-ONLINE@@")
    type_at(chen, " @@KEEP-C-ONLINE@@")
    net("pass"); dctx.set_offline(False)
    same, t = wait_same(rest, 20)
    check("setup: offline and online edits merged", same and all(t.count(m) == 1 for m in ["@@UNDO-D-OFFLINE@@", "@@KEEP-B-ONLINE@@", "@@KEEP-C-ONLINE@@"]))
    dev.locator("#code").focus(); dev.keyboard.press("Control+z")
    same, t = wait_same(rest, 8)
    check("Dev's Ctrl+Z removes only Dev's offline edit, on every screen", same and "@@UNDO-D-OFFLINE@@" not in t and t.count("@@KEEP-B-ONLINE@@") == 1 and t.count("@@KEEP-C-ONLINE@@") == 1, t[-160:])
    bilal.locator("#code").focus(); bilal.keyboard.press("Control+z")
    same, t = wait_same(rest, 8)
    check("Bilal's Ctrl+Z removes only Bilal's edit", same and "@@KEEP-B-ONLINE@@" not in t and t.count("@@KEEP-C-ONLINE@@") == 1 and "@@UNDO-D-OFFLINE@@" not in t, t[-160:])
    dev.locator("#code").focus(); dev.keyboard.press("Control+Shift+z")
    same, t = wait_same(rest, 8)
    check("Dev's redo restores Dev's edit only (Bilal's undo stays undone)", same and t.count("@@UNDO-D-OFFLINE@@") == 1 and "@@KEEP-B-ONLINE@@" not in t and t.count("@@KEEP-C-ONLINE@@") == 1, t[-160:])
    chen.locator("#code").focus(); chen.keyboard.press("Control+z")
    same, t = wait_same(rest, 8)
    check("Chen's Ctrl+Z removes only Chen's edit", same and "@@KEEP-C-ONLINE@@" not in t and t.count("@@UNDO-D-OFFLINE@@") == 1, t[-160:])

    # ------------------------------------------------------------------ 11. status honesty
    print("\n== 11. Status messages")
    wait_until(lambda: all(synced(pg) for pg in rest), 10)
    allowed = {"Saving on this device", "Saved on this device", "Syncing", "Synced"}
    sessions_logs = [("Bilal", status_log(bilal), ws_log(bilal)), ("Chen", status_log(chen), ws_log(chen)), ("Dev", status_log(dev), ws_log(dev))]
    sessions_logs += [("Dev before refresh", s, w) for s, w in logs_before_reload]
    bad = {name: synced_violations(s, w) for name, s, w in sessions_logs}
    check("every switch to Synced came right after a server acknowledgement", all(not v for v in bad.values()), bad)
    seen = set(x for _, s, _ in sessions_logs for _, x in s)
    check("only the four status messages were ever shown", seen <= allowed, seen)
    check("all four messages were seen during the test", seen == allowed, seen)
    counts = {name: sum(1 for _, x in s if x == "Synced") for name, s, _ in sessions_logs}
    log(f"times each session switched to Synced: {counts}")

    check("no JavaScript errors in any session", not errors, errors[:3])
    browser.close()

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} checks passed in {time.time() - T0:.0f}s")
for name, ok in results:
    if not ok: print("FAILED:", name)
sys.exit(0 if passed == len(results) else 1)
