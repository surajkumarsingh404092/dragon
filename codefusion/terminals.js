'use strict';
// DRAGON shared terminals and debug sessions (server side).
//
// - A terminal belongs to the room (the shared workspace), not to the tab that opened it.
//   Closing the creator's tab does not stop it; anyone allowed can re-attach later and gets
//   the scrollback. It ends when its owner ends it, its program exits, or nobody has been
//   attached for DRAGON_TERMINAL_IDLE_MIN minutes (default 30).
// - The shell runs in a folder that mirrors the room's project: the server keeps its own
//   copy of the CRDT document and writes every file to data/workspaces/<room>/.
//   (One way only: files the shell changes are not copied back into the editor.)
// - Permissions are checked here, on the server: the owner can type; everyone else is
//   view-only until the owner gives them typing access. People are recognised by a
//   per-browser identity (ID + secret key, trusted on first use per room).
// - Input is only ever accepted live. Nothing is queued: input from a socket that has
//   been replaced is dropped, and input that arrives more than 2.5 s late (for example a
//   TCP backlog flushed after Wi-Fi comes back) is refused instead of executed.
// - Line mode over pipes. Full-screen programs (vim, top) need a real PTY, which needs the
//   node-pty package; it is not installed, so they are not supported.
//
// Off by default: anyone who can open a terminal can run commands on this machine.
// Turn it on with DRAGON_TERMINAL=1 only on a machine you trust your team with.

const { spawn, spawnSync, execFile } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { StringDecoder } = require('string_decoder');

const ENABLED = /^(1|true|on|yes)$/i.test(process.env.DRAGON_TERMINAL || '');
const IDLE_MS = (Number(process.env.DRAGON_TERMINAL_IDLE_MIN) || 30) * 60000;
const MAX_PER_ROOM = 4;
const MAX_TOTAL = 20;
const BUFFER_LIMIT = 200000;           // characters of scrollback kept per terminal
const STALE_INPUT_MS = 2500;           // input delayed longer than this is refused
const WIN = process.platform === 'win32';

// ---------------------------------------------------------------- tools on this machine
function which(cmd) {
  const r = WIN ? spawnSync('where', [cmd], { encoding: 'utf8' }) : spawnSync('sh', ['-c', 'command -v ' + cmd], { encoding: 'utf8' });
  return r.status === 0 && r.stdout && r.stdout.trim() ? r.stdout.trim().split(/\r?\n/)[0] : null;
}
const TOOL_NAMES = ['bash', 'python3', 'gcc', 'g++', 'gdb', 'javac', 'jdb'];
const tools = { node: true };          // this server is Node itself
if (ENABLED) for (const t of TOOL_NAMES) tools[t] = !!which(t);

// How each language is debugged. Scripts get the file as "$1" (never pasted into the
// command), so a file name cannot inject shell commands.
const DEBUGGERS = {
  python: { needs: ['bash', 'python3'], label: 'pdb', script: 'exec python3 -u -m pdb "$1"' },
  js: { needs: ['bash', 'node'], label: 'node inspect', script: 'exec "$3" inspect --port=0 "$1"' },
  c: { needs: ['bash', 'gcc', 'gdb'], label: 'gdb', script: 'mkdir -p .dragon-debug && gcc -g -O0 "$1" -o ".dragon-debug/$2" && exec gdb -q -ex "set pagination off" -ex "set confirm off" ".dragon-debug/$2"' },
  cpp: { needs: ['bash', 'g++', 'gdb'], label: 'gdb', script: 'mkdir -p .dragon-debug && g++ -g -O0 "$1" -o ".dragon-debug/$2" && exec gdb -q -ex "set pagination off" -ex "set confirm off" ".dragon-debug/$2"' },
  java: { needs: ['bash', 'javac', 'jdb'], label: 'jdb', script: 'mkdir -p .dragon-debug/java && javac -g -d .dragon-debug/java "$1" && exec jdb -classpath .dragon-debug/java -sourcepath "$(dirname "$1")" "$2"' }
};
function debugMode(file) {
  const ext = (file.split('.').pop() || '').toLowerCase();
  if (ext === 'py') return 'python';
  if (['js', 'mjs', 'cjs'].includes(ext)) return 'js';
  if (ext === 'c') return 'c';
  if (['cpp', 'cc', 'cxx'].includes(ext)) return 'cpp';
  if (ext === 'java') return 'java';
  return null;
}

function capabilities() {
  if (!ENABLED) return { terminal: false, reason: 'The sync server was started without DRAGON_TERMINAL=1.' };
  const debuggers = {};
  for (const [mode, d] of Object.entries(DEBUGGERS)) {
    const missing = d.needs.filter(t => !tools[t]);
    debuggers[mode] = missing.length ? { ok: false, label: d.label, missing } : { ok: true, label: d.label };
  }
  return { terminal: true, shell: WIN ? 'cmd.exe' : (tools.bash ? 'bash' : 'sh'), platform: process.platform, debuggers, lineMode: true };
}

// ---------------------------------------------------------------- the project on disk
let Engine = null;
function engine() {
  if (Engine) return Engine;
  for (const file of [path.join(__dirname, 'src', 'script.js'), path.join(__dirname, 'index.html')]) {
    let code;
    try { code = fs.readFileSync(file, 'utf8'); } catch (e) { continue; }
    const start = code.indexOf('// ===== CRDT-CORE-START =====');
    const end = code.indexOf('// ===== CRDT-CORE-END =====');
    if (start < 0 || end < 0) continue;
    const sandbox = { module: { exports: {} } };
    sandbox.globalThis = sandbox;
    vm.runInNewContext(code.slice(start, end), sandbox, { filename: file + '#crdt-core' });
    Engine = sandbox.module.exports;
    return Engine;
  }
  throw new Error('CRDT engine not found (src/script.js or index.html)');
}

function cleanRelPath(name) {
  const parts = String(name || '').replace(/\\/g, '/').split('/')
    .map(p => p.replace(/[\x00-\x1f]/g, '').trim())
    .filter(p => p && p !== '.' && p !== '..');
  return parts.join('/').slice(0, 240);
}

class Workspace {
  constructor(room, dataDir) {
    this.room = room;
    this.dir = path.join(dataDir, 'workspaces', room.name);
    this.doc = null;
    this.written = new Map();          // relative path -> content we last wrote
    this.fidPath = new Map();
    this.dirty = true;
    this.timer = null;
  }
  ensure() {
    if (!this.doc) {
      const { Doc } = engine();
      this.doc = new Doc('~server');
      this.doc.apply(this.room.log, 'remote');
    }
    fs.mkdirSync(this.dir, { recursive: true });
    this.flush();
  }
  add(ops) {
    if (!this.doc) return;
    this.doc.apply(ops, 'remote');
    this.dirty = true;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.flush(), 150);
  }
  flush() {
    clearTimeout(this.timer);
    if (!this.doc || !this.dirty) return;
    this.dirty = false;
    const doc = this.doc;
    const want = new Map();
    this.fidPath.clear();
    for (const fid of doc.fileIds().sort()) {
      let rel = cleanRelPath(doc.get(fid, 'name')) || 'untitled.txt';
      if (want.has(rel)) {
        const slash = rel.lastIndexOf('/'), dotAt = rel.lastIndexOf('.');
        const dot = dotAt > slash ? dotAt : rel.length;
        let n = 2;
        while (want.has(rel.slice(0, dot) + ' (' + n + ')' + rel.slice(dot))) n++;
        rel = rel.slice(0, dot) + ' (' + n + ')' + rel.slice(dot);
      }
      if (rel === '.dragon-debug' || rel.startsWith('.dragon-debug/')) continue;
      const data = doc.get(fid, 'data');
      const m = typeof data === 'string' && /^data:[^,]*;base64,(.*)$/s.exec(data);
      want.set(rel, m ? Buffer.from(m[1], 'base64') : doc.text(fid));
      this.fidPath.set(fid, rel);
    }
    for (const folder of doc.folders()) {
      const rel = cleanRelPath(folder);
      const full = path.resolve(this.dir, rel);
      if (rel && full.startsWith(this.dir + path.sep)) fs.mkdirSync(full, { recursive: true });
    }
    for (const [rel, content] of want) {
      const full = path.resolve(this.dir, rel);
      if (!full.startsWith(this.dir + path.sep)) continue;
      const prev = this.written.get(rel);
      const same = prev !== undefined && (Buffer.isBuffer(content) ? Buffer.isBuffer(prev) && prev.equals(content) : prev === content);
      if (same && fs.existsSync(full)) continue;
      try {
        fs.mkdirSync(path.dirname(full), { recursive: true });
        fs.writeFileSync(full, content);
        this.written.set(rel, content);
      } catch (e) { /* a folder and a file with the same name: skip */ }
    }
    for (const rel of [...this.written.keys()]) {
      if (want.has(rel)) continue;
      this.written.delete(rel);
      try { fs.unlinkSync(path.resolve(this.dir, rel)); } catch (e) { /* already gone */ }
    }
  }
  pathOf(fid) { this.flush(); return this.fidPath.get(fid) || null; }
}

// ---------------------------------------------------------------- terminals
function createTerminalManager({ dataDir, send }) {
  let total = 0;

  function identify(room, user) {
    if (!user || typeof user.id !== 'string' || typeof user.key !== 'string') return null;
    const id = user.id.slice(0, 40), key = user.key.slice(0, 100);
    if (!/^[\w-]{6,40}$/.test(id) || key.length < 16) return null;
    if (!room.identities) room.identities = new Map();
    const hash = crypto.createHash('sha256').update(room.name + ':' + key).digest();
    const known = room.identities.get(id);
    if (!known) { room.identities.set(id, hash); return id; }
    return known.length === hash.length && crypto.timingSafeEqual(known, hash) ? id : null;
  }

  function terms(room) { if (!room.terms) room.terms = new Map(); return room.terms; }
  function names(room) { if (!room.userNames) room.userNames = new Map(); return room.userNames; }
  function roleOf(term, userId) {
    if (!userId) return 'view';
    if (term.owner === userId) return 'owner';
    return term.perms.get(userId) === 'interactive' ? 'interactive' : 'view';
  }

  function listFor(room) {
    const online = new Map();
    for (const ws of room.clients) if (ws.userId) online.set(ws.userId, (ws.aw && ws.aw.name) || names(room).get(ws.userId) || 'Guest');
    for (const [id, name] of online) names(room).set(id, name);
    const ids = new Set(online.keys());
    for (const term of terms(room).values()) { ids.add(term.owner); for (const id of term.perms.keys()) ids.add(id); }
    return {
      t: 'term-list',
      caps: capabilities(),
      members: [...ids].map(id => ({ userId: id, name: names(room).get(id) || 'Guest', online: online.has(id) })),
      terms: [...terms(room).values()].map(term => ({
        id: term.id, title: term.title, kind: term.kind, status: term.status, exitCode: term.exitCode,
        owner: term.owner, ownerName: names(room).get(term.owner) || 'Guest',
        perms: Object.fromEntries(term.perms), viewers: term.attached.size, pid: term.proc ? term.proc.pid : null
      }))
    };
  }
  function broadcastList(room) {
    if (!ENABLED) return;
    const list = listFor(room);
    for (const ws of room.clients) send(ws, list);
  }

  function output(term, text) {
    if (!text) return;
    term.buffer += text;
    if (term.buffer.length > BUFFER_LIMIT) term.buffer = term.buffer.slice(term.buffer.length - BUFFER_LIMIT);
    for (const ws of term.attached) send(ws, { t: 'term-out', id: term.id, data: text });
  }

  function safeEnv(home) {
    const env = { PATH: process.env.PATH || '/usr/bin:/bin', HOME: home, LANG: process.env.LANG || 'C.UTF-8', TERM: 'dumb', NO_COLOR: '1', PYTHONUNBUFFERED: '1', PAGER: 'cat', GIT_PAGER: 'cat' };
    if (WIN) for (const k of ['SystemRoot', 'ComSpec', 'PATHEXT', 'TEMP', 'TMP', 'windir']) if (process.env[k]) env[k] = process.env[k];
    return env;
  }

  function start(room, ws, msg) {
    if (!ENABLED) return deny(ws, null, 'The sync server was started without DRAGON_TERMINAL=1.');
    if (!ws.userId) return deny(ws, null, 'Your browser identity could not be verified for this room, so you cannot open terminals.');
    const running = [...terms(room).values()].filter(t => t.status === 'running').length;
    if (running >= MAX_PER_ROOM || total >= MAX_TOTAL) return deny(ws, null, 'Too many terminals are running. End one first.');
    if (!room.workspace) room.workspace = new Workspace(room, dataDir);
    room.workspace.ensure();
    const cwd = room.workspace.dir;
    const who = (ws.aw && ws.aw.name) || 'Guest';
    let cmd, args, title, kind = 'shell', banner;
    if (msg.kind === 'debug') {
      const rel = room.workspace.pathOf(String(msg.fid || ''));
      if (!rel) return deny(ws, null, 'That file is not in the project (yet). Wait for it to sync, then try again.');
      const mode = debugMode(rel);
      const d = mode && DEBUGGERS[mode];
      if (!d) return deny(ws, null, 'DRAGON has no debugger for ' + rel + '.');
      const missing = d.needs.filter(t => !tools[t]);
      if (missing.length) return deny(ws, null, 'Debug unavailable: ' + missing.join(', ') + ' not installed on the sync server.');
      const base = path.basename(rel).replace(/\.[^.]+$/, '').replace(/[^\w]/g, '_') || 'program';
      cmd = 'bash'; args = ['-c', d.script, 'dragon-debug', rel, base, process.execPath];
      kind = 'debug'; title = 'Debug ' + path.basename(rel);
      banner = '● ' + d.label + ' on ' + rel + ' · started by ' + who + '. Type debugger commands below (for example: n, s, p x, c).\n';
    } else {
      if (WIN) { cmd = 'cmd.exe'; args = ['/Q', '/K', 'prompt $G']; }
      else { cmd = tools.bash ? 'bash' : 'sh'; args = tools.bash ? ['--noprofile', '--norc'] : []; }
      title = 'Terminal ' + (terms(room).size + 1);
      banner = '● Shared ' + (WIN ? 'cmd.exe' : cmd) + ' in the project folder · started by ' + who + '. Line mode: type a command and press Enter.\n';
    }
    let proc;
    try { proc = spawn(cmd, args, { cwd, env: safeEnv(cwd), detached: !WIN, stdio: ['pipe', 'pipe', 'pipe'] }); }
    catch (e) { return deny(ws, null, 'Could not start ' + cmd + ': ' + e.message); }
    const term = {
      id: 't' + crypto.randomBytes(5).toString('hex'), title, kind, owner: ws.userId, perms: new Map(),
      proc, status: 'running', exitCode: null, buffer: '', attached: new Set(), lastAttached: Date.now()
    };
    total++;
    terms(room).set(term.id, term);
    const out = new StringDecoder('utf8'), err = new StringDecoder('utf8');
    const clean = s => s.replace(/\x1b\[[0-9;?]*[ -\/]*[@-~]/g, '').replace(/\r(?!\n)/g, '');
    proc.stdout.on('data', b => output(term, clean(out.write(b))));
    proc.stderr.on('data', b => output(term, clean(err.write(b))));
    proc.stdin.on('error', () => {});
    proc.on('error', e => output(term, '\n[could not start: ' + e.message + ']\n'));
    proc.on('exit', code => {
      if (term.status !== 'running') return;
      term.status = 'exited'; term.exitCode = code; total--;
      output(term, '\n[process exited' + (code == null ? '' : ' with code ' + code) + ']\n');
      broadcastList(room);
      term.removeTimer = setTimeout(() => { terms(room).delete(term.id); broadcastList(room); }, 10 * 60000);
    });
    output(term, banner);
    attach(room, ws, term.id);
    broadcastList(room);
  }

  function attach(room, ws, id) {
    for (const t of terms(room).values()) if (t.attached.delete(ws)) t.lastAttached = Date.now();
    const term = terms(room).get(String(id));
    if (!term) return deny(ws, id, 'That terminal has ended.');
    term.attached.add(ws);
    ws.termId = term.id;
    send(ws, { t: 'term-snapshot', id: term.id, data: term.buffer, role: roleOf(term, ws.userId) });
  }

  function deny(ws, id, reason) { send(ws, { t: 'term-denied', id: id || null, reason }); }

  function input(room, ws, msg) {
    const term = terms(room).get(String(msg.id));
    if (!term || term.status !== 'running') return deny(ws, msg.id, 'That terminal is not running.');
    if (!term.attached.has(ws)) return deny(ws, msg.id, 'Not attached to that terminal. Nothing was run.');
    const role = roleOf(term, ws.userId);
    if (role === 'view') return deny(ws, msg.id, 'View only: you cannot type in this terminal. Nothing was run.');
    const delay = Number.isFinite(msg.ct) && Number.isFinite(ws.minLag) ? (Date.now() - msg.ct) - ws.minLag : Infinity;
    if (delay > STALE_INPUT_MS) {
      output(term, '[input from ' + ((ws.aw && ws.aw.name) || 'Guest') + ' refused: it arrived ' + (Number.isFinite(delay) ? (delay / 1000).toFixed(1) + ' s' : 'too') + ' late after a connection problem, so it was not run]\n');
      return deny(ws, msg.id, 'Not run: your input arrived ' + (Number.isFinite(delay) ? (delay / 1000).toFixed(1) + ' s' : '') + ' late after a connection problem. Nothing is queued; type it again if you still want it.');
    }
    if (msg.signal === 'INT') {
      output(term, '^C (' + ((ws.aw && ws.aw.name) || 'Guest') + ')\n');
      if (WIN) return;
      if (term.kind === 'shell') execFile('pkill', ['-INT', '-P', String(term.proc.pid)], () => {});
      else { try { term.proc.kill('SIGINT'); } catch (e) {} }
      return;
    }
    let data = String(msg.data || '').replace(/\r/g, '').replace(/\x00/g, '').slice(0, 4000);
    if (!data.endsWith('\n')) data += '\n';
    if (room.workspace) room.workspace.flush();            // the shell sees the latest code
    const who = (ws.aw && ws.aw.name) || 'Guest';
    const lead = term.buffer && !term.buffer.endsWith('\n') ? '\n' : '';
    output(term, lead + data.split('\n').slice(0, -1).map(line => '› ' + who + ': ' + line).join('\n') + '\n');
    try { term.proc.stdin.write(data); } catch (e) { /* process gone */ }
  }

  function setPerm(room, ws, msg) {
    const term = terms(room).get(String(msg.id));
    if (!term) return deny(ws, msg.id, 'That terminal has ended.');
    if (roleOf(term, ws.userId) !== 'owner') return deny(ws, msg.id, 'Only the person who started this terminal can change who may type.');
    const user = String(msg.user || '');
    if (!user || user === term.owner) return;
    if (msg.role === 'interactive') term.perms.set(user, 'interactive'); else term.perms.delete(user);
    output(term, '[' + ((ws.aw && ws.aw.name) || 'Owner') + ' set ' + (names(room).get(user) || 'Guest') + ' to ' + (msg.role === 'interactive' ? 'can type' : 'view only') + ']\n');
    broadcastList(room);
  }

  function end(room, ws, msg) {
    const term = terms(room).get(String(msg.id));
    if (!term) return;
    if (roleOf(term, ws.userId) !== 'owner') return deny(ws, msg.id, 'Only the person who started this terminal can end it.');
    kill(term);
    clearTimeout(term.removeTimer);
    terms(room).delete(term.id);
    broadcastList(room);
  }

  // The process's own "exit" handler does the bookkeeping once it has really stopped.
  function kill(term) {
    const pid = term.proc && term.proc.pid;
    if (!pid) return;
    try { if (WIN) term.proc.kill(); else process.kill(-pid, 'SIGTERM'); } catch (e) {}
    setTimeout(() => { try { if (!WIN) process.kill(-pid, 'SIGKILL'); } catch (e) {} }, 1500).unref();
  }

  return {
    enabled: ENABLED,
    capabilities,
    identify,
    hello(room, ws) { if (ENABLED) broadcastList(room); },
    ops(room, added) { if (room.workspace && added.length) room.workspace.add(added); },
    leave(room, ws) {
      for (const t of terms(room).values()) if (t.attached.delete(ws)) t.lastAttached = Date.now();
      broadcastList(room);
    },
    handle(room, ws, msg) {
      switch (msg.t) {
        case 'ping': send(ws, { t: 'pong' }); return true;
        case 'term-new': start(room, ws, msg); return true;
        case 'term-attach': if (!ENABLED) deny(ws, msg.id, capabilities().reason); else attach(room, ws, msg.id); return true;
        case 'term-detach': for (const t of terms(room).values()) if (t.attached.delete(ws)) t.lastAttached = Date.now(); return true;
        case 'term-input': if (!ENABLED) deny(ws, msg.id, capabilities().reason); else input(room, ws, msg); return true;
        case 'term-perm': setPerm(room, ws, msg); return true;
        case 'term-close': end(room, ws, msg); return true;
        default: return false;
      }
    },
    reap(rooms) {
      const now = Date.now();
      for (const room of rooms) for (const term of terms(room).values()) {
        if (term.status === 'running' && term.attached.size === 0 && now - term.lastAttached > IDLE_MS) {
          output(term, '\n[ended after ' + Math.round(IDLE_MS / 60000) + ' minutes with nobody attached]\n');
          kill(term);
          broadcastList(room);
        }
      }
    },
    shutdown(rooms) { for (const room of rooms) for (const term of terms(room).values()) kill(term); }
  };
}

module.exports = { createTerminalManager, capabilities, ENABLED };
