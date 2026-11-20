"""Accounts, the login page, Settings and themes, tested through the UI in real Chrome.

Needs the sync server (npm start). Run:
  python test/account_scenario.py [http://localhost:8787] [chrome] [screenshot-folder]
"""
import sys, os, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from accounts_helper import make_account, login_script, api
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "http://localhost:8787"
CHROME = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
SHOTS = sys.argv[3] if len(sys.argv) > 3 else ""
results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(detail)[:220]) if detail and not cond else ""))

def shot(pg, name, **kw):
    if SHOTS: pg.screenshot(path=os.path.join(SHOTS, name + ".png"), **kw)

def wait_until(fn, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn(): return True
        except Exception:
            pass
        time.sleep(0.15)
    return False

INIT = "if (window === window.top) { window.__appErrors = []; addEventListener('error', e => window.__appErrors.push(String(e.message))); }"
EMAIL = f"suraj.{int(time.time())}@example.com"
PASSWORD = "dragon-fire-2026"

with sync_playwright() as p:
    launch = {"args": ["--no-sandbox"]}
    if CHROME: launch["executable_path"] = CHROME
    browser = p.chromium.launch(**launch)
    app_errors = []

    def new_page(viewport=(1440, 900), init=None):
        ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        ctx.add_init_script(INIT)
        if init: ctx.add_init_script(init)
        pg = ctx.new_page()
        pg.on("dialog", lambda d: d.accept())
        return ctx, pg

    auth_visible = lambda pg: pg.locator("#authScreen").is_visible()
    def err(pg): return pg.locator("#authError").inner_text()
    def tab(pg): return pg.locator("#authTabs [aria-selected='true']").inner_text()
    def synced(pg): return pg.locator("#syncBadge").inner_text() == "Synced" and pg.locator("#connBadge").inner_text() == "Online"

    # ------------------------------------------------------------------ sign up
    print("\n== First visit: Create account")
    ctx1, pg = new_page()
    pg.goto(BASE + "/")
    check("the account page comes first", wait_until(lambda: auth_visible(pg), 5))
    check("first time it opens on Create account (name, email, password)", tab(pg) == "Create account"
          and all(pg.locator(s).is_visible() for s in ["#authName", "#authEmail", "#authPassword"]), tab(pg))
    check("the editor behind it cannot be used yet", pg.evaluate("document.querySelector('main.workspace').inert === true"))
    check("nothing syncs before logging in", wait_until(lambda: pg.locator("#connBadge").inner_text() == "Log in to sync", 3)
          and not pg.evaluate("performance.getEntriesByType('resource').some(r => r.name.includes('/ws'))"), pg.locator("#connBadge").inner_text())
    shot(pg, "1-create-account")
    pg.locator("#authSubmit").click()
    check("an empty name is refused", "enter your name" in err(pg), err(pg))
    pg.locator("#authName").fill("Suraj Singh")
    pg.locator("#authEmail").fill("not-an-email")
    pg.locator("#authSubmit").click()
    check("a bad email is refused", "valid email" in err(pg), err(pg))
    pg.locator("#authEmail").fill(EMAIL)
    pg.locator("#authPassword").fill("short")
    pg.locator("#authSubmit").click()
    check("a short password is refused", "at least 8 characters" in err(pg), err(pg))
    pg.locator("#authPassword").fill(PASSWORD)
    pg.locator("#authShowPw").click()
    check("the eye button shows the password", pg.locator("#authPassword").get_attribute("type") == "text")
    pg.locator("#authShowPw").click()
    pg.locator("#authSubmit").click()
    check("Create account opens the editor", wait_until(lambda: not auth_visible(pg), 8), err(pg))
    check("the editor is usable and syncing", wait_until(lambda: synced(pg), 8) and pg.evaluate("document.querySelector('main.workspace').inert === false"))
    check("Settings button shows the account name", pg.locator("#settingsName").inner_text() == "Suraj Singh" and pg.locator("#settingsAvatar").inner_text() == "SS")
    token = pg.evaluate("JSON.parse(localStorage.getItem('dragonAuth')).token")
    status, me = api(BASE, "/api/me", token=token)
    check("the account exists on the server", status == 200 and me["user"]["email"] == EMAIL and me["user"]["name"] == "Suraj Singh", (status, me))
    check("the password is not kept in the browser", PASSWORD not in pg.evaluate("JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage})"))
    time.sleep(0.6)
    shot(pg, "2-editor-after-signup")
    room = pg.url.split("room=")[1].split("&")[0]

    # ------------------------------------------------------------------ teammate + settings
    print("\n== Settings: profile and theme, seen by a teammate")
    bilal_acct = make_account(BASE, "Bilal")
    ctx2, mate = new_page(init=login_script(bilal_acct))
    mate.goto(f"{BASE}/?room={room}")
    check("a teammate who is logged in goes straight to the editor", wait_until(lambda: synced(mate), 8) and not auth_visible(mate))
    check("the teammate sees 'Suraj Singh'", wait_until(lambda: "Suraj Singh" in mate.locator("#collabPeople").inner_text(), 8), mate.locator("#collabPeople").inner_text())
    pg.locator("#settingsBtn").click()
    check("⚙ Settings shows who is logged in", wait_until(lambda: pg.locator("#settingsPanel").is_visible(), 3) and pg.locator("#setEmail").inner_text() == "Logged in as " + EMAIL, pg.locator("#setEmail").inner_text())
    for t in ["midnight", "ocean", "aurora", "forest", "sunset", "graphite", "classic", "ember"]:
        pg.locator(f"#setThemes .theme-card[data-t='{t}']").click()
        time.sleep(0.15)
        check(f"theme '{t}' applies right away", pg.evaluate("document.documentElement.dataset.theme") == t)
    check("the chosen theme is saved on the account", wait_until(lambda: api(BASE, "/api/me", token=token)[1]["user"]["theme"] == "ember", 5))
    pg.locator("#setName").fill("Suraj")
    pg.locator("#setColors .color-swatch[aria-label='Violet']").click()
    shot(pg, "3-settings")
    pg.locator("#setSave").click()
    check("Save updates the account", wait_until(lambda: pg.locator("#settingsPanel").is_hidden() and api(BASE, "/api/me", token=token)[1]["user"]["name"] == "Suraj", 5))
    check("the teammate sees the new name", wait_until(lambda: "Suraj" in mate.locator("#collabPeople").inner_text() and "Suraj Singh" not in mate.locator("#collabPeople").inner_text(), 8), mate.locator("#collabPeople").inner_text())

    print("\n== The server decides the name, not the browser")
    mate.evaluate("""() => { const s = [...(window.__ws || [])]; }""")
    forged = mate.evaluate("""() => new Promise(resolve => {
        const s = new WebSocket(location.origin.replace('http', 'ws') + '/ws'); const got = [];
        s.onopen = () => s.send(JSON.stringify({ t: 'hello', room: 'x-nologin', client: 'mallory', sv: {}, state: { name: 'Mallory' } }));
        s.onmessage = e => got.push(JSON.parse(e.data));
        s.onclose = e => resolve({ got, code: e.code });
        setTimeout(() => resolve({ got, code: 'timeout' }), 4000); })""")
    check("joining a room without logging in is refused by the server", forged["code"] == 4003 and forged["got"] and forged["got"][0].get("reason") == "login", forged)
    fake = mate.evaluate("""tok => new Promise(resolve => {
        const s = new WebSocket(location.origin.replace('http', 'ws') + '/ws');
        s.onopen = () => s.send(JSON.stringify({ t: 'hello', room: 'x', client: 'm2', sv: {}, state: { name: 'Mallory' }, auth: tok }));
        s.onclose = e => resolve(e.code); setTimeout(() => resolve('timeout'), 4000); })""", "this-is-not-a-real-session-token")
    check("a made-up session token is refused too", fake == 4003, fake)

    # ------------------------------------------------------------------ reload, offline reload
    print("\n== Coming back")
    pg.reload()
    check("a reload goes straight to the editor (still logged in)", wait_until(lambda: synced(pg), 8) and not auth_visible(pg))
    check("name, colour and theme are remembered", pg.locator("#settingsName").inner_text() == "Suraj" and pg.evaluate("document.documentElement.dataset.theme") == "ember"
          and "rgb(167, 139, 250)" in pg.locator("#settingsAvatar").evaluate("e => getComputedStyle(e).backgroundColor"))
    cached = pg.evaluate("""async () => { await navigator.serviceWorker.ready; for (let i = 0; i < 40; i++) { if (await caches.match('./')) return true; await new Promise(r => setTimeout(r, 100)); } return false; }""")
    ctx1.set_offline(True)
    pg.reload()
    check("offline reload still opens the editor, no login needed", cached and wait_until(lambda: pg.locator("#code").is_visible(), 8) and not auth_visible(pg)
          and pg.locator("#syncBadge").inner_text() == "Saved on this device")
    ctx1.set_offline(False)
    check("and it reconnects when back online", wait_until(lambda: synced(pg), 15))

    # ------------------------------------------------------------------ log out, log in
    print("\n== Log out, then log in")
    pg.locator("#settingsBtn").click()
    pg.locator("#setSignOut").click()
    check("after logging out, the login page is back", wait_until(lambda: auth_visible(pg), 8))
    check("after the first time it opens on Log in, email filled in", tab(pg) == "Log in" and pg.locator("#authEmail").input_value() == EMAIL
          and pg.locator("#authName").is_hidden(), (tab(pg), pg.locator("#authEmail").input_value()))
    check("the old session no longer works on the server", api(BASE, "/api/me", token=token)[0] == 401)
    shot(pg, "4-log-in")
    pg.locator("#authPassword").fill("wrong-password-1")
    pg.locator("#authSubmit").click()
    check("a wrong password is refused", wait_until(lambda: "Wrong email or password" in err(pg), 5), err(pg))
    pg.locator("#authPassword").fill(PASSWORD)
    pg.locator("#authRemember").uncheck()
    pg.locator("#authSubmit").click()
    check("the right password opens the editor", wait_until(lambda: not auth_visible(pg) and synced(pg), 8), err(pg))
    check("'Keep me logged in' off: the session is only kept for this browser session",
          pg.evaluate("localStorage.getItem('dragonAuth') === null && sessionStorage.getItem('dragonAuth') !== null"))
    check("theme and name came back from the account", pg.evaluate("document.documentElement.dataset.theme") == "ember" and pg.locator("#settingsName").inner_text() == "Suraj")

    print("\n== Another device, same account")
    ctx3, dev2 = new_page(viewport=(1280, 800))
    dev2.goto(f"{BASE}/?room={room}")
    wait_until(lambda: auth_visible(dev2), 5)
    dev2.locator("#authName").fill("Someone")
    dev2.locator("#authEmail").fill(EMAIL)
    dev2.locator("#authPassword").fill(PASSWORD)
    dev2.locator("#authSubmit").click()
    check("signing up with a used email switches to Log in and says why", wait_until(lambda: tab(dev2) == "Log in" and "already exists" in err(dev2), 5), (tab(dev2), err(dev2)))
    dev2.locator("#authSubmit").click()
    check("logging in on the second device works", wait_until(lambda: not auth_visible(dev2) and synced(dev2), 8), err(dev2))
    check("the account's name, colour and theme follow to the new device", dev2.locator("#settingsName").inner_text() == "Suraj"
          and dev2.evaluate("document.documentElement.dataset.theme") == "ember")

    print("\n== Session ended on the server")
    ctx4, stale = new_page(init="if (window === window.top) localStorage.setItem('dragonAuth', JSON.stringify({ token: 'expired-token-0123456789abcdef', user: { id: 'gone', name: 'Old Me', email: 'old@example.com', color: '#22c55e' }, remember: true }));")
    stale.goto(f"{BASE}/?room={room}")
    check("an expired session brings back the login page with a reason", wait_until(lambda: auth_visible(stale) and "session has ended" in err(stale), 8), err(stale))

    print("\n== No connection while logging in")
    ctx5, off = new_page()
    off.goto(BASE + "/")
    wait_until(lambda: auth_visible(off), 5)
    off.locator("#authTabLogin").click()
    off.locator("#authEmail").fill(EMAIL)
    off.locator("#authPassword").fill(PASSWORD)
    ctx5.set_offline(True)
    off.locator("#authSubmit").click()
    check("offline: the page says a connection is required to log in", wait_until(lambda: "Connection required to log in" in err(off), 8), err(off))
    ctx5.set_offline(False)

    print("\n== Explorer corner")
    pg.locator("#toggleExplorer").click(); time.sleep(0.3)
    check("collapsed Explorer still shows the ⚙ button", pg.locator("#settingsBtn").is_visible())
    pg.locator("#toggleExplorer").click()
    check("no language list in the Explorer footer any more", "Kotlin" not in pg.locator(".explorer-foot").inner_text())

    for page in [pg, mate, dev2, stale, off]:
        app_errors += page.evaluate("window.__appErrors")
    check("no JavaScript errors from the editor", not app_errors, app_errors[:3])
    browser.close()

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} checks passed")
for name, ok in results:
    if not ok: print("FAILED:", name)
sys.exit(0 if passed == len(results) else 1)
