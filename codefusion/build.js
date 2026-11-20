#!/usr/bin/env node
// Builds index.html (the file you open) from the separate source files in src/:
//   src/index.html  +  src/style.css  +  src/script.js   ->   index.html
// The built index.html has the CSS and JavaScript inside it, so it looks and works
// the same however it is opened. After editing anything in src/, run:  npm run build
'use strict';
const fs = require('fs');
const path = require('path');

const src = file => fs.readFileSync(path.join(__dirname, 'src', file), 'utf8');
let html = src('index.html');
const css = src('style.css');
const js = src('script.js');

if (/<\/style/i.test(css)) throw new Error('src/style.css contains "</style"');
if (/<\/script/i.test(js)) throw new Error('src/script.js contains "</script"');

function swap(tag, replacement) {
  if (!html.includes(tag)) throw new Error('Could not find ' + tag + ' in src/index.html');
  html = html.replace(tag, () => replacement);
}
swap('<link rel="stylesheet" href="style.css">', '<style>\n' + css + '</style>');
swap('<script src="script.js"></script>', '<script>\n' + js + '</script>');
// The "files did not load" warning only matters for the split version.
html = html.replace(/<p class="files-missing">[\s\S]*?<\/p>\n/, '');

fs.writeFileSync(path.join(__dirname, 'index.html'), html);
console.log('Built index.html (' + Math.round(html.length / 1024) + ' KB) from src/index.html, src/style.css and src/script.js');
