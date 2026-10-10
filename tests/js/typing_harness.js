/**
 * Front-end runtime harness.
 *
 * Loads src/static/index.html + src/static/app.js into jsdom and drives real
 * keyboard events, so a runtime error inside the typing handler fails loudly
 * instead of silently freezing the UI (which is exactly what shipped when
 * `let correctCount = 0;` was dropped from handleTypingInput).
 *
 * Prints one JSON verdict on the last stdout line.
 *
 * Run:  node tests/js/typing_harness.js
 */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require(path.join(__dirname, 'node_modules', 'jsdom'));

const ROOT = path.resolve(__dirname, '..', '..');

function loadApp() {
  let html = fs.readFileSync(path.join(ROOT, 'src/static/index.html'), 'utf8');
  // Drop external <script src> tags; app.js is injected below as a real
  // top-level script so its `let`/`const` bindings are reachable.
  html = html.replace(/<script[^>]*src=[^>]*><\/script>/gi, '');

  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    url: 'http://127.0.0.1:8765/',
  });
  const { window } = dom;

  // --- Minimal browser API surface the app touches at load time ---
  window.WebSocket = class { constructor() { this.readyState = 1; } send() {} close() {} };
  window.WebSocket.OPEN = 1;
  window.WebSocket.CONNECTING = 0;
  window.BroadcastChannel = class { postMessage() {} addEventListener() {} close() {} };
  window.AudioContext = window.webkitAudioContext = class {
    constructor() { this.sampleRate = 48000; this.destination = {}; this.state = 'running'; }
    createGain() { return { gain: { value: 0 }, connect() {}, disconnect() {} }; }
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    createScriptProcessor() { return { connect() {}, disconnect() {}, onaudioprocess: null }; }
    createAnalyser() {
      return { fftSize: 256, frequencyBinCount: 128, connect() {}, disconnect() {}, getByteTimeDomainData() {} };
    }
    close() {} resume() {}
  };
  window.fetch = () => Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve({}),
    text: () => Promise.resolve(''),
  });
  window.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
  window.cancelAnimationFrame = (id) => clearTimeout(id);
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  window.navigator.sendBeacon = () => true;
  window.alert = () => {};
  window.scrollTo = () => {};
  window.HTMLCanvasElement.prototype.getContext = () => ({
    clearRect() {}, fillRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, stroke() {},
    arc() {}, fill() {}, save() {}, restore() {}, setLineDash() {}, scale() {},
    translate() {}, rotate() {}, closePath() {}, quadraticCurveTo() {}, bezierCurveTo() {},
    createLinearGradient() { return { addColorStop() {} }; },
    measureText() { return { width: 10 }; },
  });

  const errors = [];
  window.addEventListener('error', (e) => errors.push(String(e.message)));
  const originalError = console.error;
  console.error = (...args) => {
    const text = args.map(String).join(' ');
    // jsdom noise that has nothing to do with app health.
    if (/Not implemented|Could not parse CSS/.test(text)) return;
    errors.push(text);
  };

  const script = window.document.createElement('script');
  script.textContent = fs.readFileSync(path.join(ROOT, 'src/static/app.js'), 'utf8');
  window.document.body.appendChild(script);
  console.error = originalError;

  return { window, loadErrors: errors };
}

function typeSequence(window, display, text) {
  for (const ch of text) {
    display.dispatchEvent(new window.KeyboardEvent('keydown', {
      key: ch, bubbles: true, cancelable: true,
    }));
  }
}

function visibleText(display) {
  return display.textContent.replace(/\s+/g, ' ').trim();
}

function main() {
  const { window, loadErrors } = loadApp();
  const doc = window.document;
  const display = doc.getElementById('typing-target-display');
  const errorsDuringTyping = [];
  window.addEventListener('error', (e) => errorsDuringTyping.push(String(e.message)));

  const verdict = { loadErrors, errorsDuringTyping };

  // ---- copy mode: typing must work at all ----
  const TARGET = 'I think so.';
  window.eval(`setupShadowTyping(${JSON.stringify(TARGET)}, "我认为如此。")`);
  display.focus();
  verdict.copyVisibleInitially = visibleText(display);
  typeSequence(window, display, TARGET);
  const progressEl = doc.getElementById('typing-progress-text');
  verdict.copyMode = {
    target: TARGET,
    buffer: window.eval('typedBuffer'),
    visibleAfter: visibleText(display),
    completed: window.eval('isTypingCompleted'),
    progressText: progressEl ? progressEl.textContent : '',
  };
  verdict.translationDisplay = {
    copy: doc.getElementById('typing-translation-display').style.display || '',
  };

  // ---- backspace ----
  window.eval('typedBuffer = ""; isTypingCompleted = false; setupShadowTyping("Hello world.", null)');
  display.focus();
  typeSequence(window, display, 'Hello world.');
  display.dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Backspace', bubbles: true, cancelable: true }));
  verdict.backspace = {
    before: 'Hello world.',
    target: 'Hello world',
    buffer: window.eval('typedBuffer'),
    // After deleting, the last character is re-rendered as the pending caret
    // target, so the visible text is the full target; what matters is the
    // buffer and that the sentence is no longer marked complete.
    visibleAfter: visibleText(display),
    completedBeforeBackspace: true,
  };

  // ---- a wrong character must be flagged and must not complete ----
  window.eval('typedBuffer = ""; isTypingCompleted = false; setupShadowTyping("abc", null)');
  display.focus();
  typeSequence(window, display, 'axc');
  verdict.wrongChar = {
    completed: window.eval('isTypingCompleted'),
    hasWrongSpan: !!display.querySelector('.char-wrong'),
  };

  // ---- retrieval modes must hide the answer while still accepting input ----
  for (const mode of ['cloze', 'recall', 'dictation']) {
    window.eval('typedBuffer = ""; isTypingCompleted = false;');
    window.eval(`setPracticeMode(${JSON.stringify(mode)})`);
    window.eval(`setupShadowTyping(${JSON.stringify(TARGET)}, "我认为如此。")`);
    display.focus();
    const beforeTyping = visibleText(display);
    typeSequence(window, display, 'I th');
    verdict[mode] = {
      target: TARGET,
      beforeTyping,
      visibleAfter: visibleText(display),
      buffer: window.eval('typedBuffer'),
    };
    verdict.translationDisplay[mode] = doc.getElementById('typing-translation-display').style.display;
  }
  window.eval('typedBuffer = ""; setPracticeMode("copy")');

  // ---- mode cycling ----
  const order = [];
  window.eval('setPracticeMode("copy", { rerender: false })');
  for (let i = 0; i < 4; i++) {
    window.eval('cyclePracticeMode()');
    order.push(window.eval('currentPracticeMode'));
  }
  verdict.modeCycle = {
    order,
    stored: window.localStorage.getItem('ai_coach_practice_mode'),
  };

  process.stdout.write('\n' + JSON.stringify(verdict) + '\n');
  // The app registers intervals (heartbeat, sync ping) that would otherwise keep
  // the Node event loop alive forever; exit as soon as the verdict is written.
  process.exit(0);
}

main();
