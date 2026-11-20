#!/usr/bin/env node
// DRAGON sync server.
// - Serves the editor (index.html, built from src/) and a service worker so it also opens offline.
// - Relays CRDT operations between everyone in the same room over WebSockets.
// - Saves every operation to data/<room>.jsonl so documents survive a server restart.
// - Optional room password: only its salted hash is stored, in data/<room>.meta.json.
// - Accounts (name, email, password) at /api/...; joining a room needs a valid login.
// The server never merges text itself: clients do that with the CRDT. It only stores
// operations, removes duplicates, and tells each client what it is missing.
'use strict';
const http = require('http');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { WebSocketServer } = require('ws');
const { createTerminalManager } = require('./terminals');
const { createAccounts } = require('./accounts');

const PORT = Number(process.env.PORT) || 8787;
const DATA_DIR = process.env.DATA_DIR || path.join(__dirname, 'data');
const ROOT = __dirname;
// Only these files are public. Everything else (server.js, data/, test/) is never served.
const PUBLIC = { '/': 'index.html', '/index.html': 'index.html', '/sw.js': 'sw.js' };
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8' };
fs.mkdirSync(DATA_DIR, { recursive: true });
const accounts = createAccounts({ dataDir: DATA_DIR });


// ---------------------------------------------------------------- rooms and storage
const rooms = new Map();
const span = op => (op.k === 'ins' ? op.x.length : 1);
const safeRoom = name => String(name || 'main').replace(/[^\w-]/g, '').slice(0, 40) || 'main';

function getRoom(name) {
  let room = rooms.get(name);
  if (room) return room;
  room = { name, log: [], sv: Object.create(null), pending: [], clients: new Set(), file: path.join(DATA_DIR, name + '.jsonl'), meta: path.join(DATA_DIR, name + '.meta.json'), passHash: null, salt: null };
  try { const m = JSON.parse(fs.readFileSync(room.meta, 'utf8')); room.passHash = m.passHash || null; room.salt = m.salt || null; } catch (e) { /* no password */ }
  if (fs.existsSync(room.file)) {
    const lines = fs.readFileSync(room.file, 'utf8').split('\n');
    const ops = [];
    for (const line of lines) { if (!line.trim()) continue; try { ops.push(JSON.parse(line)); } catch (e) { /* skip a torn last line */ } }
    addOps(room, ops, false);
    console.log(`[room ${name}] loaded ${room.log.length} operations from disk`);
  }
  rooms.set(name, room);
  return room;
}

function hashPassword(room, password) {
  return crypto.createHash('sha256').update(room.salt + ':' + room.name + ':' + password).digest('hex');
}
function checkPassword(room, password) {
  if (!room.passHash) return true;
  const given = Buffer.from(hashPassword(room, String(password || '')), 'hex');
  const want = Buffer.from(room.passHash, 'hex');
  return given.length === want.length && crypto.timingSafeEqual(given, want);
}
function setPassword(room, password) {
  if (password) { room.salt = crypto.randomBytes(16).toString('hex'); room.passHash = hashPassword(room, password); }
  else { room.salt = null; room.passHash = null; }
  fs.writeFileSync(room.meta, JSON.stringify({ passHash: room.passHash, salt: room.salt }));
}

function validOp(op) {
  if (!op || typeof op !== 'object') return false;
  if (typeof op.c !== 'string' || !op.c || op.c.length > 40) return false;
  if (!Number.isInteger(op.s) || op.s < 1 || !Number.isInteger(op.lam) || op.lam < 0) return false;
  if (typeof op.f !== 'string' || op.f.length > 120) return false;
  if (op.k === 'ins') return typeof op.x === 'string' && op.x.length > 0 && typeof op.o === 'string';
  if (op.k === 'del') return Array.isArray(op.d);
  if (op.k === 'set') return typeof op.key === 'string';
  return false;
}

// Store operations in per-client order. Returns the ones that were new.
function addOps(room, ops, persist = true) {
  const added = [];
  const tryAdd = op => {
    const have = room.sv[op.c] || 0;
    if (op.s + span(op) - 1 <= have) return 'dup';
    if (op.s !== have + 1) return 'gap';
    room.sv[op.c] = op.s + span(op) - 1;
    room.log.push(op);
    added.push(op);
    return 'ok';
  };
  for (const op of ops) if (validOp(op) && tryAdd(op) === 'gap') room.pending.push(op);
  let progress = true;
  while (progress && room.pending.length) {
    progress = false;
    room.pending = room.pending.filter(op => {
      const r = tryAdd(op);
      if (r === 'ok') progress = true;
      return r === 'gap';
    });
  }
  if (persist && added.length) fs.appendFileSync(room.file, added.map(op => JSON.stringify(op)).join('\n') + '\n');
  return added;
}

function missingFor(room, sv) {
  sv = sv || {};
  return room.log.filter(op => op.s + span(op) - 1 > (sv[op.c] || 0));
}

// ---------------------------------------------------------------- HTTP
function sendFile(res, file) {
  fs.readFile(file, (err, data) => {
    if (err) { res.writeHead(404); res.end('Not found'); return; }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-cache' });
    res.end(data);
  });
}

const server = http.createServer((req, res) => {
  let pathname;
  try { pathname = decodeURIComponent(new URL(req.url, 'http://x').pathname); }
  catch (e) { res.writeHead(400); res.end('Bad request'); return; }
  if (PUBLIC[pathname]) return sendFile(res, path.join(ROOT, PUBLIC[pathname]));
  if (pathname.startsWith('/api/')) { accounts.handleHttp(req, res, pathname); return; }
  if (pathname === '/health') {
    const info = {};
    for (const [name, r] of rooms) info[name] = { users: r.clients.size, operations: r.log.length };
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true, rooms: info }));
    return;
  }
  res.writeHead(404); res.end('Not found');
});

// ---------------------------------------------------------------- WebSockets
const wss = new WebSocketServer({ server, path: '/ws', maxPayload: 32 * 1024 * 1024 });
const send = (ws, msg) => { if (ws.readyState === 1) ws.send(JSON.stringify(msg)); };
// The name teammates see is the account's name, whatever the browser claims.
function withAccountName(ws, state) {
  if (!state || typeof state !== 'object') return null;
  return ws.account ? Object.assign({}, state, { name: ws.account.name }) : state;
}
const terminals = createTerminalManager({ dataDir: DATA_DIR, send });

wss.on('connection', ws => {
  ws.isAlive = true;
  ws.on('pong', () => { ws.isAlive = true; });
  ws.on('message', raw => {
    let msg;
    try { msg = JSON.parse(raw); } catch (e) { return; }
    if (!msg || typeof msg !== 'object') return;
    // Smallest observed (arrival - client clock) on this socket: the baseline for spotting
    // terminal input that was held up in the network and arrives late.
    if (Number.isFinite(msg.ct)) { const lag = Date.now() - msg.ct; if (!(ws.minLag <= lag)) ws.minLag = lag; }
    if (msg.t === 'hello') {
      // Log in first: no valid session, no room. (Checked before the room password.)
      const account = accounts.verify(msg.auth);
      if (!account) {
        send(ws, { t: 'denied', reason: 'login' });
        ws.close(4003, 'login required');
        return;
      }
      const room = getRoom(safeRoom(msg.room));
      if (!checkPassword(room, msg.password)) {
        send(ws, { t: 'denied', reason: msg.password ? 'wrong' : 'locked' });
        ws.close(4001, 'room locked');
        return;
      }
      ws.room = room;
      ws.client = String(msg.client || '').slice(0, 40);
      ws.account = account;
      ws.aw = withAccountName(ws, msg.state);
      ws.userId = 'acct-' + account.id;                  // terminal permissions follow the account
      // Same tab reconnecting (e.g. after Wi-Fi dropped): its old socket may still look open
      // here. Retire it quietly so it cannot later announce that this person left.
      for (const other of room.clients) {
        if (other !== ws && other.client === ws.client) { other.replaced = true; room.clients.delete(other); try { other.terminate(); } catch (e) {} }
      }
      room.clients.add(ws);
      send(ws, { t: 'welcome', sv: room.sv, locked: !!room.passHash, caps: terminals.capabilities(), account: accounts.publicUser(account) });
      const ops = missingFor(room, msg.sv);
      for (let i = 0; i < ops.length; i += 2000) send(ws, { t: 'ops', ops: ops.slice(i, i + 2000) });
      for (const other of room.clients) if (other !== ws && other.aw) send(ws, { t: 'aw', client: other.client, state: other.aw });
      if (ws.aw) for (const other of room.clients) if (other !== ws) send(other, { t: 'aw', client: ws.client, state: ws.aw });
      terminals.hello(room, ws);
      return;
    }
    const room = ws.room;
    if (!room) return;
    if (msg.t === 'ops' && Array.isArray(msg.ops)) {
      const added = addOps(room, msg.ops);
      terminals.ops(room, added);
      if (added.length) for (const other of room.clients) if (other !== ws) send(other, { t: 'ops', ops: added });
      const ack = {};
      for (const op of msg.ops) if (op && typeof op.c === 'string') ack[op.c] = room.sv[op.c] || 0;
      send(ws, { t: 'ack', sv: ack });
    } else if (msg.t === 'sync') {
      // Self-healing: the client checks in now and then; send anything either side lacks.
      const ops = missingFor(room, msg.sv);
      for (let i = 0; i < ops.length; i += 2000) send(ws, { t: 'ops', ops: ops.slice(i, i + 2000) });
      send(ws, { t: 'resync', sv: room.sv });
    } else if (msg.t === 'lock') {
      const password = String(msg.password || '').slice(0, 200);
      setPassword(room, password);
      for (const client of room.clients) send(client, { t: 'locked', locked: !!password, by: ws.aw && ws.aw.name ? String(ws.aw.name).slice(0, 24) : 'Someone' });
    } else if (msg.t === 'aw') {
      const oldName = ws.aw && ws.aw.name;
      ws.aw = withAccountName(ws, msg.state);
      for (const other of room.clients) if (other !== ws) send(other, { t: 'aw', client: ws.client, state: ws.aw });
      if (ws.aw && ws.aw.name !== oldName) terminals.hello(room, ws);
    } else {
      terminals.handle(room, ws, msg);
    }
  });
  ws.on('close', () => {
    const room = ws.room;
    if (!room) return;
    if (ws.replaced) { terminals.leave(room, ws); return; }
    room.clients.delete(ws);
    terminals.leave(room, ws);
    for (const other of room.clients) if (other.client === ws.client) return;   // still here on another socket
    for (const other of room.clients) send(other, { t: 'aw-leave', client: ws.client });
  });
});

// Drop connections that stopped answering (closed laptop lids, dead Wi-Fi). A dead socket
// is closed within 10-20 seconds, and teammates are told to remove that person's cursor.
const HEARTBEAT_MS = Number(process.env.HEARTBEAT_MS) || 10000;
const heartbeat = setInterval(() => {
  for (const ws of wss.clients) {
    if (!ws.isAlive) { ws.terminate(); continue; }
    ws.isAlive = false;
    try { ws.ping(); } catch (e) { /* ignore */ }
  }
}, HEARTBEAT_MS);
wss.on('close', () => clearInterval(heartbeat));
setInterval(() => terminals.reap(rooms.values()), 60000).unref();
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { terminals.shutdown(rooms.values()); process.exit(0); });

server.listen(PORT, () => {
  console.log(`DRAGON sync server on http://localhost:${PORT}  (data in ${DATA_DIR})`);
  console.log(`Accounts: ${accounts.count()} (log in required to join rooms)`);
  const caps = terminals.capabilities();
  if (!caps.terminal) console.log('Shared terminals and Debug: off (start with DRAGON_TERMINAL=1 to turn them on)');
  else console.log('Shared terminals and Debug: ON. Anyone who can open a terminal can run commands on this machine. Debuggers: ' +
    Object.entries(caps.debuggers).map(([m, d]) => m + '=' + (d.ok ? d.label : 'missing ' + d.missing.join('+'))).join(', '));
});
