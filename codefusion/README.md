# DRAGON: multiplayer code editor that works offline (Code Fuzion 2026 · H9)

DRAGON is a browser code editor (15 languages, multi-file projects, live preview) where
4+ people edit the same files live, keep working offline, and merge cleanly on reconnect.

| H9 requirement | How DRAGON does it |
|---|---|
| CRDT or OT sync, no lost characters | Own CRDT (RGA) at the top of `src/script.js`, fuzz-tested with 5 users × 3,000 random runs |
| Offline editing, automatic merge on reconnect | Every change is saved in IndexedDB first; on reconnect both sides swap state vectors and send only what is missing |
| Live cursors | Named, coloured cursors and selections, plus presence dots in the Explorer |
| Per-user undo | Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y only revert your own changes, per file |

### Beyond the H9 minimum

| Feature | What it does |
|---|---|
| 🕘 **Version history** | Save named checkpoints (shared with the room), see a line-by-line diff since then, restore one file or the whole project. A restore is a normal edit: it syncs, and Ctrl+Z undoes it. |
| **Offline version kept automatically** | When you reconnect with offline edits, DRAGON first saves "your version before merging" as a checkpoint. |
| **Merged text in green** | After reconnecting, text that teammates wrote while you were away is highlighted for 8 seconds. |
| 👤 **Who wrote this** | Line numbers are coloured by who wrote each line, with each person's share of the file. |
| 🔒 **Room password** | Lock a room. The server stores only a salted SHA-256 hash; wrong passwords are refused. |
| **Undo survives reload** | Your undo history is saved for the tab, so Ctrl+Z still works after a reload. |
| **Follow a teammate** | Click a teammate's name in the status bar (under the toolbar) to jump to their file and cursor. |
| **Self-healing sync** | Every 20 seconds browser and server compare state vectors and fill any gap. |

## Just want to open it?

Double-click **`index.html`**. It has its CSS and JavaScript inside, so it always looks like the
full colourful editor, however you open it. Tabs in the same browser sync with each other; click
**⚙ Server** to connect to a sync server for other devices.

The same code as separate files is in **`src/`**: `src/index.html` (HTML), `src/style.css` (CSS)
and `src/script.js` (JavaScript). That is the code to read and edit. After editing it, run
`npm run build` to rebuild `index.html`.

## Run it

```bash
npm install
npm start                      # http://localhost:8787
```

Open `http://localhost:8787`. The app gives the project a stable room link
(`?room=p-…`) and keeps it in the address bar, so a refresh, a bookmark or an offline
reload always lands in the same project. Click **🔗 Share link** and send it to your
teammates; everyone with the same link edits the same project. You can also pick a name
yourself: `http://localhost:8787/?room=team1`. Nobody owns a room: when the person who
created it closes their tab, everyone else keeps editing and syncing.

The status next to the room name is always one of four messages:

| Status | Meaning |
|---|---|
| **Saving on this device** | Your latest edits are being written to this browser's storage (IndexedDB). |
| **Saved on this device** | All your edits are stored in this browser, but not confirmed by the server (you are offline, reconnecting, or the room is locked). They survive a refresh and sync automatically when you reconnect. |
| **Syncing** | Connected; the server has not yet confirmed all your edits. |
| **Synced** | The server has written every edit you made to disk and confirmed it. |

The label beside it shows the connection: Online, Connecting…, Offline · retrying or
Offline (by you). Hover the status to see how many of your edits are still unconfirmed.

- **Same Wi-Fi, several laptops:** open `http://<your-laptop-ip>:8787` on one laptop and share its room link (with your laptop's IP in place of `localhost`).
  Live sync and offline edits work. Reloading the page *with no network* needs HTTPS or
  localhost (a browser rule for service workers), so test that one on the deployed site.
- **Opened as a plain file (no server):** tabs in the same browser still sync with each
  other. Click **⚙ Server** to connect to a deployed server.
- Every user can set a name with the **You:** button, or add `&name=Asha` to the link.

## Accounts, login page, Settings and themes

- **Account page first:** the first time, people **Create account** with a name, email and
  password. After that the page opens on **Log in** (email filled in). Each account works
  from any device on the same server.
- **The server enforces it:** joining a room needs a valid login. A browser without one,
  or with a made-up or expired session, is refused before it sees any project data.
- **Passwords** are stored only as a scrypt hash with a random salt, never in plain text.
  The browser keeps only a random session token; the server stores only its SHA-256.
  Sessions last 30 days (`DRAGON_SESSION_DAYS`) or until **Log out**.
- **Guessing is slowed down:** after 10 wrong passwords, an email is locked out for 15
  minutes. New accounts are limited to 100 per hour per network (`DRAGON_SIGNUPS_PER_HOUR`).
- **Keep me logged in** (on by default) remembers the login on this device, so reloads, even
  offline, open the editor straight away. Turn it off on shared computers: the login is then
  forgotten when the browser closes.
- **Your name is your account's name.** The server replaces whatever name a browser claims,
  so nobody can appear as someone else. Terminal permissions also belong to the account,
  so they follow you to other devices.
- **⚙ Settings** (bottom of the Explorer, or click **You: …**) shows who is logged in, lets
  you change your name and cursor colour (saved on the account, so every teammate sees the
  change at once), choose a background theme (also saved on the account), and **Log out**.
- **8 background themes:** Classic (the original look), Midnight, Ocean, Aurora, Ember,
  Forest, Sunset and Graphite.
- **Opened as a file, with no server:** accounts are impossible, so the page asks only for a
  name, kept in this browser.
- Accounts live in `data/accounts.json` and sessions in `data/sessions.json`; only the server
  user can read them. **Use HTTPS when deployed**, or passwords cross the network
  unencrypted (fine on your own laptop at localhost).

## Shared terminal and Debug (optional, off by default)

```bash
DRAGON_TERMINAL=1 npm start          # macOS / Linux / Git Bash
set DRAGON_TERMINAL=1 && npm start   # Windows cmd (untested)
```

**Warning:** with this on, anyone who can open a terminal in a room can run commands on the
machine running the server. Turn it on only on a machine you trust your team with, and
lock the room with **🔓 Lock room** so only people with the password can join.

- **⌨ Terminal** opens a panel at the bottom. **＋ New terminal** starts a shell in a folder
  the server keeps in step with the project (`data/workspaces/<room>/`), so `ls`, `python3
  main.py` or `node script.js` see everyone's latest code. Changes made by the shell are not
  copied back into the editor.
- A terminal belongs to the room, not to a tab. It keeps running when the person who started it
  closes their tab, and anyone in the room can re-attach and see the scrollback. It ends when
  its owner clicks **End session**, its program exits, or nobody has been attached for 30
  minutes (`DRAGON_TERMINAL_IDLE_MIN`).
- **Permissions are checked by the server.** The person who started a terminal can type.
  Everyone else is **View only** until the owner gives them **Can type** under **👥 Access**.
  People are recognised by a per-browser identity, so the permission still applies after a
  reconnect or a page reload in the same browser.
- **Input is never queued.** While disconnected, the input box is disabled. When you
  reconnect you are re-attached automatically, but nothing you tried to type runs. The
  server also refuses input that arrives more than 2.5 s late (a stalled connection
  delivering its backlog) instead of running it.
- **🐞 Debug** starts a real debugger on the open file in a shared terminal: Python → pdb,
  JavaScript → `node inspect`, C/C++ → gdb, Java → jdb. The server needs the matching tools:
  python3; node; gcc/g++ + gdb; javac + jdb. The server log lists what it found.
- Line mode only: type a command and press Enter. Full-screen programs (vim, top) need a real
  PTY, which needs the `node-pty` package; it is not installed.

**Run and Debug availability** is shown next to the sync status and on the buttons, per file:

| File | Run | Debug |
|---|---|---|
| HTML, CSS, JavaScript | **Run: in browser**, works offline | JavaScript: node inspect on the server. HTML/CSS: unavailable (nothing to step through) |
| Python, SQL, TypeScript | In this browser. The first run downloads the runtime from cdn.jsdelivr.net. If that can't be reached: **Connection required to run** | Python: pdb on the server |
| Java, C, C++, C#, PHP, Ruby, Swift, Kotlin, Rust | On Judge0 (ce.judge0.com). If it can't be reached: **Connection required to run** | Java: jdb, C/C++: gdb on the server. Others: unavailable |

Offline, every Debug becomes **Connection required to debug**. If the server runs without
`DRAGON_TERMINAL=1`, Debug says **needs DRAGON_TERMINAL=1**. If a tool is missing, it says
which one (for example **needs gdb**). Reachability is checked for real (a request to the
service), not guessed from "online". Editing and saving on this device never depend on any
of this.

## Deploy (mandatory for the submission)

The server is a single Node process that serves the app *and* the WebSocket sync, so one
deployment is enough.

1. Push this folder to GitHub.
2. Create a Node web service on a host that supports WebSockets (Render, Railway, Fly.io…).
   Build command `npm install`, start command `npm start`. The host sets `PORT`.
3. Open `https://<your-app>` and share the room link it shows.

If you host only the frontend on Vercel/Netlify (upload `index.html` and `sw.js`), the
Node server must run elsewhere: click **⚙ Server** and enter `wss://<server-host>/ws`, or
add `&server=wss://<server-host>/ws` to the link.

Rooms are saved to `data/<room>.jsonl`. Some hosts wipe the disk on redeploy. The room
still rebuilds itself: the next browser that connects uploads every change it has saved.

## Demo script for judges (5 minutes)

1. One device opens the app and clicks **🔗 Share link**; three more open that link. Point at
   the names, coloured cursors and Explorer dots. Close the first device's tab: the other
   three keep working.
2. Everyone types on the same line at once. All four screens end up identical, and
   each person's text stays in one piece.
3. Asha types, Bilal types, Asha presses Ctrl+Z: only Asha's text disappears everywhere.
4. One laptop clicks **Go offline** (or turns Wi-Fi off) and edits the same lines others are
   editing. Its status shows **Saved on this device**. Go back online: it shows **Syncing**,
   then **Synced** once the server confirms, a toast says how many changes merged, and nothing
   is lost.
5. Turn Wi-Fi off and **reload**: the app still opens with the project (deployed HTTPS site).
6. Delete a file while an offline teammate edits it. When they reconnect, the file comes
   back with their work (edits beat deletes).
7. Click **🕘 History → Save checkpoint**, let someone change the file, then show the diff and
   **Restore this file**. Press Ctrl+Z to undo the restore.
8. Click **👤 Who wrote this**: line numbers show each teammate's colour.
9. Click **🔒 Lock room**, set a password, and have a new device join: it must type the password.
10. Run `npm test` live: the fuzz test proves convergence on thousands of random histories.

## Tests

```bash
npm test                                   # CRDT scenarios + random fuzz (no install needed)
node test/fuzz.js 5000                     # more random runs
python test/e2e.py http://localhost:8787   # 4-user browser test (needs Python Playwright)
python test/account_scenario.py http://localhost:8787   # account page, login, Settings, themes

# Hostel Wi-Fi scenario: start the network breaker in front of the server, then run it
node test/netproxy.js 8790 8787 8791 &
python test/offline_scenario.py http://localhost:8787
```

### Terminal and Debug tests

```bash
# A: terminals on, B: terminals off, C: terminals on but gdb and jdb missing
DRAGON_TERMINAL=1 PORT=8787 node server.js &
node test/netproxy.js 8790 8787 8791 &
PORT=8788 DATA_DIR=/tmp/dragon-b node server.js &
mkdir -p /tmp/nodbg-bin && for d in /usr/local/bin /usr/bin /bin $(dirname "$(command -v node)"); do
  for f in "$d"/*; do n=$(basename "$f"); case "$n" in gdb|jdb) continue;; esac
  [ -e /tmp/nodbg-bin/$n ] || ln -s "$f" /tmp/nodbg-bin/$n; done; done
DRAGON_TERMINAL=1 PORT=8789 DATA_DIR=/tmp/dragon-c PATH=/tmp/nodbg-bin "$(command -v node)" server.js &
python test/terminal_scenario.py
```

`test/terminal_scenario.py` (52 checks) checks four teammates in the UI:
- A view-only guest can't run anything, even with a forged message.
- The owner gives Dev typing access.
- The terminal survives its creator closing her tab.
- Dev's Wi-Fi drops, he reconnects and re-attaches to the same shell, and a command held up in the network is refused.
- Debug works with pdb and jdb.
- Run and Debug show the right state for each language, compared with what this machine can really reach.
- A server started without the setting, and one without gdb/jdb, both name what is missing.

`test/offline_scenario.py` (54 checks) opens four separate browser profiles through the UI.
The creator closes her tab and the other three keep collaborating. One teammate reaches the
server through `test/netproxy.js`, which cuts their network for real: Wi-Fi off,
"connected but dead" Wi-Fi, lost acknowledgements, and a connection that flaps on and off.
That teammate edits offline, refreshes the page while offline, reconnects, and undoes their
offline edits. Every screen must end up with identical text, every edit exactly once. The
test also checks that **Synced** only ever appears right after the server acknowledged.

`test/fuzz.js` takes the engine straight out of `src/script.js`, so it tests the code that ships. `test/e2e.py` runs 38 judge-style checks in real Chrome, covering every feature above.
Each user is a separate browser profile, and network loss is real DevTools offline mode.

## How it works (viva notes)

```
 browser A ──┐                          ┌── browser C
 textarea    │  ops (JSON) over         │  textarea
   ↕ diff    ├─ WebSocket ── server ────┤    ↕
 CRDT Doc    │  (relay + disk log)      │  CRDT Doc
   ↕         └─ BroadcastChannel ── browser B (same machine, other tab)
 IndexedDB (every op saved before it is sent)
```

**1. Every character has a permanent ID.** An ID is `client:seq`, for example `k3f9:42`. Each
character also stores its *origin*, the character it was typed after, and a Lamport
timestamp. Text is a linked list of these characters (`TextSeq` in `src/script.js`).

**2. Merging concurrent inserts (RGA rule).** To insert after an origin, start right after it and skip
every character with a *bigger* timestamp, then insert. Concurrent inserts at the same spot
are therefore ordered by timestamp, and every replica applies the same rule. So every replica gets the
same order, whatever order the operations arrived in. A run of typing chains each character
to the previous one, so two people's words never interleave letter by letter.

**3. Deletes leave tombstones.** A deleted character stays in the list, hidden, so later
operations that point at it still work. That is why nothing is ever lost or misplaced.

**4. State vectors make offline sync cheap.** Each replica tracks, per client, the last
sequence number it has (`sv`). On reconnect it sends its state vector. The other side replies
with exactly the operations above those numbers. Operations that arrive before something
they depend on wait in a `pending` queue.

**5. Relative cursors.** A cursor is stored as "after character `k3f9:42`", not as a
number. When others type above you, your cursor and theirs stay in the right place.

**6. Per-user undo.** The undo manager records only *your* operations, grouped every 500 ms
(like VS Code). Undo deletes the characters you inserted and re-inserts copies of what you
deleted, right where it was. Teammates' edits are never recorded, so they are never undone.

**7. Files have IDs, names are fields.** Rename only changes the `name` field (last
writer wins), so edits made to the old name still land in the right file. A delete records
which edits its author had seen. Any edit they had *not* seen (made at the same time, or
offline) brings the file back, so offline work is never silently thrown away.

**8. The textarea binding.** After every input event the editor compares old and new text
(common prefix/suffix, helped by the caret position) and turns the difference into insert and delete
operations. Remote changes are applied to the document, then the textarea is refreshed and
your selection is restored from its relative position.

**9. The server is simple on purpose.** It stores operations per room, drops duplicates
and relays new ones. All merging happens in the browsers, which is why offline works.

**10. Version history is free.** A checkpoint is just a copy of the state vector. To see the
project as it was, replay only the operations at or below those numbers (`snapshotAt`).
Restoring writes the old text back as new edits, so it syncs and can be undone.

**11. Authorship comes from the IDs.** Every character's ID already says who typed it
(`asha:42`), so "who wrote this line" is just a count per line (`lineAuthors`).

**12. Surviving hostel Wi-Fi.** Your own edits are written to IndexedDB at once, so a refresh
straight after typing keeps them. "Pending" is simply every edit of yours above the server's
state vector, so nothing extra has to be stored. A connection that goes silent (Wi-Fi
"connected" but dead) is dropped after 10 seconds without an acknowledgement, or 45 seconds
without any message. The browser's `offline` event drops it at once, and the client dials
again with backoff. Anything a dead socket swallowed is sent again after the next `welcome`;
the server ignores operations it already has, so retries never duplicate text. The server
pings every 10 seconds, closes dead sockets, and tells everyone to remove that person's
cursor. A tab that reconnects replaces its old socket, so the old one can't wrongly
announce that it left. A page refresh that hangs on dead Wi-Fi falls back to the cached
app after 4 seconds.

**13. Shared terminals are owned by the room.** The server keeps its own replica of the CRDT
document per room and writes it to disk, so a shell can run the team's latest code. A
terminal process is tied to the room, not to the socket that started it; sockets only attach
and detach. Every `term-input` is checked on the server:
- the terminal is running and this socket is attached to it;
- this browser's verified identity is the owner or was given "Can type";
- it is not late. Each message carries the tab's clock, the server keeps the smallest
  (arrival - clock) seen on that socket, and input more than 2.5 s above that baseline was
  held up in the network, so it is refused rather than run.

## Known limits (say these honestly in the demo)

- Accounts have no email check and no "forgot password" (no email is sent). An admin can
  delete an account from `data/accounts.json`. Hosts that wipe the disk on redeploy also
  wipe accounts.
- A locked room's password is one shared password on top of each person's login.
- Edits made offline stay in that browser until it reconnects. If someone edits offline and
  closes the tab, their edits sync the next time they open the room link in the same browser.
- Undo history is kept per browser tab: a reload keeps it, a brand-new tab starts empty.
- Tombstones are never cleaned up, so a document's history only grows (fine for a hackathon).
- Images over about 1 MB stay on the computer that added them and are not synced.
- Java, C, C++, C#, PHP, Ruby, Swift, Kotlin and Rust run on the online Judge0 service, so
  **Run** needs internet for them. Python, SQL and TypeScript need internet the first time
  to download their runtime. Editing and syncing never need internet.
- Two files created with the same name at the same moment are shown as `name (2).ext`.
- Terminals are line mode (no vim/top: that needs `node-pty`). They end if the server restarts.
  Only the person who started a terminal can change who may type in it. The Windows shell
  (cmd.exe) path is untested.
- "Run: in browser" for Python/SQL/TypeScript after a reload offline: the runtime is only
  counted as available once it is loaded in that tab, even if the browser cached it.
- Large files (tens of thousands of lines) get slower, because syntax colouring redraws the
  whole file on every keystroke.

## Files

```
index.html          open this: the editor, built from src/ with its CSS and JavaScript inside
src/index.html      HTML: page structure only (no inline CSS or JavaScript)
src/style.css       CSS: all editor styles
src/script.js       JavaScript: part 1 is the sync engine (TextSeq, Doc, UndoManager, between the
                    CRDT-CORE markers); part 2 is the editor, with collaboration in the section
                    "H9: real-time collaboration"
build.js            joins src/ into index.html (npm run build)
sw.js               service worker: caches index.html so the app opens offline
server.js           sync server (Node + ws); serves only index.html and sw.js
test/fuzz.js        CRDT convergence + undo tests (npm test)
test/e2e.py         5-user browser test (Python Playwright)
test/offline_scenario.py  hostel Wi-Fi scenario: 4 sessions, creator leaves, real network cuts
test/netproxy.js    tiny TCP proxy the scenario uses to break one teammate's network
test/terminal_scenario.py  terminals, permissions, reattach, Run/Debug availability (52 checks)
test/account_scenario.py   create account, log in/out, wrong password, server refusal, Settings, themes (48 checks)
test/accounts_helper.py    creates real accounts for the browser tests
accounts.js         accounts and sessions on the server (scrypt hashes, rate limits, /api/...)
terminals.js        shared terminals + debug sessions (server side, used by server.js)
logo.svg            the dragon logo as a vector (also inlined in the page header)
favicon.svg         the browser-tab icon (also inlined in the page)
```

