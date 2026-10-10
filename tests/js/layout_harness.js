/**
 * Layout measurement harness.
 *
 * jsdom does not implement layout, so we hand the real HTML + CSS to Chromium
 * (headless) if it is available and read back geometry via --dump-dom + a
 * devtools-free approach: we inject a script that writes measurements into the
 * document title, which --dump-dom prints.
 *
 * Falls back to reporting "unavailable" when no Chromium binary exists.
 */
const fs = require('fs');
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
  const arg = process.argv[2] || '2560x1440';
  const [w, h] = arg.split('x').map(Number);

  const html = fs.readFileSync(path.join(ROOT, 'src/static/index.html'), 'utf8');
  const css = fs.readFileSync(path.join(ROOT, 'src/static/style.css'), 'utf8');

  // Build a self-contained page: inline CSS, stub JS, then measure.
  let page = html
    .replace(/<script[^>]*src=[^>]*><\/script>/gi, '')
    .replace('</head>', `<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"><style>html{font-size:16px}:root{--sans:'Inter',system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}body{font-family:var(--sans)}</style><style>${css}</style></head>`)
    .replace('</body>', `
<script>
window.__measure = function () {
  function box(sel) {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return { x: Math.round(r.x), y: Math.round(r.y),
             h: Math.round(r.height), w: Math.round(r.width),
             font: cs.fontSize, display: cs.display,
             scrollH: el.scrollHeight, clientH: el.clientHeight,
             overflowY: cs.overflowY };
  }
  const out = {
    viewport: { w: window.innerWidth, h: window.innerHeight },
    header: box('.app-header'),
    workspace: box('.workspace-three-col'),
    colLeft: box('.col-panel.col-left'),
    colCenter: box('.col-panel.col-center'),
    colRight: box('.col-panel.col-right'),
    centerTop: box('#col-center-top'),
    centerBottom: box('#col-center-bottom'),
    typingGroup: box('.typing-actions-group'),
    typingHeader: box('.typing-card-header'),
    typingHeaderLeft: box('.typing-card-header .typing-header-left'),
    typingCardBox: box('#shadow-typing-card'),
    hintBar: box('.typing-hint-bar'),
    fsHintBar: (function () { return null; })(),
    fsHintChildren: null,
    hintChildren: (function () {
      const h = document.querySelector('.typing-hint-bar');
      if (!h) return null;
      const hr = h.getBoundingClientRect();
      return Array.from(h.children).map(function (c) {
        const r = c.getBoundingClientRect();
        return { cls: c.className.split(' ')[0], x: Math.round(r.x), y: Math.round(r.y),
                 w: Math.round(r.width), h: Math.round(r.height),
                 overflowRight: Math.round(r.right - hr.right) };
      });
    })(),
    headerChildren: (function () {
      const h = document.querySelector('.typing-card-header');
      if (!h) return null;
      return Array.from(h.children).map(function (c) {
        const r = c.getBoundingClientRect();
        return { cls: c.className, x: Math.round(r.x), y: Math.round(r.y),
                 w: Math.round(r.width), h: Math.round(r.height) };
      });
    })(),
    typingControls: (function () {
      const g = document.querySelector('.typing-actions-group');
      if (!g) return null;
      const gr = g.getBoundingClientRect();
      return Array.from(g.children).map(function (c) {
        const r = c.getBoundingClientRect();
        return {
          id: c.id || c.className.split(' ')[0] || c.tagName,
          x: Math.round(r.x), y: Math.round(r.y),
          w: Math.round(r.width), h: Math.round(r.height),
          scrollH: c.scrollHeight,
          visible: r.width > 0,
        };
      });
    })(),
    docs: document.querySelectorAll('.app-header select, .app-header button').length,
    fonts: (function () {
      function fs(sel) {
        const el = document.querySelector(sel);
        if (!el) return null;
        const cs = getComputedStyle(el);
        return { size: cs.fontSize, family: cs.fontFamily.split(',')[0].replace(/["']/g, '') };
      }
      return {
        body: fs('body'),
        chineseLabel: fs('.typing-card-title'),
        typingTarget: fs('#typing-target-display'),
        sentenceText: fs('.sentence-text-val'),
      };
    })(),
  };
  // Fullscreen mode measurements
  const card = document.getElementById('sentence-focus-card');
  if (card) {
    card.classList.add('fullscreen-mode');
    document.body.classList.add('typing-fullscreen-active');
    // Mirror ensureFullscreenColumns(): group the reference blocks into the
    // left grid column so the measurement reflects the real layout.
    (function () {
      var left = document.createElement('div');
      left.className = 'fs-left-col';
      card.appendChild(left);
      ['.sentence-preview-box', '#typing-translation-display', '#video-player-container']
        .forEach(function (sel) { var el = card.querySelector(sel); if (el) left.appendChild(el); });
    })();
    const pm = box('.sentence-preview-box');
    const tr = box('#typing-translation-display');
    const tc = box('#shadow-typing-card');
    const td = box('#typing-target-display');
    const fsHeader = box('.sentence-fs-header');
    const cardBox = box('#sentence-focus-card');
    // Fullscreen toolbar geometry: the labelled mode button used to inherit the
    // 44px icon width and spill its label out of its own box.
    var fsGroup = document.querySelector('.typing-actions-group');
    var fsKids = fsGroup ? Array.from(fsGroup.children).filter(function (c) {
      return c.getBoundingClientRect().width > 0;
    }) : [];
    // Count rows honestly: flex align-items:center gives every control a
    // slightly different top offset (902/908/914), so bucketing by exact top
    // reports three "rows" for what is visually one line. Bucket by vertical
    // overlap instead.
    var fsRanges = fsKids.map(function (c) {
      var r = c.getBoundingClientRect();
      return { top: r.top, bottom: r.bottom };
    }).sort(function (a, b) { return a.top - b.top; });
    var fsRows = [];
    fsRanges.forEach(function (range) {
      var bucket = null;
      for (var i = 0; i < fsRows.length; i++) {
        // Same visual row when the vertical ranges overlap.
        if (range.top < fsRows[i].bottom - 1 && range.bottom > fsRows[i].top + 1) {
          bucket = fsRows[i];
          break;
        }
      }
      if (bucket) {
        bucket.count += 1;
        bucket.top = Math.min(bucket.top, range.top);
        bucket.bottom = Math.max(bucket.bottom, range.bottom);
      } else {
        fsRows.push({ top: range.top, bottom: range.bottom, count: 1 });
      }
    });
    var cardEdge = cardBox ? { right: cardBox.x + cardBox.w, bottom: cardBox.y + cardBox.h } : null;
    var fsToolbar = fsGroup ? {
      rows: fsRows.length,
      maxPerRow: fsRows.length ? Math.max.apply(null, fsRows.map(function (r) { return r.count; })) : 0,
      visibleControls: fsKids.length,
      insideCard: !!(cardEdge && Math.max.apply(null, fsKids.map(function (c) {
        return c.getBoundingClientRect().right;
      })) <= cardEdge.right + 1),
      overflowingLabels: fsKids.filter(function (c) {
        return c.scrollHeight > c.getBoundingClientRect().height + 2;
      }).map(function (c) { return c.id || c.className; }),
      items: fsKids.map(function (c) {
        var r = c.getBoundingClientRect();
        return { id: c.id || c.className.split(' ')[0], w: Math.round(r.width),
                 left: Math.round(r.left), top: Math.round(r.top) };
      }),
      hintBarInside: (function () {
        var hb = document.querySelector('.typing-hint-bar');
        if (!hb) return null;
        var r = hb.getBoundingClientRect();
        return !!cardEdge && r.right <= cardEdge.right + 1 && r.bottom <= cardEdge.bottom + 1;
      })(),
      debug: (function () {
        var hb = document.querySelector('.typing-hint-bar');
        return {
          cardRight: cardEdge ? Math.round(cardEdge.right) : null,
          cardBottom: cardEdge ? Math.round(cardEdge.bottom) : null,
          toolbarRight: fsKids.length ? Math.round(Math.max.apply(null, fsKids.map(function (c) {
            return c.getBoundingClientRect().right; }))) : null,
          hintRight: hb ? Math.round(hb.getBoundingClientRect().right) : null,
          hintBottom: hb ? Math.round(hb.getBoundingClientRect().bottom) : null,
        };
      })(),
    } : null;

    out.fullscreen = {
      toolbar: fsToolbar,
      card: cardBox, fsHeader, preview: pm, translation: tr, typingCard: tc, typingDisplay: td,
      // How much vertical space the stacked sections want inside the viewport.
      stackedNeed: (fsHeader?fsHeader.h:0)+(pm?pm.h:0)+(tr?tr.h:0)+(tc?tc.h:0),
      overflows: cardBox ? cardBox.scrollH > cardBox.clientH : null,
    };
    card.classList.remove('fullscreen-mode');
    document.body.classList.remove('typing-fullscreen-active');
  }
  document.title = 'MEASURE:' + JSON.stringify(out);
};
window.addEventListener('load', function(){ setTimeout(window.__measure, 120); });
<\/script></body>`);

  const tmp = path.join(require('os').tmpdir(), `layout-${w}x${h}.html`);
  fs.writeFileSync(tmp, page, 'utf8');

  const dom = execFileSync(bin, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    `--window-size=${w},${h}`, '--virtual-time-budget=3000', '--dump-dom',
    `file://${tmp}`,
  ], { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, stdio: ['ignore', 'pipe', 'ignore'] });

  const m = dom.match(/MEASURE:(\{.*?\})<\/title>/s);
  if (!m) { console.log(JSON.stringify({ available: false, reason: 'measure script did not run' })); return; }
  const parsed = JSON.parse(m[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&'));
  parsed.available = true;
  parsed.chromium = bin;
  console.log(JSON.stringify(parsed));
}

main();
