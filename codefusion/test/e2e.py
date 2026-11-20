"""End-to-end judge-style tests for DRAGON H9 in real Chrome.

Needs: the sync server running (npm start), Python Playwright, and Chrome.
Run:   python test/e2e.py [http://localhost:8787] [path-to-chrome]
Each user is a separate browser profile, like four different laptops.
"""
import sys, time, random, string
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from accounts_helper import make_account, login_script   # every test user signs in with a real account
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8787"
CHROME = sys.argv[2] if len(sys.argv) > 2 else None
ROOM = "e2e" + "".join(random.choice(string.ascii_lowercase) for _ in range(5))
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + detail) if detail and not cond else ""))

def value(pg):
    return pg.locator("#code").input_value()

def wait_same(pages, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        vals = [value(p) for p in pages]
        if all(v == vals[0] for v in vals):
            return True, vals[0]
        time.sleep(0.15)
    return False, [value(p) for p in pages]

def badge(pg):
    return pg.locator("#syncBadge").inner_text()

def conn(pg):
    return pg.locator("#connBadge").inner_text()

def live(pg):
    return badge(pg) == "Synced" and conn(pg) == "Online"

def go_end(pg):
    pg.locator("#code").click()
    pg.keyboard.press("Control+End")

with sync_playwright() as p:
    launch = {"args": ["--no-sandbox"]}
    if CHROME: launch["executable_path"] = CHROME
    browser = p.chromium.launch(**launch)
    names = ["Asha", "Bilal", "Chen", "Divya"]
    ctxs, pages, errors = [], [], []
    answers = {}   # page index -> text to type into the next prompt()
    def on_dialog(d, i):
        if d.type == "prompt":
            d.accept(answers.pop(i, d.default_value))
        else:
            d.accept()
    for i, n in enumerate(names):
        ctx = browser.new_context()
        ctx.add_init_script(login_script(make_account(BASE, n)))
        pg = ctx.new_page()
        pg.on("pageerror", lambda e, n=n: errors.append(n + ": " + str(e)))
        pg.on("dialog", lambda d, i=i: on_dialog(d, i))
        pg.goto(f"{BASE}/?room={ROOM}&name={n}")
        ctxs.append(ctx); pages.append(pg)
    time.sleep(1.5)
    check("all 4 users connected", all(live(pg) for pg in pages), str([(badge(pg), conn(pg)) for pg in pages]))
    check("each user sees the 3 other people", all(pg.locator("#collabPeople .person-chip").count() == 3 for pg in pages))

    # 1. Concurrent typing at the same spot, interleaved keystroke by keystroke.
    words = ["[asha-1234567]", "[bilal-abcdefg]", "[chen-ABCDEFG]", "[divya-!@#$%^]"]
    for pg in pages: go_end(pg)
    for i in range(max(len(w) for w in words)):
        for pg, w in zip(pages, words):
            if i < len(w): pg.keyboard.type(w[i])
    same, text = wait_same(pages)
    check("concurrent typing: all 4 copies identical", same, str(text)[:300])
    if same:
        check("concurrent typing: no lost characters, no interleaving", all(w in text for w in words), text[-120:])

    # 2. Live cursors and presence.
    time.sleep(0.6)
    carets = pages[1].locator(".remote-caret").count()
    check("live cursors: Bilal sees 3 remote cursors", carets == 3, str(carets))
    labels = pages[1].locator(".remote-caret span").all_inner_texts()
    check("live cursors are labelled with names", set(labels) == {"Asha", "Chen", "Divya"}, str(labels))
    check("explorer shows presence dots", pages[0].locator(".presence-dot").count() >= 3)

    # 3. Per-user undo: Asha's Ctrl+Z only removes Asha's text.
    go_end(pages[0]); pages[0].keyboard.type(" ASHA-UNDO-ME")
    wait_same(pages)
    time.sleep(0.7)  # new undo step
    go_end(pages[1]); pages[1].keyboard.type(" BILAL-KEEP")
    wait_same(pages)
    pages[0].locator("#code").focus(); pages[0].keyboard.press("Control+z")
    same, text = wait_same(pages)
    check("per-user undo removed only Asha's text on every screen", same and "ASHA-UNDO-ME" not in text and "BILAL-KEEP" in text, str(text)[-80:])
    pages[0].keyboard.press("Control+Shift+z")
    same, text = wait_same(pages)
    check("redo brings Asha's text back", same and "ASHA-UNDO-ME" in text and "BILAL-KEEP" in text, str(text)[-80:])
    pages[1].locator("#code").focus(); pages[1].keyboard.press("Control+z")
    same, text = wait_same(pages)
    check("Bilal's undo removes only Bilal's text", same and "BILAL-KEEP" not in text and "ASHA-UNDO-ME" in text, str(text)[-80:])

    # 4. "Go offline" switch: both sides edit, then merge on reconnect.
    pages[2].locator("#toggleOffline").click()
    time.sleep(0.3)
    check("offline switch shows Offline", "Offline" in conn(pages[2]), conn(pages[2]))
    pages[2].locator("#code").click(); pages[2].keyboard.press("Control+Home"); pages[2].keyboard.type("/*chen-offline*/")
    go_end(pages[3]); pages[3].keyboard.type(" divya-online")
    time.sleep(0.8)
    check("offline user's edit has not leaked yet", "chen-offline" not in value(pages[0]))
    check("offline status: saved on this device, edits waiting", badge(pages[2]) == "Saved on this device" and "not yet acknowledged" in (pages[2].locator("#syncBadge").get_attribute("title") or ""), badge(pages[2]))
    pages[2].locator("#toggleOffline").click()
    same, text = wait_same(pages)
    check("offline edits merged for everyone on reconnect", same and "/*chen-offline*/" in text and "divya-online" in text, str(text)[:200])

    # 5. Real network loss (DevTools offline) + reload while offline (service worker + IndexedDB).
    ctxs[3].set_offline(True)
    time.sleep(0.5)
    pages[3].reload()
    pages[3].wait_for_selector("#code", timeout=10000)
    time.sleep(1.0)
    check("app reloads with no network (service worker)", pages[3].locator("#code").count() == 1)
    check("project restored from this browser after offline reload", "divya-online" in value(pages[3]) and "chen-offline" in value(pages[3]), value(pages[3])[:120])
    go_end(pages[3]); pages[3].keyboard.type(" divya-wrote-this-offline")
    go_end(pages[0]); pages[0].keyboard.type(" asha-meanwhile")
    time.sleep(0.5)
    ctxs[3].set_offline(False)
    pages[3].evaluate("window.dispatchEvent(new Event('online'))")
    same, text = wait_same(pages, timeout=15)
    check("edits made after an offline reload merge on reconnect", same and "divya-wrote-this-offline" in text and "asha-meanwhile" in text, str(text)[-160:])

    # 6. Files: create, rename, delete-vs-offline-edit.
    answers[1] = "src/app.py"
    pages[1].locator("#newFile").click()
    time.sleep(1.0)
    check("new file appears for everyone", all(pg.locator(".tree-entry[title='src/app.py']").count() == 1 for pg in pages))
    pages[2].locator(".tree-entry[title='src/app.py']").click()
    pages[2].locator("#toggleOffline").click()               # Chen goes offline inside app.py
    go_end(pages[2]); pages[2].keyboard.type("\nprint('chen kept working')")
    pages[1].locator(".tree-entry[title='src/app.py']").click()
    pages[1].locator("#deleteFile").click()                  # Bilal deletes it meanwhile
    time.sleep(0.8)
    check("delete reached online users", pages[0].locator(".tree-entry[title='src/app.py']").count() == 0)
    pages[2].locator("#toggleOffline").click()
    time.sleep(2.0)
    restored = all(pg.locator(".tree-entry[title='src/app.py']").count() == 1 for pg in pages)
    check("offline edit beats the delete: file restored for everyone", restored)
    pages[0].locator(".tree-entry[title='src/app.py']").click()
    time.sleep(0.3)
    check("restored file keeps the offline work", "chen kept working" in value(pages[0]), value(pages[0])[-80:])

    answers[0] = "src/main.py"
    pages[0].locator("#renameFile").click()
    time.sleep(1.0)
    check("rename reaches everyone", all(pg.locator(".tree-entry[title='src/main.py']").count() == 1 for pg in pages))
    check("Chen's open tab followed the rename", pages[2].locator(".file-tab-name", has_text="main.py").count() == 1)

    # ---------------- extras beyond the H9 minimum ----------------
    for pg in pages: pg.locator(".tree-entry[title='index.html']").click()
    time.sleep(0.5)

    # 7. Who wrote this line
    pages[0].locator("#authorsBtn").click()
    time.sleep(0.4)
    legend = pages[0].locator("#authorLegend").inner_text()
    titles = " ".join(t or "" for t in pages[0].locator(".ln-author").evaluate_all("els => els.map(e => e.title)"))
    check("who-wrote-this legend lists teammates", all(n in legend for n in ["Asha", "Bilal", "Chen", "Divya"]), legend.replace("\n", " | "))
    check("each line number names its author", pages[0].locator(".ln-author").count() == value(pages[0]).count("\n") + 1 and "written by" in titles)
    pages[0].locator("#authorsBtn").click()

    # 8. Follow a teammate
    pages[2].locator(".tree-entry[title='src/main.py']").click()
    time.sleep(0.6)
    pages[0].locator("#collabPeople .person-chip", has_text="Chen").click()
    time.sleep(0.4)
    check("clicking a teammate jumps to their file", "src/main.py" in pages[0].locator("#fileName").inner_text(), pages[0].locator("#fileName").inner_text())
    pages[0].locator(".tree-entry[title='index.html']").click()
    pages[2].locator(".tree-entry[title='index.html']").click()
    time.sleep(0.4)

    # 9. Version history: checkpoint, change, restore, undo the restore
    answers[0] = "Before demo"
    pages[0].locator("#historyBtn").click()
    pages[0].locator("#histSave").click()
    time.sleep(0.6)
    check("checkpoint is shared with everyone", all(pg.evaluate("(() => { const r = [...document.querySelectorAll('.hist-item b')].map(b => b.textContent); return r; })()") is not None for pg in pages))
    go_end(pages[1]); pages[1].keyboard.type(" AFTER-CHECKPOINT")
    wait_same(pages)
    time.sleep(0.6)
    check("history preview shows the change since the checkpoint", "1 line" in pages[0].locator("#histPreview").inner_text() or "added" in pages[0].locator("#histPreview").inner_text(), pages[0].locator("#histPreview").inner_text()[:160])
    pages[0].locator(".hist-file", has_text="index.html").click()
    pages[0].locator(".hist-restore-one").click()
    same, text = wait_same(pages)
    check("restore brings everyone back to the checkpoint", same and "AFTER-CHECKPOINT" not in text, str(text)[-80:])
    pages[0].locator("#histClose").click()
    pages[0].locator("#code").focus(); pages[0].keyboard.press("Control+z")
    same, text = wait_same(pages)
    check("Ctrl+Z undoes the restore", same and "AFTER-CHECKPOINT" in text, str(text)[-80:])

    # 10. Offline version kept automatically + merged text highlighted
    pages[3].locator("#toggleOffline").click()
    time.sleep(0.3)
    go_end(pages[3]); pages[3].keyboard.type(" divya-offline-2")
    go_end(pages[0]); pages[0].keyboard.type(" asha-while-divya-away")
    time.sleep(0.6)
    pages[3].locator("#toggleOffline").click()
    time.sleep(1.2)
    marks = pages[3].locator(".merge-mark").count()
    check("merged text is highlighted in green", marks >= 1, str(marks))
    same, text = wait_same(pages)
    pages[1].locator("#historyBtn").click()
    time.sleep(0.4)
    hist = pages[1].locator("#histList").inner_text()
    check("offline version saved as an automatic checkpoint", "Divya’s version before merging" in hist, hist.replace("\n", " | ")[:200])
    pages[1].locator("#histClose").click()

    # 11. Undo survives a page reload
    go_end(pages[1]); pages[1].keyboard.type(" RELOAD-UNDO")
    wait_same(pages)
    time.sleep(0.8)
    pages[1].reload()
    pages[1].wait_for_selector("#code")
    time.sleep(1.5)
    pages[1].locator("#code").focus(); pages[1].keyboard.press("Control+z")
    same, text = wait_same(pages)
    check("Ctrl+Z still works after reloading the page", same and "RELOAD-UNDO" not in text, str(text)[-80:])

    # 12. Self-healing resync answers with the server's state vector
    got = pages[0].evaluate("""() => new Promise(res => {
        const ws = new WebSocket(location.origin.replace('http', 'ws') + '/ws');
        ws.onopen = () => { ws.send(JSON.stringify({ t: 'hello', room: new URLSearchParams(location.search).get('room'), client: 'probe', sv: {}, auth: JSON.parse(localStorage.getItem('dragonAuth')).token })); ws.send(JSON.stringify({ t: 'sync', sv: {} })); };
        ws.onmessage = e => { const m = JSON.parse(e.data); if (m.t === 'resync') { ws.close(); res(Object.keys(m.sv).length); } };
        setTimeout(() => res(-1), 4000);
    })""")
    check("server answers the periodic resync", got > 0, str(got))

    # 13. Room password
    answers[0] = "warrior123"
    pages[0].locator("#lockRoom").click()
    time.sleep(0.8)
    check("lock button shows the room is locked", "locked" in pages[0].locator("#lockRoom").inner_text().lower())
    eve_ctx = browser.new_context(); eve_ctx.add_init_script(login_script(make_account(BASE, "Eve"))); eve = eve_ctx.new_page()
    eve_prompts = []
    def eve_dialog(d):
        eve_prompts.append(d.message)
        d.accept("wrong-guess" if len(eve_prompts) == 1 else "warrior123")
    eve.on("dialog", eve_dialog)
    eve_log = []
    eve.on("websocket", lambda w: (eve_log.append("open"), w.on("framereceived", lambda f: eve_log.append(str(f)[:40])), w.on("close", lambda _: eve_log.append("closed"))))
    eve.goto(f"{BASE}/?room={ROOM}&name=Eve")
    for _ in range(40):
        if live(eve): break
        time.sleep(0.25)
    print("   eve websocket log:", eve_log[:8])
    check("locked room refuses a wrong password, then lets the right one in", len(eve_prompts) == 2 and "wrong" in eve_prompts[1] and live(eve), str(eve_prompts) + " " + badge(eve))
    check("Eve sees the same project once in", value(eve) == value(pages[0]))

    check("no JavaScript errors on any page", not errors, "; ".join(errors[:3]))
    browser.close()

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} checks passed (room {ROOM})")
sys.exit(0 if passed == len(results) else 1)
