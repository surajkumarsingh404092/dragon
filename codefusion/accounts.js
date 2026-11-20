'use strict';
// DRAGON accounts: name, email and password, kept by the sync server.
//
// - Passwords are hashed with scrypt and a random salt per account. The password itself is
//   never stored or logged.
// - Logging in returns a random 256-bit session token. Only its SHA-256 is stored, so a copy
//   of the data folder does not let anyone log in. Sessions last DRAGON_SESSION_DAYS
//   (default 30) or until logout.
// - Joining a room needs a valid session (checked in server.js), so the login page is
//   enforced by the server, not only shown by the browser.
// - Rate limits slow down password guessing: per email and per address.
// - Stored in data/accounts.json and data/sessions.json, written atomically.
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const SESSION_MS = (Number(process.env.DRAGON_SESSION_DAYS) || 30) * 86400000;
const SIGNUPS_PER_HOUR = Number(process.env.DRAGON_SIGNUPS_PER_HOUR) || 100;   // per address
const FAILED_LOGINS_PER_EMAIL = 10;                                            // per 15 minutes
const FAILED_LOGINS_PER_ADDRESS = 100;                                         // per 15 minutes
const EMAIL_RE = /^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$/;
const COLORS = ['#f97316', '#22c55e', '#38bdf8', '#f43f5e', '#a78bfa', '#2dd4bf', '#facc15', '#f472b6'];
const THEMES = ['classic', 'midnight', 'ocean', 'aurora', 'ember', 'forest', 'sunset', 'graphite'];
const SCRYPT = { N: 16384, r: 8, p: 1, maxmem: 64 * 1024 * 1024 };

function httpError(status, message) { const e = new Error(message); e.status = status; return e; }
const sha256 = text => crypto.createHash('sha256').update(text).digest('hex');
const cleanName = value => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();

function scrypt(password, salt) {
  return new Promise((resolve, reject) => crypto.scrypt(password, salt, 64, SCRYPT, (err, key) => err ? reject(err) : resolve(key)));
}
async function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  const key = await scrypt(password, salt);
  return ['scrypt', SCRYPT.N, SCRYPT.r, SCRYPT.p, salt.toString('base64'), key.toString('base64')].join('$');
}
async function passwordMatches(password, stored) {
  const [alg, , , , salt, hash] = String(stored || '').split('$');
  if (alg !== 'scrypt' || !salt || !hash) return false;
  const want = Buffer.from(hash, 'base64');
  const got = await scrypt(password, Buffer.from(salt, 'base64'));
  return got.length === want.length && crypto.timingSafeEqual(got, want);
}

function createAccounts({ dataDir }) {
  const accountsFile = path.join(dataDir, 'accounts.json');
  const sessionsFile = path.join(dataDir, 'sessions.json');
  let accounts = [];
  const sessions = new Map();                         // sha256(token) -> { id, exp }
  try { accounts = JSON.parse(fs.readFileSync(accountsFile, 'utf8')).accounts || []; } catch (e) { /* none yet */ }
  try { for (const [h, s] of Object.entries(JSON.parse(fs.readFileSync(sessionsFile, 'utf8')))) sessions.set(h, s); } catch (e) { /* none yet */ }
  // Same work for unknown emails as for real ones, so timing does not reveal who has an account.
  const dummyHash = hashPassword(crypto.randomBytes(12).toString('hex'));

  function writeAtomic(file, text) {
    const tmp = file + '.' + process.pid + '.tmp';
    fs.writeFileSync(tmp, text, { mode: 0o600 });
    fs.renameSync(tmp, file);
  }
  const saveAccounts = () => writeAtomic(accountsFile, JSON.stringify({ accounts }, null, 1));
  function saveSessions() {
    const now = Date.now();
    for (const [h, s] of sessions) if (s.exp < now) sessions.delete(h);
    writeAtomic(sessionsFile, JSON.stringify(Object.fromEntries(sessions)));
  }
  const byEmail = email => accounts.find(a => a.email === email) || null;
  const byId = id => accounts.find(a => a.id === id) || null;
  const publicUser = a => ({ id: a.id, name: a.name, email: a.email, color: a.color, theme: a.theme });

  function newSession(account) {
    const token = crypto.randomBytes(32).toString('base64url');
    sessions.set(sha256(token), { id: account.id, exp: Date.now() + SESSION_MS });
    saveSessions();
    return token;
  }
  // The account for a session token, or null (unknown, expired, or account removed).
  function verify(token) {
    if (typeof token !== 'string' || token.length < 20 || token.length > 200) return null;
    const s = sessions.get(sha256(token));
    if (!s || s.exp < Date.now()) return null;
    return byId(s.id);
  }

  // ---------- rate limits (in memory) ----------
  const hits = new Map();
  function tooMany(key, max, windowMs) {
    const now = Date.now();
    const list = (hits.get(key) || []).filter(t => now - t < windowMs);
    hits.set(key, list);
    return list.length >= max;
  }
  function note(key) { const list = hits.get(key) || []; list.push(Date.now()); hits.set(key, list); }
  setInterval(() => { const now = Date.now(); for (const [k, list] of hits) if (!list.some(t => now - t < 3600000)) hits.delete(k); }, 600000).unref();

  // ---------- validation ----------
  function checkName(value) {
    const name = cleanName(value);
    if (!name || name.length > 24) throw httpError(400, 'Please enter your name (up to 24 characters).');
    return name;
  }
  function checkEmail(value) {
    const email = String(value == null ? '' : value).trim().toLowerCase();
    if (!email || email.length > 254 || !EMAIL_RE.test(email)) throw httpError(400, 'Please enter a valid email address.');
    return email;
  }
  function checkNewPassword(value) {
    const password = typeof value === 'string' ? value : '';
    if (password.length < 8) throw httpError(400, 'Use at least 8 characters for your password.');
    if (password.length > 200) throw httpError(400, 'That password is too long (200 characters at most).');
    if (!password.trim()) throw httpError(400, 'A password cannot be only spaces.');
    return password;
  }

  // ---------- actions ----------
  async function signup(body, address) {
    const name = checkName(body.name);
    const email = checkEmail(body.email);
    const password = checkNewPassword(body.password);
    if (tooMany('signup:' + address, SIGNUPS_PER_HOUR, 3600000)) throw httpError(429, 'Too many new accounts from this network. Try again later.');
    if (byEmail(email)) throw httpError(409, 'An account with this email already exists. Log in instead.');
    const pass = await hashPassword(password);
    if (byEmail(email)) throw httpError(409, 'An account with this email already exists. Log in instead.');
    note('signup:' + address);
    const account = {
      id: crypto.randomBytes(8).toString('hex'), name, email, pass,
      color: COLORS.includes(body.color) ? body.color : COLORS[Math.floor(Math.random() * COLORS.length)],
      theme: THEMES.includes(body.theme) ? body.theme : 'classic',
      createdAt: new Date().toISOString()
    };
    accounts.push(account);
    saveAccounts();
    return { token: newSession(account), user: publicUser(account) };
  }

  async function login(body, address) {
    const email = String(body.email == null ? '' : body.email).trim().toLowerCase();
    const password = typeof body.password === 'string' ? body.password : '';
    if (!email || !password) throw httpError(400, 'Enter your email and password.');
    if (tooMany('fail-email:' + email, FAILED_LOGINS_PER_EMAIL, 900000) || tooMany('fail-addr:' + address, FAILED_LOGINS_PER_ADDRESS, 900000)) {
      throw httpError(429, 'Too many wrong passwords. Wait 15 minutes and try again.');
    }
    const account = byEmail(email);
    const ok = account ? await passwordMatches(password, account.pass) : (await passwordMatches(password, await dummyHash), false);
    if (!ok) {
      note('fail-email:' + email); note('fail-addr:' + address);
      throw httpError(401, 'Wrong email or password.');
    }
    return { token: newSession(account), user: publicUser(account) };
  }

  function logout(token) {
    if (typeof token === 'string' && sessions.delete(sha256(token))) saveSessions();
  }

  function update(account, body) {
    if (body.name !== undefined) account.name = checkName(body.name);
    if (body.color !== undefined) { if (!COLORS.includes(body.color)) throw httpError(400, 'Unknown colour.'); account.color = body.color; }
    if (body.theme !== undefined) { if (!THEMES.includes(body.theme)) throw httpError(400, 'Unknown theme.'); account.theme = body.theme; }
    saveAccounts();
    return { user: publicUser(account) };
  }

  // ---------- HTTP: /api/... ----------
  function send(res, status, data) {
    res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end(data === undefined ? '' : JSON.stringify(data));
  }
  function readJson(req) {
    return new Promise((resolve, reject) => {
      let size = 0; const chunks = [];
      req.on('data', c => { size += c.length; if (size > 16384) { reject(httpError(413, 'Request too large.')); req.destroy(); } else chunks.push(c); });
      req.on('end', () => {
        if (!chunks.length) return resolve({});
        try { const v = JSON.parse(Buffer.concat(chunks).toString('utf8')); resolve(v && typeof v === 'object' ? v : {}); }
        catch (e) { reject(httpError(400, 'Invalid JSON.')); }
      });
      req.on('error', reject);
    });
  }
  const bearer = req => { const m = /^Bearer\s+(\S+)$/i.exec(req.headers.authorization || ''); return m ? m[1] : null; };
  const addressOf = req => String(req.headers['x-forwarded-for'] || '').split(',')[0].trim() || req.socket.remoteAddress || '?';

  // Returns true if it handled the request.
  async function handleHttp(req, res, pathname) {
    if (!pathname.startsWith('/api/')) return false;
    // Tokens travel in the Authorization header (no cookies), so any page may call the API;
    // this lets the editor be hosted on a different site from the server.
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PATCH, OPTIONS');
    if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return true; }
    try {
      if (pathname === '/api/signup' && req.method === 'POST') send(res, 201, await signup(await readJson(req), addressOf(req)));
      else if (pathname === '/api/login' && req.method === 'POST') send(res, 200, await login(await readJson(req), addressOf(req)));
      else if (pathname === '/api/logout' && req.method === 'POST') { logout(bearer(req)); send(res, 204); }
      else if (pathname === '/api/me' && (req.method === 'GET' || req.method === 'PATCH')) {
        const account = verify(bearer(req));
        if (!account) throw httpError(401, 'Please log in again.');
        send(res, 200, req.method === 'GET' ? { user: publicUser(account) } : update(account, await readJson(req)));
      }
      else throw httpError(404, 'Not found.');
    } catch (e) {
      if (!e.status) console.error('[accounts]', e);
      send(res, e.status || 500, { error: e.status ? e.message : 'Something went wrong on the server.' });
    }
    return true;
  }

  return { verify, publicUser, handleHttp, count: () => accounts.length };
}

module.exports = { createAccounts };
