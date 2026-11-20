#!/usr/bin/env node
// Convergence + undo tests for DragonCRDT.
// Usage:  node test/fuzz.js            (tests the engine inside index.html)
//         node test/fuzz.js 2000       (more random runs)
// It takes the engine straight out of src/script.js, so it tests the exact code that ships.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function loadEngine() {
  const file = process.env.CRDT_FILE || path.join(__dirname, '..', 'src', 'script.js');
  const code = fs.readFileSync(file, 'utf8');
  const start = code.indexOf('// ===== CRDT-CORE-START =====');
  const end = code.indexOf('// ===== CRDT-CORE-END =====');
  if (start < 0 || end < 0) throw new Error('CRDT-CORE markers not found in ' + file);
  const sandbox = { module: { exports: {} } };
  sandbox.globalThis = sandbox;
  vm.runInNewContext(code.slice(start, end), sandbox, { filename: file + '#crdt-core' });
  return sandbox.module.exports;
}
const { Doc, UndoManager, expandIds } = loadEngine();

let failures = 0;
function check(cond, msg) { if (!cond) { failures++; console.error('  FAIL:', msg); } }

// Deterministic random numbers so a failing run can be replayed.
function rng(seed) {
  let x = seed >>> 0 || 1;
  return () => { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return (x >>> 0) / 4294967296; };
}

function syncPair(from, to, rand, partial) {
  let ops = from.opsMissing(to.sv);
  if (rand) ops = ops.slice().sort(() => rand() - 0.5);            // out-of-order delivery
  if (partial && ops.length > 1) ops = ops.slice(0, 1 + Math.floor(rand() * ops.length)); // lost messages
  to.apply(JSON.parse(JSON.stringify(ops)));                        // like the network: copies only
}
function syncAll(docs) {
  for (let round = 0; round < 4; round++)
    for (const a of docs) for (const b of docs) if (a !== b) syncPair(a, b);
}
function snapshot(doc) {
  const files = {};
  for (const fid of doc.reg.keys()) {
    files[fid] = { visible: doc.isVisible(fid), name: doc.get(fid, 'name'), text: doc.text(fid) };
  }
  for (const fid of doc.seqs.keys()) if (!files[fid]) files[fid] = { text: doc.text(fid) };
  return JSON.stringify(files, Object.keys(files).sort());
}

// ---------------------------------------------------------------- scenario tests
function scenarios() {
  console.log('Scenario tests');
  // 1. Concurrent typing at the same spot never interleaves characters.
  {
    const a = new Doc('a'), b = new Doc('b');
    a.insert('f', 0, 'aaa'); b.insert('f', 0, 'bbb');
    syncAll([a, b]);
    check(a.text('f') === b.text('f'), 'same text after concurrent insert');
    check(['aaabbb', 'bbbaaa'].includes(a.text('f')), 'no interleaving: got ' + a.text('f'));
  }
  // 2. Per-user undo: A's undo removes only A's text.
  {
    const a = new Doc('a'), b = new Doc('b');
    const ua = new UndoManager(a);
    ua.track(a.insert('f', 0, 'hello'));
    syncAll([a, b]);
    b.insert('f', 5, ' world');
    syncAll([a, b]);
    const caret = ua.undo('f');
    syncAll([a, b]);
    check(a.text('f') === ' world' && b.text('f') === ' world', 'per-user undo kept teammate text: ' + JSON.stringify(a.text('f')));
    check(caret === 0, 'caret after undo at 0, got ' + caret);
    ua.redo('f');
    syncAll([a, b]);
    check(a.text('f') === 'hello world' && b.text('f') === 'hello world', 'redo restores: ' + JSON.stringify(b.text('f')));
  }
  // 3. Undo a deletion of a teammate's text brings it back in the same place.
  {
    const a = new Doc('a'), b = new Doc('b');
    b.insert('f', 0, 'one two three');
    syncAll([a, b]);
    const ua = new UndoManager(a);
    ua.track(a.delete('f', 3, 4)); // remove " two"
    syncAll([a, b]);
    check(b.text('f') === 'one three', 'delete synced');
    ua.undo('f');
    syncAll([a, b]);
    check(a.text('f') === 'one two three' && b.text('f') === 'one two three', 'undo of delete restored: ' + JSON.stringify(b.text('f')));
  }
  // 4. Offline edits on both sides of the same line merge with nothing lost.
  {
    const a = new Doc('a'), b = new Doc('b');
    a.insert('f', 0, 'let x = 1;\n');
    syncAll([a, b]);
    a.insert('f', 0, '// A was here\n');          // both offline now
    b.insert('f', 10, ' // B: changed');
    b.delete('f', 8, 1); b.insert('f', 8, '2');
    syncAll([a, b]);
    const t = a.text('f');
    check(t === b.text('f'), 'offline merge converged');
    check(t.includes('// A was here') && t.includes('// B: changed') && t.includes('= 2'), 'offline merge kept both edits: ' + JSON.stringify(t));
  }
  // 5. Delete vs edit: an offline edit to a deleted file brings the file back.
  {
    const a = new Doc('a'), b = new Doc('b');
    a.set('f', 'name', 'app.js'); a.insert('f', 0, 'code');
    syncAll([a, b]);
    a.deleteFile('f');                             // A deletes the file
    b.insert('f', 4, ' more');                     // B, offline, keeps editing it
    syncAll([a, b]);
    check(a.isVisible('f') && b.isVisible('f'), 'edited file restored on both');
    check(a.text('f') === 'code more', 'restored file has the offline edit');
  }
  // 5b. Even a single concurrent character wins over the delete.
  {
    const a = new Doc('a'), b = new Doc('b');
    a.set('f', 'name', 'x.js'); for (let i = 0; i < 30; i++) a.insert('f', i, 'z');
    syncAll([a, b]);
    a.deleteFile('f');
    b.insert('f', 0, '!');
    syncAll([a, b]);
    check(a.isVisible('f') && b.isVisible('f'), 'one-character concurrent edit restores the file');
  }
  // 5c. Edits the deleter had already seen do not bring the file back.
  {
    const a = new Doc('a'), b = new Doc('b');
    a.set('f', 'name', 'x.js'); b.insert('f', 0, 'old work');
    syncAll([a, b]);
    a.deleteFile('f');
    syncAll([a, b]);
    check(!a.isVisible('f') && !b.isVisible('f'), 'seen edits do not resurrect a deleted file');
  }
  // 6. Rename vs edit: content stays attached to the file ID.
  {
    const a = new Doc('a'), b = new Doc('b');
    a.set('f', 'name', 'app.js'); a.insert('f', 0, 'x');
    syncAll([a, b]);
    a.set('f', 'name', 'main.js');
    b.insert('f', 1, 'yz');
    syncAll([a, b]);
    check(b.get('f', 'name') === 'main.js' && b.text('f') === 'xyz' && a.text('f') === 'xyz', 'rename + edit merged');
  }
  // 7. Typing inside your own just-typed text, then undo, leaves nothing behind.
  {
    const a = new Doc('a');
    const ua = new UndoManager(a);
    let t = 0;
    ua.track(a.insert('f', 0, 'abc'), t += 10);
    ua.track(a.delete('f', 2, 1), t += 10);
    ua.track(a.insert('f', 2, 'Z'), t += 10);
    ua.undo('f');
    check(a.text('f') === '', 'grouped step fully undone: ' + JSON.stringify(a.text('f')));
  }
  // 9. Version history: a snapshot shows the project exactly as it was.
  {
    const a = new Doc('a'), b = new Doc('b');
    a.set('f', 'name', 'app.js'); a.insert('f', 0, 'v1');
    syncAll([a, b]);
    const checkpoint = Object.assign({}, a.sv);
    b.delete('f', 0, 2); b.insert('f', 0, 'v2 by b'); b.set('g', 'name', 'new.js');
    syncAll([a, b]);
    const old = a.snapshotAt(checkpoint);
    check(old.text('f') === 'v1' && !old.isVisible('g') && a.text('f') === 'v2 by b', 'snapshot shows the old version');
  }
  // 10. Who wrote each line.
  {
    const a = new Doc('a'), b = new Doc('b');
    a.insert('f', 0, 'alpha\n');
    syncAll([a, b]);
    b.insert('f', 6, 'bravo bravo\nxy');
    a.insert('f', 0, 'z');
    syncAll([a, b]);
    const authors = b.lineAuthors('f');
    check(JSON.stringify(authors) === JSON.stringify(['a', 'b', 'b']), 'line authors: ' + JSON.stringify(authors));
  }
  // 11. Undo history survives a reload (saved and loaded as plain data).
  {
    const a = new Doc('a');
    const u1 = new UndoManager(a);
    u1.track(a.insert('f', 0, 'keep '), 0);
    u1.track(a.insert('f', 5, 'undo-me'), 1000);
    const saved = JSON.parse(JSON.stringify(u1.toJSON()));
    const reloaded = new Doc('a2');             // a new page load gets a new client ID
    reloaded.apply(JSON.parse(JSON.stringify(a.log)));
    const u2 = new UndoManager(reloaded);
    u2.load(saved);
    u2.undo('f');
    check(reloaded.text('f') === 'keep ', 'undo after reload: ' + JSON.stringify(reloaded.text('f')));
  }
  // 8. Out-of-order delivery waits for missing dependencies.
  {
    const a = new Doc('a'), b = new Doc('b'), c = new Doc('c');
    a.insert('f', 0, 'A');
    syncPair(a, b);
    b.insert('f', 1, 'B');               // depends on A's character
    syncPair(b, c);                      // c only has B's op (B relays A's too? no: only missing)
    check(c.text('f') === 'AB', 'relay carries dependencies: ' + JSON.stringify(c.text('f')));
    const d = new Doc('d');
    d.apply(b.log.filter(op => op.c === 'b'));  // only B's op, A's missing
    check(d.text('f') === '' && d.pending.length === 1, 'op waits in pending queue');
    d.apply(a.log);
    check(d.text('f') === 'AB', 'pending op applied once dependency arrived');
  }
}

// ---------------------------------------------------------------- random fuzzing
function fuzz(runs) {
  console.log('Random fuzz: ' + runs + ' runs x 5 users');
  const alphabet = 'abcdefghij \n{}();';
  for (let run = 0; run < runs; run++) {
    const rand = rng(run + 1);
    const docs = ['u1', 'u2', 'u3', 'u4', 'u5'].map(id => new Doc(id));
    const undos = docs.map(d => new UndoManager(d, { captureTimeout: 0 }));
    docs[0].set('a', 'name', 'a.js'); docs[0].set('b', 'name', 'b.py');
    syncAll(docs);
    const allIns = new Set(), allDel = new Set();
    let clock = 0;
    for (let step = 0; step < 250; step++) {
      const i = Math.floor(rand() * docs.length);
      const d = docs[i], u = undos[i];
      const fid = rand() < 0.6 ? 'a' : 'b';
      const len = d.text(fid).length;
      const r = rand();
      clock += 1 + Math.floor(rand() * 3);
      if (r < 0.38) {
        let s = ''; const n = 1 + Math.floor(rand() * 5);
        for (let k = 0; k < n; k++) s += alphabet[Math.floor(rand() * alphabet.length)];
        u.track(d.insert(fid, Math.floor(rand() * (len + 1)), s), clock);
      } else if (r < 0.6) {
        if (len) { const at = Math.floor(rand() * len); u.track(d.delete(fid, at, 1 + Math.floor(rand() * Math.min(4, len - at))), clock); }
      } else if (r < 0.68) {
        u.undo(fid);
      } else if (r < 0.71) {
        u.redo(fid);
      } else if (r < 0.75) {
        if (rand() < 0.5) d.set(fid, 'name', fid + '-' + Math.floor(rand() * 9) + '.js');
        else if (rand() < 0.5) d.deleteFile(fid); else d.set(fid, 'deleted', false);
      } else {
        const j = Math.floor(rand() * docs.length);
        if (j !== i) syncPair(d, docs[j], rand, rand() < 0.3);
      }
    }
    syncAll(docs);
    for (const d of docs) for (const op of d.log) {
      if (op.k === 'ins') for (let k = 0; k < op.x.length; k++) allIns.add(op.f + '|' + op.c + ':' + (op.s + k));
      if (op.k === 'del') for (const id of expandIds(op.d)) allDel.add(op.f + '|' + id);
    }
    const snap0 = snapshot(docs[0]);
    for (const d of docs) {
      if (snapshot(d) !== snap0) { check(false, 'run ' + run + ': replicas diverged'); break; }
      check(d.pending.length === 0, 'run ' + run + ': nothing left pending');
      for (const [fid, s] of d.seqs) {
        check(s.text().length === s.length, 'run ' + run + ': length counter matches text');
        // No lost characters: visible = every inserted character minus every deleted one.
        const visible = [];
        for (let it = s.head.next; it; it = it.next) if (!it.deleted) visible.push(fid + '|' + it.id);
        const expected = Array.from(allIns).filter(id => id.startsWith(fid + '|') && !allDel.has(id));
        if (visible.length !== expected.length || !visible.every(id => allIns.has(id) && !allDel.has(id))) {
          check(false, 'run ' + run + ': characters lost or resurrected in ' + fid);
        }
      }
    }
    if (failures) { console.error('  first failing run: ' + run); break; }
  }
}

function perf() {
  const a = new Doc('a'), b = new Doc('b');
  const t0 = Date.now();
  for (let i = 0; i < 20000; i++) a.insert('f', a.text('f').length, 'x');
  const t1 = Date.now();
  b.apply(JSON.parse(JSON.stringify(a.log)));
  const t2 = Date.now();
  console.log('Perf: 20,000 keystrokes typed in ' + (t1 - t0) + ' ms, replayed on a fresh replica in ' + (t2 - t1) + ' ms');
  check(b.text('f').length === 20000, 'perf replica has all characters');
}

scenarios();
fuzz(Number(process.argv[2]) || 400);
perf();
if (failures) { console.error('\n' + failures + ' check(s) FAILED'); process.exit(1); }
console.log('\nAll checks passed.');
