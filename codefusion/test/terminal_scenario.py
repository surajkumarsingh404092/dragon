"""Shared terminals, Run and Debug, tested through the editor UI in real Chrome.

Servers (see README "Terminal and Debug tests"):
  A  DRAGON_TERMINAL=1 PORT=8787 node server.js           terminals on
     node test/netproxy.js 8790 8787 8791                 Dev's Wi-Fi to A
  B  PORT=8788 node server.js                             terminals off
  C  DRAGON_TERMINAL=1 PORT=8789 PATH=<no gdb/jdb> node server.js   debuggers missing
Run:
  python test/terminal_scenario.py [chrome]
"""
import sys, time, re, urllib.request
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from accounts_helper import make_account, login_script   # every test user signs in with a real account
from playwright.sync_api import sync_playwright

CHROME = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
A, PROXY, CONTROL = "http://localhost:8787", "http://localhost:8790", "http://127.0.0.1:8791"
B, C = "http://localhost:8788", "http://localhost:8789"
T0 = time.time()
results = []

def log(msg): print(f"   [{time.time() - T0:6.1f}s] {msg}")
def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(detail)[:300]) if detail and not cond else ""))
    return bool(cond)
def net(mode):
    urllib.request.urlopen(f"{CONTROL}/mode/{mode}", timeout=5).read()
    log(f"Dev's network: {mode}")

INIT = r"""
(() => {
  // Errors of the editor itself: only the top window. User code in the preview iframe
  // reports to the iframe's own window, so it never lands here.
  if (window === window.top) {
    window.__appErrors = [];
    window.addEventListener('error', e => window.__appErrors.push(String(e.message || e.error)));
    window.addEventListener('unhandledrejection', e => window.__appErrors.push('unhandled rejection: ' + String((e.reason && e.reason.message) || e.reason)));
  }
  window.__sockets = []; window.__termList = null; window.__denied = [];
  const Native = window.WebSocket;
  function Logged(url, protocols) {
    const s = protocols === undefined ? new Native(url) : new Native(url, protocols);
    window.__sockets.push(s);
    s.addEventListener('message', e => { try { const m = JSON.parse(e.data);
      if (m.t === 'term-list') window.__termList = m;
      if (m.t === 'term-denied') window.__denied.push(m.reason); } catch (err) {} });
    return s;
  }
  Logged.prototype = Native.prototype;
  Logged.CONNECTING = 0; Logged.OPEN = 1; Logged.CLOSING = 2; Logged.CLOSED = 3;
  window.WebSocket = Logged;
})();
"""

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

def text(pg): return pg.locator("#code").input_value()
def badge(pg): return pg.locator("#syncBadge").inner_text()
def conn(pg): return pg.locator("#connBadge").inner_text()
def synced(pg): return badge(pg) == "Synced" and conn(pg) == "Online"
def out(pg): return pg.locator("#termOut").inner_text()
def note(pg): return pg.locator("#termNote").inner_text() if pg.locator("#termNote").is_visible() else ""
def role(pg): return pg.locator("#termRole").inner_text()
def chip(pg, which): return pg.locator("#runState" if which == "run" else "#debugState").inner_text()
def chip_title(pg, which): return pg.locator("#runState" if which == "run" else "#debugState").get_attribute("title") or ""
def can_type(pg): return pg.locator("#termInput").is_enabled()
def tabs(pg): return pg.locator("#termTabs .term-tab span").all_inner_texts()

def open_panel(pg):
    if pg.locator("#terminalPanel").is_hidden(): pg.locator("#terminalBtn").click()

def run_cmd(pg, cmd):
    pg.locator("#termInput").fill(cmd)
    pg.locator("#termInput").press("Enter")

def forge(pg, msg):
    return pg.evaluate("""m => { const s = window.__sockets.filter(x => x.readyState === 1).pop();
        if (!s) return false; s.send(JSON.stringify(Object.assign({ ct: Math.round(performance.now()) }, m))); return true; }""", msg)

def pid_in(s):
    found = re.findall(r"PID=(\d+)", s)
    return found[-1] if found else None

def open_file(pg, path):
    pg.locator(f".tree-entry[title='{path}']").click()
    wait_until(lambda: path in pg.locator("#fileName").inner_text(), 4)

def new_file(pg, path, code):
    pg.answers.append(path)
    pg.locator("#newFile").click()
    wait_until(lambda: pg.locator(f".tree-entry[title='{path}']").count() == 1, 5)
    open_file(pg, path)
    pg.locator("#code").fill(code)

def reachable(pg, url):
    return pg.evaluate("u => fetch(u, { method: 'HEAD', mode: 'no-cors', cache: 'no-store' }).then(() => true, () => false)", url)

PY = "x = 41\ny = x + 1\nprint('y is', y)\n"
JAVA = 'public class Main {\n  public static void main(String[] args) {\n    int a = 20;\n    int b = a + 22;\n    System.out.println("b=" + b);\n  }\n}\n'

with sync_playwright() as p:
    launch = {"args": ["--no-sandbox"]}
    if CHROME: launch["executable_path"] = CHROME
    browser = p.chromium.launch(**launch)
    errors = []

    def session(name, url, simulate_internet=False):
        ctx = browser.new_context(viewport={"width": 1500, "height": 950})
        server = A if url.startswith(PROXY) else url.split("/?")[0]   # Dev reaches server A through the proxy
        ctx.add_init_script(login_script(make_account(server, name)))
        ctx.add_init_script(INIT)
        if simulate_internet:   # pretend Judge0 and jsDelivr answer (this test machine has no internet)
            ctx.route("https://ce.judge0.com/**", lambda r: r.fulfill(status=200, body="{}"))
            ctx.route("https://cdn.jsdelivr.net/**", lambda r: r.fulfill(status=200, body="{}"))
        pg = ctx.new_page()
        pg.answers = []
        pg.on("pageerror", lambda e: errors.append(name + ": " + str(e)))   # includes the preview iframe
        pg.on("dialog", lambda d: d.accept(pg.answers.pop(0)) if (d.type == "prompt" and pg.answers) else d.accept())
        pg.goto(url)
        return ctx, pg

    # ------------------------------------------------------------------ setup
    print("\n== 1. Four teammates join the same room (server with terminals on)")
    actx, asha = session("Asha", f"{A}/?name=Asha")
    wait_until(lambda: synced(asha), 10)
    room = asha.url.split("room=")[1].split("&")[0]
    link = f"{A}/?room={room}"
    bctx, bilal = session("Bilal", link + "&name=Bilal")
    cctx, chen = session("Chen", link + "&name=Chen", simulate_internet=True)
    dctx, dev = session("Dev", link.replace(A, PROXY) + "&name=Dev")
    team = [asha, bilal, chen, dev]
    check("all 4 Synced and Online", wait_until(lambda: all(synced(pg) for pg in team), 15), [(badge(pg), conn(pg)) for pg in team])

    # ------------------------------------------------------------------ terminal + workspace
    print("\n== 2. Asha starts a shared terminal in the project folder")
    open_panel(asha)
    asha.locator("#termNew").click()
    wait_until(lambda: can_type(asha), 5)
    run_cmd(asha, "echo PID=$$; ls")
    check("Asha's terminal runs in the project folder (sees index.html, script.js, style.css)",
          wait_until(lambda: all(f in out(asha) for f in ["index.html", "script.js", "style.css"]) and pid_in(out(asha)), 6), out(asha)[-300:])
    pid = pid_in(out(asha))
    check("Asha is the owner and can type", "You started this" in role(asha) and can_type(asha), role(asha))

    print("\n== 3. Bilal joins as a view-only guest")
    open_panel(bilal)
    check("Bilal sees the running terminal and its output", wait_until(lambda: "Terminal 1" in tabs(bilal) and f"PID={pid}" in out(bilal), 6), (tabs(bilal), out(bilal)[-200:]))
    check("Bilal is View only and his input box is disabled", role(bilal) == "View only" and not can_type(bilal), (role(bilal), can_type(bilal)))
    check("the disabled box says why", "View only" in (bilal.locator("#termInput").get_attribute("placeholder") or ""), bilal.locator("#termInput").get_attribute("placeholder"))
    tid = bilal.evaluate("window.__termList && window.__termList.terms[0].id")
    forged = forge(bilal, {"t": "term-input", "id": tid, "data": "touch pwned.txt; echo PWNED-BY-BILAL"})
    time.sleep(1.0)
    check("a forged command from the view-only guest is refused by the server",
          forged and any("View only" in r for r in bilal.evaluate("window.__denied")), bilal.evaluate("window.__denied"))
    run_cmd(asha, "ls pwned.txt 2>&1 || echo NO-PWNED-FILE")
    check("...and nothing ran: no output, no file", wait_until(lambda: "NO-PWNED-FILE" in out(asha), 5) and "PWNED-BY-BILAL" not in out(asha), out(asha)[-200:])
    forge(bilal, {"t": "term-input", "id": tid, "signal": "INT"})
    forge(bilal, {"t": "term-perm", "id": tid, "user": bilal.evaluate("'acct-' + JSON.parse(localStorage.getItem('dragonAuth')).user.id"), "role": "interactive"})
    time.sleep(0.6)
    check("the guest cannot send Ctrl+C or give himself typing access either",
          role(bilal) == "View only" and sum("Only the person who started" in r or "View only" in r for r in bilal.evaluate("window.__denied")) >= 3, bilal.evaluate("window.__denied"))

    print("\n== 4. The terminal is attached to the shared workspace")
    open_file(bilal, "script.js")
    bilal.locator("#code").click(); bilal.keyboard.press("Control+End"); bilal.keyboard.type("\nconsole.log('from-bilal-editor');")
    wait_until(lambda: synced(bilal), 5)
    run_cmd(asha, "cat script.js")
    check("a teammate's latest edit is in the terminal's files", wait_until(lambda: "from-bilal-editor" in out(asha), 5), out(asha)[-200:])
    new_file(dev, "main.py", PY)
    new_file(dev, "Main.java", JAVA)
    new_file(dev, "app.rb", "puts 'hi'\n")
    wait_until(lambda: synced(dev), 6)
    run_cmd(asha, "python3 main.py")
    check("files created in the editor run in the terminal", wait_until(lambda: "y is 42" in out(asha), 6), out(asha)[-200:])

    print("\n== 5. Asha gives Dev typing access")
    asha.locator("#termAccess").click()
    asha.locator("#termAccessList select[aria-label='Access for Dev']").select_option("interactive")
    open_panel(dev)
    check("Dev can now type (Can type), Chen stays view only", wait_until(lambda: role(dev) == "You can type" and can_type(dev), 6) and (open_panel(chen) or True) and wait_until(lambda: role(chen) == "View only", 5), (role(dev), role(chen)))
    run_cmd(dev, "echo dev-can-type")
    check("Dev's command runs and everyone sees who typed it", wait_until(lambda: all("› Dev: echo dev-can-type" in out(pg) and "\ndev-can-type" in out(pg) for pg in [asha, bilal, dev]), 6))

    print("\n== 6. Asha (who started the terminal) closes her tab")
    app_errors = [("Asha", m) for m in asha.evaluate("window.__appErrors")]
    asha.close(); actx.close()
    time.sleep(1.0)
    run_cmd(dev, "echo PID=$$")
    check("the terminal keeps running: same shell process after the creator left",
          wait_until(lambda: out(dev).count("PID=") >= 2 and pid_in(out(dev)) == pid, 6), (pid, pid_in(out(dev))))
    check("Bilal still sees it running", "Terminal 1" in tabs(bilal) and f"PID={pid}" in out(bilal))

    # ------------------------------------------------------------------ disconnect / reattach
    print("\n== 7. Dev's Wi-Fi drops")
    net("drop"); dctx.set_offline(True)
    check("Dev's terminal input is disabled with a clear reason", wait_until(lambda: not can_type(dev), 6) and "Connection required" in (dev.locator("#termInput").get_attribute("placeholder") or ""), dev.locator("#termInput").get_attribute("placeholder"))
    dev.evaluate("""() => { const i = document.getElementById('termInput'); i.disabled = false; i.value = 'touch queued.txt; echo QUEUED-RAN';
                       document.getElementById('termForm').requestSubmit(); }""")
    check("forcing Enter while offline is refused on the spot (not queued)", wait_until(lambda: "Not sent" in note(dev), 3), note(dev))
    open_file(dev, "index.html")
    check("offline: Run still available for HTML (Run: in browser)", chip(dev, "run") == "Run: in browser" and dev.locator("#run").is_enabled(), chip(dev, "run"))
    dev.locator("#code").click(); dev.keyboard.press("Control+End"); dev.keyboard.type("<!-- offline-preview-check -->")
    check("offline: the HTML preview still updates", wait_until(lambda: "offline-preview-check" in (dev.locator("#preview").get_attribute("srcdoc") or ""), 4))
    check("offline: editing still saves on this device", wait_until(lambda: badge(dev) == "Saved on this device", 4), badge(dev))
    open_file(dev, "main.py")
    check("offline: Debug shows Connection required to debug (button disabled)", wait_until(lambda: chip(dev, "debug") == "Connection required to debug", 4) and dev.locator("#debugBtn").is_disabled(), chip(dev, "debug"))
    check("offline: Python Run shows Connection required to run", chip(dev, "run") == "Connection required to run" and dev.locator("#run").is_disabled(), chip(dev, "run"))
    open_file(dev, "Main.java")
    check("offline: Java Run shows Connection required to run", chip(dev, "run") == "Connection required to run", chip(dev, "run"))
    time.sleep(1)
    check("other teammates saw nothing from Dev while offline", "QUEUED-RAN" not in out(bilal))

    print("\n== 8. Dev's Wi-Fi comes back: Dev re-attaches to the running terminal")
    net("pass"); dctx.set_offline(False)
    check("Dev is re-attached automatically and can type again", wait_until(lambda: can_type(dev) and role(dev) == "You can type", 15), (role(dev), note(dev)))
    check("Dev got the scrollback of the running terminal", "dev-can-type" in out(dev) and f"PID={pid}" in out(dev))
    run_cmd(dev, "echo PID=$$; ls queued.txt 2>&1 || echo NO-QUEUED-FILE")
    check("same shell process, and the offline attempt never ran", wait_until(lambda: "NO-QUEUED-FILE" in out(dev), 6) and pid_in(out(dev)) == pid and "QUEUED-RAN" not in out(dev), out(dev)[-200:])

    print("\n== 9. A command held up in the network is refused, not run late")
    net("holdup")
    run_cmd(dev, "touch late.txt; echo LATE-RAN")
    time.sleep(4.0)
    net("pass")
    check("the server refused the late command", wait_until(lambda: "late" in note(dev) and "Not run" in note(dev), 6), note(dev))
    run_cmd(dev, "ls late.txt 2>&1 || echo NO-LATE-FILE")
    check("...and it never ran (no output, no file)", wait_until(lambda: "NO-LATE-FILE" in out(dev), 6) and "LATE-RAN" not in out(dev).replace("› Dev: touch late.txt; echo LATE-RAN", ""), out(dev)[-300:])

    print("\n== 10. Dev reloads the page: still the same person, still allowed")
    app_errors += [("Dev", m) for m in dev.evaluate("window.__appErrors")]
    dev.reload()
    wait_until(lambda: synced(dev), 10)
    open_panel(dev)
    check("after a reload Dev re-attaches with typing access", wait_until(lambda: can_type(dev) and role(dev) == "You can type" and f"PID={pid}" in out(dev), 10), (role(dev), note(dev)))
    run_cmd(dev, "echo after-reload-ok")
    check("and can run commands", wait_until(lambda: "\nafter-reload-ok" in out(dev), 5))

    # ------------------------------------------------------------------ Run and Debug states
    print("\n== 11. Run and Debug follow the language and what is really reachable")
    judge0_up = reachable(dev, "https://ce.judge0.com/about")
    cdn_up = reachable(dev, "https://cdn.jsdelivr.net/npm/sql.js@1.15.1/package.json")
    log(f"this machine can reach Judge0: {judge0_up}, jsDelivr: {cdn_up}")
    open_file(dev, "index.html")
    wait_until(lambda: chip(dev, "debug").startswith("Debug unavailable"), 3)
    check("HTML: Run in browser on, Debug off with the reason", chip(dev, "run") == "Run: in browser" and dev.locator("#run").is_enabled()
          and dev.locator("#debugBtn").is_disabled() and "no program to step through" in chip_title(dev, "debug"), (chip(dev, "run"), chip(dev, "debug")))
    open_file(dev, "main.py")
    wait_until(lambda: "checking" not in chip(dev, "run"), 7)
    want_run = "Run: in browser" if cdn_up else "Connection required to run"
    check(f"Python: Run shows '{want_run}' (matches reality), Debug: pdb on server", chip(dev, "run") == want_run and dev.locator("#run").is_enabled() == cdn_up
          and chip(dev, "debug") == "Debug: pdb on server" and dev.locator("#debugBtn").is_enabled(), (chip(dev, "run"), chip(dev, "debug")))
    open_file(dev, "Main.java")
    wait_until(lambda: "checking" not in chip(dev, "run"), 7)
    want_run = "Run: Judge0 online" if judge0_up else "Connection required to run"
    check(f"Java: Run shows '{want_run}' (matches reality), Debug: jdb on server", chip(dev, "run") == want_run and chip(dev, "debug") == "Debug: jdb on server", (chip(dev, "run"), chip(dev, "debug")))
    check("Java's message names the remote service", "ce.judge0.com" in chip_title(dev, "run"), chip_title(dev, "run"))
    open_file(dev, "app.rb")
    check("Ruby: Debug unavailable, naming the file", chip(dev, "debug") == "Debug unavailable" and "no debugger for app.rb" in chip_title(dev, "debug"), chip_title(dev, "debug"))
    open_file(chen, "Main.java")
    check("(simulated internet) Java: Run: Judge0 online and the button is on", wait_until(lambda: chen.locator("#runState").inner_text() == "Run: Judge0 online" and chen.locator("#run").is_enabled(), 7), chip(chen, "run"))
    open_file(chen, "main.py")
    check("(simulated internet) Python: Run: in browser", wait_until(lambda: chip(chen, "run") == "Run: in browser", 7), chip(chen, "run"))
    check("the editor keeps working whatever Run says", chen.locator("#code").is_editable() and dev.locator("#code").is_editable())

    print("\n== 12. Debug Python with pdb in a shared terminal")
    open_file(dev, "main.py")
    dev.locator("#debugBtn").click()
    check("Debug opens a 'Debug main.py' session running pdb", wait_until(lambda: "Debug main.py" in tabs(dev) and "(Pdb)" in out(dev), 8), (tabs(dev), out(dev)[-200:]))
    run_cmd(dev, "n"); time.sleep(0.3); run_cmd(dev, "n"); time.sleep(0.3); run_cmd(dev, "p y")
    check("stepping and printing a variable works (y = 42)", wait_until(lambda: re.search(r"\(Pdb\)\s*\n?› Dev: p y\n42", out(dev)) or "\n42\n" in out(dev), 6), out(dev)[-300:])
    check("Bilal can watch the debug session, view only", wait_until(lambda: "Debug main.py" in tabs(bilal), 5))
    run_cmd(dev, "q")
    open_file(dev, "Main.java")
    dev.locator("#debugBtn").click()
    wait_until(lambda: "Debug Main.java" in tabs(dev) and "Initializing jdb" in out(dev), 15)
    run_cmd(dev, "stop in Main.main"); time.sleep(0.8); run_cmd(dev, "run")
    wait_until(lambda: "Breakpoint hit" in out(dev), 15)
    run_cmd(dev, "next"); time.sleep(0.8); run_cmd(dev, "next"); time.sleep(0.8); run_cmd(dev, "print b")
    check("Java: jdb stops at a breakpoint and prints b = 42", wait_until(lambda: "b = 42" in out(dev), 10), out(dev)[-300:])

    # ------------------------------------------------------------------ missing infrastructure
    print("\n== 13. A server started without DRAGON_TERMINAL=1")
    ectx, eve = session("Eve", f"{B}/?name=Eve")
    wait_until(lambda: synced(eve), 10)
    open_panel(eve)
    check("Terminal shows: unavailable, started without DRAGON_TERMINAL=1", wait_until(lambda: "Terminal unavailable: the sync server was started without DRAGON_TERMINAL=1" in note(eve), 5) and eve.locator("#termNew").is_disabled(), note(eve))
    open_file(eve, "script.js")
    check("Debug chip names the missing setting", wait_until(lambda: chip(eve, "debug") == "Debug unavailable · needs DRAGON_TERMINAL=1", 4) and eve.locator("#debugBtn").is_disabled(), chip(eve, "debug"))
    check("Run still works there (JavaScript runs in the browser)", chip(eve, "run") == "Run: in browser")
    forge(eve, {"t": "term-new", "kind": "shell"})
    time.sleep(0.6)
    check("a forged 'new terminal' is refused by that server", any("DRAGON_TERMINAL=1" in r for r in eve.evaluate("window.__denied")), eve.evaluate("window.__denied"))

    print("\n== 14. A server without gdb and jdb installed")
    fctx, finn = session("Finn", f"{C}/?name=Finn")
    wait_until(lambda: synced(finn), 10)
    new_file(finn, "m.c", "#include <stdio.h>\nint main(void) { int b = 42; printf(\"%d\\n\", b); return 0; }\n")
    check("C: Debug unavailable · needs gdb", wait_until(lambda: chip(finn, "debug") == "Debug unavailable · needs gdb", 5) and "gdb is not installed on the sync server" in chip_title(finn, "debug"), (chip(finn, "debug"), chip_title(finn, "debug")))
    new_file(finn, "Main.java", JAVA)
    check("Java: Debug unavailable · needs jdb", wait_until(lambda: chip(finn, "debug") == "Debug unavailable · needs jdb", 5), chip(finn, "debug"))
    new_file(finn, "main.py", PY)
    check("Python still debuggable there (pdb on server)", wait_until(lambda: chip(finn, "debug") == "Debug: pdb on server", 5), chip(finn, "debug"))
    open_panel(finn)
    check("and its terminal is available", wait_until(lambda: finn.locator("#termNew").is_enabled(), 4))

    for name, pg in [("Bilal", bilal), ("Chen", chen), ("Dev", dev), ("Eve", eve), ("Finn", finn)]:
        app_errors += [(name, m) for m in pg.evaluate("window.__appErrors")]
    log(f"all page errors incl. user code in the live preview (half-typed JavaScript): {errors}")
    check("no JavaScript errors from the editor itself in any session", not app_errors, app_errors[:3])
    browser.close()

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} checks passed in {time.time() - T0:.0f}s")
for name, ok in results:
    if not ok: print("FAILED:", name)
sys.exit(0 if passed == len(results) else 1)
