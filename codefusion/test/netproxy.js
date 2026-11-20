#!/usr/bin/env node
// A tiny TCP proxy that can break one teammate's network on purpose, for tests.
// Usage: node test/netproxy.js <listenPort> <targetPort> <controlPort>
// Control: GET http://127.0.0.1:<controlPort>/mode/<mode>
//   pass      : forward everything
//   drop      : close every connection and refuse new ones (Wi-Fi switched off)
//   blackhole : keep connections open but forward nothing, ever again (Wi-Fi "connected"
//               but dead: no close, no error, just silence)
//   holddown  : forward client -> server, but hold server -> client until "pass"
//               (the server has the edits, the browser has not heard back yet)
//   holdup    : hold client -> server until "pass", then deliver it all late
//               (a stalled connection that recovers: TCP delivers the backlog)
'use strict';
const net = require('net');
const http = require('http');

const [listenPort, targetPort, controlPort] = process.argv.slice(2).map(Number);
let mode = 'pass';
const pairs = new Set();

function kill(pair) {
  pairs.delete(pair);
  pair.client.destroy();
  if (pair.upstream) pair.upstream.destroy();
}

net.createServer(client => {
  if (mode === 'drop') { client.destroy(); return; }
  const pair = { client, upstream: null, held: [], heldUp: [], dead: mode === 'blackhole' };
  pairs.add(pair);
  client.on('error', () => kill(pair));
  client.on('close', () => kill(pair));
  if (pair.dead) { client.on('data', () => {}); return; }   // accepted, never answered
  const upstream = net.connect(targetPort, '127.0.0.1');
  pair.upstream = upstream;
  upstream.on('error', () => kill(pair));
  upstream.on('close', () => kill(pair));
  client.on('data', data => {
    if (pair.dead) return;
    if (mode === 'holdup') pair.heldUp.push(data);
    else upstream.write(data);
  });
  upstream.on('data', data => {
    if (pair.dead) return;
    if (mode === 'holddown') pair.held.push(data);
    else client.write(data);
  });
}).listen(listenPort, '127.0.0.1');

http.createServer((req, res) => {
  const m = /^\/mode\/(pass|drop|blackhole|holddown|holdup)$/.exec(req.url);
  if (m) {
    mode = m[1];
    if (mode === 'drop') for (const pair of [...pairs]) kill(pair);
    if (mode === 'blackhole') for (const pair of pairs) pair.dead = true;
    if (mode === 'pass') for (const pair of pairs) {
      for (const d of pair.held) if (!pair.dead) pair.client.write(d);
      for (const d of pair.heldUp) if (!pair.dead && pair.upstream) pair.upstream.write(d);
      pair.held = []; pair.heldUp = [];
    }
  }
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ mode, connections: pairs.size }));
}).listen(controlPort, '127.0.0.1', () => console.log(`netproxy ${listenPort} -> ${targetPort}, control on ${controlPort}`));
