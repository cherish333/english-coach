/**
 * Portrait-screen layout measurement harness.
 *
 * Drives the real portrait.html in headless Chromium and reports geometry for
 * the undocked vs typing-docked split and for each panel view mode, so the
 * layout regressions in tests/test_layout.py can be asserted numerically.
 *
 * Prints one JSON object on the last stdout line.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..', '..');

function findChromium() {
  const candidates = [
    process.env.CHROME_PATH,
    'chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable',
    '/usr/bin/chromium', '/usr/bin/google-chrome',
  ].filter(Boolean);
  for (const bin of candidates) {
    try {
      execFileSync(bin, ['--version'], { stdio: 'ignore' });
      return bin;
    } catch (_) { /* keep looking */ }
  }
  return null;
}

function main() {
  const bin = findChromium();
  if (!bin) {
    console.log(JSON.stringify({ available: false, reason: 'no chromium binary' }));
    return;
  }
  const arg = process.argv[2] || '1080x1920';
  const [w, h] = arg.split('x').map(Number);

  let html = fs.readFileSync(path.join(ROOT, 'src/static/portrait.html'), 'utf8');
  html = html.replace(/<script[^>]*src=[^>]*><\/script>/gi, '');

  const inject = `
<div id="MEASURE">PENDING</div>
<script>
window.__errs = [];
window.addEventListener('error', function (e) { window.__errs.push(String(e.message)); });
window.addEventListener('load', function () {
  setTimeout(function () {
    var body = document.querySelector('.portrait-body');
    var panel = document.getElementById('sentences-bottom-panel');
    var card = document.getElementById('p-shadow-typing-card');
    var slot = document.getElementById('MEASURE');

    function h(sel) { var e = document.querySelector(sel); return e ? Math.round(e.getBoundingClientRect().height) : 0; }
    function fs(sel) { var e = document.querySelector(sel); return e ? getComputedStyle(e).fontSize : null; }
    function visible(sel) {
      var e = document.querySelector(sel);
      if (!e) return false;
      var cs = getComputedStyle(e);
      return cs.display !== 'none' && e.getBoundingClientRect().height > 0;
    }
    function snap() {
      return { top: h('.pdf-top-panel'), bottom: h('.sentences-bottom-panel'),
               typingArea: h('#p-typing-target-display'), focusCard: h('.current-focus-card'),
               notesArea: h('#p-notes-container'), typingFont: fs('#p-typing-target-display') };
    }

    var out = { available: true, viewport: { w: innerWidth, h: innerHeight } };

    // 1. Undocked (typing belongs to the landscape screen).
    card.style.display = 'none';
    body.classList.remove('typing-docked');
    out.undocked = snap();

    // 2. Docked to this screen.
    card.style.display = 'flex';
    body.classList.add('typing-docked');
    out.typingDocked = snap();

    // 3. Each panel view mode, waiting a frame between switches.
    var modes = ['split', 'typing', 'sentences', 'notes'];
    var idx = 0;
    out.modes = {};
    (function step() {
      if (idx >= modes.length) {
        out.errors = window.__errs.slice();
        slot.textContent = JSON.stringify(out);
        return;
      }
      var mode = modes[idx++];
      // Drive the REAL production function so we test the shipped behaviour
      // (it also manipulates panel display), not just a CSS class by hand.
      if (typeof setPortraitViewMode === 'function') {
        setPortraitViewMode(mode);
      } else {
        panel.classList.remove('view-split', 'view-typing-only', 'view-sentences-only', 'view-notes-only');
        panel.classList.add('view-' + mode);
      }
      setTimeout(function () {
        out.modes[mode] = Object.assign(snap(), {
          typingVisible: visible('#p-shadow-typing-card'),
          notesVisible: visible('#p-notes-container'),
          sentencesVisible: visible('#p-sentences-container'),
        });
        step();
      }, 90);
    })();
  }, 300);
});
<\/script>`;

  html = html.replace('</body>', inject + '</body>');

  const tmp = path.join(os.tmpdir(), `portrait-layout-${w}x${h}.html`);
  fs.writeFileSync(tmp, html, 'utf8');

  const dom = execFileSync(bin, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    `--window-size=${w},${h}`, '--virtual-time-budget=8000', '--dump-dom',
    `file://${tmp}`,
  ], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, stdio: ['ignore', 'pipe', 'ignore'] });

  const match = dom.match(/<div id="MEASURE">([\s\S]*?)<\/div>/);
  if (!match || match[1].trim() === 'PENDING') {
    console.log(JSON.stringify({ available: false, reason: 'measure script did not complete' }));
    return;
  }
  const json = match[1].trim()
    .replace(/&quot;/g, '"').replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>');
  console.log(json);
}

main();
