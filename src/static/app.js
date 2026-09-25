let ws = null;
let reconnectTimer = null;
let audioContext = null;
let micStream = null;
let processor = null;
let gainNode = null;
let isRecording = false;
let recordPressStartTime = 0;

// Audio playback queue
const audioQueue = [];
let isPlayingAudio = false;
let currentAudioElement = null;
let currentAudioUrl = null;
let playbackGeneration = 0;

// Whiteboard text buffer
let currentNotesMarkdown = "";
let activeTurnId = 0;

const statusPill = document.getElementById("status-pill");
const micBtn = document.getElementById("mic-btn");
const interruptBtn = document.getElementById("interrupt-btn");
const chatStream = document.getElementById("chat-stream");
const whiteboardContent = document.getElementById("whiteboard-content");
const voiceSelect = document.getElementById("voice-select");
const speedSlider = document.getElementById("speed-slider");
const speedVal = document.getElementById("speed-val");
const resetBtn = document.getElementById("reset-btn");
const textInput = document.getElementById("text-input");
const sendTextBtn = document.getElementById("send-text-btn");
const canvas = document.getElementById("waveform-canvas");
const canvasCtx = canvas.getContext("2d");
const lectureDocSelect = document.getElementById("lecture-doc-select");
const lectureUnitSelect = document.getElementById("lecture-unit-select");
const lecturePageInput = document.getElementById("lecture-page-input");
const lectureLoadBtn = document.getElementById("lecture-load-btn");
const lecturePrevBtn = document.getElementById("lecture-prev-btn");
const lectureNextBtn = document.getElementById("lecture-next-btn");
const lectureExplainBtn = document.getElementById("lecture-explain-btn");
const lecturePracticeBtn = document.getElementById("lecture-practice-btn");
const lectureExitBtn = document.getElementById("lecture-exit-btn");
const lectureStatus = document.getElementById("lecture-status");
const lecturePageMeta = document.getElementById("lecture-page-meta");
const lecturePageImage = document.getElementById("lecture-page-image");

// Dual-Screen, Sentence Focus, and Notes Elements
const dualScreenBtn = document.getElementById("dual-screen-btn");
const sidebarToggleBtn = document.getElementById("sidebar-toggle-btn");
const expandSidebarTab = document.getElementById("expand-sidebar-tab");
const collapseColLeftBtn = document.getElementById("collapse-col-left-btn");
const workspaceContainer = document.getElementById("workspace-container");
const studioPageBadge = document.getElementById("studio-page-badge");
const studioPageNum = document.getElementById("studio-page-num");
const studioPrevPageBtn = document.getElementById("studio-prev-page-btn");
const studioNextPageBtn = document.getElementById("studio-next-page-btn");
const notesLibraryBtn = document.getElementById("notes-library-btn");
const sentPrevBtn = document.getElementById("sent-prev-btn");
const sentNextBtn = document.getElementById("sent-next-btn");
const sentProgressLabel = document.getElementById("sent-progress-label");
const sentencePreviewBox = document.getElementById("sentence-preview-box");
const headerActionRunBtn = document.getElementById("header-action-run-btn");
const generateNotesBtn = document.getElementById("generate-notes-btn");
const saveNotesBtn = document.getElementById("save-notes-btn");
const copyNotesBtn = document.getElementById("copy-notes-btn");
const exportNotesBtn = document.getElementById("export-notes-btn");
const notesModal = document.getElementById("notes-modal");
const modalCloseBtn = document.getElementById("modal-close-btn");
const modalExportBtn = document.getElementById("modal-export-btn");
const modalNotesList = document.getElementById("modal-notes-list");

// BroadcastChannel for instant local multi-window synchronization
const syncChannel = new BroadcastChannel("english_coach_sync");

// Dual-screen sync and portrait detection state
let isPortraitConnected = false;
let lastPortraitHeartbeat = 0;
let isLocalVideoForced = false;
let hasCurrentSentenceVideoPlayed = false;
let currentDocHasMedia = false;
let currentDocMediaType = null;
let currentDocMediaUrl = null;
let videoCheckInterval = null;
let currentVideoPlaybackSession = 0;
let isVideoAutoplayEnabled = localStorage.getItem("ai_coach_video_autoplay") !== "false";
let hasUserInteracted = false;
window.addEventListener("pointerdown", () => { hasUserInteracted = true; }, { capture: true, once: true });
window.addEventListener("keydown", () => { hasUserInteracted = true; }, { capture: true, once: true });

function formatSeconds(sec) {
  if (sec === null || sec === undefined || isNaN(sec)) return "--:--";
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  const remSec = s % 60;
  return `${String(m).padStart(2, '0')}:${String(remSec).padStart(2, '0')}`;
}

try {
  syncChannel.postMessage({ type: "ping_portrait" });
} catch (_) {}

function updateVideoSyncStrip() {
  const container = document.getElementById("video-player-container");
  const badgeLabel = document.getElementById("video-badge-label");
  const syncPill = document.getElementById("video-sync-status-pill");
  const videoTsBadge = document.getElementById("video-timestamp-badge");
  const origMediaBtn = document.getElementById("typing-original-media-btn");
  const visibilityBtn = document.getElementById("video-visibility-toggle-btn");
  const wrapper = document.getElementById("video-viewport-wrapper");

  if (!currentDocHasMedia) {
    if (container) container.style.display = "none";
    if (origMediaBtn) origMediaBtn.style.display = "none";
    return;
  }

  if (container) container.style.display = "flex";
  if (origMediaBtn) origMediaBtn.style.display = "inline-flex";

  const s = lectureSentences[currentSentenceIndex - 1];
  if (s && typeof s.start_time === "number" && typeof s.end_time === "number") {
    if (videoTsBadge) {
      videoTsBadge.textContent = `${formatSeconds(s.start_time)} - ${formatSeconds(s.end_time)}`;
    }
  } else if (videoTsBadge) {
    videoTsBadge.textContent = "--:-- - --:--";
  }

  if (isPortraitConnected) {
    if (syncPill) {
      syncPill.classList.add("portrait-connected");
      syncPill.title = "已检测到竖屏教材端：视频原声在竖屏高清置顶播放，打字空间已完全释放";
    }
    if (badgeLabel) badgeLabel.textContent = "📺 竖屏声画同步中";
    if (!isLocalVideoForced && wrapper) {
      wrapper.classList.add("collapsed");
      wrapper.classList.remove("expanded");
      if (visibilityBtn) {
        visibilityBtn.textContent = "🖥️ 本地视口";
        visibilityBtn.classList.remove("active");
      }
    }
  } else {
    if (syncPill) {
      syncPill.classList.remove("portrait-connected");
      syncPill.title = "单屏模式：视频由本窗口播放，可开启悬浮窗或展开本地视口";
    }
    if (badgeLabel) badgeLabel.textContent = "🎬 视频原声伴学";
  }
}

let lectureDocumentId = null;
let lecturePage = 1;
let lectureTotalPages = 0;
let lecturePageRequestId = 0;
let lectureUnits = [];
let lectureSentences = [];
let currentSentenceIndex = 1;
let lastTurnVoice = "";
let lastTurnNotes = "";
let isContinuousLecture = false;
let continuousAction = null;
let continuousTimer = null;
let isCurrentTurnComplete = false;
let audioPlaybackEndTime = 0;
let currentTeachingStyle = localStorage.getItem("ai_coach_teaching_style") || "spoken";
let currentActionMode = localStorage.getItem("ai_coach_action_mode") || "explain";
let pendingWsLectureContext = null;

function getEffectiveActionMode() {
  if (currentActionMode === "read_only" || currentActionMode === "continuous_read") {
    return "read_only";
  }
  if (currentActionMode === "practice") {
    return "practice";
  }
  if (isContinuousLecture && continuousAction) {
    return continuousAction;
  }
  if (currentActionMode === "continuous_explain") {
    return "explain";
  }
  return currentActionMode || "explain";
}

const sentenceNotesCache = {};

// Progress Persistence Manager
const PROGRESS_STORAGE_KEY = "english_coach_learning_progress";
let learningProgress = null;
let pendingTargetSentenceIndex = null;

try {
  const cached = localStorage.getItem(PROGRESS_STORAGE_KEY);
  if (cached) {
    learningProgress = JSON.parse(cached);
    if (learningProgress && learningProgress.teaching_style) {
      currentTeachingStyle = localStorage.getItem("ai_coach_teaching_style") || learningProgress.teaching_style;
    }
  }
} catch (_) {}

async function fetchLearningProgress() {
  try {
    const res = await fetch("/api/progress");
    if (res.ok) {
      const data = await res.json();
      learningProgress = data;
      try {
        localStorage.setItem(PROGRESS_STORAGE_KEY, JSON.stringify(data));
      } catch (_) {}
    }
  } catch (e) {
    console.warn("Failed to fetch progress from server:", e);
  }
  return learningProgress;
}

function saveLearningProgress(updates) {
  if (!updates) return;
  if (!learningProgress) learningProgress = {};
  Object.assign(learningProgress, updates);
  if (!learningProgress.books) learningProgress.books = {};
  if (updates.document_id) {
    const prevBook = learningProgress.books[updates.document_id] || {};
    learningProgress.books[updates.document_id] = {
      last_page: updates.page ?? (prevBook.last_page || 1),
      last_sentence_index: updates.sentence_index ?? (prevBook.last_sentence_index || 1),
      updated_at: new Date().toISOString()
    };
    try {
      localStorage.setItem("ai_coach_last_doc_id", updates.document_id);
      if (updates.page) localStorage.setItem(`ai_coach_page_${updates.document_id}`, String(updates.page));
      if (updates.sentence_index) localStorage.setItem(`ai_coach_sent_${updates.document_id}`, String(updates.sentence_index));
    } catch (_) {}
  }
  if (updates.teaching_style) {
    try {
      localStorage.setItem("ai_coach_teaching_style", updates.teaching_style);
    } catch (_) {}
  }
  try {
    localStorage.setItem(PROGRESS_STORAGE_KEY, JSON.stringify(learningProgress));
  } catch (_) {}

  try {
    fetch("/api/progress", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    }).catch(() => {});
  } catch (_) {}
}

// Connect WebSocket
function connectWs() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat`);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    activeTurnId = 0;
    stopAudioPlayback();
    setStatus("Ready", "idle");

    // Sync saved settings to server session so user choices are never lost on reconnect/reopen
    if (currentTeachingStyle) {
      ws.send(JSON.stringify({ type: "set_teaching_style", style: currentTeachingStyle }));
    }
    let voiceVal = localStorage.getItem("ai_coach_voice") || (voiceSelect && voiceSelect.value);
    if (!voiceVal || voiceVal === "af_maple") {
      voiceVal = "en-US-JennyNeural";
      localStorage.setItem("ai_coach_voice", voiceVal);
    }
    const speedValNum = parseFloat((speedSlider && speedSlider.value) || localStorage.getItem("ai_coach_speed") || "1.0");
    ws.send(JSON.stringify({
      type: "update_settings",
      voice: voiceVal,
      speed: speedValNum
    }));

    // Sync lecture context to server session
    if (pendingWsLectureContext) {
      ws.send(JSON.stringify({
        type: "set_lecture_context",
        document_id: pendingWsLectureContext.document_id,
        page: pendingWsLectureContext.page,
        sentence_index: pendingWsLectureContext.sentence_index
      }));
      pendingWsLectureContext = null;
    } else if (lectureDocumentId && lecturePage) {
      ws.send(JSON.stringify({
        type: "set_lecture_context",
        document_id: lectureDocumentId,
        page: lecturePage,
        sentence_index: currentSentenceIndex
      }));
    }
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleServerMessage(data);
    } catch (e) {
      console.error("Failed to parse websocket message:", e);
    }
  };

  ws.onclose = (event) => {
    if (event.code === 4001) {
      setStatus("Replaced by a newer session", "idle");
      return;
    }
    setStatus("Disconnected", "idle");
    if (reconnectTimer === null) {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectWs();
      }, 2000);
    }
  };
}

function setStatus(text, stateClass) {
  const labels = {
    "Ready": "准备就绪",
    "Disconnected": "连接已断开",
    "Replaced by a newer session": "已在新标签页打开",
    "Listening...": "正在聆听",
    "Listening... Speak now": "正在聆听",
    "Processing speech...": "正在识别语音",
    "Coach is thinking...": "老师正在思考",
    "Coach speaking...": "老师正在朗读",
    "Interrupted": "已停止",
  };
  statusPill.textContent = labels[text] || text;
  statusPill.className = `status-pill ${stateClass}`;
}

function handleServerMessage(data) {
  // Every generated turn carries a monotonically increasing id.  Discarding
  // old-turn events keeps a cancelled LLM/TTS task from leaking into the new
  // conversation after an interrupt or reconnect.
  if (data.turn_id !== undefined && data.turn_id !== null) {
    const turnId = Number(data.turn_id);
    if (!Number.isInteger(turnId)) return;
    if (data.type === "session_reset") {
      activeTurnId = turnId;
      stopAudioPlayback();
    } else if (data.type === "user_transcript" || data.type === "lecture_context") {
      if (turnId < activeTurnId) return;
      if (turnId !== activeTurnId) {
        activeTurnId = turnId;
        stopAudioPlayback();
      }
    } else if (turnId !== activeTurnId) {
      return;
    }
  }

  switch (data.type) {
    case "lecture_context":
      if (data.active === false) {
        lectureDocumentId = null;
        lectureStatus.textContent = "自由对话模式";
        lecturePageMeta.textContent = "选择教材与单元，载入后开始学习。";
        lecturePageImage.hidden = true;
        const pw = document.getElementById("pdf-page-wrapper");
        if (pw) pw.style.display = "none";
        break;
      }
      lectureDocumentId = data.document_id;
      lecturePage = Number(data.page);
      lectureTotalPages = Number(data.pages);
      lecturePageInput.value = lecturePage;
      lecturePageInput.max = lectureTotalPages;
      lectureStatus.textContent = `已载入 · 第 ${lecturePage}/${lectureTotalPages} 页`;
      if (data.sentences && Array.isArray(data.sentences)) {
        lectureSentences = data.sentences;
        if (pendingTargetSentenceIndex !== null && pendingTargetSentenceIndex >= 1) {
          currentSentenceIndex = Math.min(pendingTargetSentenceIndex, lectureSentences.length || 1);
          pendingTargetSentenceIndex = null;
        } else if (!currentSentenceIndex || currentSentenceIndex > lectureSentences.length) {
          currentSentenceIndex = 1;
        }
        renderPdfSentenceHighlights(lectureSentences);
        updateSentencePreview();
        // Prefetch translations for the whole page in the background
        prefetchPageTranslations(lectureSentences);
      }
      break;

    case "status":
      if (data.text.includes("Listening")) {
        if (!isCurrentTurnComplete && (statusPill.textContent.includes("Coach is thinking") || statusPill.classList.contains("speaking")) && (data.turn_id === undefined || data.turn_id === null)) {
          break;
        }
        isCurrentTurnComplete = true;
        setStatus(data.text, "listening");
      }
      else if (data.text.includes("thinking") || data.text.includes("Transcribing")) setStatus(data.text, "thinking");
      else setStatus(data.text, "idle");
      break;

    case "user_transcript":
      appendUserMessage(data.text);
      isCurrentTurnComplete = false;
      // Clear whiteboard for new turn if not already cached
      const cleanedTranscript = (data.text || "").replace(/[\u00a0\u202f\u2009\u3000]/g, " ").replace(/\s+/g, " ").trim();
      if (sentenceNotesCache[cleanedTranscript]) {
        currentNotesMarkdown = sentenceNotesCache[cleanedTranscript];
        renderWhiteboard(currentNotesMarkdown);
      } else {
        currentNotesMarkdown = "";
        whiteboardContent.innerHTML = "<div class='loading-notes'>正在提炼重点难词与核心知识点…</div>";
      }
      break;

    case "voice_audio":
      setStatus("Coach speaking...", "speaking");
      interruptBtn.style.display = "inline-block";
      enqueueAudio(data.audio, data.sentence, data.mime_type || "audio/mpeg");
      break;

    case "notes_delta":
      currentNotesMarkdown += data.delta;
      renderWhiteboard(currentNotesMarkdown);
      break;

    case "turn_complete":
      if (data.voice_text) lastTurnVoice = data.voice_text;
      if (data.notes_markdown) {
        lastTurnNotes = data.notes_markdown;
        const s = lectureSentences[currentSentenceIndex - 1];
        const rawTarget = data.sentence_text || (s ? s.text : "");
        if (rawTarget) {
          const cleanedText = rawTarget.replace(/[\u00a0\u202f\u2009\u3000]/g, " ").replace(/\s+/g, " ").trim();
          sentenceNotesCache[cleanedText] = data.notes_markdown;
        }
      }
      isCurrentTurnComplete = true;
      checkContinuousAdvance();
      break;

    case "note_saved":
      showToast("笔记已成功收藏！");
      break;

    case "teaching_style_updated":
      if (data.style) {
        currentTeachingStyle = data.style;
        localStorage.setItem("ai_coach_teaching_style", data.style);
        const styleSelect = document.getElementById("teaching-style-select");
        if (styleSelect) styleSelect.value = data.style;
      }
      break;

    case "pronunciation_eval":
      if (data.eval) {
        displayPronunciationResult(data.eval);
      }
      break;

    case "interrupted":
      stopAudioPlayback();
      stopContinuousLecture();
      setStatus("Interrupted", "idle");
      interruptBtn.style.display = "none";
      break;


    case "session_reset":
      chatStream.innerHTML = "";
      whiteboardContent.innerHTML = "<div class='empty-state'><div class='empty-icon'>✦</div><h3>新的会话已开始</h3><p>开始说英语，或载入一页教材开始学习。</p></div>";
      stopAudioPlayback();
      lectureDocumentId = null;
      lectureStatus.textContent = "选择一页教材";
      lecturePageMeta.textContent = "选择教材与单元，载入后开始学习。";
      lecturePageImage.hidden = true;
      const pwReset = document.getElementById("pdf-page-wrapper");
      if (pwReset) pwReset.style.display = "none";
      const hlReset = document.getElementById("pdf-highlight-layer");
      if (hlReset) hlReset.innerHTML = "";
      setStatus("Ready", "idle");
      break;

    case "error":
      console.error("Server error:", data.message);
      isCurrentTurnComplete = true;
      setStatus("错误：" + data.message, "idle");
      break;
  }
}

function appendUserMessage(text) {
  const div = document.createElement("div");
  div.className = "message user-message";
  div.innerHTML = `
    <div class="msg-avatar">我</div>
    <div class="msg-content">
      <div class="msg-text">${escapeHtml(text)}</div>
    </div>
  `;
  chatStream.appendChild(div);
  chatStream.scrollTop = chatStream.scrollHeight;
}

function appendCoachMessage(text) {
  const div = document.createElement("div");
  div.className = "message coach-message";
  div.innerHTML = `
    <div class="msg-avatar">AI</div>
    <div class="msg-content">
      <div class="msg-text">${escapeHtml(text)}</div>
    </div>
  `;
  chatStream.appendChild(div);
  chatStream.scrollTop = chatStream.scrollHeight;
}

function renderMarkdownSafe(mdText) {
  if (!mdText) return "";
  if (window.marked && window.marked.parse) {
    try {
      const template = document.createElement("template");
      template.innerHTML = marked.parse(mdText);
      template.content
        .querySelectorAll("script, iframe, object, embed, style, link, meta, base, form, img, video, audio, source, svg, math")
        .forEach(node => node.remove());
      template.content.querySelectorAll("*").forEach(node => {
        [...node.attributes].forEach(attr => {
          const name = attr.name.toLowerCase();
          const value = attr.value.trim().toLowerCase();
          if (name.startsWith("on") || name === "srcdoc" ||
              ((name === "href" || name === "src") && /^(javascript:|data:|vbscript:)/.test(value))) {
            node.removeAttribute(attr.name);
          }
        });
      });
      const wrapper = document.createElement("div");
      wrapper.appendChild(template.content);
      return wrapper.innerHTML;
    } catch (_) {
      return escapeHtml(mdText);
    }
  }
  return escapeHtml(mdText);
}

// Word-level Edge-TTS Pronunciation Engine
let currentWordAudio = null;
let currentWordPlayBtn = null;

function playNoteWordAudio(word, btnEl = null) {
  if (!word || typeof word !== "string") return;
  const cleanWord = word.replace(/^[\[\(`'"\s]+|[\]\)`'"\s.,:;!?]+$/g, '').trim();
  if (!cleanWord) return;

  // If coach streaming or direct audio is playing, stop it so word audio is clear
  if (isPlayingAudio) {
    stopAudioPlayback();
  }
  if (typeof currentDirectAudio !== "undefined" && currentDirectAudio) {
    try {
      currentDirectAudio.pause();
      currentDirectAudio.src = "";
    } catch (_) {}
    currentDirectAudio = null;
  }

  // Stop previous word audio
  if (currentWordAudio) {
    try {
      currentWordAudio.pause();
      currentWordAudio.src = "";
    } catch (_) {}
    currentWordAudio = null;
  }
  if (currentWordPlayBtn) {
    currentWordPlayBtn.classList.remove("playing");
    currentWordPlayBtn = null;
  }

  // Determine Edge-TTS voice (Default to pure native US English Jenny)
  let voice = "en-US-JennyNeural";
  const voiceSelectEl = document.getElementById("voice-select");
  const selectedVoice = (voiceSelectEl && voiceSelectEl.value) || localStorage.getItem("ai_coach_voice");
  if (selectedVoice && selectedVoice.startsWith("en-")) {
    voice = selectedVoice;
  }

  const cleanLower = cleanWord.toLowerCase();
  const cacheKey = `${cleanLower}:${voice}`;

  let audioUrl = `/api/tts?text=${encodeURIComponent(cleanWord)}&voice=${encodeURIComponent(voice)}`;
  if (wordAudioCache.has(cacheKey)) {
    audioUrl = wordAudioCache.get(cacheKey);
  }

  const audio = new Audio(audioUrl);
  currentWordAudio = audio;

  // Pre-cache blob if not already cached
  if (!wordAudioCache.has(cacheKey)) {
    fetch(audioUrl)
      .then(r => r.ok ? r.blob() : null)
      .then(blob => {
        if (blob) {
          wordAudioCache.set(cacheKey, URL.createObjectURL(blob));
        }
      })
      .catch(() => {});
  }

  const targetBtn = btnEl || (document.querySelector(`.vocab-play-btn[data-word="${CSS.escape ? CSS.escape(cleanWord) : cleanWord}"]`));
  if (targetBtn) {
    targetBtn.classList.add("playing");
    currentWordPlayBtn = targetBtn;
  }

  const resetState = () => {
    if (targetBtn) targetBtn.classList.remove("playing");
    if (currentWordPlayBtn === targetBtn) currentWordPlayBtn = null;
    if (currentWordAudio === audio) currentWordAudio = null;
  };

  audio.onended = resetState;
  audio.onerror = (err) => {
    console.warn("Edge-TTS word playback error, fallback to Web Speech:", err);
    resetState();
    if ('speechSynthesis' in window) {
      try {
        window.speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(cleanWord);
        u.lang = voice.startsWith("en-GB") ? 'en-GB' : 'en-US';
        u.onend = resetState;
        u.onerror = resetState;
        if (targetBtn) {
          targetBtn.classList.add("playing");
          currentWordPlayBtn = targetBtn;
        }
        window.speechSynthesis.speak(u);
      } catch (_) {
        resetState();
      }
    }
  };

  audio.play().catch(e => {
    console.warn("Word audio play blocked or failed:", e);
    resetState();
  });
}

const VOCAB_EXCLUDE_WORDS = new Set([
  "sentence text", "term", "word", "phrase", "word/phrase", "word 1", "word 2",
  "key vocabulary", "syntax structure", "sentence", "translation",
  "core skeleton", "syntax hierarchy tree", "syntax tree", "sense groups", "reading flow",
  "reading flow / sense groups", "key knowledge point"
]);

function enhanceWhiteboardVocab(container) {
  if (!container) return;
  const strongs = container.querySelectorAll("li strong, p strong");
  strongs.forEach(strong => {
    if (strong.closest("h1, h2, h3, h4")) return;
    const rawText = strong.textContent || "";
    // If it contains Chinese, it's a structural label like 原句, 中文释义, 搭配/用法, 例句, 要点 etc.
    if (/[\u4e00-\u9fff]/.test(rawText)) return;
    const cleanWord = rawText.replace(/^[\[\(`'"\s]+|[\]\)`'"\s.,:;!?]+$/g, '').trim();
    if (!cleanWord || cleanWord.length < 2 || cleanWord.length > 45) return;
    if (!/^[a-zA-Z\s'-]+$/.test(cleanWord)) return;
    if (cleanWord.split(/\s+/).filter(Boolean).length > 5) return;
    if (VOCAB_EXCLUDE_WORDS.has(cleanWord.toLowerCase())) return;

    strong.classList.add("vocab-word-clickable");
    strong.dataset.word = cleanWord;
    strong.title = `点击听 "${cleanWord}" 发音 (Edge-TTS)`;

    // Check if play button already exists right after
    let next = strong.nextElementSibling;
    if (!next || !next.classList.contains("vocab-play-btn")) {
      const btn = document.createElement("button");
      btn.className = "vocab-play-btn";
      btn.type = "button";
      btn.dataset.word = cleanWord;
      btn.title = `点击听 "${cleanWord}" 发音 (Edge-TTS)`;
      btn.setAttribute("aria-label", `朗读 ${cleanWord}`);
      btn.innerHTML = `<span class="vocab-play-icon" aria-hidden="true">🔊</span>`;
      strong.insertAdjacentElement("afterend", btn);
    }

    // Proactively prefetch Edge-TTS audio for this word so clicking is instant
    const voice = getEffectiveWordTtsVoice();
    const cacheKey = `${cleanWord.toLowerCase()}:${voice}`;
    if (!wordAudioCache.has(cacheKey)) {
      const url = `/api/tts?text=${encodeURIComponent(cleanWord)}&voice=${encodeURIComponent(voice)}&speed=1.0`;
      fetch(url)
        .then(r => r.ok ? r.blob() : null)
        .then(blob => {
          if (blob) {
            wordAudioCache.set(cacheKey, URL.createObjectURL(blob));
          }
        })
        .catch(() => {});
    }
  });
}

let latestWhiteboardMarkdown = "";

function renderWhiteboard(mdText) {
  latestWhiteboardMarkdown = mdText || "";
  whiteboardContent.innerHTML = renderMarkdownSafe(latestWhiteboardMarkdown);
  enhanceWhiteboardVocab(whiteboardContent);
  whiteboardContent.scrollTop = whiteboardContent.scrollHeight;

  try {
    const s = (typeof lectureSentences !== "undefined" && lectureSentences && currentSentenceIndex) ? lectureSentences[currentSentenceIndex - 1] : null;
    const sentText = s ? (s.text || "").replace(/[\u00a0\u202f\u2009\u3000]/g, " ").replace(/\s+/g, " ").trim() : "";
    syncChannel.postMessage({
      type: "notes_update",
      data: {
        notes: latestWhiteboardMarkdown,
        sentence: sentText
      }
    });
  } catch (_) {}
}

// Click delegation for whiteboard vocabulary pronunciation
if (whiteboardContent) {
  whiteboardContent.addEventListener("click", (e) => {
    const btn = e.target.closest(".vocab-play-btn");
    if (btn) {
      e.stopPropagation();
      e.preventDefault();
      playNoteWordAudio(btn.dataset.word, btn);
      return;
    }
    const wordEl = e.target.closest(".vocab-word-clickable");
    if (wordEl) {
      e.stopPropagation();
      e.preventDefault();
      const word = wordEl.dataset.word || wordEl.textContent;
      const siblingBtn = wordEl.nextElementSibling?.classList.contains("vocab-play-btn") ? wordEl.nextElementSibling : null;
      playNoteWordAudio(word, siblingBtn || wordEl);
      return;
    }
  });
}

// Audio queue playback
function enqueueAudio(b64Audio, sentence, mimeType = "audio/mpeg") {
  audioQueue.push({ b64: b64Audio, sentence: sentence, mimeType: mimeType });
  if (!isPlayingAudio) {
    playNextAudio(playbackGeneration);
  }
}

function playNextAudio(generation = playbackGeneration) {
  if (generation !== playbackGeneration) return;

  if (audioQueue.length === 0) {
    isPlayingAudio = false;
    audioPlaybackEndTime = Date.now();
    interruptBtn.style.display = "none";
    if (getEffectiveActionMode() !== "read_only") {
      setStatus("Listening...", "listening");
    } else {
      setStatus("已就绪", "idle");
    }
    checkContinuousAdvance();
    return;
  }


  isPlayingAudio = true;
  const item = audioQueue.shift();
  appendCoachMessage(item.sentence);

  const audioBlob = b64ToBlob(item.b64, item.mimeType || "audio/mpeg");
  const audioUrl = URL.createObjectURL(audioBlob);
  currentAudioUrl = audioUrl;
  currentAudioElement = new Audio(audioUrl);
  let finished = false;

  const finish = () => {
    if (finished) return;
    finished = true;
    if (currentAudioUrl === audioUrl) currentAudioUrl = null;
    URL.revokeObjectURL(audioUrl);
    if (currentAudioElement && currentAudioElement.src === audioUrl) {
      currentAudioElement = null;
    }
    playNextAudio(generation);
  };

  currentAudioElement.onended = finish;

  currentAudioElement.onerror = finish;

  currentAudioElement.play().catch(e => {
    console.error("Audio playback failed:", e);
    finish();
  });
}

function stopAudioPlayback() {
  playbackGeneration += 1;
  audioQueue.length = 0;
  if (currentAudioElement) {
    try {
      currentAudioElement.onended = null;
      currentAudioElement.onerror = null;
      currentAudioElement.pause();
      currentAudioElement.src = "";
    } catch (_) {}
    currentAudioElement = null;
  }
  if (typeof currentDirectAudio !== "undefined" && currentDirectAudio) {
    try {
      currentDirectAudio.onended = null;
      currentDirectAudio.onerror = null;
      currentDirectAudio._cancelled = true;
      currentDirectAudio.pause();
      currentDirectAudio.src = "";
    } catch (_) {}
    currentDirectAudio = null;
  }
  if (typeof currentDirectBtn !== "undefined" && currentDirectBtn) {
    currentDirectBtn.classList.remove("playing");
    currentDirectBtn = null;
  }
  document.querySelectorAll(".inline-play-btn.playing").forEach(b => b.classList.remove("playing"));
  if (currentAudioUrl) {
    URL.revokeObjectURL(currentAudioUrl);
    currentAudioUrl = null;
  }
  if (typeof loopPlaybackTimer !== "undefined" && loopPlaybackTimer) {
    clearTimeout(loopPlaybackTimer);
    loopPlaybackTimer = null;
  }
  const docVideo = document.getElementById("document-video-element");
  if (docVideo && !docVideo.paused) {
    try { docVideo.pause(); } catch (_) {}
  }
  try {
    syncChannel.postMessage({ type: "pause", data: { sentence_index: currentSentenceIndex, ended: false } });
    syncChannel.postMessage({ type: "video_pause" });
  } catch (_) {}
  if (typeof videoCheckInterval !== "undefined" && videoCheckInterval) {
    clearInterval(videoCheckInterval);
    videoCheckInterval = null;
  }
  isPlayingAudio = false;
  audioPlaybackEndTime = Date.now();
}


// Continuous Lecture & Shadow Typing Engine with Gamified Juice
const SoundEngine = {
  ctx: null,
  enabled: localStorage.getItem("ai_coach_sound_effects") !== "false",
  init() {
    if (!this.ctx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (AudioCtx) this.ctx = new AudioCtx();
    }
    if (this.ctx && this.ctx.state === "suspended") {
      this.ctx.resume();
    }
  },
  toggle() {
    this.enabled = !this.enabled;
    localStorage.setItem("ai_coach_sound_effects", this.enabled ? "true" : "false");
    const btn = document.getElementById("typing-sound-btn");
    if (btn) {
      btn.textContent = this.enabled ? "🔊" : "🔇";
      btn.title = this.enabled ? "敲击音效开关 (开启中)" : "敲击音效开关 (已静音)";
    }
    showToast(this.enabled ? "🔊 键盘敲击音效已开启" : "🔇 敲击音效已静音");
  },
  playKeyClick() {
    if (!this.enabled) return;
    try {
      this.init();
      if (!this.ctx) return;
      const now = this.ctx.currentTime;
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.type = "sine";
      // Natural mechanical keyboard click without combo pitch shifts
      const freq = 680 + (Math.random() * 40 - 20);
      osc.frequency.setValueAtTime(freq, now);
      osc.frequency.exponentialRampToValueAtTime(freq * 0.5, now + 0.02);
      gain.gain.setValueAtTime(0.05, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.02);
      osc.connect(gain);
      gain.connect(this.ctx.destination);
      osc.start(now);
      osc.stop(now + 0.02);
    } catch (_) {}
  },
  playComboMilestone(combo) {
    // Combo milestone audio removed per user request
    return;
  },
  playSuccessChime() {
    if (!this.enabled) return;
    try {
      this.init();
      if (!this.ctx) return;
      const now = this.ctx.currentTime;
      const notes = [523.25, 659.25, 783.99, 1046.50];
      notes.forEach((freq, idx) => {
        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();
        osc.type = "sine";
        const noteTime = now + (idx * 0.07);
        osc.frequency.setValueAtTime(freq, noteTime);
        gain.gain.setValueAtTime(0.1, noteTime);
        gain.gain.exponentialRampToValueAtTime(0.001, noteTime + 0.3);
        osc.connect(gain);
        gain.connect(this.ctx.destination);
        osc.start(noteTime);
        osc.stop(noteTime + 0.3);
      });
    } catch (_) {}
  },
  playPerfectFanfare() {
    if (!this.enabled) return;
    try {
      this.init();
      if (!this.ctx) return;
      const now = this.ctx.currentTime;
      const notes = [587.33, 739.99, 880.00, 1174.66];
      notes.forEach((freq, idx) => {
        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();
        osc.type = "triangle";
        const noteTime = now + (idx * 0.08);
        osc.frequency.setValueAtTime(freq, noteTime);
        gain.gain.setValueAtTime(0.12, noteTime);
        gain.gain.exponentialRampToValueAtTime(0.001, noteTime + 0.4);
        osc.connect(gain);
        gain.connect(this.ctx.destination);
        osc.start(noteTime);
        osc.stop(noteTime + 0.4);
      });
    } catch (_) {}
  },
  playErrorThud() {
    if (!this.enabled) return;
    try {
      this.init();
      if (!this.ctx) return;
      const now = this.ctx.currentTime;
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.type = "sawtooth";
      osc.frequency.setValueAtTime(140, now);
      osc.frequency.exponentialRampToValueAtTime(70, now + 0.05);
      gain.gain.setValueAtTime(0.06, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.05);
      osc.connect(gain);
      gain.connect(this.ctx.destination);
      osc.start(now);
      osc.stop(now + 0.05);
    } catch (_) {}
  }
};

const ConfettiEngine = {
  canvas: null,
  ctx: null,
  particles: [],
  animating: false,
  init() {
    this.canvas = document.getElementById("confetti-canvas");
    if (this.canvas) {
      this.ctx = this.canvas.getContext("2d");
      this.resize();
      window.addEventListener("resize", () => this.resize());
    }
  },
  resize() {
    if (!this.canvas) return;
    this.canvas.width = window.innerWidth;
    this.canvas.height = window.innerHeight;
  },
  fireConfetti(originX, originY, count = 45) {
    if (!this.canvas || !this.ctx) this.init();
    if (!this.canvas) return;
    const x = originX !== undefined ? originX : window.innerWidth / 2;
    const y = originY !== undefined ? originY : window.innerHeight * 0.35;
    const colors = ["#38bdf8", "#4ade80", "#fbbf24", "#f43f5e", "#a855f7", "#ffffff"];

    for (let i = 0; i < count; i++) {
      const angle = Math.random() * Math.PI * 2;
      const speed = 4 + Math.random() * 8;
      this.particles.push({
        x,
        y,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed - 3,
        size: 5 + Math.random() * 6,
        color: colors[Math.floor(Math.random() * colors.length)],
        alpha: 1,
        decay: 0.015 + Math.random() * 0.02,
        rotation: Math.random() * 360,
        rotSpeed: (Math.random() - 0.5) * 12
      });
    }

    if (!this.animating) {
      this.animating = true;
      this.loop();
    }
  },
  loop() {
    if (!this.ctx || !this.canvas) return;
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

    for (let i = this.particles.length - 1; i >= 0; i--) {
      const p = this.particles[i];
      p.x += p.vx;
      p.y += p.vy;
      p.vy += 0.22;
      p.vx *= 0.98;
      p.alpha -= p.decay;
      p.rotation += p.rotSpeed;

      if (p.alpha <= 0 || p.y > this.canvas.height) {
        this.particles.splice(i, 1);
        continue;
      }

      this.ctx.save();
      this.ctx.translate(p.x, p.y);
      this.ctx.rotate((p.rotation * Math.PI) / 180);
      this.ctx.fillStyle = p.color;
      this.ctx.globalAlpha = Math.max(0, p.alpha);
      this.ctx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.6);
      this.ctx.restore();
    }

    if (this.particles.length > 0) {
      requestAnimationFrame(() => this.loop());
    } else {
      this.animating = false;
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    }
  }
};

function showFloatingXp(amount, targetEl) {
  if (!amount) return;
  const el = document.createElement("div");
  el.className = "floating-xp";
  el.textContent = `+${amount} XP ✨`;

  let left = window.innerWidth / 2;
  let top = window.innerHeight * 0.35;
  if (targetEl && targetEl.getBoundingClientRect) {
    const rect = targetEl.getBoundingClientRect();
    left = rect.left + rect.width / 2;
    top = rect.top;
  }

  el.style.left = `${left - 35}px`;
  el.style.top = `${top - 20}px`;
  document.body.appendChild(el);

  setTimeout(() => el.remove(), 1250);
}

let currentTypingTarget = "";
let isTypingCompleted = false;
let currentTypingCombo = 0;
let maxTypingComboInSentence = 0;
let typingStartTime = null;
let lastTypedLength = 0;
let typedBuffer = "";
let currentTypingRepeatRounds = parseInt(localStorage.getItem("ai_coach_typing_repeats") || "1", 10);
if (![1, 2, 3].includes(currentTypingRepeatRounds)) currentTypingRepeatRounds = 1;
let currentTypingRound = 1;
const completedWordIndicesInCurrentRound = new Set();
let typingScrollTargetTop = null;
let typingScrollRafId = null;

function updateTypingRepeatButton() {
  const btn = document.getElementById("typing-repeat-btn");
  const label = document.getElementById("typing-repeat-label");
  if (!btn || !label) return;
  label.textContent = `${currentTypingRepeatRounds}遍`;
  btn.classList.remove("repeat-2", "repeat-3");
  if (currentTypingRepeatRounds === 2) {
    btn.classList.add("repeat-2");
    btn.title = "单句跟打遍数：需敲打 2 遍才过关 (点击切换为 3 遍)";
  } else if (currentTypingRepeatRounds === 3) {
    btn.classList.add("repeat-3");
    btn.title = "单句跟打遍数：需敲打 3 遍才过关 (点击切换为 1 遍)";
  } else {
    btn.title = "单句跟打遍数：打 1 遍过关 (点击切换为 2 遍)";
  }
}

function cycleTypingRepeatRounds() {
  let next = currentTypingRepeatRounds + 1;
  if (next > 3) next = 1;
  currentTypingRepeatRounds = next;
  localStorage.setItem("ai_coach_typing_repeats", next.toString());
  updateTypingRepeatButton();
  try {
    syncChannel.postMessage({ type: "repeat_rounds_changed", data: { rounds: next } });
  } catch (_) {}

  // If a sentence is currently active and not completed, reset round progress
  if (currentTypingTarget && !isTypingCompleted) {
    currentTypingRound = 1;
    typedBuffer = "";
    lastTypedLength = 0;
    const display = document.getElementById("typing-target-display");
    if (display) {
      if (typingScrollRafId) {
        cancelAnimationFrame(typingScrollRafId);
        typingScrollRafId = null;
      }
      display.scrollTop = 0;
      typingScrollTargetTop = null;
      display.innerHTML = currentTypingTarget
        .split("")
        .map((ch, idx) => `<span class="${idx === 0 ? 'char-current' : 'char-pending'}" data-idx="${idx}">${escapeHtml(ch)}</span>`)
        .join("");
    }
    const progressText = document.getElementById("typing-progress-text");
    if (progressText) {
      if (currentTypingRepeatRounds > 1) {
        progressText.textContent = `进度: 0 / ${currentTypingTarget.length} 字符 (第 1/${currentTypingRepeatRounds} 遍)`;
      } else {
        progressText.textContent = `进度: 0 / ${currentTypingTarget.length} 字符`;
      }
    }
  }
  showToast(`🎯 已切换为单句跟打「${next} 遍」过关模式`);
}

// --- Word-Level Speech Audio on Typing (Edge-TTS) ---
let isWordAudioEnabled = localStorage.getItem("ai_coach_word_audio") !== "false";
let isWordTranslationAudioEnabled = localStorage.getItem("ai_coach_word_trans_audio") !== "false";
const wordAudioCache = new Map(); // key: "word:voice" or "zh:word:voice" -> blobUrl
let wordAudioEl = null;
let wordZhAudioEl = null;
let currentSentenceWords = [];
let lastSpokenWordIndex = -1;
let currentWordAudioSessionId = 0;
let currentWordAudioReqTime = 0;
let currentWordZhAudioReqTime = 0;
const sentenceWordGlosses = new Map(); // key: cleaned sentence text -> { word_lower: zh_gloss }
const sentenceWordGlossesPromises = new Map(); // key: cleaned sentence text -> Promise
const singleWordGlossCache = new Map(); // key: word_lower -> zh_gloss

function parseSentenceWords(sentence) {
  if (!sentence) return [];
  const words = [];
  const regex = /[a-zA-Z0-9]+(?:['’\-][a-zA-Z0-9]+)*/g;
  let match;
  while ((match = regex.exec(sentence)) !== null) {
    const rawWord = match[0];
    const startIndex = match.index;
    const endIndex = startIndex + rawWord.length;
    const cleanWord = rawWord.replace(/[’‘]/g, "'").trim();
    if (cleanWord.length > 0) {
      words.push({
        raw: rawWord,
        clean: cleanWord,
        startIndex: startIndex,
        endIndex: endIndex
      });
    }
  }
  return words;
}

function getEffectiveWordTtsVoice() {
  const voiceSelectEl = document.getElementById("voice-select");
  const selectedVoice = (voiceSelectEl && voiceSelectEl.value) || localStorage.getItem("ai_coach_voice") || "";
  if (selectedVoice.includes("Neural")) {
    return selectedVoice;
  }
  return "en-US-JennyNeural";
}

const IRREGULAR_WORD_MAP = {
  went: "go", gone: "go", was: "be", were: "be", been: "be", is: "be", are: "be", am: "be",
  had: "have", has: "have", did: "do", done: "do", does: "do",
  said: "say", made: "make", came: "come", took: "take", taken: "take",
  saw: "see", seen: "see", knew: "know", known: "know", got: "get", gotten: "get",
  gave: "give", given: "give", found: "find", thought: "think", told: "tell",
  became: "become", left: "leave", felt: "feel", brought: "bring", began: "begin", begun: "begin",
  kept: "keep", held: "hold", wrote: "write", written: "write", stood: "stand",
  heard: "hear", meant: "mean", met: "meet", ran: "run", paid: "pay", sat: "sit",
  spoke: "speak", spoken: "speak", lay: "lie", lain: "lie", led: "lead",
  read: "read", grew: "grow", grown: "grow", lost: "lose", fell: "fall", fallen: "fall",
  sent: "send", built: "build", understood: "understand", drew: "draw", drawn: "draw",
  broke: "break", broken: "break", spent: "spend", bought: "buy", wore: "wear", worn: "wear",
  chose: "choose", chosen: "choose", better: "good", best: "good", worse: "bad", worst: "bad",
  more: "many", most: "many", less: "little", least: "little",
  children: "child", men: "man", women: "woman", feet: "foot", teeth: "tooth", mice: "mouse", people: "person"
};

function getGlossCandidateWords(word) {
  const raw = (word || "").trim();
  if (!raw) return [];
  const lower = raw.toLowerCase().replace(/[’‘]/g, "'");
  const candidates = [lower, raw];

  const checkAndAdd = (w) => {
    if (!w) return;
    candidates.push(w);
    if (IRREGULAR_WORD_MAP[w]) candidates.push(IRREGULAR_WORD_MAP[w]);
  };

  checkAndAdd(lower);

  if (lower.endsWith("'s")) checkAndAdd(lower.slice(0, -2));
  if (lower.endsWith("s'")) checkAndAdd(lower.slice(0, -1));
  if (lower.endsWith("'t")) checkAndAdd(lower.slice(0, -2));

  if (lower.endsWith("ies") && lower.length > 4) checkAndAdd(lower.slice(0, -3) + "y");
  if (lower.endsWith("es") && lower.length > 3) {
    checkAndAdd(lower.slice(0, -2));
    checkAndAdd(lower.slice(0, -1));
  }
  if (lower.endsWith("s") && !lower.endsWith("ss") && lower.length > 2) {
    checkAndAdd(lower.slice(0, -1));
  }
  if (lower.endsWith("ied") && lower.length > 4) checkAndAdd(lower.slice(0, -3) + "y");
  if (lower.endsWith("ed") && lower.length > 3) {
    checkAndAdd(lower.slice(0, -2));
    checkAndAdd(lower.slice(0, -1));
    if (lower.length > 4 && lower[lower.length - 3] === lower[lower.length - 4]) {
      checkAndAdd(lower.slice(0, -3));
    }
  }
  if (lower.endsWith("ing") && lower.length > 4) {
    checkAndAdd(lower.slice(0, -3));
    checkAndAdd(lower.slice(0, -3) + "e");
    if (lower.length > 5 && lower[lower.length - 4] === lower[lower.length - 5]) {
      checkAndAdd(lower.slice(0, -4));
    }
  }
  if (lower.endsWith("ly") && lower.length > 3) {
    checkAndAdd(lower.slice(0, -2));
    checkAndAdd(lower.slice(0, -3) + "y");
    checkAndAdd(lower.slice(0, -2) + "e");
  }
  if (lower.endsWith("er") && lower.length > 3) {
    checkAndAdd(lower.slice(0, -2));
    checkAndAdd(lower.slice(0, -1));
  }
  if (lower.endsWith("est") && lower.length > 4) {
    checkAndAdd(lower.slice(0, -3));
    checkAndAdd(lower.slice(0, -2));
  }
  return [...new Set(candidates)];
}

function findGlossForWord(glosses, word) {
  if (!glosses || !word) return "";
  const candidates = getGlossCandidateWords(word);
  for (const c of candidates) {
    if (glosses[c]) return glosses[c];
  }
  const cleanWord = word.toLowerCase().replace(/[^a-z0-9]/g, "");
  if (cleanWord) {
    for (const [k, v] of Object.entries(glosses)) {
      const cleanKey = k.toLowerCase().replace(/[^a-z0-9]/g, "");
      if (cleanKey === cleanWord) return v;
    }
  }
  return "";
}

async function getOrFetchWordGloss(word, sentenceText) {
  const cleanedSentence = (sentenceText || "").replace(/[\u00a0\u202f\u2009\u3000]/g, " ").replace(/\s+/g, " ").trim();
  const lower = (word || "").toLowerCase().trim();

  // 1. Direct memory cache
  const cachedGlosses = sentenceWordGlosses.get(cleanedSentence);
  if (cachedGlosses) {
    const g = findGlossForWord(cachedGlosses, word);
    if (g) return g;
  }

  // 2. Await in-flight promise if available
  if (sentenceWordGlossesPromises.has(cleanedSentence)) {
    const fetchedGlosses = await sentenceWordGlossesPromises.get(cleanedSentence);
    if (fetchedGlosses) {
      const g = findGlossForWord(fetchedGlosses, word);
      if (g) return g;
    }
  } else if (!cachedGlosses && cleanedSentence) {
    // 3. Trigger sentence glosses fetch
    const fetchedGlosses = await fetchSentenceWordGlosses(cleanedSentence);
    if (fetchedGlosses) {
      const g = findGlossForWord(fetchedGlosses, word);
      if (g) return g;
    }
  }

  // 4. Check single-word cache
  if (singleWordGlossCache.has(lower)) {
    return singleWordGlossCache.get(lower);
  }

  // 5. Fallback: query /api/translate for this single word
  try {
    const res = await fetch("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: lower })
    });
    if (res.ok) {
      const data = await res.json();
      if (data && data.translation) {
        let t = data.translation.trim();
        t = t.split(/[/,、;；\s]/)[0].trim();
        t = t.replace(/[.。,，、/／\\~～…\-_~～\s]+$/, "");
        if (t.length > 4) t = t.slice(0, 4);
        if (t) {
          singleWordGlossCache.set(lower, t);
          if (cachedGlosses) cachedGlosses[lower] = t;
          return t;
        }
      }
    }
  } catch (e) {
    console.debug("Single word translate fallback failed for:", lower, e);
  }

  return "";
}

async function fetchSentenceWordGlosses(sentenceText) {
  if (!sentenceText) return {};
  const cleaned = sentenceText.replace(/[\u00a0\u202f\u2009\u3000]/g, " ").replace(/\s+/g, " ").trim();
  if (!cleaned) return {};
  if (sentenceWordGlosses.has(cleaned)) {
    return sentenceWordGlosses.get(cleaned);
  }
  if (sentenceWordGlossesPromises.has(cleaned)) {
    return sentenceWordGlossesPromises.get(cleaned);
  }

  const promise = (async () => {
    try {
      const res = await fetch("/api/sentence/word-glosses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sentence: cleaned })
      });
      if (res.ok) {
        const data = await res.json();
        if (data && data.glosses) {
          sentenceWordGlosses.set(cleaned, data.glosses);
          if (isWordTranslationAudioEnabled) {
            prefetchGlossTranslations(Object.values(data.glosses));
          }
          return data.glosses;
        }
      }
    } catch (err) {
      console.warn("Failed to fetch word glosses:", err);
    } finally {
      sentenceWordGlossesPromises.delete(cleaned);
    }
    return {};
  })();

  sentenceWordGlossesPromises.set(cleaned, promise);
  return promise;
}

function prefetchGlossTranslations(zhWords) {
  if (!zhWords || zhWords.length === 0) return;
  const voice = "zh-CN-XiaoxiaoNeural";
  const uniqueZh = [...new Set(zhWords.filter(Boolean))];
  uniqueZh.forEach(zh => {
    const cacheKey = `zh:${zh}:${voice}`;
    if (wordAudioCache.has(cacheKey)) return;
    const url = `/api/tts?text=${encodeURIComponent(zh)}&voice=${encodeURIComponent(voice)}&speed=1.0`;
    fetch(url)
      .then(r => r.ok ? r.blob() : null)
      .then(blob => {
        if (blob) {
          const blobUrl = URL.createObjectURL(blob);
          wordAudioCache.set(cacheKey, blobUrl);
        }
      })
      .catch(e => console.debug("Prefetch failed for zh gloss:", zh, e));
  });
}

function prefetchSentenceWords(words) {
  if (!words || words.length === 0) return;
  const voice = getEffectiveWordTtsVoice();
  const uniqueWords = [...new Set(words.map(w => w.clean))];
  uniqueWords.forEach(w => {
    const cleanLower = w.toLowerCase();
    const cacheKey = `${cleanLower}:${voice}`;
    if (wordAudioCache.has(cacheKey)) return;
    let ttsText = w;
    if (cleanLower === "i") ttsText = "I";
    const url = `/api/tts?text=${encodeURIComponent(ttsText)}&voice=${encodeURIComponent(voice)}&speed=1.0`;
    fetch(url)
      .then(r => r.ok ? r.blob() : null)
      .then(blob => {
        if (blob) {
          const blobUrl = URL.createObjectURL(blob);
          wordAudioCache.set(cacheKey, blobUrl);
        }
      })
      .catch(e => console.debug("Prefetch failed for word:", w, e));
  });
}

function playWordTranslationAudio(zhText, sessionId) {
  if (!isWordTranslationAudioEnabled) return;
  const cleanZh = (zhText || "").trim();
  if (!cleanZh) return;
  if (sessionId !== undefined && sessionId !== currentWordAudioSessionId) return;

  const voice = "zh-CN-XiaoxiaoNeural";
  const cacheKey = `zh:${cleanZh}:${voice}`;

  if (!wordZhAudioEl) {
    wordZhAudioEl = new Audio();
  }

  try {
    wordZhAudioEl.pause();
    wordZhAudioEl.currentTime = 0;
  } catch (e) {}

  if (wordAudioCache.has(cacheKey)) {
    if (sessionId === undefined || sessionId === currentWordAudioSessionId) {
      wordZhAudioEl.src = wordAudioCache.get(cacheKey);
      wordZhAudioEl.play().catch(e => console.debug("Zh word audio play interrupted:", e));
    }
    return;
  }

  const reqTime = Date.now();
  currentWordZhAudioReqTime = reqTime;
  const url = `/api/tts?text=${encodeURIComponent(cleanZh)}&voice=${encodeURIComponent(voice)}&speed=1.0`;
  fetch(url)
    .then(r => r.ok ? r.blob() : null)
    .then(blob => {
      if (!blob) return;
      const blobUrl = URL.createObjectURL(blob);
      wordAudioCache.set(cacheKey, blobUrl);
      if (currentWordZhAudioReqTime === reqTime && (sessionId === undefined || sessionId === currentWordAudioSessionId)) {
        wordZhAudioEl.src = blobUrl;
        wordZhAudioEl.play().catch(e => console.debug("Zh word audio play interrupted:", e));
      }
    })
    .catch(err => console.warn("Failed to play zh word audio:", err));
}

function playWordAudio(wordClean, force = false, wordIndex = -1) {
  if (!isWordAudioEnabled && !force) return;
  const raw = (wordClean || "").trim();
  if (!raw) return;
  const cleanLower = raw.toLowerCase();
  const voice = getEffectiveWordTtsVoice();
  const cacheKey = `${cleanLower}:${voice}`;

  const sessionId = ++currentWordAudioSessionId;

  if (!wordAudioEl) {
    wordAudioEl = new Audio();
  }

  try {
    wordAudioEl.pause();
    wordAudioEl.currentTime = 0;
  } catch (e) {}
  if (wordZhAudioEl) {
    try {
      wordZhAudioEl.pause();
      wordZhAudioEl.currentTime = 0;
    } catch (e) {}
  }

  // Chained Chinese translation pronunciation on English audio completion
  wordAudioEl.onended = () => {
    if (!isWordTranslationAudioEnabled && !force) return;
    if (sessionId !== currentWordAudioSessionId) return;

    getOrFetchWordGloss(raw, currentTypingTarget)
      .then(gloss => {
        if (sessionId === currentWordAudioSessionId && gloss) {
          playWordTranslationAudio(gloss, sessionId);
        }
      })
      .catch(e => console.debug("Gloss resolution for audio failed:", e));
  };

  if (wordAudioCache.has(cacheKey)) {
    wordAudioEl.src = wordAudioCache.get(cacheKey);
    wordAudioEl.play().catch(e => console.debug("Word audio playback interrupted:", e));
    return;
  }

  let ttsText = raw;
  if (cleanLower === "i") ttsText = "I";
  const reqTime = Date.now();
  currentWordAudioReqTime = reqTime;
  const url = `/api/tts?text=${encodeURIComponent(ttsText)}&voice=${encodeURIComponent(voice)}&speed=1.0`;
  fetch(url)
    .then(r => {
      if (!r.ok) throw new Error("TTS fetch failed");
      return r.blob();
    })
    .then(blob => {
      const blobUrl = URL.createObjectURL(blob);
      wordAudioCache.set(cacheKey, blobUrl);
      if (currentWordAudioReqTime === reqTime && sessionId === currentWordAudioSessionId) {
        wordAudioEl.src = blobUrl;
        wordAudioEl.play().catch(e => console.debug("Word audio play interrupted:", e));
      }
    })
    .catch(err => console.warn("Failed to play word audio:", err));
}

function checkAndPlayWordAtChar(charIndex) {
  if (!isWordAudioEnabled || !currentSentenceWords || currentSentenceWords.length === 0) return;
  const wordIdx = currentSentenceWords.findIndex(w => charIndex >= w.startIndex && charIndex < w.endIndex);
  if (wordIdx !== -1) {
    if (wordIdx !== lastSpokenWordIndex) {
      lastSpokenWordIndex = wordIdx;
      playWordAudio(currentSentenceWords[wordIdx].clean, false, wordIdx);
    }
  }
}

function handleTypingBackspace(currentLen) {
  currentWordAudioSessionId++;
  if (wordAudioEl) {
    try { wordAudioEl.pause(); wordAudioEl.currentTime = 0; } catch (_) {}
  }
  if (wordZhAudioEl) {
    try { wordZhAudioEl.pause(); wordZhAudioEl.currentTime = 0; } catch (_) {}
  }
  if (!currentSentenceWords || currentSentenceWords.length === 0) return;
  if (currentLen === 0) {
    lastSpokenWordIndex = -1;
    return;
  }
  const lastCharIdx = currentLen - 1;
  const wordIdx = currentSentenceWords.findIndex(w => lastCharIdx >= w.startIndex && lastCharIdx < w.endIndex);
  lastSpokenWordIndex = wordIdx;
}

function normalizeTypingChar(ch) {
  if (!ch) return "";
  if (ch === "’" || ch === "‘" || ch === "‛" || ch === "′" || ch === "`") return "'";
  if (ch === "“" || ch === "”" || ch === "‟" || ch === "″") return '"';
  if (ch === "–" || ch === "—" || ch === "−" || ch === "‒") return "-";
  if (ch === "\u00a0" || ch === "\u202f" || ch === "\u2009" || ch === "\u3000") return " ";
  if (ch === "…") return ".";
  return ch;
}

function areTypingCharsEqual(a, b) {
  if (a === b) return true;
  return normalizeTypingChar(a) === normalizeTypingChar(b);
}

// Sentence Translation Cache & Management
const sentenceTranslations = {};

function isValidChineseTranslation(sourceText, trans) {
  if (!trans || typeof trans !== "string") return false;
  const t = trans.trim();
  const s = (sourceText || "").trim();
  if (!t || !s) return false;
  if (t.toLowerCase() === s.toLowerCase()) return false;
  const hasLetters = /[a-zA-Z]{2,}/.test(s);
  const hasChinese = /[\u4e00-\u9fa5]/.test(t);
  if (hasLetters && !hasChinese) return false;
  return true;
}

async function fetchSentenceTranslation(text) {
  if (!text) return "";
  const cleaned = text.replace(/[\u00a0\u202f\u2009\u3000]/g, " ").replace(/\s+/g, " ").trim();
  if (!cleaned) return "";
  if (isValidChineseTranslation(cleaned, sentenceTranslations[cleaned])) {
    return sentenceTranslations[cleaned];
  } else {
    delete sentenceTranslations[cleaned];
  }

  try {
    const res = await fetch("/api/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: cleaned })
    });
    if (res.ok) {
      const data = await res.json();
      if (isValidChineseTranslation(cleaned, data.translation)) {
        sentenceTranslations[cleaned] = data.translation;
        updateTranslationUI(cleaned, data.translation);
        if (typeof syncChannel !== "undefined" && syncChannel) {
          syncChannel.postMessage({
            type: "sentence_translation",
            data: { text: cleaned, translation: data.translation }
          });
        }
        return data.translation;
      }
    }
  } catch (err) {
    console.warn("Translation request failed:", err);
  }

  const transDisplay = document.getElementById("typing-translation-display");
  const transText = document.getElementById("typing-translation-text");
  if (transDisplay && transText && currentTypingTarget === cleaned) {
    transDisplay.classList.remove("loading");
    transText.textContent = "暂无中文释义";
  }
  return "";
}

function updateTranslationUI(targetText, translation) {
  const transDisplay = document.getElementById("typing-translation-display");
  const transText = document.getElementById("typing-translation-text");
  if (!transDisplay || !transText) return;
  if (currentTypingTarget !== targetText) return;

  if (isValidChineseTranslation(targetText, translation)) {
    transText.textContent = translation;
    transDisplay.classList.remove("loading");
  }
}

function prefetchPageTranslations(sentences) {
  if (!sentences || !Array.isArray(sentences) || sentences.length === 0) return;
  const missing = [];
  for (const s of sentences) {
    const t = (s.text || "").replace(/[\u00a0\u202f\u2009\u3000]/g, " ").replace(/\s+/g, " ").trim();
    if (isValidChineseTranslation(t, s.translation)) {
      sentenceTranslations[t] = s.translation;
    } else if (t && !isValidChineseTranslation(t, sentenceTranslations[t])) {
      missing.push(t);
    }
  }

  if (missing.length === 0) return;

  fetch("/api/translate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ texts: missing })
  })
    .then(r => r.json())
    .then(data => {
      if (data && data.translations) {
        for (const [k, v] of Object.entries(data.translations)) {
          if (isValidChineseTranslation(k, v)) {
            sentenceTranslations[k] = v;
          }
        }
        if (currentTypingTarget && isValidChineseTranslation(currentTypingTarget, sentenceTranslations[currentTypingTarget])) {
          updateTranslationUI(currentTypingTarget, sentenceTranslations[currentTypingTarget]);
        }
      }
    })
    .catch(err => console.warn("Prefetch translations failed:", err));
}

let isGeneratingSmartNotes = false;
let currentSmartNotesSentence = "";

async function fetchSentenceSmartNotes(sentenceText, translation = "", forceRefresh = false) {
  if (!sentenceText || !sentenceText.trim()) return;
  const cleaned = sentenceText.replace(/[\u00a0\u202f\u2009\u3000]/g, " ").replace(/\s+/g, " ").trim();
  if (!cleaned) return;

  // 1. If already cached and not forcing refresh, display immediately
  if (!forceRefresh && sentenceNotesCache[cleaned]) {
    currentNotesMarkdown = sentenceNotesCache[cleaned];
    renderWhiteboard(currentNotesMarkdown);
    return;
  }

  // If already in flight for this sentence and not forcing refresh, return
  if (isGeneratingSmartNotes && currentSmartNotesSentence === cleaned && !forceRefresh) {
    return;
  }

  isGeneratingSmartNotes = true;
  currentSmartNotesSentence = cleaned;

  // Display loading state on whiteboard
  if (whiteboardContent) {
    whiteboardContent.innerHTML = `
      <div class="loading-notes">
        <div class="loading-icon">⚡</div>
        <div style="font-size: 13.5px; font-weight: 600; color: var(--accent, #38bdf8); margin-bottom: 4px;">正在通过云端 AI 提炼句法骨架与生词精讲…</div>
        <div style="font-size: 11.5px; color: var(--muted); max-width: 280px; line-height: 1.5;">深度拆解长难句语法树，精准提炼重点生词短语及原生例句</div>
      </div>
    `;
  }

  if (generateNotesBtn) {
    generateNotesBtn.disabled = true;
    generateNotesBtn.innerHTML = `⏳ 生成中…`;
  }

  try {
    const resp = await fetch("/api/sentence/smart-notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sentence: cleaned,
        translation: translation || sentenceTranslations[cleaned] || "",
        force_refresh: !!forceRefresh
      })
    });

    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }

    const data = await resp.json();
    if (data.success && data.notes_markdown) {
      sentenceNotesCache[cleaned] = data.notes_markdown;
      currentNotesMarkdown = data.notes_markdown;
      renderWhiteboard(currentNotesMarkdown);
      if (forceRefresh) {
        showToast("✨ 智能板书已更新！");
      }
    } else {
      throw new Error(data.error || "生成失败");
    }
  } catch (err) {
    console.error("Failed to generate smart notes:", err);
    if (whiteboardContent && currentSmartNotesSentence === cleaned) {
      whiteboardContent.innerHTML = `
        <div class="empty-state" style="padding: 30px 16px; text-align: center;">
          <div class="empty-icon" style="color: #ef4444;">⚠️</div>
          <h3 style="font-size: 13.5px; margin: 8px 0;">智能板书生成异常</h3>
          <p style="font-size: 12px; color: var(--muted); margin-bottom: 12px;">${escapeHtml(err.message || "网络超时或服务异常")}</p>
          <button class="btn btn-xs btn-accent" id="retry-smart-notes-btn">重试生成</button>
        </div>
      `;
      const retryBtn = document.getElementById("retry-smart-notes-btn");
      if (retryBtn) {
        retryBtn.addEventListener("click", () => fetchSentenceSmartNotes(cleaned, translation, true));
      }
    }
  } finally {
    isGeneratingSmartNotes = false;
    if (generateNotesBtn) {
      generateNotesBtn.disabled = false;
      generateNotesBtn.innerHTML = `⚡ 智能板书`;
    }
  }
}

function setupShadowTyping(targetText, translation = null) {
  const display = document.getElementById("typing-target-display");
  const pill = document.getElementById("typing-status-pill");
  const progressText = document.getElementById("typing-progress-text");
  const card = document.getElementById("shadow-typing-card");
  const comboBadge = document.getElementById("typing-combo-badge");
  const wpmBadge = document.getElementById("typing-wpm-badge");
  const soundBtn = document.getElementById("typing-sound-btn");
  const transDisplay = document.getElementById("typing-translation-display");
  const transText = document.getElementById("typing-translation-text");
  const transToggleBtn = document.getElementById("typing-trans-toggle-btn");

  if (!display) return;

  if (typingScrollRafId) {
    cancelAnimationFrame(typingScrollRafId);
    typingScrollRafId = null;
  }
  display.scrollTop = 0;
  typingScrollTargetTop = null;

  currentTypingTarget = (targetText || "")
    .replace(/[\u00a0\u202f\u2009\u3000]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  isTypingCompleted = false;
  currentTypingRound = 1;
  currentTypingCombo = 0;
  maxTypingComboInSentence = 0;
  typingStartTime = null;
  lastTypedLength = 0;
  typedBuffer = "";
  completedWordIndicesInCurrentRound.clear();
  if (window.OdometerEngine) {
    window.OdometerEngine.updateDisplays(false);
  }

  currentWordAudioSessionId++;
  if (wordAudioEl) { try { wordAudioEl.pause(); wordAudioEl.currentTime = 0; } catch (_) {} }
  if (wordZhAudioEl) { try { wordZhAudioEl.pause(); wordZhAudioEl.currentTime = 0; } catch (_) {} }

  currentSentenceWords = parseSentenceWords(currentTypingTarget);
  lastSpokenWordIndex = -1;
  if (isWordAudioEnabled && currentSentenceWords.length > 0) {
    prefetchSentenceWords(currentSentenceWords);
    if (Array.isArray(lectureSentences) && currentSentenceIndex < lectureSentences.length) {
      const nextSentObj = lectureSentences[currentSentenceIndex];
      if (nextSentObj && nextSentObj.text) {
        prefetchSentenceWords(parseSentenceWords(nextSentObj.text));
      }
    }
  }

  // Pre-fetch contextual Chinese word glosses for high-speed typing playback
  if (currentTypingTarget) {
    fetchSentenceWordGlosses(currentTypingTarget);
    if (Array.isArray(lectureSentences) && currentSentenceIndex < lectureSentences.length) {
      const nextSentObj = lectureSentences[currentSentenceIndex];
      if (nextSentObj && nextSentObj.text) {
        fetchSentenceWordGlosses(nextSentObj.text);
      }
    }
  }

  if (comboBadge) {
    comboBadge.textContent = "Combo x0 🔥";
    comboBadge.className = "typing-combo-badge";
  }
  if (wpmBadge) {
    wpmBadge.textContent = "0 WPM";
  }

  if (soundBtn && !soundBtn.dataset.bound) {
    soundBtn.dataset.bound = "true";
    soundBtn.addEventListener("click", () => SoundEngine.toggle());
  }

  if (transToggleBtn && !transToggleBtn.dataset.bound) {
    transToggleBtn.dataset.bound = "true";
    transToggleBtn.addEventListener("click", () => {
      if (transDisplay) {
        const isHidden = transDisplay.classList.toggle("hidden");
        transToggleBtn.classList.toggle("active", !isHidden);
        transToggleBtn.title = isHidden ? "显示参考译文" : "隐藏参考译文";
      }
    });
  }

  if (card) {
    card.classList.remove("completed");
  }

  if (!currentTypingTarget) {
    display.innerHTML = '<span class="char-pending">暂无可练习的句子</span>';
    if (transText) transText.textContent = "载入教材后显示中文释义";
    if (transDisplay) transDisplay.classList.remove("loading");
    display.classList.remove("typing-active");
    if (pill) { pill.className = "typing-status-pill"; pill.textContent = "待打字"; }
    if (progressText) progressText.textContent = "进度: 0 / 0 字符";
    display.scrollTop = 0;
    typingScrollTargetTop = null;
    return;
  }

  if (pill) {
    pill.className = "typing-status-pill";
    pill.textContent = currentTypingRepeatRounds > 1 ? `点击开始跟打 (共需 ${currentTypingRepeatRounds} 遍)` : "点击开始跟打";
  }
  if (progressText) {
    if (currentTypingRepeatRounds > 1) {
      progressText.textContent = `进度: 0 / ${currentTypingTarget.length} 字符 (第 1/${currentTypingRepeatRounds} 遍)`;
    } else {
      progressText.textContent = `进度: 0 / ${currentTypingTarget.length} 字符`;
    }
  }

  const dockedSentTag = document.getElementById("docked-tag-sentence");
  if (dockedSentTag) {
    dockedSentTag.textContent = `当前第 ${currentSentenceIndex || 1} 句`;
  }

  // Render character spans (English target only)
  display.innerHTML = currentTypingTarget
    .split("")
    .map((ch, idx) => `<span class="${idx === 0 ? 'char-current' : 'char-pending'}" data-idx="${idx}">${escapeHtml(ch)}</span>`)
    .join("");
  display.classList.remove("typing-active");
  display.scrollTop = 0;
  typingScrollTargetTop = null;

  // Update translation display
  if (isValidChineseTranslation(currentTypingTarget, translation)) {
    sentenceTranslations[currentTypingTarget] = translation;
  }
  if (transDisplay && transText) {
    if (isValidChineseTranslation(currentTypingTarget, sentenceTranslations[currentTypingTarget])) {
      transText.textContent = sentenceTranslations[currentTypingTarget];
      transDisplay.classList.remove("loading");
    } else {
      transText.textContent = "正在获取中文释义...";
      transDisplay.classList.add("loading");
      fetchSentenceTranslation(currentTypingTarget);
    }
  }
}

function requestScrollTypingTarget() {
  if (typingScrollRafId) cancelAnimationFrame(typingScrollRafId);
  typingScrollRafId = requestAnimationFrame(() => {
    typingScrollRafId = null;
    scrollTypingTargetToActiveChar();
  });
}

function scrollTypingTargetToActiveChar() {
  const display = document.getElementById("typing-target-display");
  if (!display) return;
  if (display.clientHeight <= 0 || display.scrollHeight <= display.clientHeight) return;

  const activeSpan = display.querySelector(".char-current") || display.querySelector("span[data-idx]:last-child");
  if (!activeSpan) return;

  const displayRect = display.getBoundingClientRect();
  const spanRect = activeSpan.getBoundingClientRect();

  const currentScrollTop = display.scrollTop;
  const visibleHeight = display.clientHeight;

  // Absolute coordinate inside scrollable content
  const spanContentTop = (spanRect.top - displayRect.top) + currentScrollTop;
  const spanContentBottom = (spanRect.bottom - displayRect.top) + currentScrollTop;

  const spanHeight = spanRect.height || 36;
  // Keep generous breathing room so user can easily see upcoming line(s)
  const bottomPadding = Math.min(visibleHeight * 0.45, Math.max(spanHeight * 1.25, 48));
  const topPadding = Math.min(visibleHeight * 0.25, Math.max(spanHeight * 0.5, 20));

  let targetScrollTop = currentScrollTop;

  if (spanContentBottom > currentScrollTop + visibleHeight - bottomPadding) {
    targetScrollTop = spanContentBottom - visibleHeight + bottomPadding;
  } else if (spanContentTop < currentScrollTop + topPadding) {
    targetScrollTop = spanContentTop - topPadding;
  }

  targetScrollTop = Math.max(0, Math.min(targetScrollTop, display.scrollHeight - visibleHeight));

  if (Math.abs(targetScrollTop - currentScrollTop) > 3) {
    if (typingScrollTargetTop === null || Math.abs(targetScrollTop - typingScrollTargetTop) > 3) {
      typingScrollTargetTop = targetScrollTop;
      display.scrollTo({
        top: targetScrollTop,
        behavior: "smooth"
      });
    }
  }
}

function handleTypingInput() {
  const typed = typedBuffer;
  const display = document.getElementById("typing-target-display");
  const pill = document.getElementById("typing-status-pill");
  const progressText = document.getElementById("typing-progress-text");
  const card = document.getElementById("shadow-typing-card");
  const comboBadge = document.getElementById("typing-combo-badge");
  const wpmBadge = document.getElementById("typing-wpm-badge");
  if (!display || !currentTypingTarget) return;

  if (!typingStartTime && typed.length > 0) {
    typingStartTime = Date.now();
    if (currentDocHasMedia && !hasCurrentSentenceVideoPlayed && isVideoAutoplayEnabled) {
      hasCurrentSentenceVideoPlayed = true;
      playSentenceAudioDirect(currentSentenceIndex);
    }
  }

  const spans = display.querySelectorAll("span[data-idx]");
  let correctCount = 0;
  let hasError = false;

  for (let i = 0; i < spans.length; i++) {
    const span = spans[i];
    const targetChar = currentTypingTarget[i];
    if (i < typed.length) {
      if (areTypingCharsEqual(typed[i], targetChar)) {
        span.className = "char-correct";
        correctCount++;
      } else {
        span.className = "char-wrong";
        hasError = true;
      }
    } else if (i === typed.length) {
      span.className = "char-current";
    } else {
      span.className = "char-pending";
    }
  }

  // Auto-scroll display container to keep active typing character/cursor in comfortable view
  requestScrollTypingTarget();

  // Hit audio and combo calculation
  if (typed.length > lastTypedLength) {
    const charIndex = typed.length - 1;
    checkAndPlayWordAtChar(charIndex);
    if (charIndex < currentTypingTarget.length && areTypingCharsEqual(typed[charIndex], currentTypingTarget[charIndex])) {
      currentTypingCombo++;
      if (currentTypingCombo > maxTypingComboInSentence) {
        maxTypingComboInSentence = currentTypingCombo;
      }
      SoundEngine.playKeyClick();
    } else {
      currentTypingCombo = 0;
      SoundEngine.playErrorThud();
    }
  }
  lastTypedLength = typed.length;

  // Odometer word tracking: detect each completed word and update odometer
  if (currentSentenceWords && currentSentenceWords.length > 0) {
    for (let i = 0; i < currentSentenceWords.length; i++) {
      if (!completedWordIndicesInCurrentRound.has(i)) {
        const word = currentSentenceWords[i];
        if (typed.length >= word.endIndex) {
          let allCorrect = true;
          for (let k = word.startIndex; k < word.endIndex; k++) {
            if (!areTypingCharsEqual(typed[k], currentTypingTarget[k])) {
              allCorrect = false;
              break;
            }
          }
          if (allCorrect) {
            completedWordIndicesInCurrentRound.add(i);
            if (window.OdometerEngine) {
              window.OdometerEngine.recordWord(word.clean);
            }
          }
        }
      }
    }
  }

  // Update Combo Badge
  if (comboBadge) {
    comboBadge.textContent = `Combo x${currentTypingCombo} 🔥`;
    if (currentTypingCombo >= 10) {
      comboBadge.className = "typing-combo-badge super";
    } else if (currentTypingCombo >= 3) {
      comboBadge.className = "typing-combo-badge active";
    } else {
      comboBadge.className = "typing-combo-badge";
    }
  }

  // Update WPM
  let currentWpm = 0;
  if (typingStartTime && typed.length > 0) {
    const elapsedSec = (Date.now() - typingStartTime) / 1000;
    if (elapsedSec > 0.6) {
      const words = typed.length / 5;
      currentWpm = Math.round((words / elapsedSec) * 60);
      if (wpmBadge) wpmBadge.textContent = `${currentWpm} WPM`;
    }
  }

  if (progressText) {
    if (currentTypingRepeatRounds > 1) {
      progressText.textContent = `进度: ${correctCount} / ${currentTypingTarget.length} 字符 (第 ${currentTypingRound}/${currentTypingRepeatRounds} 遍)`;
    } else {
      progressText.textContent = `进度: ${correctCount} / ${currentTypingTarget.length} 字符`;
    }
  }

  if (hasError) {
    if (pill) { pill.className = "typing-status-pill"; pill.textContent = "存在拼写错误"; }
  } else if (typed.length > 0 && typed.length < currentTypingTarget.length) {
    let wordHint = "";
    if (currentSentenceWords && lastSpokenWordIndex >= 0 && lastSpokenWordIndex < currentSentenceWords.length) {
      const activeWord = currentSentenceWords[lastSpokenWordIndex];
      const glosses = sentenceWordGlosses.get(currentTypingTarget);
      const zh = glosses ? findGlossForWord(glosses, activeWord.clean) : (singleWordGlossCache.get(activeWord.clean.toLowerCase()) || "");
      wordHint = zh ? ` [${activeWord.clean}: ${zh}]` : ` [${activeWord.clean}]`;
    }
    if (pill) {
      pill.className = "typing-status-pill active";
      pill.textContent = currentTypingRepeatRounds > 1
        ? `跟打中 (第 ${currentTypingRound}/${currentTypingRepeatRounds} 遍)${wordHint}…`
        : `跟打中${wordHint}…`;
    }
  }

  // Broadcast real-time typing sync to companion screen (portrait)
  try {
    syncChannel.postMessage({
      type: "typing_sync",
      data: {
        source: "landscape",
        sentence_index: currentSentenceIndex,
        target: currentTypingTarget,
        typed: typedBuffer,
        combo: currentTypingCombo,
        wpm: currentWpm,
        completed: false,
        round: currentTypingRound,
        repeatRounds: currentTypingRepeatRounds
      }
    });
  } catch (_) {}

  // Check completion
  const isMatch = (typed.length === currentTypingTarget.length && !hasError);
  if (isMatch && !isTypingCompleted) {
    if (currentTypingRound < currentTypingRepeatRounds) {
      // Intermediate round completed!
      SoundEngine.playSuccessChime();
      showToast(`👏 第 ${currentTypingRound}/${currentTypingRepeatRounds} 遍完成！请继续输入第 ${currentTypingRound + 1} 遍巩固记忆~`);
      if (pill) {
        pill.className = "typing-status-pill active";
        pill.textContent = `第 ${currentTypingRound} 遍完成，继续第 ${currentTypingRound + 1} 遍`;
      }
      currentTypingRound++;
      typedBuffer = "";
      lastTypedLength = 0;
      lastSpokenWordIndex = -1;
      completedWordIndicesInCurrentRound.clear();
      display.innerHTML = currentTypingTarget
        .split("")
        .map((ch, idx) => `<span class="${idx === 0 ? 'char-current' : 'char-pending'}" data-idx="${idx}">${escapeHtml(ch)}</span>`)
        .join("");
      display.scrollTop = 0;
      typingScrollTargetTop = null;
      if (progressText) {
        progressText.textContent = `进度: 0 / ${currentTypingTarget.length} 字符 (第 ${currentTypingRound}/${currentTypingRepeatRounds} 遍)`;
      }
      display.focus();
      return;
    }

    isTypingCompleted = true;
    if (pill) {
      pill.className = "typing-status-pill success";
      pill.textContent = currentTypingRepeatRounds > 1 ? `✓ 连续 ${currentTypingRepeatRounds} 遍跟打通关！` : "✓ 完美跟打达成！";
    }
    if (card) card.classList.add("completed");
    SoundEngine.playSuccessChime();
    if (maxTypingComboInSentence >= 8 || currentTypingRepeatRounds > 1) {
      ConfettiEngine.fireConfetti();
    }
    showToast(currentTypingRepeatRounds > 1 ? `🎉 太棒了！连续 ${currentTypingRepeatRounds} 遍跟打 100% 正确！` : "🎉 太棒了！本句跟打拼写 100% 正确！");

    // Flush any pending words in OdometerEngine immediately
    if (window.OdometerEngine) {
      window.OdometerEngine.flush();
    }

    // Record action in Gamification Engine
    const wordCount = currentTypingTarget.split(/\s+/).filter(Boolean).length;
    if (window.recordGameAction) {
      window.recordGameAction("typing_completed", {
        words_count: wordCount * currentTypingRepeatRounds,
        combo: maxTypingComboInSentence,
        increment_words: false
      }, card);
    }

    syncChannel.postMessage({
      type: "typing_completed",
      data: { sentence_index: currentSentenceIndex, rounds: currentTypingRepeatRounds }
    });

    if (isContinuousLecture) {
      setStatus("拼写完成！准备进入下一句…", "speaking");
      setTimeout(() => {
        if (!isContinuousLecture) return;
        if (currentSentenceIndex < lectureSentences.length) {
          currentSentenceIndex++;
          updateSentencePreview();
          const nextSent = lectureSentences[currentSentenceIndex - 1];
          const activeAction = getEffectiveActionMode();
          triggerSentenceAction(currentSentenceIndex, nextSent ? nextSent.text : "", activeAction);
        } else {
          stopContinuousLecture();
          showToast("🎉 本页所有句子已完成练习！");
        }
      }, 1400);
    }
  }
}

function checkContinuousAdvance() {
  if (!isCurrentTurnComplete || isPlayingAudio || audioQueue.length > 0) return;

  const autoTypingToggle = document.getElementById("auto-typing-toggle");
  const autoTyping = autoTypingToggle ? autoTypingToggle.checked : true;

  if (!isContinuousLecture && autoTyping) {
    const typingDisplay = document.getElementById("typing-target-display");
    if (typingDisplay && currentTypingTarget && !isTypingCompleted) {
      typingDisplay.focus();
      setStatus("请直接键入练习本句（按 Enter 跳过）", "listening");
      return;
    }
  }

  if (isContinuousLecture) {
    if (currentSentenceIndex > 0 && currentSentenceIndex < lectureSentences.length) {
      clearTimeout(continuousTimer);
      const nextIndex = currentSentenceIndex + 1;
      const nextAction = getEffectiveActionMode();
      setStatus(nextAction === "read_only" ? `准备朗读第 ${nextIndex} 句…` : `准备精讲第 ${nextIndex} 句…`, "speaking");
      continuousTimer = setTimeout(() => {
        if (!isContinuousLecture) return;
        currentSentenceIndex = nextIndex;
        updateSentencePreview();
        const nextSent = lectureSentences[currentSentenceIndex - 1];
        triggerSentenceAction(currentSentenceIndex, nextSent ? nextSent.text : "", nextAction);
        syncChannel.postMessage({
          type: "continuous_progress",
          data: { running: true, sentence_index: currentSentenceIndex, total: lectureSentences.length }
        });
      }, 1200);
    } else if (currentSentenceIndex >= lectureSentences.length) {
      stopContinuousLecture();
      showToast("🎉 本页所有句子已连续播放完成！");
      syncChannel.postMessage({
        type: "continuous_progress",
        data: { running: false, finished: true }
      });
    }
  }
}

function startContinuousLecture(action = null, startIndex = null) {
  if (!lectureSentences || lectureSentences.length === 0) {
    showToast("当前页未识别到句子，无法连播");
    return;
  }
  isContinuousLecture = true;
  continuousAction = action || (getEffectiveActionMode() === "read_only" ? "read_only" : "explain");
  if (startIndex && startIndex >= 1 && startIndex <= lectureSentences.length) {
    currentSentenceIndex = startIndex;
  } else if (currentSentenceIndex < 1 || currentSentenceIndex > lectureSentences.length) {
    currentSentenceIndex = 1;
  }
  updateContinuousUI(true);
  updateSentencePreview();
  const s = lectureSentences[currentSentenceIndex - 1];
  triggerSentenceAction(currentSentenceIndex, s ? s.text : "", continuousAction);
  syncChannel.postMessage({
    type: "continuous_progress",
    data: { running: true, sentence_index: currentSentenceIndex, total: lectureSentences.length }
  });
}

function stopContinuousLecture() {
  isContinuousLecture = false;
  continuousAction = null;
  clearTimeout(continuousTimer);
  stopAudioPlayback();
  updateContinuousUI(false);
  syncChannel.postMessage({
    type: "continuous_progress",
    data: { running: false }
  });
}

function toggleContinuousLecture(action = null, startIndex = null) {
  if (isContinuousLecture) {
    stopContinuousLecture();
  } else {
    startContinuousLecture(action, startIndex);
  }
}

function updateContinuousUI(running) {
  updateActionRunButton();
  const btn = document.getElementById("continuous-lecture-btn");
  const icon = document.getElementById("continuous-icon");
  const label = document.getElementById("continuous-label");
  if (!btn) return;
  if (running) {
    btn.classList.add("btn-primary");
    btn.classList.remove("btn-outline");
    btn.style.color = "#fff";
    if (icon) icon.textContent = "⏸️";
    if (label) label.textContent = "暂停连续精讲";
  } else {
    btn.classList.remove("btn-primary");
    btn.classList.add("btn-outline");
    btn.style.color = "var(--blue)";
    if (icon) icon.textContent = "▶️";
    if (label) label.textContent = "连续自动精讲整页";
  }
}

function b64ToBlob(b64Data, contentType) {
  const byteCharacters = atob(b64Data);
  const byteArrays = [];
  for (let offset = 0; offset < byteCharacters.length; offset += 512) {
    const slice = byteCharacters.slice(offset, offset + 512);
    const byteNumbers = new Array(slice.length);
    for (let i = 0; i < slice.length; i++) {
      byteNumbers[i] = slice.charCodeAt(i);
    }
    byteArrays.push(new Uint8Array(byteNumbers));
  }
  return new Blob(byteArrays, { type: contentType });
}

// Resample arbitrary rate to 16000Hz
function downsampleTo16000(inputData, inputSampleRate) {
  if (inputSampleRate === 16000) {
    return inputData;
  }
  const ratio = inputSampleRate / 16000;
  const newLength = Math.round(inputData.length / ratio);
  const result = new Float32Array(newLength);
  for (let i = 0; i < newLength; i++) {
    const origIndex = i * ratio;
    const indexFloor = Math.floor(origIndex);
    const indexCeil = Math.min(indexFloor + 1, inputData.length - 1);
    const fraction = origIndex - indexFloor;
    result[i] = inputData[indexFloor] * (1 - fraction) + inputData[indexCeil] * fraction;
  }
  return result;
}

// Float32 [-1.0, 1.0] to Int16 PCM
function floatTo16BitPCM(float32Array) {
  const pcm16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return pcm16;
}

// Microphone capture & VAD streaming
async function startRecording() {
  if (isRecording) return;
  if (isPlayingAudio) {
    interruptCoach();
  }

  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    });

    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioContext.createMediaStreamSource(micStream);
    processor = audioContext.createScriptProcessor(4096, 1, 1);

    // Mute destination to prevent audio loopback / microphone feedback
    gainNode = audioContext.createGain();
    gainNode.gain.value = 0.0;

    source.connect(processor);
    processor.connect(gainNode);
    gainNode.connect(audioContext.destination);

    processor.onaudioprocess = (e) => {
      if (!isRecording || !ws || ws.readyState !== WebSocket.OPEN) return;

      const inputData = e.inputBuffer.getChannelData(0);
      drawWaveform(inputData);

      // Acoustic Echo Guard (防回音门禁):
      // When AI voice is actively playing or in cooldown right after playback,
      // gate the mic packets so the speaker's own output does not re-trigger VAD!
      const echoGuardToggle = document.getElementById("echo-guard-toggle");
      const isEchoGuard = !echoGuardToggle || echoGuardToggle.checked;
      if (isEchoGuard && (isPlayingAudio || Date.now() < audioPlaybackEndTime + 350)) {
        return;
      }

      // Resample to 16000Hz for Silero VAD and SenseVoice
      const resampled = downsampleTo16000(inputData, audioContext.sampleRate);
      const pcm16 = floatTo16BitPCM(resampled);

      ws.send(pcm16.buffer);
    };


    isRecording = true;
    micBtn.className = "mic-button recording";
    micBtn.querySelector(".mic-label").textContent = "正在聆听 · 点击结束";
    const waveContainer = document.getElementById("waveform-container");
    if (waveContainer) waveContainer.classList.add("active");
    setStatus("Listening... Speak now", "listening");
  } catch (err) {
    alert("Microphone access denied: " + err.message);
  }
}

function stopRecording() {
  if (!isRecording) return;
  isRecording = false;

  // Send a short burst of silence (0.3s) and audio_end signal to flush VAD
  if (ws && ws.readyState === WebSocket.OPEN) {
    const silence = new Int16Array(16000 * 0.3);
    ws.send(silence.buffer);
    ws.send(JSON.stringify({ type: "audio_end" }));
  }

  if (processor) {
    processor.disconnect();
    processor = null;
  }
  if (gainNode) {
    gainNode.disconnect();
    gainNode = null;
  }
  if (micStream) {
    micStream.getTracks().forEach(t => t.stop());
    micStream = null;
  }
  if (audioContext) {
    audioContext.close();
    audioContext = null;
  }

  micBtn.className = "mic-button idle";
  micBtn.querySelector(".mic-label").textContent = "点击或按住说话";
  const waveContainer = document.getElementById("waveform-container");
  if (waveContainer) waveContainer.classList.remove("active");
  setStatus("Processing speech...", "thinking");
  isCurrentTurnComplete = false;
  clearWaveform();
}

function interruptCoach() {
  stopAudioPlayback();
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "interrupt" }));
  }
}

// Waveform visualizer
function drawWaveform(samples) {
  canvasCtx.clearRect(0, 0, canvas.width, canvas.height);
  canvasCtx.lineWidth = 2;
  canvasCtx.strokeStyle = "#38bdf8";
  canvasCtx.beginPath();

  const sliceWidth = canvas.width / samples.length;
  let x = 0;

  for (let i = 0; i < samples.length; i += 8) {
    const v = samples[i] * 35;
    const y = (canvas.height / 2) + v;
    if (i === 0) canvasCtx.moveTo(x, y);
    else canvasCtx.lineTo(x, y);
    x += sliceWidth * 8;
  }
  canvasCtx.stroke();
}

function clearWaveform() {
  canvasCtx.clearRect(0, 0, canvas.width, canvas.height);
}

// Mouse / Touch Event Handlers supporting both Click and Hold
micBtn.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  recordPressStartTime = Date.now();
  if (!isRecording) {
    startRecording();
  }
});

micBtn.addEventListener("mouseup", () => {
  const pressDuration = Date.now() - recordPressStartTime;
  // If user held for > 400ms, release means finish speaking
  if (pressDuration > 400 && isRecording) {
    stopRecording();
  }
  // If short click (< 400ms), leave recording active for toggle mode
});

micBtn.addEventListener("click", () => {
  // If clicked while recording was already active from a previous click (> 300ms ago), stop
  if (isRecording && (Date.now() - recordPressStartTime > 300)) {
    stopRecording();
  }
});

micBtn.addEventListener("touchstart", (e) => {
  e.preventDefault();
  recordPressStartTime = Date.now();
  if (!isRecording) {
    startRecording();
  }
});

micBtn.addEventListener("touchend", (e) => {
  e.preventDefault();
  const pressDuration = Date.now() - recordPressStartTime;
  if (pressDuration > 400 && isRecording) {
    stopRecording();
  }
});

interruptBtn.addEventListener("click", interruptCoach);

resetBtn.addEventListener("click", () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "reset_session" }));
  }
});

voiceSelect.addEventListener("change", (e) => {
  localStorage.setItem("ai_coach_voice", e.target.value);
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      type: "update_settings",
      voice: e.target.value,
      speed: parseFloat(speedSlider.value)
    }));
  }
});

speedSlider.addEventListener("input", (e) => {
  speedVal.textContent = `${e.target.value}x`;
  localStorage.setItem("ai_coach_speed", e.target.value);
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      type: "update_settings",
      voice: voiceSelect.value,
      speed: parseFloat(e.target.value)
    }));
  }
});

sendTextBtn.addEventListener("click", () => {
  const text = textInput.value.trim();
  if (text && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "text_input", text }));
    textInput.value = "";
    if (window.recordGameAction) window.recordGameAction("dialogue_sent");
  }
});

function switchBetweenOralAndTyping(e) {
  const textInputEl = document.getElementById("text-input");
  const typingDisplayEl = document.getElementById("typing-target-display");

  // Check if an interactive modal or popup dialog is currently open and focused
  const activeModal = document.querySelector(".modal-backdrop:not([style*='display: none']), .modal:not(.hidden)");
  if (activeModal && activeModal.contains(document.activeElement)) {
    // Allow normal Tab cycling within open modals for accessibility
    return false;
  }

  // Prevent default Tab navigation and stop propagation so no other listener conflicts
  if (e) {
    e.preventDefault();
    if (typeof e.stopPropagation === "function") {
      e.stopPropagation();
    }
    if (typeof e.stopImmediatePropagation === "function") {
      e.stopImmediatePropagation();
    }
  }

  const activeEl = document.activeElement;

  // Case 1: Focus is currently on oral dialogue input or interaction bar -> Switch to shadow typing
  if (activeEl === textInputEl || (activeEl && activeEl.closest && activeEl.closest(".interaction-bar"))) {
    if (typingDisplayEl) {
      typingDisplayEl.focus();
      try {
        typingDisplayEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
      } catch (_) {}
    }
    return true;
  }

  // Case 2: Focus is currently on shadow typing display or typing card -> Switch to oral dialogue input
  if (activeEl === typingDisplayEl || (activeEl && activeEl.closest && activeEl.closest(".shadow-typing-card"))) {
    if (textInputEl) {
      textInputEl.focus();
      try {
        textInputEl.select();
        textInputEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
      } catch (_) {}
    }
    return true;
  }

  // Case 3: Focus is anywhere else on the page
  // Intelligently toggle: prefer shadow typing if a sentence is loaded and incomplete; otherwise oral dialogue
  if (typingDisplayEl && currentTypingTarget && !isTypingCompleted) {
    typingDisplayEl.focus();
    try {
      typingDisplayEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (_) {}
  } else if (textInputEl) {
    textInputEl.focus();
    try {
      textInputEl.select();
      textInputEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (_) {}
  } else if (typingDisplayEl) {
    typingDisplayEl.focus();
    try {
      typingDisplayEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch (_) {}
  }

  return true;
}

textInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    sendTextBtn.click();
  } else if (e.key === "Tab") {
    switchBetweenOralAndTyping(e);
  }
});

function isTypingContext(el) {
  if (!el) return false;
  const tag = el.tagName ? el.tagName.toLowerCase() : "";
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  if (el.isContentEditable) return true;
  if (el.closest && (el.closest("input, textarea, select, [contenteditable='true']") || el.closest(".shadow-typing-card") || el.closest(".modal") || el.closest(".current-focus-card"))) return true;
  return false;
}

// Spacebar shortcut: in pure read mode plays current sentence; otherwise push-to-talk
window.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !isTypingContext(document.activeElement) && !e.repeat) {
    e.preventDefault();
    if (getEffectiveActionMode() === "read_only") {
      executeCurrentActionMode();
      return;
    }
    if (!isRecording) {
      startRecording();
    }
  }
});

window.addEventListener("keyup", (e) => {
  if (e.code === "Space" && !isTypingContext(document.activeElement)) {
    e.preventDefault();
    if (getEffectiveActionMode() === "read_only") {
      return;
    }
    if (isRecording) {
      stopRecording();
    }
  }
});

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

async function loadLectureDocuments() {
  try {
    const progress = await fetchLearningProgress();

    // 1. Restore teaching style
    const savedStyle = localStorage.getItem("ai_coach_teaching_style") || (progress && progress.teaching_style) || "spoken";
    currentTeachingStyle = savedStyle;
    const styleSelect = document.getElementById("teaching-style-select");
    if (styleSelect) styleSelect.value = currentTeachingStyle;

    // 2. Restore action mode
    const savedMode = localStorage.getItem("ai_coach_action_mode") || "explain";
    currentActionMode = savedMode;
    const modeSelect = document.getElementById("sentence-action-mode-select");
    if (modeSelect) modeSelect.value = currentActionMode;
    updateActionRunButton();
    document.querySelectorAll(".quick-mode-btn").forEach(b => {
      b.classList.toggle("active", b.dataset.mode === currentActionMode);
    });

    // 3. Load available textbooks
    const response = await fetch("/api/documents");
    const data = await response.json();
    lectureDocSelect.innerHTML = "";
    for (const book of (data.documents || [])) {
      const option = document.createElement("option");
      option.value = book.id;
      option.textContent = `${book.title} (${book.pages || "?"} pages)`;
      option.disabled = !book.available;
      lectureDocSelect.appendChild(option);
    }
    const docs = data.documents || [];
    const savedDocId = localStorage.getItem("ai_coach_last_doc_id") || (progress && progress.last_document_id) || "vocabulary";
    let selectedDoc = docs.find(b => b.available && b.id === savedDocId);
    if (!selectedDoc && progress && progress.last_document_id) {
      selectedDoc = docs.find(b => b.available && b.id === progress.last_document_id);
    }
    if (!selectedDoc) {
      selectedDoc = docs.find(b => b.available);
    }

    if (selectedDoc) {
      lectureDocumentId = selectedDoc.id;
      lectureDocSelect.value = selectedDoc.id;
      lectureTotalPages = selectedDoc.pages || 0;
      lecturePageInput.max = lectureTotalPages || "";
      await loadLectureUnits(selectedDoc.id);

      // 4. Restore exact page and sentence for this document
      const localSavedPage = parseInt(localStorage.getItem(`ai_coach_page_${selectedDoc.id}`), 10);
      const localSavedSent = parseInt(localStorage.getItem(`ai_coach_sent_${selectedDoc.id}`), 10);

      let targetPage = null;
      let targetSentence = null;
      if (Number.isInteger(localSavedPage) && localSavedPage >= 1) {
        targetPage = localSavedPage;
      }
      if (Number.isInteger(localSavedSent) && localSavedSent >= 1) {
        targetSentence = localSavedSent;
      }

      if (!targetPage && progress) {
        if (progress.books && progress.books[selectedDoc.id]) {
          targetPage = progress.books[selectedDoc.id].last_page;
          targetSentence = targetSentence || progress.books[selectedDoc.id].last_sentence_index;
        } else if (progress.last_document_id === selectedDoc.id) {
          targetPage = progress.last_page;
          targetSentence = targetSentence || progress.last_sentence_index;
        }
      }
      if (!targetPage) {
        targetPage = selectedDoc.id === "west_civ" ? 38 : ((lectureUnits && lectureUnits.length > 0) ? lectureUnits[0].page : 1);
      }
      if (!targetSentence) {
        targetSentence = 1;
      }

      lecturePage = targetPage;
      lecturePageInput.value = targetPage;
      const currentUnit = [...lectureUnits].reverse().find(unit => unit.page <= targetPage);
      lectureUnitSelect.value = currentUnit ? String(currentUnit.page) : "";
      await loadLecturePage(targetPage, targetSentence);
    }
  } catch (err) {
    lectureStatus.textContent = "教材载入失败";
    console.warn("Could not fetch textbooks:", err);
  }
}

async function loadLectureUnits(documentId) {
  lectureUnits = [];
  lectureUnitSelect.innerHTML = '<option value="">选择单元</option>';
  try {
    const response = await fetch(`/api/documents/${encodeURIComponent(documentId)}/units`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    lectureUnits = Array.isArray(data.units) ? data.units : [];
    for (const unit of lectureUnits) {
      const option = document.createElement("option");
      option.value = unit.page;
      const label = unit.title && (unit.title.startsWith("Ch.") || unit.title.startsWith("Chapter") || unit.title.startsWith("Unit"))
        ? unit.title
        : `Unit ${unit.unit}${unit.title ? ` · ${unit.title}` : ""}`;
      option.textContent = label;
      lectureUnitSelect.appendChild(option);
    }
  } catch (err) {
    console.warn("Could not fetch textbook units:", err);
  } finally {
    updateHeaderProgress();
  }
}

async function loadLecturePage(requestedPage, targetSentenceIndex = null) {
  const documentId = lectureDocSelect.value || lectureDocumentId;
  if (!documentId) return;
  if (lectureDocumentId && documentId !== lectureDocumentId) {
    lectureTotalPages = 0;
  }
  const maxPage = lectureTotalPages > 0 ? lectureTotalPages : 99999;
  const page = Math.max(1, Math.min(Number(requestedPage) || 1, maxPage));
  const requestId = ++lecturePageRequestId;
  if (targetSentenceIndex !== null) {
    pendingTargetSentenceIndex = Number(targetSentenceIndex);
  }
  lectureStatus.textContent = "正在载入教材页…";
  try {
    const safeId = encodeURIComponent(documentId);
    const [pageRes, sentRes] = await Promise.all([
      fetch(`/api/documents/${safeId}/pages/${page}`),
      fetch(`/api/documents/${safeId}/pages/${page}/sentences`)
    ]);
    if (!pageRes.ok) throw new Error(`HTTP ${pageRes.status}`);
    const data = await pageRes.json();
    if (requestId !== lecturePageRequestId) return;
    lectureDocumentId = data.id;
    lecturePage = data.page;
    lectureTotalPages = data.pages;
    lecturePageInput.value = lecturePage;
    lecturePageInput.max = lectureTotalPages;
    if (studioPageNum) studioPageNum.textContent = `P. ${lecturePage}`;
    const currentUnit = [...lectureUnits].reverse().find(unit => unit.page <= lecturePage);
    lectureUnitSelect.value = currentUnit ? String(currentUnit.page) : "";
    lectureStatus.textContent = `已载入 · 第 ${data.page}/${data.pages} 页`;
    updateHeaderProgress();
    lecturePageMeta.textContent = data.has_text
      ? "教材页已就绪。点击“执行双语精讲”，或直接通过文字、语音提问。"
      : "此页没有可提取文字；可查看预览，但讲解可能不完整。";
    lecturePageImage.src = `/api/documents/${safeId}/pages/${page}/image?request=${requestId}`;
    lecturePageImage.hidden = false;
    const pageWrapper = document.getElementById("pdf-page-wrapper");
    if (pageWrapper) pageWrapper.style.display = "inline-block";

    // Load sentences directly from HTTP endpoint without WebSocket race
    if (sentRes.ok) {
      const sentData = await sentRes.json();
      currentDocHasMedia = !!sentData.has_media;
      currentDocMediaType = sentData.media_type || "video/mp4";
      currentDocMediaUrl = sentData.media_url || (currentDocHasMedia ? `/api/documents/${safeId}/media` : null);

      const videoContainer = document.getElementById("video-player-container");
      const origMediaBtn = document.getElementById("typing-original-media-btn");
      const videoEl = document.getElementById("document-video-element");

      if (currentDocHasMedia && currentDocMediaUrl) {
        if (videoEl && (!videoEl.src || !videoEl.src.includes(`/api/documents/${safeId}/media`))) {
          videoEl.src = currentDocMediaUrl;
          videoEl.load();
        }
      } else {
        if (videoEl) {
          if (!videoEl.paused) {
            try { videoEl.pause(); } catch (_) {}
          }
          videoEl.removeAttribute("src");
          videoEl.load();
        }
      }
      updateVideoSyncStrip();

      if (Array.isArray(sentData.sentences)) {
        lectureSentences = sentData.sentences;
        const targetIdx = (pendingTargetSentenceIndex !== null && pendingTargetSentenceIndex >= 1)
          ? pendingTargetSentenceIndex
          : ((targetSentenceIndex !== null && targetSentenceIndex !== undefined && targetSentenceIndex >= 1) ? targetSentenceIndex : 1);
        currentSentenceIndex = Math.max(1, Math.min(targetIdx, lectureSentences.length || 1));
        pendingTargetSentenceIndex = null;
        renderPdfSentenceHighlights(lectureSentences);
        updateSentencePreview();
        prefetchPageTranslations(lectureSentences);
      }
    }

    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: "set_lecture_context",
        document_id: data.id,
        page: data.page,
        sentence_index: currentSentenceIndex
      }));
    } else {
      pendingWsLectureContext = {
        document_id: data.id,
        page: data.page,
        sentence_index: currentSentenceIndex
      };
    }

    // Broadcast page update and video mode to portrait screen
    syncChannel.postMessage({
      type: "video_mode",
      data: {
        enabled: currentDocHasMedia,
        has_media: currentDocHasMedia,
        media_url: currentDocMediaUrl,
        document_id: data.id
      }
    });
    syncChannel.postMessage({
      type: "page_updated",
      data: { document_id: data.id, page: data.page, sentence_index: currentSentenceIndex }
    });

    // Save learning progress
    saveLearningProgress({
      document_id: data.id,
      page: data.page,
      sentence_index: currentSentenceIndex,
      teaching_style: currentTeachingStyle
    });
  } catch (err) {
    lectureStatus.textContent = "此页载入失败";
    lecturePageMeta.textContent = String(err.message || err);
  }
}

// Direct Sentence Audio Player & Single-Sentence Loop Engine
let currentDirectAudio = null;
let currentDirectBtn = null;
let isLoopPlayback = localStorage.getItem("ai_coach_loop_playback") === "1";
let loopPlaybackTimer = null;
let userStartedLoop = false;

function replayCurrentSentenceAudio() {
  const replayBtn = document.getElementById("typing-original-media-btn") || document.getElementById("typing-replay-btn");
  playSentenceAudioDirect(currentSentenceIndex, replayBtn);
  showToast(currentDocHasMedia ? "🎬 重放原声视频片段 (Alt+V / Alt+R)" : "🔊 重新播放原句读音 (Alt+R)");
}

function toggleLoopPlayback(explicitState = null, broadcast = true) {
  if (explicitState !== null) {
    if (isLoopPlayback === !!explicitState) return;
    isLoopPlayback = !!explicitState;
  } else {
    isLoopPlayback = !isLoopPlayback;
  }
  localStorage.setItem("ai_coach_loop_playback", isLoopPlayback ? "1" : "0");
  const loopBtn = document.getElementById("typing-loop-btn");
  if (loopBtn) {
    loopBtn.classList.toggle("active", isLoopPlayback);
    loopBtn.title = isLoopPlayback 
      ? "单句循环播放 (开启中·读完自动复读，点击关闭，快捷键: Alt+L)" 
      : "单句循环播放 (已关闭·点击开启，快捷键: Alt+L)";
  }
  if (broadcast) {
    try {
      syncChannel.postMessage({
        type: "loop_mode",
        data: { loop: isLoopPlayback }
      });
    } catch (_) {}
  }
  if (isLoopPlayback) {
    userStartedLoop = true;
    showToast("🔂 已开启单句循环播放 (快捷键: Alt+L)");
    if (!currentDirectAudio || currentDirectAudio.paused) {
      playSentenceAudioDirect(currentSentenceIndex, loopBtn);
    }
  } else {
    userStartedLoop = false;
    if (loopPlaybackTimer) {
      clearTimeout(loopPlaybackTimer);
      loopPlaybackTimer = null;
    }
    showToast("🔁 已关闭单句循环播放");
  }
}

function playSentenceAudioDirect(index, btnEl) {
  if (loopPlaybackTimer) {
    clearTimeout(loopPlaybackTimer);
    loopPlaybackTimer = null;
  }
  if (!lectureSentences || lectureSentences.length === 0) return;
  const s = lectureSentences.find(item => item.index === index) || lectureSentences[index - 1];
  if (!s || !s.text) return;
  const text = s.text.trim();
  if (!text) return;

  // Stop previous direct audio and streaming audio cleanly
  stopAudioPlayback();

  const matchingBtns = document.querySelectorAll(`.inline-play-btn[data-index="${index}"], #typing-original-media-btn, #video-replay-clip-btn`);
  matchingBtns.forEach(b => b.classList.add("playing"));
  if (btnEl) {
    btnEl.classList.add("playing");
    currentDirectBtn = btnEl;
  }

  // Branch 1: Play authentic YouTube video/audio clip if document has original media
  const videoEl = document.getElementById("document-video-element");
  const hasOriginalMedia = currentDocHasMedia && typeof s.start_time === "number" && typeof s.end_time === "number" && s.end_time > s.start_time;

  if (hasOriginalMedia) {
    hasCurrentSentenceVideoPlayed = true;

    // When portrait screen is connected and local video is not forced open:
    if (isPortraitConnected && !isLocalVideoForced) {
      try {
        syncChannel.postMessage({
          type: "video_play_clip",
          data: {
            sentence_index: index,
            start_time: s.start_time,
            end_time: s.end_time,
            loop: isLoopPlayback
          }
        });
      } catch (_) {}
      if (videoEl && !videoEl.paused) {
        try { videoEl.pause(); } catch (_) {}
      }
      return;
    }

    const sessionId = ++currentVideoPlaybackSession;
    if (videoCheckInterval) {
      clearInterval(videoCheckInterval);
      videoCheckInterval = null;
    }

    const startTime = Math.max(0, s.start_time);
    const endTime = Math.max(startTime + 0.1, s.end_time);

    const resetVideoPlayState = () => {
      matchingBtns.forEach(b => b.classList.remove("playing"));
      if (btnEl) btnEl.classList.remove("playing");
      if (currentDirectBtn === btnEl) currentDirectBtn = null;
      if (videoCheckInterval) {
        clearInterval(videoCheckInterval);
        videoCheckInterval = null;
      }
    };

    const startMonitor = () => {
      if (sessionId !== currentVideoPlaybackSession) return;
      const playPromise = videoEl.play();
      if (playPromise !== undefined) {
        playPromise.then(() => {
          if (sessionId !== currentVideoPlaybackSession) return;
          if (videoCheckInterval) clearInterval(videoCheckInterval);
          videoCheckInterval = setInterval(() => {
            if (sessionId !== currentVideoPlaybackSession) {
              clearInterval(videoCheckInterval);
              return;
            }
            if (videoEl.seeking) return;
            if (videoEl.currentTime < startTime - 0.5) return;

            if (videoEl.currentTime >= endTime || videoEl.ended) {
              videoEl.pause();
              resetVideoPlayState();

              // Loop playback
              if (isLoopPlayback) {
                if (loopPlaybackTimer) clearTimeout(loopPlaybackTimer);
                loopPlaybackTimer = setTimeout(() => {
                  if (isLoopPlayback && sessionId === currentVideoPlaybackSession) {
                    const loopBtn = document.getElementById("typing-loop-btn");
                    playSentenceAudioDirect(index, loopBtn || btnEl);
                  }
                }, 650);
                return;
              }

              // Continuous reading advance
              if (isContinuousLecture && continuousAction === "read_only") {
                if (currentSentenceIndex < lectureSentences.length) {
                  setStatus(`第 ${currentSentenceIndex} 句播放完毕，准备下一句…`, "speaking");
                  clearTimeout(continuousTimer);
                  continuousTimer = setTimeout(() => {
                    if (!isContinuousLecture) return;
                    currentSentenceIndex++;
                    updateSentencePreview();
                    playSentenceAudioDirect(currentSentenceIndex);
                    syncChannel.postMessage({
                      type: "continuous_progress",
                      data: { running: true, sentence_index: currentSentenceIndex, total: lectureSentences.length }
                    });
                  }, 600);
                } else {
                  stopContinuousLecture();
                  showToast("🎉 本页所有句子视频原声播放完成！");
                  syncChannel.postMessage({
                    type: "continuous_progress",
                    data: { running: false, finished: true }
                  });
                }
              }
            }
          }, 30);
        }).catch(err => {
          console.warn("Video playback failed, falling back to TTS:", err);
          resetVideoPlayState();
          playSyntheticSentenceTts(s, text, index, btnEl, matchingBtns);
        });
      }
    };

    const seekAndStart = () => {
      if (Math.abs(videoEl.currentTime - startTime) > 0.15) {
        let seekHandled = false;
        const timeoutId = setTimeout(() => {
          if (!seekHandled) {
            seekHandled = true;
            try { videoEl.removeEventListener("seeked", onSeeked); } catch (_) {}
            startMonitor();
          }
        }, 600);
        const onSeeked = () => {
          if (!seekHandled) {
            seekHandled = true;
            clearTimeout(timeoutId);
            videoEl.removeEventListener("seeked", onSeeked);
            startMonitor();
          }
        };
        videoEl.addEventListener("seeked", onSeeked, { once: true });
        try {
          videoEl.currentTime = startTime;
        } catch (err) {
          if (!seekHandled) {
            seekHandled = true;
            clearTimeout(timeoutId);
            try { videoEl.removeEventListener("seeked", onSeeked); } catch (_) {}
            startMonitor();
          }
        }
      } else {
        startMonitor();
      }
    };

    if (videoEl.readyState >= 1) {
      seekAndStart();
    } else {
      let metaHandled = false;
      const metaTimeoutId = setTimeout(() => {
        if (!metaHandled) {
          metaHandled = true;
          try { videoEl.removeEventListener("loadedmetadata", onMeta); } catch (_) {}
          seekAndStart();
        }
      }, 1000);
      const onMeta = () => {
        if (!metaHandled) {
          metaHandled = true;
          clearTimeout(metaTimeoutId);
          videoEl.removeEventListener("loadedmetadata", onMeta);
          seekAndStart();
        }
      };
      videoEl.addEventListener("loadedmetadata", onMeta, { once: true });
    }
    return;
  }

  // Branch 2: Synthetic TTS (Edge-TTS / Web Speech)
  playSyntheticSentenceTts(s, text, index, btnEl, matchingBtns);
}

function playSyntheticSentenceTts(s, text, index, btnEl, matchingBtns) {
  // Determine Edge-TTS voice for sentence pronunciation (Default to pure native US English Jenny)
  let voice = "en-US-JennyNeural";
  const voiceSelectEl = document.getElementById("voice-select");
  const selectedVoice = (voiceSelectEl && voiceSelectEl.value) || localStorage.getItem("ai_coach_voice");
  if (selectedVoice && (selectedVoice.startsWith("en-") || selectedVoice.startsWith("zh-"))) {
    voice = selectedVoice;
  }

  const speedVal = (speedSlider && speedSlider.value) || localStorage.getItem("ai_coach_speed") || "1.0";
  const audioUrl = `/api/tts?text=${encodeURIComponent(text)}&voice=${encodeURIComponent(voice)}&speed=${encodeURIComponent(speedVal)}`;
  const audio = new Audio(audioUrl);
  audio._cancelled = false;
  currentDirectAudio = audio;

  const resetDirectAudioState = () => {
    matchingBtns.forEach(b => b.classList.remove("playing"));
    if (btnEl) btnEl.classList.remove("playing");
    if (currentDirectBtn === btnEl) currentDirectBtn = null;
    if (currentDirectAudio === audio) currentDirectAudio = null;
  };

  audio.onended = () => {
    resetDirectAudioState();
    // Auto-loop if single-sentence loop playback is enabled
    if (isLoopPlayback && !audio._cancelled) {
      if (loopPlaybackTimer) clearTimeout(loopPlaybackTimer);
      loopPlaybackTimer = setTimeout(() => {
        if (isLoopPlayback && !audio._cancelled) {
          const loopBtn = document.getElementById("typing-loop-btn");
          playSentenceAudioDirect(index, loopBtn || btnEl);
        }
      }, 650);
    }
  };
  audio.onerror = (err) => {
    if (loopPlaybackTimer) {
      clearTimeout(loopPlaybackTimer);
      loopPlaybackTimer = null;
    }
    if (audio._cancelled || !audio.src || audio.src === window.location.href) {
      resetDirectAudioState();
      return;
    }
    console.warn("Direct TTS audio error, falling back to Web Speech:", err);
    resetDirectAudioState();
    if ('speechSynthesis' in window) {
      try {
        window.speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(text);
        u.lang = 'en-US';
        u.onend = resetDirectAudioState;
        u.onerror = resetDirectAudioState;
        matchingBtns.forEach(b => b.classList.add("playing"));
        window.speechSynthesis.speak(u);
      } catch (_) {
        resetDirectAudioState();
      }
    }
  };

  audio.play().catch(err => {
    console.warn("Audio play blocked or interrupted:", err);
    resetDirectAudioState();
  });
}

// ==========================================================================
// Real-time PDF Sentence Highlighting & Point-and-Read Interaction
// ==========================================================================

function renderPdfSentenceHighlights(sentences) {
  const overlay = document.getElementById("pdf-highlight-layer");
  const wrapper = document.getElementById("pdf-page-wrapper");
  if (!overlay) return;

  overlay.innerHTML = "";
  if (!sentences || sentences.length === 0) return;

  if (wrapper) wrapper.style.display = "inline-block";

  sentences.forEach((sent) => {
    const boxes = sent.boxes || [];
    boxes.forEach((box, bIdx) => {
      const boxEl = document.createElement("div");
      boxEl.className = "pdf-sentence-box";
      boxEl.dataset.idx = sent.index;
      boxEl.dataset.boxIdx = bIdx;
      boxEl.style.left = `${box.x}%`;
      boxEl.style.top = `${box.y}%`;
      boxEl.style.width = `${box.w}%`;
      boxEl.style.height = `${box.h}%`;
      boxEl.title = `第 ${sent.index} 句 · 点击定位学习\n"${(sent.text || '').slice(0, 60)}"`;

      // Interactive Point-and-Read (点击跳转学习本句并发送至大模型)
      boxEl.addEventListener("click", (e) => {
        e.stopPropagation();
        currentSentenceIndex = sent.index;
        updateSentencePreview();
        executeCurrentActionMode();
      });

      overlay.appendChild(boxEl);
    });
  });

  highlightActivePdfSentence(currentSentenceIndex);
}

function highlightActivePdfSentence(index) {
  const overlay = document.getElementById("pdf-highlight-layer");
  if (!overlay) return;

  const allBoxes = overlay.querySelectorAll(".pdf-sentence-box");
  let firstActiveEl = null;

  allBoxes.forEach((b) => {
    const bIdx = parseInt(b.dataset.idx, 10);
    const isActive = (bIdx === index);
    b.classList.toggle("active", isActive);
    if (isActive && !firstActiveEl) {
      firstActiveEl = b;
    }
  });

  // Smoothly center the active sentence inside the PDF viewport
  if (firstActiveEl) {
    const container = document.getElementById("pdf-viewport-container");
    if (container) {
      const containerRect = container.getBoundingClientRect();
      const elRect = firstActiveEl.getBoundingClientRect();
      if (elRect.top < containerRect.top + 30 || elRect.bottom > containerRect.bottom - 30) {
        firstActiveEl.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }
  }
}

// Real-time Header Learning Progress (本页进度 / 章节进度 / 全书进度)
function updateHeaderProgress() {
  const pageVal = document.getElementById("prog-page-val");
  const pageFill = document.getElementById("prog-page-fill");
  const pageItem = document.getElementById("prog-page-item");

  const chapterVal = document.getElementById("prog-chapter-val");
  const chapterFill = document.getElementById("prog-chapter-fill");
  const chapterItem = document.getElementById("prog-chapter-item");

  const bookVal = document.getElementById("prog-book-val");
  const bookFill = document.getElementById("prog-book-fill");
  const bookItem = document.getElementById("prog-book-item");

  if (!pageVal || !chapterVal || !bookVal) return;

  // 1. 本页进度 (Current sentence progress on active page)
  const totalSentences = Array.isArray(lectureSentences) ? lectureSentences.length : 0;
  const currentSent = totalSentences > 0 ? Math.max(1, Math.min(currentSentenceIndex || 1, totalSentences)) : 0;
  const pagePct = totalSentences > 0 ? Math.round((currentSent / totalSentences) * 100) : 0;

  pageVal.textContent = totalSentences > 0 ? `${currentSent}/${totalSentences} 句` : "0/0 句";
  if (pageFill) pageFill.style.width = `${pagePct}%`;
  if (pageItem) pageItem.title = `本页进度：第 ${currentSent} / ${totalSentences} 句 (${pagePct}%)`;

  // 2. 全书进度 (Current page progress in the book)
  const totalPages = lectureTotalPages || 1;
  const currentPage = Math.max(1, Math.min(lecturePage || 1, totalPages));
  const bookPct = totalPages > 0 ? Math.round((currentPage / totalPages) * 100) : 0;

  bookVal.textContent = `${currentPage}/${totalPages} 页`;
  if (bookFill) bookFill.style.width = `${bookPct}%`;
  if (bookItem) bookItem.title = `全书进度：第 ${currentPage} / ${totalPages} 页 (${bookPct}%)`;

  // 3. 章节进度 (Current page progress within active unit/chapter)
  let unitTitle = "章节";
  let unitCurrentPage = 1;
  let unitTotalPages = 1;
  let chapterPct = 0;

  if (Array.isArray(lectureUnits) && lectureUnits.length > 0) {
    const sortedUnits = [...lectureUnits].sort((a, b) => (Number(a.page) || 0) - (Number(b.page) || 0));
    let currentUnit = null;
    let nextUnit = null;
    for (let i = sortedUnits.length - 1; i >= 0; i--) {
      if ((Number(sortedUnits[i].page) || 0) <= currentPage) {
        currentUnit = sortedUnits[i];
        nextUnit = sortedUnits[i + 1] || null;
        break;
      }
    }
    if (!currentUnit) {
      currentUnit = sortedUnits[0];
      nextUnit = sortedUnits[1] || null;
    }

    const uStart = Number(currentUnit.page) || 1;
    const uEnd = nextUnit ? Math.max(uStart, (Number(nextUnit.page) || uStart) - 1) : totalPages;
    unitTotalPages = Math.max(1, uEnd - uStart + 1);
    unitCurrentPage = Math.max(1, Math.min(currentPage - uStart + 1, unitTotalPages));
    chapterPct = Math.round((unitCurrentPage / unitTotalPages) * 100);
    unitTitle = currentUnit.title || (currentUnit.unit ? `Unit ${currentUnit.unit}` : "章节");
  } else {
    unitCurrentPage = currentPage;
    unitTotalPages = totalPages;
    chapterPct = bookPct;
  }

  chapterVal.textContent = `${unitCurrentPage}/${unitTotalPages} 页`;
  if (chapterFill) chapterFill.style.width = `${chapterPct}%`;
  if (chapterItem) chapterItem.title = `章节进度 (${unitTitle})：第 ${unitCurrentPage} / ${unitTotalPages} 页 (${chapterPct}%)`;
}

// Collapsible Top Settings Menu Popover
function initHeaderMenu() {
  const menuBtn = document.getElementById("header-menu-toggle-btn");
  const popover = document.getElementById("header-menu-popover");
  if (!menuBtn || !popover) return;

  function toggleMenu(show) {
    const isCurrentlyShown = popover.style.display !== "none";
    const nextShow = typeof show === "boolean" ? show : !isCurrentlyShown;
    if (nextShow) {
      popover.style.display = "flex";
      menuBtn.setAttribute("aria-expanded", "true");
      menuBtn.classList.add("active");
    } else {
      popover.style.display = "none";
      menuBtn.setAttribute("aria-expanded", "false");
      menuBtn.classList.remove("active");
    }
  }

  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleMenu();
  });

  popover.addEventListener("click", (e) => {
    e.stopPropagation();
  });

  document.addEventListener("click", (e) => {
    if (!popover.contains(e.target) && !menuBtn.contains(e.target)) {
      toggleMenu(false);
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && popover.style.display !== "none") {
      toggleMenu(false);
    }
  });

  const autoCloseBtns = ["sidebar-toggle-btn", "reset-btn"];
  autoCloseBtns.forEach(id => {
    const btn = document.getElementById(id);
    if (btn) {
      btn.addEventListener("click", () => {
        toggleMenu(false);
      });
    }
  });
}

// Interactive clicks on progress chips
function initHeaderProgressClickHandlers() {
  const pageItem = document.getElementById("prog-page-item");
  const chapterItem = document.getElementById("prog-chapter-item");
  const bookItem = document.getElementById("prog-book-item");

  if (pageItem) {
    pageItem.addEventListener("click", () => {
      const typingDisplay = document.getElementById("typing-target-display");
      if (typingDisplay) {
        typingDisplay.focus();
        try { typingDisplay.scrollIntoView({ block: "nearest", behavior: "smooth" }); } catch (_) {}
      }
      const totalSent = Array.isArray(lectureSentences) ? lectureSentences.length : 0;
      showToast(`📄 本页进度: 第 ${currentSentenceIndex || 1} / ${totalSent || 1} 句`);
    });
  }

  if (chapterItem) {
    chapterItem.addEventListener("click", () => {
      if (typeof isSidebarCollapsed !== "undefined" && isSidebarCollapsed && typeof setSidebarCollapsed === "function") {
        setSidebarCollapsed(false, true);
      }
      const currentUnit = [...lectureUnits].reverse().find(u => Number(u.page) <= lecturePage);
      const title = currentUnit ? (currentUnit.title || `Unit ${currentUnit.unit}`) : "当前章节";
      showToast(`📑 章节: ${title}`);
      const typingDisplay = document.getElementById("typing-target-display");
      if (typingDisplay) {
        try { typingDisplay.focus(); } catch (_) {}
      }
    });
  }

  if (bookItem) {
    bookItem.addEventListener("click", () => {
      const totalPages = lectureTotalPages || 1;
      showToast(`📚 全书进度: 第 ${lecturePage || 1} / ${totalPages} 页 (${Math.round((lecturePage / totalPages) * 100)}%)`);
      const typingDisplay = document.getElementById("typing-target-display");
      if (typingDisplay) {
        try { typingDisplay.focus(); } catch (_) {}
      }
    });
  }
}

function getCurrentDocumentTitle() {
  if (lectureDocSelect && lectureDocSelect.selectedIndex >= 0) {
    const opt = lectureDocSelect.options[lectureDocSelect.selectedIndex];
    if (opt && opt.textContent) {
      return opt.textContent.replace(/\s*\(\d+\s*pages\)$/i, "").trim();
    }
  }
  return "逐句精读与跟打";
}

// Sentence Focus & Navigation
function updateSentencePreview(broadcast = true, triggerAutoplay = true) {
  if (!lectureSentences || lectureSentences.length === 0) {
    sentProgressLabel.textContent = "第 0 / 0 句";
    sentencePreviewBox.textContent = "当前页未提取到独立句子";
    highlightActivePdfSentence(0);
    updateHeaderProgress();
    return;
  }
  currentSentenceIndex = Math.max(1, Math.min(currentSentenceIndex, lectureSentences.length));
  sentProgressLabel.textContent = `第 ${currentSentenceIndex} / ${lectureSentences.length} 句`;
  const s = lectureSentences[currentSentenceIndex - 1];
  if (s && s.text) {
    const hasMedia = !!(currentDocHasMedia && typeof s.start_time === "number");
    const playBtnTitle = hasMedia ? "播放视频原声 (Alt+V / Alt+R)" : "播放原句读音 (Alt+R)";
    const playBtnIcon = hasMedia ? "🎬 原声" : "🔊";
    sentencePreviewBox.innerHTML = `<span class="sentence-text-val">${escapeHtml(s.text)}</span> <button id="landscape-sent-play-btn" class="inline-play-btn ${hasMedia ? 'has-media-btn' : ''}" data-index="${currentSentenceIndex}" onclick="event.stopPropagation(); playSentenceAudioDirect(currentSentenceIndex, this);" title="${playBtnTitle}" aria-label="${playBtnTitle}">${playBtnIcon}</button>`;
  } else {
    sentencePreviewBox.textContent = "";
  }

  hasCurrentSentenceVideoPlayed = false;
  updateVideoSyncStrip();

  const videoEl = document.getElementById("document-video-element");
  if (s && typeof s.start_time === "number" && typeof s.end_time === "number") {
    if (currentDocHasMedia && isVideoAutoplayEnabled && hasUserInteracted && triggerAutoplay) {
      playSentenceAudioDirect(currentSentenceIndex);
    } else if (!isPortraitConnected && videoEl && !videoEl.seeking && videoEl.paused && Math.abs(videoEl.currentTime - s.start_time) > 0.5) {
      try {
        if (videoEl.readyState >= 1) {
          videoEl.currentTime = s.start_time;
        } else {
          videoEl.addEventListener("loadedmetadata", () => {
            try { videoEl.currentTime = s.start_time; } catch (_) {}
          }, { once: true });
        }
      } catch (_) {}
    } else if (isPortraitConnected && !triggerAutoplay && typeof s.start_time === "number") {
      try {
        syncChannel.postMessage({
          type: "seek",
          data: { time: s.start_time, sentence_index: currentSentenceIndex }
        });
      } catch (_) {}
    }
  }
  const docTitleEl = document.getElementById("fs-doc-title");
  if (docTitleEl) {
    const docName = getCurrentDocumentTitle();
    docTitleEl.textContent = `${docName} (P. ${lecturePage || 1} · 第 ${currentSentenceIndex} / ${lectureSentences.length} 句)`;
  }
  const cleanedText = s ? (s.text || "").replace(/[\u00a0\u202f\u2009\u3000]/g, " ").replace(/\s+/g, " ").trim() : "";
  const rawTrans = cleanedText ? (sentenceTranslations[cleanedText] || s?.translation || null) : null;
  const cachedTranslation = isValidChineseTranslation(cleanedText, rawTrans) ? rawTrans : null;
  setupShadowTyping(s ? s.text : "", cachedTranslation);

  // If notes are cached for this sentence, display immediately on whiteboard
  if (cleanedText && sentenceNotesCache[cleanedText]) {
    currentNotesMarkdown = sentenceNotesCache[cleanedText];
    renderWhiteboard(currentNotesMarkdown);
  } else if (cleanedText) {
    // Proactively fetch smart notes from server (from SQLite cache or Cloud API)
    fetchSentenceSmartNotes(cleanedText, cachedTranslation, false);
  }

  // Update PDF page real-time highlight
  highlightActivePdfSentence(currentSentenceIndex);

  // Broadcast to portrait window if requested
  if (broadcast) {
    syncChannel.postMessage({
      type: "sentence_change",
      data: {
        sentence_index: currentSentenceIndex,
        start_time: s ? s.start_time : null,
        end_time: s ? s.end_time : null,
        auto_play: isVideoAutoplayEnabled && hasUserInteracted && triggerAutoplay,
        source: "landscape"
      }
    });
  }

  // Save learning progress
  if (lectureDocumentId) {
    saveLearningProgress({
      document_id: lectureDocumentId,
      page: lecturePage,
      sentence_index: currentSentenceIndex,
      teaching_style: currentTeachingStyle
    });
  }

  // If single sentence loop playback is running, loop the newly selected sentence
  if (isLoopPlayback && userStartedLoop && s && s.text) {
    if (loopPlaybackTimer) {
      clearTimeout(loopPlaybackTimer);
      loopPlaybackTimer = null;
    }
    const loopBtn = document.getElementById("typing-loop-btn");
    playSentenceAudioDirect(currentSentenceIndex, loopBtn);
  }
  updateHeaderProgress();
}

function triggerSentenceAction(index, text, action = "explain") {
  if (!text) {
    const s = lectureSentences.find(item => item.index === index);
    if (s) text = s.text;
  }
  if (!text) {
    showToast("请先选择要讲解的句子");
    return;
  }
  currentSentenceIndex = index;
  updateSentencePreview();

  const cleanedText = text.replace(/[\u00a0\u202f\u2009\u3000]/g, " ").replace(/\s+/g, " ").trim();
  if (cleanedText && sentenceNotesCache[cleanedText]) {
    currentNotesMarkdown = sentenceNotesCache[cleanedText];
    renderWhiteboard(currentNotesMarkdown);
  } else if (cleanedText) {
    fetchSentenceSmartNotes(cleanedText, "", false);
  }

  if (action === "read_only" && currentDocHasMedia) {
    const s = lectureSentences.find(item => item.index === index) || lectureSentences[index - 1];
    if (s && typeof s.start_time === "number" && typeof s.end_time === "number" && s.end_time > s.start_time) {
      lectureStatus.textContent = "正在播放视频原声…";
      playSentenceAudioDirect(index);
      return;
    }
  }

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({
      type: "lecture_sentence",
      sentence_index: index,
      text: text,
      action: action
    }));
    const actionLabels = { "explain": "正在双语精讲…", "read_only": "正在纯原音朗读…", "practice": "正在准备跟读练习…" };
    lectureStatus.textContent = actionLabels[action] || "正在讲解…";
  }
}

// Multi-window synchronization listener
syncChannel.onmessage = (event) => {
  const { type, data } = event.data || {};
  if (type === "page_change_requested") {
    const currentDoc = (lectureDocSelect && lectureDocSelect.value) || lectureDocumentId;
    if (data && data.document_id && data.document_id !== currentDoc) {
      switchDocument(data.document_id, data.page, data.sentence_index);
    } else if (data && data.page && data.page !== lecturePage) {
      loadLecturePage(data.page, data.sentence_index || 1);
    } else if (data && data.sentence_index && data.sentence_index !== currentSentenceIndex) {
      currentSentenceIndex = Math.max(1, Math.min(data.sentence_index, lectureSentences.length));
      hasCurrentSentenceVideoPlayed = true;
      updateSentencePreview(false, false);
    }
  } else if (type === "sentence_action_requested") {
    triggerSentenceAction(data.sentence_index, data.sentence_text, data.action);
  } else if (type === "toggle_continuous_lecture") {
    if (data && data.active !== undefined) {
      if (data.active) startContinuousLecture(data.action || "explain", data.start_index);
      else stopContinuousLecture();
    } else {
      toggleContinuousLecture(data ? data.action : "explain", data ? data.start_index : null);
    }
  } else if (type === "teaching_style") {
    if (data && data.style) {
      currentTeachingStyle = data.style;
      localStorage.setItem("ai_coach_teaching_style", data.style);
      const styleSelect = document.getElementById("teaching-style-select");
      if (styleSelect) styleSelect.value = data.style;
    }
  } else if (type === "pronunciation_eval") {
    if (data) displayPronunciationResult(data, false);
  } else if (type === "document_uploaded") {
    loadLectureDocuments();
  } else if (type === "theme_changed") {
    if (data && data.theme) {
      applyTheme(data.theme, false);
    }
  } else if (type === "game_status_updated") {
    if (data) {
      updateHudDisplays(data);
    }
  } else if (type === "odometer_updated") {
    if (window.OdometerEngine && data) {
      window.OdometerEngine.setLifetimeWords(data.total_words, data.today_words, data.unique_words);
    }
  } else if (type === "sentence_translation") {
    // Another window fetched a translation - validate before caching locally and updating UI
    if (data && data.text && isValidChineseTranslation(data.text, data.translation)) {
      sentenceTranslations[data.text] = data.translation;
      updateTranslationUI(data.text, data.translation);
    }
  } else if (type === "typing_dock_change") {
    if (data && data.dock) {
      setTypingDock(data.dock, false);
    }
  } else if (type === "notes_dock_change") {
    if (data && data.dock) {
      setNotesDock(data.dock, false);
    }
  } else if (type === "notes_update") {
    if (data && data.notes !== undefined) {
      if (data.sentence) {
        sentenceNotesCache[data.sentence] = data.notes;
      }
      currentNotesMarkdown = data.notes;
      latestWhiteboardMarkdown = data.notes;
      if (whiteboardContent) {
        whiteboardContent.innerHTML = renderMarkdownSafe(data.notes);
        enhanceWhiteboardVocab(whiteboardContent);
      }
    }
  } else if (type === "typing_sync") {
    if (data && data.source === "portrait") {
      syncTypingFromPortrait(data);
    }
  } else if (type === "typing_completed") {
    if (data && data.sentence_index) {
      handleTypingCompletedFromSync(data);
    }
  } else if (type === "repeat_rounds_changed") {
    if (data && data.rounds) {
      currentTypingRepeatRounds = data.rounds;
      updateTypingRepeatButton();
    }
  } else if (type === "portrait_connected") {
    const wasConnected = isPortraitConnected;
    isPortraitConnected = true;
    lastPortraitHeartbeat = Date.now();
    updateVideoSyncStrip();
    if (!wasConnected) {
      if (!isSidebarCollapsed) {
        setSidebarCollapsed(true, false);
      }
      showToast("🖥️ 竖屏教材端已连通：视频在竖屏高清置顶播放，打字空间已完全释放！");
      try {
        if (latestWhiteboardMarkdown) {
          syncChannel.postMessage({
            type: "notes_update",
            data: { notes: latestWhiteboardMarkdown }
          });
        }
        syncChannel.postMessage({
          type: "notes_dock_change",
          data: { dock: currentNotesDock }
        });
        syncChannel.postMessage({
          type: "typing_dock_change",
          data: { dock: currentTypingDock }
        });
        if (isTypingFullscreenActive()) {
          syncChannel.postMessage({
            type: "typing_fullscreen_change",
            data: { active: true }
          });
        }
      } catch (_) {}
    }
  } else if (type === "portrait_heartbeat") {
    isPortraitConnected = true;
    lastPortraitHeartbeat = Date.now();
  } else if (type === "portrait_disconnected") {
    isPortraitConnected = false;
    updateVideoSyncStrip();
    showToast("🖥️ 竖屏教材端已断开，已自动切回单屏模式");
  } else if (type === "sentence_selected_from_portrait" || (type === "sentence_change" && data && data.source === "portrait")) {
    if (data && data.sentence_index) {
      const targetIdx = Math.max(1, Math.min(data.sentence_index, lectureSentences.length || 1));
      if (currentSentenceIndex !== targetIdx || !hasCurrentSentenceVideoPlayed) {
        currentSentenceIndex = targetIdx;
        hasCurrentSentenceVideoPlayed = true;
        updateSentencePreview(false, false);
      }
      const td = document.getElementById("typing-target-display");
      if (td) {
        setTimeout(() => { try { td.focus(); } catch (_) {} }, 40);
      }
    }
  } else if (type === "play") {
    const idx = (data && data.sentence_index) || currentSentenceIndex;
    document.querySelectorAll(`.inline-play-btn[data-index="${idx}"], #typing-original-media-btn, #video-replay-clip-btn, #landscape-sent-play-btn`).forEach(b => b.classList.add("playing"));
  } else if (type === "pause") {
    document.querySelectorAll(".inline-play-btn.playing, #typing-original-media-btn.playing, #video-replay-clip-btn.playing, #landscape-sent-play-btn.playing").forEach(b => b.classList.remove("playing"));
    if (data && data.ended) {
      if (isContinuousLecture && continuousAction === "read_only") {
        if (currentSentenceIndex < lectureSentences.length) {
          setStatus(`第 ${currentSentenceIndex} 句播放完毕，准备下一句…`, "speaking");
          clearTimeout(continuousTimer);
          continuousTimer = setTimeout(() => {
            if (!isContinuousLecture) return;
            currentSentenceIndex++;
            updateSentencePreview();
            playSentenceAudioDirect(currentSentenceIndex);
            syncChannel.postMessage({
              type: "continuous_progress",
              data: { running: true, sentence_index: currentSentenceIndex, total: lectureSentences.length }
            });
          }, 600);
        } else {
          stopContinuousLecture();
          showToast("🎉 本页所有句子视频原声播放完成！");
          syncChannel.postMessage({
            type: "continuous_progress",
            data: { running: false, finished: true }
          });
        }
      }
    }
  } else if (type === "seek") {
    const videoEl = document.getElementById("document-video-element");
    if (videoEl && data && typeof data.time === "number") {
      try {
        if (videoEl.readyState >= 1) videoEl.currentTime = data.time;
        else videoEl.addEventListener("loadedmetadata", () => { videoEl.currentTime = data.time; }, { once: true });
      } catch (_) {}
    }
  } else if (type === "video_mode") {
    if (data) {
      currentDocHasMedia = !!data.has_media;
      currentDocMediaUrl = data.media_url || (currentDocHasMedia ? `/api/documents/${data.document_id || lectureDocumentId}/media` : null);
      updateVideoSyncStrip();
    }
  } else if (type === "loop_mode") {
    if (data && typeof data.loop === "boolean") {
      toggleLoopPlayback(data.loop, false);
    }
  } else if (type === "video_status") {
    if (data) {
      if (data.state === "paused") {
        document.querySelectorAll(".inline-play-btn.playing, #typing-original-media-btn.playing, #video-replay-clip-btn.playing, #landscape-sent-play-btn.playing").forEach(b => b.classList.remove("playing"));
        if (data.ended && isContinuousLecture && continuousAction === "read_only") {
          if (currentSentenceIndex < lectureSentences.length) {
            setStatus(`第 ${currentSentenceIndex} 句播放完毕，准备下一句…`, "speaking");
            clearTimeout(continuousTimer);
            continuousTimer = setTimeout(() => {
              if (!isContinuousLecture) return;
              currentSentenceIndex++;
              updateSentencePreview();
              playSentenceAudioDirect(currentSentenceIndex);
              syncChannel.postMessage({
                type: "continuous_progress",
                data: { running: true, sentence_index: currentSentenceIndex, total: lectureSentences.length }
              });
            }, 600);
          } else {
            stopContinuousLecture();
            showToast("🎉 本页所有句子视频原声播放完成！");
            syncChannel.postMessage({
              type: "continuous_progress",
              data: { running: false, finished: true }
            });
          }
        }
      } else if (data.state === "playing") {
        const idx = data.sentence_index || currentSentenceIndex;
        document.querySelectorAll(`.inline-play-btn[data-index="${idx}"], #typing-original-media-btn, #video-replay-clip-btn, #landscape-sent-play-btn`).forEach(b => b.classList.add("playing"));
      }
    }
  }
};

// Check portrait connection liveness
setInterval(() => {
  if (isPortraitConnected && Date.now() - lastPortraitHeartbeat > 12000) {
    isPortraitConnected = false;
    updateVideoSyncStrip();
  }
}, 4000);

// Notes Management
async function saveCurrentNotes() {
  if (!currentNotesMarkdown || !currentNotesMarkdown.trim()) {
    showToast("当前板书为空，无需保存");
    return;
  }
  const currentSentence = lectureSentences[currentSentenceIndex - 1];
  try {
    const res = await fetch("/api/notes/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        notes_markdown: currentNotesMarkdown,
        voice_text: lastTurnVoice,
        document_id: lectureDocumentId,
        page_number: lecturePage,
        sentence_index: currentSentence ? currentSentence.index : null,
        sentence_text: currentSentence ? currentSentence.text : null,
        category: currentSentence ? "sentence_lecture" : "lecture"
      })
    });
    if (res.ok) {
      showToast("✅ 笔记已成功收藏到本地库！");
    } else {
      showToast("保存失败，请稍后重试");
    }
  } catch (err) {
    console.error("Failed to save notes:", err);
    showToast("网络错误，保存失败");
  }
}

function copyCurrentNotes() {
  if (!currentNotesMarkdown || !currentNotesMarkdown.trim()) {
    showToast("当前板书为空");
    return;
  }
  navigator.clipboard.writeText(currentNotesMarkdown).then(() => {
    showToast("📋 板书已复制到剪贴板！");
  }).catch(() => {
    showToast("复制失败，请手动选取");
  });
}

function exportNotes() {
  let url = "/api/notes/export";
  if (lectureDocumentId) url += `?document_id=${encodeURIComponent(lectureDocumentId)}`;
  window.open(url, "_blank");
}

function exportAnkiCards() {
  const filterCheckbox = document.getElementById("modal-filter-mistakes");
  const onlyMistakes = filterCheckbox && filterCheckbox.checked;
  let url = "/api/notes/export/anki";
  const params = [];
  if (lectureDocumentId) params.push(`document_id=${encodeURIComponent(lectureDocumentId)}`);
  if (onlyMistakes) params.push("only_mistakes=true");
  if (params.length > 0) url += `?${params.join("&")}`;
  window.open(url, "_blank");
  showToast("🎓 正在导出 Anki 闪卡包 (.tsv)...");
}

async function openNotesModal() {
  notesModal.style.display = "flex";
  modalNotesList.innerHTML = `<div style="text-align:center; color:#64748b; padding:30px;">正在载入笔记…</div>`;
  const filterCheckbox = document.getElementById("modal-filter-mistakes");
  const onlyMistakes = filterCheckbox && filterCheckbox.checked;
  let url = "/api/notes?limit=200";
  if (lectureDocumentId) url += `&document_id=${encodeURIComponent(lectureDocumentId)}`;
  if (onlyMistakes) url += "&only_mistakes=true";

  try {
    const res = await fetch(url);
    const data = await res.json();
    renderNotesList(data.notes || []);
  } catch (err) {
    modalNotesList.innerHTML = `<div style="text-align:center; color:#ef4444; padding:30px;">加载笔记失败</div>`;
  }
}

function renderNotesList(notes) {
  if (!notes || notes.length === 0) {
    modalNotesList.innerHTML = `<div style="text-align:center; color:#64748b; padding:30px;">暂无匹配的学习笔记或错题</div>`;
    return;
  }
  modalNotesList.innerHTML = notes.map(n => `
    <div class="note-card-item" id="note-card-${n.id}">
      <div class="note-card-header">
        <div style="display:flex; align-items:center; gap:8px;">
          <h3>${escapeHtml(n.title || "学习笔记")}</h3>
          ${n.is_mistake ? `<span style="font-size:11px; background:#fee2e2; color:#ef4444; font-weight:700; padding:1px 6px; border-radius:4px;">⚠️ 错题强化 (${escapeHtml(n.mistake_type || '练习')})</span>` : ''}
        </div>
        <span class="note-time">${n.created_at}</span>
      </div>
      ${n.sentence_text ? `<div style="font-size:12px; color:#0284c7; margin-bottom:6px;">🎯 原句: <em>"${escapeHtml(n.sentence_text)}"</em></div>` : ""}
      <div class="note-card-content">${renderMarkdownSafe(n.notes_markdown || "")}</div>
      <div class="note-card-actions">
        <button class="btn-xs btn-quiet" onclick="deleteNote(${n.id})">🗑️ 删除</button>
      </div>
    </div>
  `).join("");
  enhanceWhiteboardVocab(modalNotesList);
}

if (modalNotesList) {
  modalNotesList.addEventListener("click", (e) => {
    const btn = e.target.closest(".vocab-play-btn");
    if (btn) {
      e.stopPropagation();
      e.preventDefault();
      playNoteWordAudio(btn.dataset.word, btn);
      return;
    }
    const wordEl = e.target.closest(".vocab-word-clickable");
    if (wordEl) {
      e.stopPropagation();
      e.preventDefault();
      const word = wordEl.dataset.word || wordEl.textContent;
      const siblingBtn = wordEl.nextElementSibling?.classList.contains("vocab-play-btn") ? wordEl.nextElementSibling : null;
      playNoteWordAudio(word, siblingBtn || wordEl);
      return;
    }
  });
}


async function deleteNote(id) {
  if (!confirm("确定要删除这条笔记吗？")) return;
  try {
    const res = await fetch(`/api/notes/${id}`, { method: "DELETE" });
    if (res.ok) {
      const el = document.getElementById(`note-card-${id}`);
      if (el) el.remove();
      showToast("笔记已删除");
    }
  } catch (err) {
    showToast("删除失败");
  }
}

function closeNotesModal() {
  notesModal.style.display = "none";
}

// Toast helper
function showToast(message) {
  const existing = document.querySelector(".toast-notice");
  if (existing) existing.remove();
  const toast = document.createElement("div");
  toast.className = "toast-notice";
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 2500);
}

function requestLectureAction(action) {
  if (!lectureDocumentId || !lecturePage) {
    lectureStatus.textContent = "请先载入一页教材";
    return;
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "lecture_request", action }));
    lectureStatus.textContent = action === "practice" ? "正在准备练习…" : "正在准备讲解…";
  }
}

// Dual-screen window launcher (opens directly on the portrait monitor)
dualScreenBtn.addEventListener("click", async () => {
  let targetLeft = 2560;
  let targetTop = 0;
  let targetWidth = 1440;
  let targetHeight = 2560;

  // 1. Multi-Screen Window Placement API (Chrome/Edge)
  if ('getScreenDetails' in window) {
    try {
      const details = await window.getScreenDetails();
      const otherScreen = details.screens.find(s => s !== details.currentScreen && (s.height > s.width || s.left !== details.currentScreen.left))
        || details.screens.find(s => s !== details.currentScreen);
      if (otherScreen) {
        targetLeft = otherScreen.availLeft ?? otherScreen.left;
        targetTop = otherScreen.availTop ?? otherScreen.top;
        targetWidth = otherScreen.availWidth ?? otherScreen.width;
        targetHeight = otherScreen.availHeight ?? otherScreen.height;
      }
    } catch (_) {}
  } else if (window.screen) {
    if (window.screen.availLeft !== undefined && window.screen.availLeft === 0) {
      targetLeft = window.screen.availWidth || 2560;
      targetTop = 0;
    }
  }

  const features = `left=${targetLeft},top=${targetTop},screenX=${targetLeft},screenY=${targetTop},width=${targetWidth},height=${targetHeight},menubar=no,toolbar=no,location=no,status=no`;
  const portraitWin = window.open("/portrait", "EnglishCoachPortrait", features);
  if (portraitWin) {
    try { portraitWin.focus(); } catch (_) {}
  }

  // 2. Trigger Hyprland window rule positioning on Linux desktop
  try {
    fetch("/api/window/portrait_to_screen", { method: "POST" });
  } catch (_) {}

  // 3. Auto-collapse left sidebar on main screen to provide wide blackboard & studio
  setSidebarCollapsed(true, false);
  showToast("🖥️ 双屏已联动：主台已自动收起左侧视口，黑板与跟打已切换为宽屏模式！");
});

// Sentence and Page Navigation helpers
function isTextEditingContext(el) {
  if (!el) return false;
  const tag = el.tagName ? el.tagName.toLowerCase() : "";
  if (tag === "textarea") return true;
  if (tag === "input") {
    const type = (el.type || "").toLowerCase();
    if (["button", "checkbox", "radio", "submit", "reset"].includes(type)) {
      return false;
    }
    if (type === "range") return true;
    return true;
  }
  if (el.isContentEditable) return true;
  const modal = el.closest ? el.closest(".modal-backdrop, .modal, .dialog-card") : null;
  if (modal && modal.style.display !== "none" && !modal.classList.contains("hidden")) {
    return true;
  }
  const popover = el.closest ? el.closest("#header-menu-popover") : null;
  if (popover && popover.style.display !== "none") {
    return true;
  }
  return false;
}

function navigateSentence(direction) {
  if (!Array.isArray(lectureSentences) || lectureSentences.length === 0) return;
  const targetTypingEl = document.getElementById("typing-target-display");
  const keepFocus = targetTypingEl && (document.activeElement === targetTypingEl || (typeof isTypingFullscreenActive === "function" && isTypingFullscreenActive()));
  console.log("[Navigation] navigateSentence:", direction, "currentIndex:", currentSentenceIndex);

  if (direction > 0) {
    if (currentSentenceIndex < lectureSentences.length) {
      currentSentenceIndex++;
      updateSentencePreview();
      if (keepFocus && targetTypingEl) {
        setTimeout(() => { try { targetTypingEl.focus(); } catch (_) {} }, 20);
      }
    } else {
      showToast("已是本页最后一句 (按 → 可翻至下一页)");
    }
  } else if (direction < 0) {
    if (currentSentenceIndex > 1) {
      currentSentenceIndex--;
      updateSentencePreview();
      if (keepFocus && targetTypingEl) {
        setTimeout(() => { try { targetTypingEl.focus(); } catch (_) {} }, 20);
      }
    } else {
      showToast("已是本页第一句 (按 ← 可翻至上一页)");
    }
  }
}

function navigatePage(direction) {
  if (!lectureDocumentId) return Promise.resolve(false);
  const curPage = Number(lecturePage) || 1;
  const totalPages = Number(lectureTotalPages) || 1;
  const targetTypingEl = document.getElementById("typing-target-display");
  const keepFocus = targetTypingEl && (document.activeElement === targetTypingEl || (typeof isTypingFullscreenActive === "function" && isTypingFullscreenActive()));
  console.log("[Navigation] navigatePage:", direction, "curPage:", curPage, "totalPages:", totalPages);

  if (direction > 0) {
    if (totalPages && curPage >= totalPages) {
      showToast("已是教材最后一页");
      return Promise.resolve(false);
    } else {
      return loadLecturePage(curPage + 1, 1).then(() => {
        if (keepFocus && targetTypingEl) {
          setTimeout(() => { try { targetTypingEl.focus(); } catch (_) {} }, 100);
        }
        return true;
      });
    }
  } else if (direction < 0) {
    if (curPage > 1) {
      return loadLecturePage(curPage - 1, 1).then(() => {
        if (keepFocus && targetTypingEl) {
          setTimeout(() => { try { targetTypingEl.focus(); } catch (_) {} }, 100);
        }
        return true;
      });
    } else {
      showToast("已是教材第一页");
      return Promise.resolve(false);
    }
  }
  return Promise.resolve(false);
}

// Sentence focus actions
sentPrevBtn.addEventListener("click", () => {
  navigateSentence(-1);
});
sentNextBtn.addEventListener("click", () => {
  navigateSentence(1);
});
if (headerActionRunBtn) {
  headerActionRunBtn.addEventListener("click", () => {
    executeCurrentActionMode();
  });
}

// Shadow Typing Event Bindings (inline typing in display area)
const typingDisplayEl = document.getElementById("typing-target-display");
if (typingDisplayEl) {
  typingDisplayEl.addEventListener("keydown", (e) => {
    // Tab: Switch immediately to dialogue input area
    if (e.key === "Tab") {
      switchBetweenOralAndTyping(e);
      return;
    }

    if (!currentTypingTarget || isTypingCompleted) {
      if (e.key === "Enter" && e.shiftKey) {
        e.preventDefault();
        replayCurrentSentenceAudio();
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        // Advance to next sentence on Enter
        if (currentSentenceIndex < lectureSentences.length) {
          currentSentenceIndex++;
          updateSentencePreview();
          const nextSent = lectureSentences[currentSentenceIndex - 1];
          const activeAction = getEffectiveActionMode();
          triggerSentenceAction(currentSentenceIndex, nextSent ? nextSent.text : "", activeAction);
        } else {
          showToast("本页句子已全部练习完毕！");
        }
      }
      return;
    }

    // Shift + Enter: Replay current sentence
    if (e.key === "Enter" && e.shiftKey) {
      e.preventDefault();
      replayCurrentSentenceAudio();
      return;
    }

    // Prevent spacebar from scrolling or triggering voice recording
    if (e.key === " ") {
      e.preventDefault();
    }

    if (e.key === "Backspace") {
      e.preventDefault();
      if (typedBuffer.length > 0) {
        typedBuffer = typedBuffer.slice(0, -1);
        handleTypingBackspace(typedBuffer.length);
        handleTypingInput();
      }
      return;
    }

    if (e.key === "Enter") {
      e.preventDefault();
      // Advance to next sentence on Enter
      if (currentSentenceIndex < lectureSentences.length) {
        currentSentenceIndex++;
        updateSentencePreview();
        const nextSent = lectureSentences[currentSentenceIndex - 1];
        const activeAction = getEffectiveActionMode();
        triggerSentenceAction(currentSentenceIndex, nextSent ? nextSent.text : "", activeAction);
      } else {
        showToast("本页句子已全部练习完毕！");
      }
      return;
    }

    if (e.key === "Escape") {
      if (typeof isTypingFullscreenActive === "function" && isTypingFullscreenActive()) {
        setTypingFullscreen(false, true);
      } else {
        typingDisplayEl.blur();
      }
      return;
    }

    // Ignore non-printable keys (Ctrl, Alt, Meta, Shift, arrow keys, function keys, Tab)
    if (e.key.length !== 1 || e.ctrlKey || e.altKey || e.metaKey) return;

    e.preventDefault();
    if (typedBuffer.length < currentTypingTarget.length) {
      typedBuffer += e.key;
      handleTypingInput();
    }
  });

  typingDisplayEl.addEventListener("click", (e) => {
    const targetSpan = e.target.closest("span[data-idx]");
    if (targetSpan) {
      const idx = parseInt(targetSpan.getAttribute("data-idx"), 10);
      if (!isNaN(idx) && currentSentenceWords && currentSentenceWords.length > 0) {
        const wordIdx = currentSentenceWords.findIndex(w => idx >= w.startIndex && idx < w.endIndex);
        if (wordIdx !== -1) {
          lastSpokenWordIndex = wordIdx;
          playWordAudio(currentSentenceWords[wordIdx].clean, true, wordIdx);
        }
      }
    }
  });

  typingDisplayEl.addEventListener("focus", () => {
    typingDisplayEl.classList.add("typing-active");
    const card = document.getElementById("shadow-typing-card");
    if (card) card.classList.add("focused");
    const pill = document.getElementById("typing-status-pill");
    if (pill && !isTypingCompleted && currentTypingTarget) {
      pill.className = "typing-status-pill active";
      pill.textContent = typedBuffer.length > 0 ? "跟打中…" : "请开始输入";
    }
    if (currentDocHasMedia && isVideoAutoplayEnabled && !hasCurrentSentenceVideoPlayed && hasUserInteracted) {
      hasCurrentSentenceVideoPlayed = true;
      playSentenceAudioDirect(currentSentenceIndex);
    }
  });

  typingDisplayEl.addEventListener("blur", () => {
    typingDisplayEl.classList.remove("typing-active");
    const card = document.getElementById("shadow-typing-card");
    if (card) card.classList.remove("focused");
    const pill = document.getElementById("typing-status-pill");
    if (pill && !isTypingCompleted && currentTypingTarget) {
      pill.className = "typing-status-pill";
      pill.textContent = typedBuffer.length > 0 ? "跟打暂停 (按 Tab 聚焦)" : "待打字 (按 Tab 聚焦)";
    }
  });

  typingDisplayEl.addEventListener("scroll", () => {
    if (typingScrollTargetTop !== null && Math.abs(typingDisplayEl.scrollTop - typingScrollTargetTop) < 3) {
      typingScrollTargetTop = null;
    }
  }, { passive: true });

  typingDisplayEl.addEventListener("wheel", () => {
    typingScrollTargetTop = null;
  }, { passive: true });
}

const typingRepeatBtn = document.getElementById("typing-repeat-btn");
if (typingRepeatBtn) {
  updateTypingRepeatButton();
  typingRepeatBtn.addEventListener("click", () => {
    cycleTypingRepeatRounds();
  });
}

const typingWordSoundBtn = document.getElementById("typing-word-sound-btn");
if (typingWordSoundBtn) {
  typingWordSoundBtn.classList.toggle("active", isWordAudioEnabled);
  typingWordSoundBtn.title = isWordAudioEnabled
    ? "单词发音：每个要打的单词开始时自动朗读英文 (开启中，点击关闭)"
    : "单词发音：每个要打的单词开始时自动朗读英文 (已关闭，点击开启)";
  typingWordSoundBtn.addEventListener("click", () => {
    isWordAudioEnabled = !isWordAudioEnabled;
    localStorage.setItem("ai_coach_word_audio", isWordAudioEnabled ? "true" : "false");
    typingWordSoundBtn.classList.toggle("active", isWordAudioEnabled);
    typingWordSoundBtn.title = isWordAudioEnabled
      ? "单词发音：每个要打的单词开始时自动朗读英文 (开启中，点击关闭)"
      : "单词发音：每个要打的单词开始时自动朗读英文 (已关闭，点击开启)";
    showToast(isWordAudioEnabled ? "🔤 已开启单词英文发音 (Edge-TTS)" : "🔇 已关闭单词英文发音");
    if (isWordAudioEnabled && currentSentenceWords && currentSentenceWords.length > 0) {
      prefetchSentenceWords(currentSentenceWords);
    }
  });
}

const typingWordTransBtn = document.getElementById("typing-word-trans-btn");
if (typingWordTransBtn) {
  typingWordTransBtn.classList.toggle("active", isWordTranslationAudioEnabled);
  typingWordTransBtn.title = isWordTranslationAudioEnabled
    ? "单词释义：英文读完后自动朗读简短中文释义 (开启中，点击关闭)"
    : "单词释义：英文读完后自动朗读简短中文释义 (已关闭，点击开启)";
  typingWordTransBtn.addEventListener("click", () => {
    isWordTranslationAudioEnabled = !isWordTranslationAudioEnabled;
    localStorage.setItem("ai_coach_word_trans_audio", isWordTranslationAudioEnabled ? "true" : "false");
    typingWordTransBtn.classList.toggle("active", isWordTranslationAudioEnabled);
    typingWordTransBtn.title = isWordTranslationAudioEnabled
      ? "单词释义：英文读完后自动朗读简短中文释义 (开启中，点击关闭)"
      : "单词释义：英文读完后自动朗读简短中文释义 (已关闭，点击开启)";
    showToast(isWordTranslationAudioEnabled ? "中 已开启单词中文释义朗读 (英文读完接读中文)" : "🔇 已关闭单词中文释义朗读");
    if (isWordTranslationAudioEnabled && currentTypingTarget) {
      fetchSentenceWordGlosses(currentTypingTarget);
    }
  });
}

const typingOriginalMediaBtn = document.getElementById("typing-original-media-btn");
if (typingOriginalMediaBtn) {
  typingOriginalMediaBtn.addEventListener("click", () => {
    playSentenceAudioDirect(currentSentenceIndex, typingOriginalMediaBtn);
  });
}

const videoAutoplayBtn = document.getElementById("video-autoplay-btn");
if (videoAutoplayBtn) {
  videoAutoplayBtn.classList.toggle("active", isVideoAutoplayEnabled);
  videoAutoplayBtn.addEventListener("click", () => {
    isVideoAutoplayEnabled = !isVideoAutoplayEnabled;
    localStorage.setItem("ai_coach_video_autoplay", isVideoAutoplayEnabled ? "true" : "false");
    videoAutoplayBtn.classList.toggle("active", isVideoAutoplayEnabled);
    showToast(isVideoAutoplayEnabled ? "▶ 已开启跟打自动播放视频原声片段" : "⏸ 已关闭跟打自动播放视频片段");
  });
}

const videoPipBtn = document.getElementById("video-pip-btn");
if (videoPipBtn) {
  videoPipBtn.addEventListener("click", async () => {
    const videoEl = document.getElementById("document-video-element");
    if (!videoEl) return;
    try {
      if (document.pictureInPictureElement) {
        await document.exitPictureInPicture();
        videoPipBtn.classList.remove("active");
        videoPipBtn.textContent = "🔲 悬浮窗";
      } else if (document.pictureInPictureEnabled && !videoEl.disablePictureInPicture) {
        const wrapper = document.getElementById("video-viewport-wrapper");
        const wasCollapsed = wrapper && wrapper.classList.contains("collapsed");
        if (wasCollapsed) {
          wrapper.style.height = "1px";
          wrapper.style.opacity = "0.01";
        }
        if (!videoEl.src && currentDocHasMedia) {
          const documentId = (lectureDocSelect && lectureDocSelect.value) || lectureDocumentId;
          videoEl.src = currentDocMediaUrl || `/api/documents/${documentId}/media`;
          videoEl.load();
        }
        if (videoEl.readyState < 1) {
          await new Promise((resolve) => {
            const onMeta = () => { cleanup(); resolve(); };
            const timer = setTimeout(() => { cleanup(); resolve(); }, 1500);
            const cleanup = () => {
              clearTimeout(timer);
              videoEl.removeEventListener("loadedmetadata", onMeta);
              videoEl.removeEventListener("error", onMeta);
            };
            videoEl.addEventListener("loadedmetadata", onMeta);
            videoEl.addEventListener("error", onMeta);
          });
        }
        await videoEl.requestPictureInPicture();
        if (wasCollapsed) {
          wrapper.style.height = "";
          wrapper.style.opacity = "";
        }
        videoPipBtn.classList.add("active");
        videoPipBtn.textContent = "🔲 内嵌";
      } else {
        showToast("当前浏览器未开启画中画悬浮窗支持");
      }
    } catch (err) {
      console.warn("PiP error:", err);
      showToast("无法开启画中画悬浮窗");
    }
  });

  const mainVideoEl = document.getElementById("document-video-element");
  if (mainVideoEl) {
    mainVideoEl.addEventListener("enterpictureinpicture", () => {
      videoPipBtn.classList.add("active");
      videoPipBtn.textContent = "🔲 内嵌";
    });
    mainVideoEl.addEventListener("leavepictureinpicture", () => {
      videoPipBtn.classList.remove("active");
      videoPipBtn.textContent = "🔲 悬浮窗";
    });
  }
}

const videoReplayClipBtn = document.getElementById("video-replay-clip-btn");
if (videoReplayClipBtn) {
  videoReplayClipBtn.addEventListener("click", () => {
    playSentenceAudioDirect(currentSentenceIndex, videoReplayClipBtn);
  });
}

const videoSizeToggleBtn = document.getElementById("video-size-toggle-btn");
if (videoSizeToggleBtn) {
  videoSizeToggleBtn.addEventListener("click", () => {
    const vc = document.getElementById("video-player-container");
    if (vc) {
      vc.classList.toggle("expanded");
      videoSizeToggleBtn.textContent = vc.classList.contains("expanded") ? "📐 紧凑" : "📐 放大";
    }
  });
}

const videoVisibilityToggleBtn = document.getElementById("video-visibility-toggle-btn");
if (videoVisibilityToggleBtn) {
  videoVisibilityToggleBtn.addEventListener("click", () => {
    const wrapper = document.getElementById("video-viewport-wrapper");
    if (wrapper) {
      const isCurrentlyCollapsed = wrapper.classList.contains("collapsed");
      if (isCurrentlyCollapsed) {
        wrapper.classList.remove("collapsed");
        wrapper.classList.add("expanded");
        isLocalVideoForced = true;
        videoVisibilityToggleBtn.classList.add("active");
        videoVisibilityToggleBtn.textContent = "▲ 收起视口";
        showToast("🖥️ 已展开本地视口 (单屏模式)");
        const videoEl = document.getElementById("document-video-element");
        if (videoEl && currentDocMediaUrl && (!videoEl.src || !videoEl.src.includes(currentDocMediaUrl))) {
          videoEl.src = currentDocMediaUrl;
          videoEl.load();
        }
      } else {
        wrapper.classList.add("collapsed");
        wrapper.classList.remove("expanded");
        isLocalVideoForced = false;
        videoVisibilityToggleBtn.classList.remove("active");
        videoVisibilityToggleBtn.textContent = "🖥️ 本地视口";
        showToast("📺 已收起本地视口，维持紧凑同步条");
      }
    }
  });
}

const typingReplayBtn = document.getElementById("typing-replay-btn");
if (typingReplayBtn) {
  typingReplayBtn.addEventListener("click", () => {
    replayCurrentSentenceAudio();
  });
}

const typingLoopBtn = document.getElementById("typing-loop-btn");
if (typingLoopBtn) {
  typingLoopBtn.classList.toggle("active", isLoopPlayback);
  typingLoopBtn.addEventListener("click", () => {
    toggleLoopPlayback();
  });
}

const typingClearBtn = document.getElementById("typing-clear-btn");
if (typingClearBtn) {
  typingClearBtn.addEventListener("click", () => {
    setupShadowTyping(currentTypingTarget);
    const td = document.getElementById("typing-target-display");
    if (td) td.focus();
  });
}

// ==========================================================================
// Fullscreen Immersive Typing Mode (全屏沉浸跟打模式)
// ==========================================================================
let currentFsBgStyle = localStorage.getItem("ai_coach_fs_bg_style") || "frosted"; // "frosted" or "black"

function isTypingFullscreenActive() {
  const card = document.getElementById("sentence-focus-card");
  return card ? card.classList.contains("fullscreen-mode") : false;
}

function applyFsBgStyle() {
  const card = document.getElementById("sentence-focus-card");
  const bgBtn = document.getElementById("fs-bg-toggle-btn");
  if (!card) return;

  if (isTypingFullscreenActive()) {
    if (currentFsBgStyle === "black") {
      card.classList.remove("bg-frosted");
      card.classList.add("bg-black");
    } else {
      card.classList.remove("bg-black");
      card.classList.add("bg-frosted");
    }
  }
  if (bgBtn) {
    if (currentFsBgStyle === "black") {
      bgBtn.textContent = "🌑 深邃纯黑";
      bgBtn.title = "当前为深邃纯黑背景，点击切换为磨砂玻璃效果";
    } else {
      bgBtn.textContent = "✨ 磨砂玻璃";
      bgBtn.title = "当前为磨砂玻璃效果，点击切换为深邃纯黑背景";
    }
  }
}

function toggleFsBgStyle() {
  currentFsBgStyle = currentFsBgStyle === "frosted" ? "black" : "frosted";
  localStorage.setItem("ai_coach_fs_bg_style", currentFsBgStyle);
  applyFsBgStyle();
  showToast(currentFsBgStyle === "black" ? "🌑 已切换为深邃纯黑背景" : "✨ 已切换为磨砂玻璃背景");
}

function setTypingFullscreen(enable, triggerNative = true) {
  const card = document.getElementById("sentence-focus-card");
  const fsBtn = document.getElementById("typing-fullscreen-btn");
  const docTitleEl = document.getElementById("fs-doc-title");
  if (!card) return;

  if (enable) {
    card.classList.add("fullscreen-mode");
    document.body.classList.add("typing-fullscreen-active");
    applyFsBgStyle();

    if (fsBtn) {
      fsBtn.classList.add("active");
      fsBtn.title = "退出全屏模式 (快捷键: Esc / F10)";
      fsBtn.textContent = "✕";
    }
    if (docTitleEl) {
      const docName = getCurrentDocumentTitle();
      const totalSents = lectureSentences ? lectureSentences.length : 0;
      docTitleEl.textContent = `${docName} (P. ${lecturePage || 1} · 第 ${currentSentenceIndex} / ${totalSents} 句)`;
    }

    if (triggerNative && !document.fullscreenElement && card.requestFullscreen) {
      card.requestFullscreen().catch(() => {});
    }

    // Auto-focus typing display
    const typingDisplay = document.getElementById("typing-target-display");
    if (typingDisplay) {
      setTimeout(() => {
        typingDisplay.focus();
        requestScrollTypingTarget();
      }, 80);
    }
    try {
      syncChannel.postMessage({
        type: "typing_fullscreen_change",
        data: { active: true }
      });
      if (latestWhiteboardMarkdown) {
        syncChannel.postMessage({
          type: "notes_update",
          data: { notes: latestWhiteboardMarkdown }
        });
      }
    } catch (_) {}
    showToast("已进入全屏沉浸跟打模式 (按 Esc 或 F10 退出)");
  } else {
    card.classList.remove("fullscreen-mode");
    card.classList.remove("bg-frosted");
    card.classList.remove("bg-black");
    document.body.classList.remove("typing-fullscreen-active");
    if (fsBtn) {
      fsBtn.classList.remove("active");
      fsBtn.title = "全屏沉浸跟打模式 (快捷键: F10 / Esc 退出)";
      fsBtn.textContent = "⛶";
    }

    if (triggerNative && document.fullscreenElement && document.exitFullscreen) {
      document.exitFullscreen().catch(() => {});
    }

    // Preserve focus
    const typingDisplay = document.getElementById("typing-target-display");
    if (typingDisplay) {
      setTimeout(() => {
        typingDisplay.focus();
        requestScrollTypingTarget();
      }, 80);
    }
    try {
      syncChannel.postMessage({
        type: "typing_fullscreen_change",
        data: { active: false }
      });
    } catch (_) {}
  }
}

function toggleTypingFullscreen() {
  setTypingFullscreen(!isTypingFullscreenActive(), true);
}

function initTypingFullscreen() {
  const fsBtn = document.getElementById("typing-fullscreen-btn");
  if (fsBtn) {
    fsBtn.addEventListener("click", () => toggleTypingFullscreen());
  }

  const exitFsBtn = document.getElementById("exit-fullscreen-btn");
  if (exitFsBtn) {
    exitFsBtn.addEventListener("click", () => setTypingFullscreen(false, true));
  }

  const bgToggleBtn = document.getElementById("fs-bg-toggle-btn");
  if (bgToggleBtn) {
    bgToggleBtn.addEventListener("click", () => toggleFsBgStyle());
  }
  applyFsBgStyle();

  // Sentence Preview Box: Click to trigger LLM action, double click to toggle fullscreen
  const previewBox = document.getElementById("sentence-preview-box");
  if (previewBox) {
    previewBox.style.cursor = "pointer";
    previewBox.title = "点击发送给大模型精讲 (双击切换全屏跟打)";
    let clickTimer = null;
    previewBox.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      const selection = window.getSelection().toString();
      if (selection && selection.length > 0) return;
      if (clickTimer) clearTimeout(clickTimer);
      clickTimer = setTimeout(() => {
        executeCurrentActionMode();
      }, 220);
    });
    previewBox.addEventListener("dblclick", (e) => {
      if (e.target.closest("button")) return;
      if (clickTimer) {
        clearTimeout(clickTimer);
        clickTimer = null;
      }
      toggleTypingFullscreen();
    });
  }

  // Sync with browser native fullscreen exit (e.g. user pressed browser Esc)
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement && isTypingFullscreenActive()) {
      setTypingFullscreen(false, false);
    }
  });

  // Global shortcut (F10 to toggle, Escape to exit)
  document.addEventListener("keydown", (e) => {
    if (e.key === "F10") {
      e.preventDefault();
      toggleTypingFullscreen();
    } else if (e.key === "Escape" && isTypingFullscreenActive()) {
      e.preventDefault();
      setTypingFullscreen(false, true);
    }
  });

  // Recalculate typing target scroll alignment on window resize
  window.addEventListener("resize", () => {
    requestScrollTypingTarget();
  });
}

// Pronunciation Evaluation UI
function displayPronunciationResult(evalResult, broadcast = true) {
  const card = document.getElementById("pronunciation-card");
  const badge = document.getElementById("pron-score-badge");
  const feedback = document.getElementById("pron-feedback-text");
  const wordsBox = document.getElementById("pron-words-display");
  if (!card || !evalResult) return;

  const levelLabels = {
    "excellent": "卓越 🌟",
    "good": "良好 👍",
    "fair": "尚可 💪",
    "needs_work": "需提升 🎯"
  };

  card.style.display = "flex";
  if (badge) {
    badge.textContent = `${evalResult.score}分 · ${levelLabels[evalResult.level] || evalResult.level}`;
    badge.className = `pron-score-badge ${evalResult.level}`;
  }
  if (feedback) {
    feedback.textContent = evalResult.feedback || "";
  }
  if (wordsBox) {
    wordsBox.innerHTML = "";
    (evalResult.words || []).forEach(w => {
      const chip = document.createElement("span");
      chip.className = `word-chip ${w.status}`;
      chip.textContent = w.word;
      if (w.tip) {
        const tipEl = document.createElement("span");
        tipEl.className = "chip-tip";
        tipEl.textContent = w.tip;
        chip.appendChild(tipEl);
      }
      wordsBox.appendChild(chip);
    });
  }

  if (broadcast) {
    if (evalResult.score >= 90) {
      SoundEngine.playPerfectFanfare();
      ConfettiEngine.fireConfetti();
    }
    if (window.recordGameAction) {
      window.recordGameAction("pronunciation_evaluated", { score: evalResult.score }, card);
    }
    syncChannel.postMessage({
      type: "pronunciation_eval",
      data: evalResult
    });
  }
}

const pronRetryBtn = document.getElementById("pron-retry-btn");
if (pronRetryBtn) {
  pronRetryBtn.addEventListener("click", () => {
    const s = lectureSentences[currentSentenceIndex - 1];
    triggerSentenceAction(currentSentenceIndex, s ? s.text : "", "practice");
    startRecording();
  });
}

const pronListenBtn = document.getElementById("pron-listen-btn");
if (pronListenBtn) {
  pronListenBtn.addEventListener("click", () => {
    playSentenceAudioDirect(currentSentenceIndex, pronListenBtn);
  });
}

const pronSaveMistakeBtn = document.getElementById("pron-save-mistake-btn");
if (pronSaveMistakeBtn) {
  pronSaveMistakeBtn.addEventListener("click", async () => {
    const s = lectureSentences[currentSentenceIndex - 1];
    if (!s) return;
    try {
      const res = await fetch("/api/notes/mistake", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sentence_text: s.text,
          mistake_type: "pronunciation",
          mistake_detail: "学生手动加入重点跟读句",
          document_id: lectureDocumentId,
          page_number: lecturePage,
          sentence_index: s.index,
          voice_text: lastTurnVoice,
          notes_markdown: currentNotesMarkdown || `### 🎙️ 跟读重难点\n\n- **句子**: \`${s.text}\``
        })
      });
      if (res.ok) {
        showToast("⭐ 已成功加入重点生词与Anki闪卡包！");
      }
    } catch (e) {
      showToast("保存失败");
    }
  });
}

// Header Settings: Teaching Style & Action Mode Selectors
function setupHeaderSettings() {
  const styleSelect = document.getElementById("teaching-style-select");
  if (styleSelect) {
    styleSelect.value = currentTeachingStyle || "spoken";
    styleSelect.addEventListener("change", (e) => {
      setTeachingStyle(e.target.value);
    });
  }

  const modeSelect = document.getElementById("sentence-action-mode-select");
  if (modeSelect) {
    modeSelect.value = currentActionMode || "explain";
    modeSelect.addEventListener("change", (e) => {
      const newMode = e.target.value;
      if (isContinuousLecture && newMode !== "continuous" && newMode !== "continuous_read" && newMode !== "continuous_explain") {
        stopContinuousLecture();
      }
      setActionMode(newMode, false);
      if (newMode === "continuous_read" && !isContinuousLecture) {
        startContinuousLecture("read_only", currentSentenceIndex || 1);
      } else if ((newMode === "continuous_explain" || newMode === "continuous") && !isContinuousLecture) {
        startContinuousLecture("explain", currentSentenceIndex || 1);
      }
    });
  }

  const runBtn = document.getElementById("header-action-run-btn");
  if (runBtn && !runBtn.dataset.bound) {
    runBtn.dataset.bound = "true";
    runBtn.addEventListener("click", () => {
      executeCurrentActionMode();
    });
  }

  updateActionRunButton();
}

function setTeachingStyle(style) {
  currentTeachingStyle = style;
  localStorage.setItem("ai_coach_teaching_style", style);
  const styleSelect = document.getElementById("teaching-style-select");
  if (styleSelect && styleSelect.value !== style) {
    styleSelect.value = style;
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "set_teaching_style", style: style }));
  }
  syncChannel.postMessage({ type: "teaching_style", data: { style } });
  saveLearningProgress({ teaching_style: style });
  const names = {
    "spoken": "🗣️ 地道口语大师",
    "history": "🏛️ 经典文史精读",
    "ielts": "🎓 雅思学术进阶",
    "grammar": "🧩 零基础句法拆解",
    "drill": "⚡ 刷题速成教练"
  };
  showToast(`已切换教学风格: ${names[style] || style}`);
}

function setActionMode(mode, execute = false) {
  currentActionMode = mode;
  localStorage.setItem("ai_coach_action_mode", mode);
  const modeSelect = document.getElementById("sentence-action-mode-select");
  if (modeSelect && modeSelect.value !== mode) {
    modeSelect.value = mode;
  }
  document.querySelectorAll(".quick-mode-btn").forEach(b => {
    b.classList.toggle("active", b.dataset.mode === mode);
  });
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "set_action_mode", mode: mode }));
  }
  syncChannel.postMessage({ type: "action_mode", data: { mode } });
  updateActionRunButton();
  if (execute) {
    executeCurrentActionMode();
  }
}

function updateActionRunButton() {
  const runBtn = document.getElementById("header-action-run-btn");
  if (!runBtn) return;
  if (isContinuousLecture) {
    const isPure = continuousAction === "read_only" || currentActionMode === "continuous_read" || currentActionMode === "read_only";
    runBtn.innerHTML = isPure ? "⏹ 停读" : "⏹ 停讲";
    runBtn.classList.add("running");
    runBtn.title = isPure ? "点击停止连续整页纯读" : "点击停止连续自动精讲整页";
  } else {
    runBtn.classList.remove("running");
    const labels = {
      "explain": { icon: "📖", text: "精讲" },
      "read_only": { icon: "🔊", text: "纯读" },
      "practice": { icon: "🎙️", text: "跟读" },
      "continuous_read": { icon: "🔁", text: "连读" },
      "continuous_explain": { icon: "▶️", text: "连讲" },
      "continuous": { icon: "▶️", text: "连讲" }
    };
    const cfg = labels[currentActionMode] || labels["explain"];
    runBtn.innerHTML = `${cfg.icon} ${cfg.text}`;
    runBtn.title = `执行当前模式: ${cfg.text}`;
  }
}

function executeCurrentActionMode() {
  const mode = currentActionMode || "explain";
  const s = lectureSentences[currentSentenceIndex - 1];
  if (!s && mode !== "continuous" && mode !== "continuous_read" && mode !== "continuous_explain") {
    showToast("请先选择句子");
    return;
  }
  const runBtn = document.getElementById("header-action-run-btn");
  if (mode === "explain") {
    triggerSentenceAction(currentSentenceIndex, s ? s.text : "", "explain");
  } else if (mode === "read_only") {
    triggerSentenceAction(currentSentenceIndex, s ? s.text : "", "read_only");
    if (window.recordGameAction) window.recordGameAction("sentence_read");
  } else if (mode === "practice") {
    triggerSentenceAction(currentSentenceIndex, s ? s.text : "", "practice");
  } else if (mode === "continuous_read") {
    toggleContinuousLecture("read_only", currentSentenceIndex || 1);
  } else if (mode === "continuous_explain" || mode === "continuous") {
    toggleContinuousLecture("explain", currentSentenceIndex || 1);
  }
}

// Custom Document Drag-and-Drop & Upload
function setupDocumentUpload() {
  const uploadBtn = document.getElementById("upload-doc-btn");
  const fileInput = document.getElementById("doc-file-input");
  const previewBox = document.querySelector(".lesson-preview");

  if (uploadBtn && fileInput) {
    uploadBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", (e) => {
      if (e.target.files && e.target.files.length > 0) {
        handlePdfUpload(e.target.files[0]);
      }
      fileInput.value = "";
    });
  }

  if (previewBox) {
    previewBox.addEventListener("dragover", (e) => {
      e.preventDefault();
      previewBox.classList.add("drag-over");
    });
    previewBox.addEventListener("dragleave", () => {
      previewBox.classList.remove("drag-over");
    });
    previewBox.addEventListener("drop", (e) => {
      e.preventDefault();
      previewBox.classList.remove("drag-over");
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        handlePdfUpload(e.dataTransfer.files[0]);
      }
    });
  }
}

async function handlePdfUpload(file) {
  if (!file || !file.name.toLowerCase().endsWith(".pdf")) {
    showToast("⚠️ 请选择标准的 .pdf 文件");
    return;
  }
  showToast(`⏳ 正在上传并解析《${file.name}》...`);
  const formData = new FormData();
  formData.append("file", file);

  try {
    const res = await fetch("/api/documents/upload", {
      method: "POST",
      body: formData
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "上传失败");
    showToast(`🎉 教材《${data.document.title}》上传成功！`);
    await loadLectureDocuments();
    lectureDocSelect.value = data.document.id;
    lecturePage = 1;
    lecturePageInput.value = 1;
    await loadLectureUnits(data.document.id);
    await loadLecturePage(1);
    syncChannel.postMessage({
      type: "document_uploaded",
      data: { id: data.document.id, title: data.document.title }
    });
  } catch (err) {
    console.error("PDF upload error:", err);
    showToast(`❌ 上传失败: ${err.message}`);
  }
}

// Web Article URL / Direct Text Import
function setupUrlImport() {
  const importBtn = document.getElementById("import-url-btn");
  const modal = document.getElementById("url-import-modal");
  const closeBtn = document.getElementById("url-import-close-btn");
  const cancelBtn = document.getElementById("url-import-cancel-btn");
  const form = document.getElementById("url-import-form");
  const urlInput = document.getElementById("import-url-input");
  const titleInput = document.getElementById("import-title-input");
  const rawTextInput = document.getElementById("import-raw-text-input");
  const statusEl = document.getElementById("url-import-status");
  const submitBtn = document.getElementById("url-import-submit-btn");

  if (!importBtn || !modal) return;

  const openModal = () => {
    modal.style.display = "flex";
    if (statusEl) statusEl.textContent = "";
    if (urlInput) {
      setTimeout(() => {
        urlInput.focus();
        urlInput.select();
      }, 50);
    }
  };

  const closeModal = () => {
    modal.style.display = "none";
    if (statusEl) statusEl.textContent = "";
  };

  importBtn.addEventListener("click", openModal);
  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  if (cancelBtn) cancelBtn.addEventListener("click", closeModal);

  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const url = urlInput ? urlInput.value.trim() : "";
      const customTitle = titleInput ? titleInput.value.trim() : "";
      const rawText = rawTextInput ? rawTextInput.value.trim() : "";

      if (!url && !rawText) {
        if (statusEl) {
          statusEl.style.color = "var(--red, #ef4444)";
          statusEl.textContent = "⚠️ 请输入网页网址或粘贴文章正文";
        }
        return;
      }

      const isYt = /(?:youtube\.com|youtu\.be)/i.test(url);
      if (submitBtn) submitBtn.disabled = true;
      if (statusEl) {
        statusEl.style.color = "var(--blue, #38bdf8)";
        statusEl.textContent = isYt
          ? "⏳ 正在通过 yt-dlp 抓取 YouTube 英文原声字幕并智能断句排版，请稍候..."
          : "⏳ 正在抓取正文并排版生成教材，请稍候...";
      }

      try {
        const res = await fetch("/api/documents/import-url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            url: url || null,
            title: customTitle || null,
            raw_text: rawText || null,
          }),
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "导入失败");

        closeModal();
        if (urlInput) urlInput.value = "";
        if (titleInput) titleInput.value = "";
        if (rawTextInput) rawTextInput.value = "";

        const toastMsg = isYt
          ? `🎉 YouTube 视频《${data.document.title}》字幕教材导入成功！已就绪`
          : `🎉 网页文章《${data.document.title}》导入成功！已就绪`;
        showToast(toastMsg);
        await loadLectureDocuments();
        lectureDocSelect.value = data.document.id;
        lecturePage = 1;
        lecturePageInput.value = 1;
        await loadLectureUnits(data.document.id);
        await loadLecturePage(1);

        syncChannel.postMessage({
          type: "document_uploaded",
          data: { id: data.document.id, title: data.document.title },
        });
      } catch (err) {
        console.error("URL import error:", err);
        const errMsg = err.message || "导入失败";
        if (statusEl) {
          statusEl.style.color = "var(--red, #ef4444)";
          statusEl.textContent = `❌ 导入失败: ${errMsg}`;
        }
        const firstLine = errMsg.split("\n")[0].trim();
        showToast(`❌ 导入失败: ${firstLine}`);
        const rawDetails = document.getElementById("url-import-raw-details");
        if (rawDetails && (errMsg.includes("防机器人") || errMsg.includes("bot") || errMsg.includes("Cookie"))) {
          rawDetails.open = true;
          if (rawTextInput) rawTextInput.focus();
        }
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }
}

// AI Custom Practice Generator Modal & Workflow
function setupAiPracticeModal() {
  const openBtn = document.getElementById("ai-practice-btn");
  const fsOpenBtn = document.getElementById("fs-ai-practice-btn");
  const modal = document.getElementById("ai-practice-modal");
  const closeBtn = document.getElementById("ai-practice-close-btn");
  const cancelBtn = document.getElementById("ai-practice-cancel-btn");
  const form = document.getElementById("ai-practice-form");
  const promptInput = document.getElementById("ai-practice-prompt-input");
  const titleInput = document.getElementById("ai-practice-title-input");
  const statusEl = document.getElementById("ai-practice-status");
  const submitBtn = document.getElementById("ai-practice-submit-btn");
  const presetContainer = document.getElementById("ai-practice-preset-container");
  const countGroup = document.getElementById("ai-practice-count-group");
  const diffGroup = document.getElementById("ai-practice-diff-group");

  if (!modal) return;

  // Fetch and render presets
  const renderPresets = async () => {
    if (!presetContainer || presetContainer.children.length > 0) return;
    try {
      const res = await fetch("/api/ai-practice/presets");
      const data = await res.json();
      if (data && data.presets && Array.isArray(data.presets)) {
        presetContainer.innerHTML = "";
        data.presets.forEach(p => {
          const chip = document.createElement("button");
          chip.type = "button";
          chip.className = "preset-chip";
          chip.innerHTML = `<span>${escapeHtml(p.icon)}</span> <span>${escapeHtml(p.label)}</span>`;
          chip.title = p.prompt;
          chip.addEventListener("click", () => {
            if (promptInput) {
              promptInput.value = p.prompt;
              promptInput.focus();
            }
            // Sync difficulty if defined
            if (p.default_difficulty && diffGroup) {
              const targetRadio = diffGroup.querySelector(`input[value="${p.default_difficulty}"]`);
              if (targetRadio) {
                targetRadio.checked = true;
                diffGroup.querySelectorAll(".radio-chip").forEach(c => c.classList.remove("active"));
                targetRadio.closest(".radio-chip")?.classList.add("active");
              }
            }
            // Sync count if defined
            if (p.default_count && countGroup) {
              const targetRadio = countGroup.querySelector(`input[value="${p.default_count}"]`);
              if (targetRadio) {
                targetRadio.checked = true;
                countGroup.querySelectorAll(".radio-chip").forEach(c => c.classList.remove("active"));
                targetRadio.closest(".radio-chip")?.classList.add("active");
              }
            }
          });
          presetContainer.appendChild(chip);
        });
      }
    } catch (e) {
      console.warn("Failed to load AI practice presets:", e);
    }
  };

  // Radio chips toggle behavior
  const setupRadioChips = (container) => {
    if (!container) return;
    const chips = container.querySelectorAll(".radio-chip");
    chips.forEach(chip => {
      const radio = chip.querySelector("input[type='radio']");
      if (radio) {
        chip.addEventListener("click", () => {
          chips.forEach(c => c.classList.remove("active"));
          chip.classList.add("active");
          radio.checked = true;
        });
      }
    });
  };
  setupRadioChips(countGroup);
  setupRadioChips(diffGroup);

  const openModal = () => {
    modal.style.display = "flex";
    if (statusEl) statusEl.textContent = "";
    renderPresets();
    if (promptInput) {
      setTimeout(() => {
        promptInput.focus();
        promptInput.select();
      }, 50);
    }
  };

  const closeModal = () => {
    modal.style.display = "none";
    if (statusEl) statusEl.textContent = "";
  };

  if (openBtn) openBtn.addEventListener("click", openModal);
  if (fsOpenBtn) fsOpenBtn.addEventListener("click", openModal);
  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  if (cancelBtn) cancelBtn.addEventListener("click", closeModal);

  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const prompt = promptInput ? promptInput.value.trim() : "";
      const customTitle = titleInput ? titleInput.value.trim() : "";

      const checkedCount = countGroup ? countGroup.querySelector("input[type='radio']:checked") : null;
      const count = checkedCount ? parseInt(checkedCount.value, 10) : 8;

      const checkedDiff = diffGroup ? diffGroup.querySelector("input[type='radio']:checked") : null;
      const difficulty = checkedDiff ? checkedDiff.value : "intermediate";

      if (!prompt) {
        if (statusEl) {
          statusEl.style.color = "var(--red, #ef4444)";
          statusEl.textContent = "⚠️ 请输入您想要练习的主题、语法点或应用场景（也可直接点击上方预设灵感）";
        }
        if (promptInput) promptInput.focus();
        return;
      }

      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.style.opacity = "0.7";
      }
      if (statusEl) {
        statusEl.style.color = "var(--blue, #38bdf8)";
        statusEl.textContent = "🤖 本地大模型正在构思高品质专属例句与语法考点，请稍候...";
      }

      try {
        const res = await fetch("/api/ai-practice/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt: prompt,
            count: count,
            difficulty: difficulty,
            title: customTitle || null,
          }),
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "生成练习失败");

        closeModal();
        if (promptInput) promptInput.value = "";
        if (titleInput) titleInput.value = "";

        showToast(`🎉 AI 定制练习《${data.title}》已生成！共 ${data.sentences ? data.sentences.length : count} 句`);
        await loadLectureDocuments();
        await switchDocument(data.document.id, 1, 1);

        // Pre-focus typing display so user can type immediately
        const typingDisplay = document.getElementById("typing-target-display");
        if (typingDisplay) {
          setTimeout(() => typingDisplay.focus(), 150);
        }

        syncChannel.postMessage({
          type: "document_uploaded",
          data: { id: data.document.id, title: data.document.title },
        });
      } catch (err) {
        console.error("AI practice generation error:", err);
        if (statusEl) {
          statusEl.style.color = "var(--red, #ef4444)";
          statusEl.textContent = `❌ ${err.message}`;
        }
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.style.opacity = "1";
        }
      }
    });
  }
}

// Global shortcuts:
// - Left/Right (ArrowLeft/ArrowRight, PageUp/PageDown): Previous/Next Page
// - Up/Down (ArrowUp/ArrowDown): Previous/Next Sentence
// - Tab: switch between typing and dialogue input
// - Alt+R: replay sentence
// - Alt+L: toggle loop
window.addEventListener("keydown", (e) => {
  // Directional navigation shortcuts (page flipping and sentence navigation)
  if (!e.altKey && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
    const isNavKey = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "PageUp", "PageDown"].includes(e.key);
    if (isNavKey) {
      // If a select dropdown or button is currently focused, blur it immediately
      // so native browser option cycling or button menu navigation NEVER gets triggered
      if (document.activeElement && (document.activeElement.tagName === "SELECT" || document.activeElement.tagName === "BUTTON")) {
        try { document.activeElement.blur(); } catch (_) {}
      }

      if (!isTextEditingContext(document.activeElement)) {
        e.preventDefault();
        e.stopPropagation();

        if (e.key === "ArrowLeft" || e.key === "PageUp") {
          if (!e.repeat) navigatePage(-1);
          return;
        }
        if (e.key === "ArrowRight" || e.key === "PageDown") {
          if (!e.repeat) navigatePage(1);
          return;
        }
        if (e.key === "ArrowUp") {
          if (!e.repeat) navigateSentence(-1);
          return;
        }
        if (e.key === "ArrowDown") {
          if (!e.repeat) navigateSentence(1);
          return;
        }
      }
    }
  }

  if (e.key === "Tab" && !e.altKey && !e.ctrlKey && !e.metaKey) {
    if (switchBetweenOralAndTyping(e)) {
      return;
    }
  }
  // Alt+R: Replay current sentence audio (global)
  if (e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "r" || e.key === "R" || e.code === "KeyR")) {
    e.preventDefault();
    replayCurrentSentenceAudio();
    return;
  }
  // Alt+V: Play current sentence video clip (global)
  if (e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "v" || e.key === "V" || e.code === "KeyV")) {
    e.preventDefault();
    const origBtn = document.getElementById("typing-original-media-btn") || document.getElementById("video-replay-clip-btn");
    playSentenceAudioDirect(currentSentenceIndex, origBtn);
    showToast(currentDocHasMedia ? "🎬 播放视频原声片段 (Alt+V)" : "🔊 播放原句读音 (Alt+V)");
    return;
  }
  // Alt+L: Toggle single sentence loop playback (global)
  if (e.altKey && !e.ctrlKey && !e.metaKey && (e.key === "l" || e.key === "L" || e.code === "KeyL")) {
    e.preventDefault();
    toggleLoopPlayback();
    return;
  }
}, true);

// Notes actions
if (generateNotesBtn) {
  generateNotesBtn.addEventListener("click", () => {
    const s = (typeof lectureSentences !== "undefined" && lectureSentences && currentSentenceIndex) ? lectureSentences[currentSentenceIndex - 1] : null;
    const text = s ? s.text : "";
    if (!text) {
      showToast("请先选择或载入句子");
      return;
    }
    const trans = sentenceTranslations[text] || (s ? s.translation : "") || "";
    fetchSentenceSmartNotes(text, trans, true);
  });
}
saveNotesBtn.addEventListener("click", saveCurrentNotes);
copyNotesBtn.addEventListener("click", copyCurrentNotes);
exportNotesBtn.addEventListener("click", exportNotes);
notesLibraryBtn.addEventListener("click", openNotesModal);
const appExitBtn = document.getElementById("app-exit-btn");
if (appExitBtn) {
  appExitBtn.addEventListener("click", () => {
    if (confirm("确定要完全退出「AI 英语私教」并停止后台服务吗？\n（退出后将释放系统内存与端口）")) {
      showToast("🛑 正在完全退出并关闭服务…");
      fetch("/api/system/shutdown", { method: "POST" })
        .then(() => {
          setTimeout(() => {
            window.close();
            document.body.innerHTML = '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;background:#090d16;color:#94a3b8;font-family:sans-serif;text-align:center;"><h2>🛑 AI 英语私教已完全退出</h2><p style="margin-top:8px;">后台服务已停止。您可以直接关闭此窗口。</p></div>';
          }, 400);
        })
        .catch(() => {
          window.close();
        });
    }
  });
}
modalCloseBtn.addEventListener("click", closeNotesModal);
modalExportBtn.addEventListener("click", exportNotes);

const modalExportAnkiBtn = document.getElementById("modal-export-anki-btn");
if (modalExportAnkiBtn) {
  modalExportAnkiBtn.addEventListener("click", exportAnkiCards);
}

const modalFilterMistakes = document.getElementById("modal-filter-mistakes");
if (modalFilterMistakes) {
  modalFilterMistakes.addEventListener("change", () => {
    openNotesModal();
  });
}

// Close modal when clicking backdrop
notesModal.addEventListener("click", (e) => {
  if (e.target === notesModal) closeNotesModal();
});

lectureLoadBtn.addEventListener("click", () => loadLecturePage(lecturePageInput.value, 1));
lecturePrevBtn.addEventListener("click", () => navigatePage(-1));
lectureNextBtn.addEventListener("click", () => navigatePage(1));
lectureExplainBtn.addEventListener("click", () => requestLectureAction("explain"));
lecturePracticeBtn.addEventListener("click", () => requestLectureAction("practice"));
lectureExitBtn.addEventListener("click", () => {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "clear_lecture_context" }));
  }
});
async function switchDocument(docId, targetPage = null, targetSentence = null) {
  if (!docId) return;
  if (lectureDocSelect) lectureDocSelect.value = docId;
  if (docId === "west_civ") {
    setTeachingStyle("history");
  }

  const localSavedPage = parseInt(localStorage.getItem(`ai_coach_page_${docId}`), 10);
  const localSavedSent = parseInt(localStorage.getItem(`ai_coach_sent_${docId}`), 10);

  if (targetPage === null || targetPage === undefined) {
    if (Number.isInteger(localSavedPage) && localSavedPage >= 1) {
      targetPage = localSavedPage;
    } else if (learningProgress && learningProgress.books && learningProgress.books[docId]) {
      targetPage = learningProgress.books[docId].last_page;
    }
    if (!targetPage) {
      targetPage = docId === "west_civ" ? 38 : (docId === "vocabulary" || docId === "grammar" ? 9 : 1);
    }
  }

  if (targetSentence === null || targetSentence === undefined) {
    if (Number.isInteger(localSavedSent) && localSavedSent >= 1) {
      targetSentence = localSavedSent;
    } else if (learningProgress && learningProgress.books && learningProgress.books[docId]) {
      targetSentence = learningProgress.books[docId].last_sentence_index;
    }
    if (!targetSentence) {
      targetSentence = 1;
    }
  }

  lecturePage = targetPage;
  if (lecturePageInput) lecturePageInput.value = targetPage;
  lectureTotalPages = 0;
  await loadLectureUnits(docId);
  const currentUnit = [...lectureUnits].reverse().find(unit => unit.page <= targetPage);
  if (lectureUnitSelect) lectureUnitSelect.value = currentUnit ? String(currentUnit.page) : "";
  await loadLecturePage(targetPage, targetSentence);
}

lectureDocSelect.addEventListener("change", async () => {
  await switchDocument(lectureDocSelect.value);
});
lectureUnitSelect.addEventListener("change", () => {
  if (lectureUnitSelect.value) loadLecturePage(lectureUnitSelect.value, 1);
});
lecturePageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadLecturePage(lecturePageInput.value, 1);
});

// Populate voices dynamically
async function loadVoices() {
  try {
    const res = await fetch("/api/voices");
    const data = await res.json();
    let current = localStorage.getItem("ai_coach_voice");
    if (!current || current === "af_maple" || current === "bf_vale" || current.startsWith("zf_") || current.startsWith("zm_")) {
      current = "en-US-JennyNeural";
      localStorage.setItem("ai_coach_voice", current);
    }

    if (data.catalog && Array.isArray(data.catalog)) {
      voiceSelect.innerHTML = "";

      const edgeGroup = document.createElement("optgroup");
      edgeGroup.label = "🌟 微软高拟真双语音色 (中英混读极佳 · 推荐)";
      const kokoroGroup = document.createElement("optgroup");
      kokoroGroup.label = "📦 本地离线音色 (Kokoro-82M 备用)";

      for (const item of data.catalog) {
        const opt = document.createElement("option");
        opt.value = item.id;
        opt.textContent = item.name;
        if (item.id === current || (!current && item.id === data.default)) {
          opt.selected = true;
        }
        if (item.engine === "edge") {
          edgeGroup.appendChild(opt);
        } else {
          kokoroGroup.appendChild(opt);
        }
      }
      if (edgeGroup.children.length > 0) voiceSelect.appendChild(edgeGroup);
      if (kokoroGroup.children.length > 0) voiceSelect.appendChild(kokoroGroup);
    } else if (data.voices && Array.isArray(data.voices)) {
      const friendlyVoices = {
        "af_maple": "Maple · 美音双语 (自然地道)",
        "af_sol": "Sol · 美音双语 (活力清澈)",
        "bf_vale": "Vale · 英音双语 (严谨标准)",
        "zf_001": "Chinese Female 001 (中文女声)",
        "zf_002": "Chinese Female 002 (中文女声)",
        "zm_009": "Chinese Male 009 (中文男声)",
        "zm_010": "Chinese Male 010 (中文男声)"
      };
      voiceSelect.innerHTML = "";
      for (const v of data.voices) {
        const label = friendlyVoices[v] ||
          (v.startsWith("zf_") ? `Chinese Female ${v.slice(3)} (中文女声)` :
           v.startsWith("zm_") ? `Chinese Male ${v.slice(3)} (中文男声)` : v);
        const opt = document.createElement("option");
        opt.value = v;
        opt.textContent = label;
        if (v === current || (!current && v === data.default)) opt.selected = true;
        voiceSelect.appendChild(opt);
      }
    }

    // Restore saved speech speed
    const savedSpeed = localStorage.getItem("ai_coach_speed");
    if (savedSpeed && speedSlider && speedVal) {
      speedSlider.value = savedSpeed;
      speedVal.textContent = `${savedSpeed}x`;
    }
  } catch (err) {
    console.warn("Could not fetch voices:", err);
  }
}

// Theme Management (Dark & Light Dual Mode)
function applyTheme(theme, broadcast = true) {
  document.documentElement.setAttribute("data-theme", theme);
  document.body.setAttribute("data-theme", theme);
  localStorage.setItem("ai_coach_theme", theme);
  const themeIcon = document.getElementById("theme-icon");
  const themeText = document.getElementById("theme-text");
  if (themeIcon) themeIcon.textContent = theme === "dark" ? "🌙" : "☀️";
  if (themeText) themeText.textContent = theme === "dark" ? "深色模式" : "浅色模式";
  if (broadcast) {
    try {
      syncChannel.postMessage({ type: "theme_changed", data: { theme } });
    } catch (_) {}
  }
}

function initThemeToggle() {
  const savedTheme = localStorage.getItem("ai_coach_theme") || "dark";
  applyTheme(savedTheme, false);
  const toggleBtn = document.getElementById("theme-toggle-btn");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "dark";
      const nextTheme = current === "dark" ? "light" : "dark";
      applyTheme(nextTheme, true);
      showToast(`已切换至: ${nextTheme === "dark" ? "炫酷深色模式 🌙" : "清爽浅色模式 ☀️"}`);
    });
  }
}

// 3-Column & Sub-panel Draggable Layout Management
function initResizableLayout() {
  const colLeft = document.getElementById("col-left");
  const colRight = document.getElementById("col-right");
  const resizerLeftCenter = document.getElementById("resizer-left-center");
  const resizerCenterRight = document.getElementById("resizer-center-right");

  const colLeftTop = document.getElementById("col-left-top");
  const resizerLeftV = document.getElementById("resizer-left-v");

  const colCenterTop = document.getElementById("col-center-top");
  const resizerCenterV = document.getElementById("resizer-center-v");

  const container = document.getElementById("workspace-container");

  // Restore saved column widths
  const savedLeftW = parseInt(localStorage.getItem("ai_coach_col_left_w"), 10);
  if (savedLeftW && colLeft && savedLeftW >= 220 && savedLeftW <= 650) {
    colLeft.style.width = savedLeftW + "px";
  }

  const isCollapsedInitial = container && container.classList.contains("sidebar-collapsed");
  if (isCollapsedInitial) {
    const savedCollapsedW = parseInt(localStorage.getItem("ai_coach_col_right_w_collapsed"), 10);
    const containerW = container ? container.clientWidth : window.innerWidth;
    const defaultCollapsedW = Math.max(380, Math.min(Math.round(containerW * 0.46), containerW - 360));
    if (colRight) colRight.style.width = (savedCollapsedW && savedCollapsedW >= 260 ? savedCollapsedW : defaultCollapsedW) + "px";
  } else {
    const savedRightW = parseInt(localStorage.getItem("ai_coach_col_right_w"), 10);
    if (savedRightW && colRight && savedRightW >= 240 && savedRightW <= 850) {
      colRight.style.width = savedRightW + "px";
    }
  }

  // Restore saved sub-panel heights
  const savedLeftTopH = parseInt(localStorage.getItem("ai_coach_left_top_h"), 10);
  if (savedLeftTopH && colLeftTop && savedLeftTopH >= 100 && savedLeftTopH <= 700) {
    colLeftTop.style.height = savedLeftTopH + "px";
  }

  const savedCenterTopH = parseInt(localStorage.getItem("ai_coach_center_top_h"), 10);
  if (savedCenterTopH && colCenterTop && savedCenterTopH >= 160 && savedCenterTopH <= 800) {
    colCenterTop.style.height = savedCenterTopH + "px";
  }

  // Generic Pointer Drag Handler
  function setupDrag({ resizer, isHorizontal, onDrag, onEnd }) {
    if (!resizer) return;
    let isDragging = false;

    resizer.addEventListener("pointerdown", (e) => {
      isDragging = true;
      resizer.classList.add("active");
      document.body.classList.add("is-resizing");
      document.body.classList.add(isHorizontal ? "is-resizing-h" : "is-resizing-v");
      resizer.setPointerCapture(e.pointerId);
      e.preventDefault();
    });

    resizer.addEventListener("pointermove", (e) => {
      if (!isDragging) return;
      onDrag(e);
    });

    const finishDrag = (e) => {
      if (!isDragging) return;
      isDragging = false;
      resizer.classList.remove("active");
      document.body.classList.remove("is-resizing", "is-resizing-h", "is-resizing-v");
      try { resizer.releasePointerCapture(e.pointerId); } catch (_) {}
      if (onEnd) onEnd();
    };

    resizer.addEventListener("pointerup", finishDrag);
    resizer.addEventListener("pointercancel", finishDrag);
  }

  // 1. Horizontal Splitter: Left & Center Columns
  if (resizerLeftCenter && colLeft && container) {
    setupDrag({
      resizer: resizerLeftCenter,
      isHorizontal: true,
      onDrag: (e) => {
        const containerRect = container.getBoundingClientRect();
        let newWidth = e.clientX - containerRect.left;
        const maxW = Math.min(containerRect.width - 620, 650);
        newWidth = Math.max(220, Math.min(newWidth, maxW));
        colLeft.style.width = newWidth + "px";
      },
      onEnd: () => {
        const w = parseInt(colLeft.style.width, 10);
        if (w) localStorage.setItem("ai_coach_col_left_w", w);
      }
    });
  }

  // 2. Horizontal Splitter: Center & Right Columns
  if (resizerCenterRight && colRight && container) {
    setupDrag({
      resizer: resizerCenterRight,
      isHorizontal: true,
      onDrag: (e) => {
        const containerRect = container.getBoundingClientRect();
        let newWidth = containerRect.right - e.clientX;
        const isCollapsedNow = container.classList.contains("sidebar-collapsed");
        const minCenterW = 340;
        const maxW = isCollapsedNow ? Math.max(300, containerRect.width - minCenterW) : Math.min(containerRect.width - 600, 850);
        newWidth = Math.max(260, Math.min(newWidth, maxW));
        colRight.style.width = newWidth + "px";
      },
      onEnd: () => {
        const w = parseInt(colRight.style.width, 10);
        if (w) {
          const isCollapsedNow = container.classList.contains("sidebar-collapsed");
          if (isCollapsedNow) {
            localStorage.setItem("ai_coach_col_right_w_collapsed", w);
          } else {
            localStorage.setItem("ai_coach_col_right_w", w);
          }
        }
      }
    });
  }

  // 3. Vertical Splitter: Left Column Top/Bottom Subpanels
  if (resizerLeftV && colLeftTop && colLeft) {
    setupDrag({
      resizer: resizerLeftV,
      isHorizontal: false,
      onDrag: (e) => {
        const colRect = colLeft.getBoundingClientRect();
        let newHeight = e.clientY - colRect.top;
        const maxH = colRect.height - 140;
        newHeight = Math.max(100, Math.min(newHeight, maxH));
        colLeftTop.style.height = newHeight + "px";
      },
      onEnd: () => {
        const h = parseInt(colLeftTop.style.height, 10);
        if (h) localStorage.setItem("ai_coach_left_top_h", h);
      }
    });
  }

  // 4. Vertical Splitter: Center Column Top/Bottom Subpanels
  const colCenter = document.getElementById("col-center");
  if (resizerCenterV && colCenterTop && colCenter) {
    setupDrag({
      resizer: resizerCenterV,
      isHorizontal: false,
      onDrag: (e) => {
        const colRect = colCenter.getBoundingClientRect();
        let newHeight = e.clientY - colRect.top;
        const maxH = colRect.height - 200;
        newHeight = Math.max(160, Math.min(newHeight, maxH));
        colCenterTop.style.height = newHeight + "px";
      },
      onEnd: () => {
        const h = parseInt(colCenterTop.style.height, 10);
        if (h) localStorage.setItem("ai_coach_center_top_h", h);
      }
    });
  }
}

// Main Window PDF Page Image Zoom Tools
function setupMainImageViewer() {
  let zoomLevel = 1.0;
  const zoomInBtn = document.getElementById("zoom-in-btn-main");
  const zoomOutBtn = document.getElementById("zoom-out-btn-main");
  const zoomFitBtn = document.getElementById("zoom-fit-btn-main");
  const img = document.getElementById("lecture-page-image");
  const wrapper = document.getElementById("pdf-page-wrapper");
  const target = wrapper || img;

  if (!target) return;

  function updateZoom() {
    target.style.transform = `scale(${zoomLevel})`;
    target.style.transformOrigin = "top center";
  }

  if (zoomInBtn) {
    zoomInBtn.addEventListener("click", () => {
      zoomLevel = Math.min(2.5, +(zoomLevel + 0.15).toFixed(2));
      updateZoom();
    });
  }
  if (zoomOutBtn) {
    zoomOutBtn.addEventListener("click", () => {
      zoomLevel = Math.max(0.5, +(zoomLevel - 0.15).toFixed(2));
      updateZoom();
    });
  }
  if (zoomFitBtn) {
    zoomFitBtn.addEventListener("click", () => {
      zoomLevel = 1.0;
      updateZoom();
    });
  }
}

// ==========================================================================
// Automotive Odometer Engine (汽车仪表盘总里程表引擎)
// Tracks every single typed word, persists to SQLite, provides rolling drum visual
// ==========================================================================

const OdometerEngine = {
  totalWords: 0,
  todayWords: 0,
  uniqueWords: 0,
  pendingWords: [],
  flushTimer: null,
  isFlushing: false,

  init(stats) {
    if (stats && stats.words_typed !== undefined) {
      this.totalWords = Number(stats.words_typed) || 0;
      this.todayWords = Number(stats.today_words) || 0;
      this.uniqueWords = Number(stats.unique_words) || 0;
    }
    this.updateDisplays(false);
  },

  setLifetimeWords(total, today, unique) {
    if (total !== undefined && total !== null) {
      const num = Number(total);
      if (!isNaN(num) && num >= this.totalWords) {
        this.totalWords = num;
      }
    }
    if (today !== undefined && today !== null) {
      const numToday = Number(today);
      if (!isNaN(numToday) && numToday >= this.todayWords) {
        this.todayWords = numToday;
      }
    }
    if (unique !== undefined && unique !== null) {
      const numUnique = Number(unique);
      if (!isNaN(numUnique)) {
        this.uniqueWords = numUnique;
      }
    }
    this.updateDisplays(false);
  },

  recordWord(cleanWord) {
    if (!cleanWord || typeof cleanWord !== "string") return;
    const trimmed = cleanWord.trim();
    if (!trimmed) return;

    // 1. Instant local increment & visual tick animation
    this.totalWords++;
    this.todayWords++;
    this.updateDisplays(true);

    // 2. Queue for persistence
    const docId = typeof currentDocumentId !== "undefined" ? currentDocumentId : null;
    const sentIdx = typeof currentSentenceIndex !== "undefined" ? currentSentenceIndex : null;
    this.pendingWords.push({ word: trimmed, docId, sentIdx });

    // 3. Debounced flush or immediate if batch size reached
    if (this.pendingWords.length >= 5) {
      this.flush();
    } else {
      if (this.flushTimer) clearTimeout(this.flushTimer);
      this.flushTimer = setTimeout(() => this.flush(), 800);
    }
  },

  async flush() {
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    if (this.pendingWords.length === 0 || this.isFlushing) return;

    const batch = [...this.pendingWords];
    this.pendingWords = [];
    this.isFlushing = true;

    try {
      const wordsList = batch.map(b => b.word);
      const first = batch[0];
      const res = await fetch("/api/game/odometer/record", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          words: wordsList,
          document_id: first ? first.docId : null,
          sentence_index: first ? first.sentIdx : null
        })
      });

      if (res.ok) {
        const data = await res.json();
        if (data && data.total_words !== undefined) {
          if (data.total_words > this.totalWords) {
            this.totalWords = data.total_words;
          }
          if (data.today_words !== undefined && data.today_words > this.todayWords) {
            this.todayWords = data.today_words;
          }
          if (data.unique_words !== undefined) {
            this.uniqueWords = data.unique_words;
          }
          this.updateDisplays(false);
          try {
            syncChannel.postMessage({
              type: "odometer_updated",
              data: {
                total_words: this.totalWords,
                today_words: this.todayWords,
                unique_words: this.uniqueWords
              }
            });
          } catch (_) {}
        }
      } else {
        this.pendingWords = batch.concat(this.pendingWords);
      }
    } catch (err) {
      console.warn("Failed to flush odometer words:", err);
      this.pendingWords = batch.concat(this.pendingWords);
    } finally {
      this.isFlushing = false;
    }
  },

  updateDisplays(triggerTick = false) {
    const formatted = this.totalWords.toLocaleString("en-US");

    // 1. Top HUD bar
    const hudVal = document.getElementById("hud-odometer-val");
    if (hudVal) {
      hudVal.textContent = formatted;
      if (triggerTick) {
        hudVal.classList.remove("tick");
        void hudVal.offsetWidth;
        hudVal.classList.add("tick");
        setTimeout(() => hudVal.classList.remove("tick"), 200);
      }
    }

    const hudPill = document.getElementById("hud-odometer-pill");
    if (hudPill) {
      hudPill.title = `汽车仪表盘总里程: 生涯累计 ${formatted} 词 | 今日: ${this.todayWords.toLocaleString("en-US")} 词 (点击查看总里程看板)`;
    }

    // 2. Typing card header
    const typingVal = document.getElementById("typing-odometer-val");
    if (typingVal) {
      typingVal.textContent = formatted;
      if (triggerTick) {
        typingVal.classList.remove("tick");
        void typingVal.offsetWidth;
        typingVal.classList.add("tick");
        setTimeout(() => typingVal.classList.remove("tick"), 200);
      }
    }

    // 3. Fullscreen mode
    const fsVal = document.getElementById("fs-odometer-val");
    if (fsVal) {
      fsVal.textContent = formatted;
      if (triggerTick) {
        fsVal.classList.remove("tick");
        void fsVal.offsetWidth;
        fsVal.classList.add("tick");
        setTimeout(() => fsVal.classList.remove("tick"), 200);
      }
    }
  }
};
window.OdometerEngine = OdometerEngine;

window.addEventListener("beforeunload", () => {
  if (OdometerEngine.pendingWords.length > 0) {
    const wordsList = OdometerEngine.pendingWords.map(b => b.word);
    const payload = JSON.stringify({ words: wordsList });
    if (navigator.sendBeacon) {
      navigator.sendBeacon("/api/game/odometer/record", new Blob([payload], { type: "application/json" }));
    }
  }
});

// ==========================================================================
// Gamification Engine (XP, Streak, Daily Quests, Boss Rush, Ranks)
// ==========================================================================

let currentGameStats = null;

function updateHudDisplays(data) {
  if (!data) return;
  currentGameStats = data;

  const streakVal = document.getElementById("hud-streak-val");
  const levelBadge = document.getElementById("hud-level-badge");
  const xpFill = document.getElementById("hud-xp-fill");
  const xpLabel = document.getElementById("hud-xp-label");
  const questIndicator = document.getElementById("hud-quest-indicator");
  const mistakesBadge = document.getElementById("modal-mistakes-badge");

  if (streakVal) streakVal.textContent = data.streak_days || 1;
  if (levelBadge) levelBadge.textContent = data.title || "Lv.1 萌新启航";
  if (xpFill) xpFill.style.width = `${data.progress_percent || 0}%`;
  if (xpLabel) xpLabel.textContent = `${data.xp || 0}/${data.next_level_xp || 100} XP`;

  if (questIndicator && Array.isArray(data.quests)) {
    const doneCount = data.quests.filter(q => q.is_completed).length;
    questIndicator.textContent = `(${doneCount}/${data.quests.length})`;
  }

  if (mistakesBadge) {
    mistakesBadge.textContent = data.mistakes_count || 0;
  }

  if (data.words_typed !== undefined && window.OdometerEngine) {
    window.OdometerEngine.setLifetimeWords(data.words_typed, data.today_words, data.unique_words);
  }
}

async function refreshGameStatus(broadcast = true) {
  try {
    const res = await fetch("/api/game/status");
    if (!res.ok) return;
    const data = await res.json();
    updateHudDisplays(data);
    if (broadcast) {
      try {
        syncChannel.postMessage({ type: "game_status_updated", data });
      } catch (_) {}
    }
  } catch (err) {
    console.warn("Failed to fetch gamification status:", err);
  }
}

window.recordGameAction = async function(actionType, extraData = {}, anchorEl = null) {
  try {
    const payload = { action_type: actionType, ...extraData };
    const res = await fetch("/api/game/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (!res.ok) return;
    const { result } = await res.json();
    if (!result) return;

    // Visual feedback
    if (result.total_xp_gained > 0) {
      showFloatingXp(result.total_xp_gained, anchorEl);
    }

    if (result.level_up) {
      ConfettiEngine.fireConfetti();
      SoundEngine.playPerfectFanfare();
      showToast(`🎉 恭喜晋升新段位：${result.title} 👑！`);
    }

    if (result.completed_quests && result.completed_quests.length > 0) {
      ConfettiEngine.fireConfetti();
      showToast(`🎯 完成每日挑战：${result.completed_quests.join("，")}！+${result.quest_bonus_xp} XP 奖励！`);
    }

    await refreshGameStatus(true);
  } catch (err) {
    console.warn("Failed to record game action:", err);
  }
};

function initGamificationModal() {
  const modal = document.getElementById("game-modal");
  const closeBtn = document.getElementById("game-modal-close-btn");
  const questBtn = document.getElementById("hud-quest-btn");
  const levelGroup = document.getElementById("hud-level-group");
  const refreshBossBtn = document.getElementById("refresh-boss-btn");

  if (!modal) return;

  function openModal(initialTab = "quests") {
    modal.style.display = "flex";
    switchTab(initialTab);
  }

  function closeModal() {
    modal.style.display = "none";
  }

  if (closeBtn) closeBtn.addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  if (questBtn) questBtn.addEventListener("click", () => openModal("quests"));
  if (levelGroup) levelGroup.addEventListener("click", () => openModal("ranks"));
  if (refreshBossBtn) refreshBossBtn.addEventListener("click", loadMistakesRushList);

  const odometerHudBtn = document.getElementById("hud-odometer-pill");
  const odometerTypingBtn = document.getElementById("typing-odometer-pill");
  const odometerFsBtn = document.getElementById("fs-odometer-pill");

  if (odometerHudBtn) odometerHudBtn.addEventListener("click", () => openModal("odometer"));
  if (odometerTypingBtn) odometerTypingBtn.addEventListener("click", () => openModal("odometer"));
  if (odometerFsBtn) odometerFsBtn.addEventListener("click", () => openModal("odometer"));

  document.querySelectorAll(".game-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      switchTab(btn.dataset.tab);
    });
  });

  function switchTab(tabName) {
    document.querySelectorAll(".game-tab-btn").forEach(b => {
      b.classList.toggle("active", b.dataset.tab === tabName);
    });
    const contents = {
      "quests": document.getElementById("tab-content-quests"),
      "boss": document.getElementById("tab-content-boss"),
      "ranks": document.getElementById("tab-content-ranks"),
      "odometer": document.getElementById("tab-content-odometer")
    };
    Object.keys(contents).forEach(k => {
      if (contents[k]) contents[k].style.display = k === tabName ? "block" : "none";
    });

    if (tabName === "quests") loadQuestsList();
    else if (tabName === "boss") loadMistakesRushList();
    else if (tabName === "ranks") loadRanksTree();
    else if (tabName === "odometer") loadOdometerDashboard();
  }

  async function loadQuestsList() {
    const listEl = document.getElementById("modal-quests-list");
    if (!listEl) return;
    listEl.innerHTML = '<div style="text-align:center; color:#64748b; padding:20px;">正在获取今日挑战…</div>';

    try {
      const res = await fetch("/api/game/status");
      const data = await res.json();
      updateHudDisplays(data);

      const quests = data.quests || [];
      if (quests.length === 0) {
        listEl.innerHTML = '<div style="text-align:center; color:#64748b; padding:20px;">暂无今日任务</div>';
        return;
      }

      listEl.innerHTML = quests.map(q => {
        const pct = Math.min(100, Math.round((q.current_count / q.target_count) * 100));
        const isDone = q.is_completed || q.current_count >= q.target_count;
        return `
          <div class="quest-item ${isDone ? 'completed' : ''}">
            <div class="quest-item-header">
              <span class="quest-item-title">${escapeHtml(q.title)}</span>
              <span class="quest-item-reward">${isDone ? '✓ 已领 +' + q.xp_reward + ' XP' : '+' + q.xp_reward + ' XP'}</span>
            </div>
            <div style="font-size: 11px; color: var(--muted); margin-bottom: 2px;">
              进度: ${q.current_count} / ${q.target_count} ${isDone ? '🎉 已圆满达成！' : ''}
            </div>
            <div class="quest-progress-track">
              <div class="quest-progress-bar" style="width: ${pct}%;"></div>
            </div>
          </div>
        `;
      }).join("");
    } catch (err) {
      listEl.innerHTML = `<div style="text-align:center; color:#dc2626; padding:20px;">加载失败: ${err.message}</div>`;
    }
  }

  async function loadMistakesRushList() {
    const listEl = document.getElementById("modal-boss-list");
    if (!listEl) return;
    listEl.innerHTML = '<div style="text-align:center; color:#64748b; padding:20px;">正在搜寻错题怪兽…</div>';

    try {
      const res = await fetch("/api/game/mistakes/rush");
      const data = await res.json();
      const mistakes = data.mistakes || [];

      const mistakesBadge = document.getElementById("modal-mistakes-badge");
      if (mistakesBadge) mistakesBadge.textContent = mistakes.length;

      if (mistakes.length === 0) {
        listEl.innerHTML = `
          <div style="text-align:center; padding:35px 20px; color:var(--green);">
            <div style="font-size: 36px; margin-bottom: 8px;">🛡️ 完美净空</div>
            <h3 style="margin:0; font-size:16px;">太棒了！当前没有任何未消灭的错题</h3>
            <p style="font-size: 12px; color: var(--muted); margin-top: 6px;">平时在发音与跟打时标记的难词错句，都会汇聚在此等待你一举消灭！</p>
          </div>
        `;
        return;
      }

      listEl.innerHTML = "";
      mistakes.forEach(m => {
        const card = document.createElement("div");
        card.className = "boss-card";
        card.id = `boss-card-${m.id}`;
        card.innerHTML = `
          <div class="boss-header">
            <span class="boss-name">👾 错题怪兽 #${m.id}</span>
            <span class="word-chip ${m.mistake_type === 'pronunciation' ? 'imprecise' : 'missing'}">
              ${m.mistake_type === 'pronunciation' ? '🎙️ 发音易错' : '✍️ 跟打易错'}
            </span>
          </div>
          <div class="boss-sentence">"${escapeHtml(m.sentence_text || m.title || '')}"</div>
          <div style="font-size: 11px; color: var(--muted);">记录时间: ${escapeHtml(m.created_at || '')}</div>
          <div class="boss-actions">
            <button class="btn btn-xs btn-outline challenge-btn" data-id="${m.id}" data-doc="${escapeHtml(m.document_id || '')}" data-page="${m.page_number || ''}" data-idx="${m.sentence_index || ''}" data-text="${escapeHtml(m.sentence_text || '')}">⚔️ 挑战此题</button>
            <button class="btn btn-xs btn-primary cleanse-btn" data-id="${m.id}">✨ 一键净化 (+35 XP)</button>
          </div>
        `;

        // Bind challenge
        card.querySelector(".challenge-btn").addEventListener("click", (e) => {
          const btn = e.currentTarget;
          const text = btn.dataset.text;
          const docId = btn.dataset.doc;
          const page = parseInt(btn.dataset.page, 10);
          const idx = parseInt(btn.dataset.idx, 10);

          closeModal();
          if (text) {
            setupShadowTyping(text);
            const previewBox = document.getElementById("sentence-preview-box");
            if (previewBox) previewBox.textContent = text;
            showToast(`已将错题载入工作室，请开始跟打练习！`);
            const input = document.getElementById("typing-target-display");
            if (input) input.focus();
          }
        });

        // Bind cleanse
        card.querySelector(".cleanse-btn").addEventListener("click", async (e) => {
          const btn = e.currentTarget;
          const noteId = btn.dataset.id;
          btn.disabled = true;
          btn.textContent = "净化中…";

          try {
            const cRes = await fetch(`/api/game/mistakes/cleanse/${noteId}`, { method: "POST" });
            const cData = await cRes.json();
            if (cData.success) {
              ConfettiEngine.fireConfetti();
              showFloatingXp(35, card);
              SoundEngine.playSuccessChime();
              showToast("⚔️ 成功击溃错题怪兽！错题已彻底净化消灭！+35 XP");

              card.style.transition = "all 0.3s ease";
              card.style.opacity = "0";
              card.style.transform = "scale(0.95)";
              setTimeout(() => {
                card.remove();
                loadMistakesRushList();
              }, 300);

              refreshGameStatus(true);
            }
          } catch (cleanseErr) {
            showToast("净化失败: " + cleanseErr.message);
            btn.disabled = false;
            btn.textContent = "✨ 一键净化";
          }
        });

        listEl.appendChild(card);
      });
    } catch (err) {
      listEl.innerHTML = `<div style="text-align:center; color:#dc2626; padding:20px;">加载错题失败: ${err.message}</div>`;
    }
  }

  function loadRanksTree() {
    const listEl = document.getElementById("modal-ranks-list");
    if (!listEl) return;

    const ranks = [
      { lvl: 1, name: "Lv.1 萌新启航", sub: "Novice Explorer", xp: 0, icon: "🥉" },
      { lvl: 2, name: "Lv.2 语音破冰者", sub: "Sound Pioneer", xp: 100, icon: "🥉" },
      { lvl: 3, name: "Lv.3 语感雏形", sub: "Early Speaker", xp: 250, icon: "🥉" },
      { lvl: 4, name: "Lv.4 语流连读者", sub: "Fluent Reader", xp: 450, icon: "🥈" },
      { lvl: 5, name: "Lv.5 节奏掌控者", sub: "Rhythm Master", xp: 700, icon: "🥈" },
      { lvl: 6, name: "Lv.6 词法探索家", sub: "Lexical Explorer", xp: 1000, icon: "🥈" },
      { lvl: 7, name: "Lv.7 句法解构官", sub: "Syntax Analyst", xp: 1400, icon: "🥇" },
      { lvl: 8, name: "Lv.8 地道表达者", sub: "Natural Communicator", xp: 1900, icon: "🥇" },
      { lvl: 10, name: "Lv.10 英语演说家", sub: "Eloquent Speaker", xp: 3200, icon: "💎" },
      { lvl: 15, name: "Lv.15 跨文化使者", sub: "Cultural Ambassador", xp: 10000, icon: "💎" },
      { lvl: 20, name: "Lv.20 殿堂同传官", sub: "Master Interpreter", xp: 20000, icon: "👑" },
    ];

    const currentLvl = (currentGameStats && currentGameStats.level) || 1;

    let statsSummary = `
      <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap:8px; margin-bottom: 14px;">
        <div style="background:var(--surface-muted); padding:10px; border-radius:8px; text-align:center; border:1px solid var(--line);">
          <div style="font-size:11px; color:var(--muted);">🔥 连续打卡</div>
          <div style="font-size:17px; font-weight:800; color:var(--amber); margin-top:2px;">${currentGameStats ? currentGameStats.streak_days : 1} 天</div>
        </div>
        <div style="background:var(--surface-muted); padding:10px; border-radius:8px; text-align:center; border:1px solid var(--line);">
          <div style="font-size:11px; color:var(--muted);">⚡ 历史最高连击</div>
          <div style="font-size:17px; font-weight:800; color:var(--blue); margin-top:2px;">Combo x${currentGameStats ? currentGameStats.max_combo : 0}</div>
        </div>
        <div style="background:var(--surface-muted); padding:10px; border-radius:8px; text-align:center; border:1px solid var(--line);">
          <div style="font-size:11px; color:var(--muted);">🎯 攻克句子</div>
          <div style="font-size:17px; font-weight:800; color:var(--green); margin-top:2px;">${currentGameStats ? currentGameStats.sentences_mastered : 0} 句</div>
        </div>
        <div style="background:var(--surface-muted); padding:10px; border-radius:8px; text-align:center; border:1px solid var(--line);">
          <div style="font-size:11px; color:var(--muted);">✍️ 累计敲击</div>
          <div style="font-size:17px; font-weight:800; color:var(--ink); margin-top:2px;">${currentGameStats ? currentGameStats.words_typed : 0} 词</div>
        </div>
      </div>
    `;

    const treeHtml = ranks.map(r => {
      const isCurrent = r.lvl === currentLvl;
      const isAchieved = currentLvl >= r.lvl;
      return `
        <div class="rank-tier-item ${isCurrent ? 'current' : ''}">
          <span class="rank-tier-badge">${r.icon}</span>
          <div class="rank-tier-info">
            <div class="rank-tier-name">
              ${escapeHtml(r.name)} · ${escapeHtml(r.sub)}
              ${isCurrent ? '<span style="font-size:10px; font-weight:800; color:var(--blue); background:var(--blue-soft); padding:1px 6px; border-radius:4px; margin-left:6px;">当前段位</span>' : ''}
              ${isAchieved && !isCurrent ? '<span style="font-size:10px; color:var(--green); margin-left:6px;">✓ 已达成</span>' : ''}
            </div>
            <div class="rank-tier-desc">需累计 ${r.xp} XP</div>
          </div>
        </div>
      `;
    }).join("");

    listEl.innerHTML = statsSummary + treeHtml;
  }

  async function loadOdometerDashboard() {
    const drumsEl = document.getElementById("modal-odometer-drums");
    const totalEl = document.getElementById("modal-odometer-total");
    const todayEl = document.getElementById("modal-odometer-today");
    const uniqueEl = document.getElementById("modal-odometer-unique");
    const comboEl = document.getElementById("modal-odometer-combo");
    const recentEl = document.getElementById("modal-odometer-recent");
    const topEl = document.getElementById("modal-odometer-top");

    try {
      const res = await fetch("/api/game/odometer");
      if (!res.ok) throw new Error("Failed to fetch odometer data");
      const data = await res.json();

      const total = Number(data.total_words) || 0;
      const today = Number(data.today_words) || 0;
      const unique = Number(data.unique_words) || 0;
      const maxCombo = Number(data.max_combo) || 0;

      if (window.OdometerEngine) {
        window.OdometerEngine.setLifetimeWords(total, today, unique);
      }

      if (totalEl) totalEl.textContent = `${total.toLocaleString("en-US")} 词`;
      if (todayEl) todayEl.textContent = `${today.toLocaleString("en-US")} 词`;
      if (uniqueEl) uniqueEl.textContent = `${unique.toLocaleString("en-US")} 词`;
      if (comboEl) comboEl.textContent = `Combo x${maxCombo}`;

      // Render digit drums (e.g. 007,380)
      if (drumsEl) {
        const strNum = String(total).padStart(6, "0");
        let drumsHtml = "";
        for (let i = 0; i < strNum.length; i++) {
          drumsHtml += `<span class="odometer-drum-digit">${strNum[i]}</span>`;
          if ((strNum.length - 1 - i) % 3 === 0 && i !== strNum.length - 1) {
            drumsHtml += `<span class="odometer-drum-sep">,</span>`;
          }
        }
        drumsEl.innerHTML = drumsHtml;
      }

      // Render recent words
      if (recentEl) {
        const recents = data.recent_words || [];
        if (recents.length === 0) {
          recentEl.innerHTML = '<span class="empty-hint">暂无最近打字记录，开始敲击跟打以累积总里程！</span>';
        } else {
          recentEl.innerHTML = recents.map(r => {
            const timeStr = r.created_at ? r.created_at.split(" ")[1] || "" : "";
            return `<span class="odometer-word-chip">${escapeHtml(r.word)} <span class="odometer-word-time">${escapeHtml(timeStr)}</span></span>`;
          }).join("");
        }
      }

      // Render top practiced words
      if (topEl) {
        const topList = data.top_words || [];
        if (topList.length === 0) {
          topEl.innerHTML = '<span class="empty-hint">暂无高频词统计，保持敲击输入！</span>';
        } else {
          const maxCount = Math.max(...topList.map(t => t.count), 1);
          topEl.innerHTML = topList.map(t => {
            const pct = Math.min(100, Math.round((t.count / maxCount) * 100));
            return `
              <div class="odometer-top-item">
                <span class="odometer-top-word">${escapeHtml(t.word)}</span>
                <div class="odometer-top-bar-box">
                  <div class="odometer-top-bar-fill" style="width: ${pct}%;"></div>
                </div>
                <span class="odometer-top-count">${t.count} 次</span>
              </div>
            `;
          }).join("");
        }
      }
    } catch (err) {
      console.warn("Failed to load odometer dashboard:", err);
      if (drumsEl) drumsEl.innerHTML = '<span style="color:#ef4444; font-size:14px;">加载失败</span>';
    }
  }
}

// Sidebar Collapse (Dual-Screen / Widescreen Mode) Management
let isSidebarCollapsed = localStorage.getItem("ai_coach_sidebar_collapsed") === "1";

function setSidebarCollapsed(collapsed, notify = false) {
  isSidebarCollapsed = !!collapsed;
  localStorage.setItem("ai_coach_sidebar_collapsed", isSidebarCollapsed ? "1" : "0");
  if (workspaceContainer) {
    workspaceContainer.classList.toggle("sidebar-collapsed", isSidebarCollapsed);
  }
  if (sidebarToggleBtn) {
    sidebarToggleBtn.textContent = isSidebarCollapsed ? "▶ 展开教材栏" : "◀ 折叠教材栏";
    sidebarToggleBtn.title = isSidebarCollapsed ? "展开左侧教材视口与章节导航" : "收起左侧教材视口 (享受宽屏智能黑板)";
  }
  if (expandSidebarTab) {
    expandSidebarTab.style.display = isSidebarCollapsed ? "flex" : "none";
  }
  if (studioPageBadge) {
    studioPageBadge.style.display = isSidebarCollapsed ? "inline-flex" : "none";
    if (studioPageNum) studioPageNum.textContent = `P. ${lecturePage}`;
  }

  // Adjust right column width between collapsed mode and normal mode
  const colRightEl = document.getElementById("col-right");
  if (colRightEl && workspaceContainer) {
    if (isSidebarCollapsed) {
      const savedCollapsedW = parseInt(localStorage.getItem("ai_coach_col_right_w_collapsed"), 10);
      const containerW = workspaceContainer.clientWidth || window.innerWidth;
      const defaultCollapsedW = Math.max(380, Math.min(Math.round(containerW * 0.46), containerW - 360));
      colRightEl.style.width = (savedCollapsedW && savedCollapsedW >= 260 ? savedCollapsedW : defaultCollapsedW) + "px";
    } else {
      const savedNormalW = parseInt(localStorage.getItem("ai_coach_col_right_w"), 10);
      colRightEl.style.width = (savedNormalW && savedNormalW >= 240 && savedNormalW <= 850 ? savedNormalW : 360) + "px";
    }
  }

  if (notify) {
    showToast(isSidebarCollapsed ? "已收起教材栏，切换至宽屏黑板模式 ✨" : "已展开教材栏 📖");
  }
}

function initSidebarCollapse() {
  if (sidebarToggleBtn) {
    sidebarToggleBtn.addEventListener("click", () => {
      setSidebarCollapsed(!isSidebarCollapsed, true);
    });
  }
  if (collapseColLeftBtn) {
    collapseColLeftBtn.addEventListener("click", () => {
      setSidebarCollapsed(true, true);
    });
  }
  if (expandSidebarTab) {
    expandSidebarTab.addEventListener("click", () => {
      setSidebarCollapsed(false, true);
    });
  }
  if (studioPrevPageBtn) {
    studioPrevPageBtn.addEventListener("click", () => {
      navigatePage(-1);
    });
  }
  if (studioNextPageBtn) {
    studioNextPageBtn.addEventListener("click", () => {
      navigatePage(1);
    });
  }
  setSidebarCollapsed(isSidebarCollapsed, false);
}

function initAutoTypingToggle() {
  const autoTypingToggle = document.getElementById("auto-typing-toggle");
  if (autoTypingToggle) {
    const savedAutoTyping = localStorage.getItem("ai_coach_auto_typing");
    if (savedAutoTyping !== null) {
      autoTypingToggle.checked = savedAutoTyping === "1";
    }
    autoTypingToggle.addEventListener("change", () => {
      localStorage.setItem("ai_coach_auto_typing", autoTypingToggle.checked ? "1" : "0");
    });
  }
  const loopBtn = document.getElementById("typing-loop-btn");
  if (loopBtn) {
    loopBtn.classList.toggle("active", isLoopPlayback);
    loopBtn.title = isLoopPlayback 
      ? "单句循环播放 (开启中·读完自动复读，点击关闭，快捷键: Alt+L)" 
      : "单句循环播放 (已关闭·点击开启，快捷键: Alt+L)";
  }
}

function initSelectBlurOnChoice() {
  document.querySelectorAll("select").forEach(sel => {
    sel.addEventListener("change", () => {
      try { sel.blur(); } catch (_) {}
      const typingDisplay = document.getElementById("typing-target-display");
      if (typingDisplay && !isTextEditingContext(document.activeElement)) {
        setTimeout(() => { try { typingDisplay.focus(); } catch (_) {} }, 40);
      }
    });
  });
}

function initModelStatusListener() {
  window.addEventListener("model-status-updated", (evt) => {
    const modelState = evt.detail;
    if (!modelState) return;
    const select = document.getElementById("local-model-select");
    if (select && modelState.active && select.value !== modelState.active && !modelState.busy) {
      select.value = modelState.active;
    }
  });
}

// =========================================================================
// Modular Panel Layout & Multi-Screen Docking Manager
// =========================================================================
let currentTypingDock = localStorage.getItem("english_coach_typing_dock") || "landscape";
let currentNotesDock = localStorage.getItem("english_coach_notes_dock") || "landscape";
let isNotesCollapsed = localStorage.getItem("english_coach_notes_collapsed") === "1";

function setTypingDock(dock, broadcast = true) {
  currentTypingDock = dock;
  localStorage.setItem("english_coach_typing_dock", dock);

  const card = document.getElementById("shadow-typing-card");
  const notice = document.getElementById("typing-docked-notice");
  const dockBtn = document.getElementById("typing-dock-btn");
  const chipLandscape = document.getElementById("chip-dock-landscape");
  const chipPortrait = document.getElementById("chip-dock-portrait");

  const isPortrait = (dock === "portrait");

  if (card) {
    card.classList.toggle("docked-to-portrait", isPortrait);
  }
  if (notice) {
    notice.style.display = isPortrait ? "flex" : "none";
  }
  if (dockBtn) {
    dockBtn.textContent = isPortrait ? "🖥️ 移回横屏" : "📱 移至竖屏";
    dockBtn.title = isPortrait ? "将跟打练习区移回当前横屏窗口" : "将跟打练习区移至竖屏模式 (DP-1 9:16)";
    dockBtn.classList.toggle("docked-active", isPortrait);
  }
  if (chipLandscape && chipPortrait) {
    chipLandscape.classList.toggle("active", !isPortrait);
    chipPortrait.classList.toggle("active", isPortrait);
    const radioLand = chipLandscape.querySelector("input");
    const radioPort = chipPortrait.querySelector("input");
    if (radioLand) radioLand.checked = !isPortrait;
    if (radioPort) radioPort.checked = isPortrait;
  }

  const sentTag = document.getElementById("docked-tag-sentence");
  if (sentTag) {
    sentTag.textContent = `当前第 ${currentSentenceIndex || 1} 句`;
  }

  if (broadcast) {
    try {
      syncChannel.postMessage({
        type: "typing_dock_change",
        data: { dock }
      });
    } catch (_) {}
    showToast(isPortrait ? "📱 跟打区域已移至竖屏模式，双屏数据实时同步！" : "🖥️ 跟打区域已移回横屏主台！");
  }
}

function setNotesDock(dock, broadcast = true) {
  currentNotesDock = dock;
  localStorage.setItem("english_coach_notes_dock", dock);

  const isPortrait = (dock === "portrait");
  const notesDockBtn = document.getElementById("notes-dock-btn");
  const chipLandscape = document.getElementById("chip-notes-dock-landscape");
  const chipPortrait = document.getElementById("chip-notes-dock-portrait");
  const expandNotesTab = document.getElementById("expand-notes-tab");

  if (notesDockBtn) {
    notesDockBtn.textContent = isPortrait ? "🖥️ 移回横屏" : "📱 移至竖屏";
    notesDockBtn.title = isPortrait ? "将智能板书移回当前横屏主台" : "将智能板书移至竖屏模式 (DP-1 9:16)";
    notesDockBtn.classList.toggle("docked-active", isPortrait);
  }

  if (chipLandscape && chipPortrait) {
    chipLandscape.classList.toggle("active", !isPortrait);
    chipPortrait.classList.toggle("active", isPortrait);
    const radioLand = chipLandscape.querySelector("input");
    const radioPort = chipPortrait.querySelector("input");
    if (radioLand) radioLand.checked = !isPortrait;
    if (radioPort) radioPort.checked = isPortrait;
  }

  if (expandNotesTab) {
    expandNotesTab.textContent = isPortrait ? "❮ 板书在竖屏 📱" : "❮ 展开板书 📝";
    expandNotesTab.title = isPortrait ? "当前板书已停靠在竖屏实时呈现 (点击可展开横屏板书)" : "展开右侧智能板书";
  }

  if (isPortrait) {
    setNotesCollapsed(true, false);
  } else {
    setNotesCollapsed(false, false);
  }

  if (broadcast) {
    try {
      syncChannel.postMessage({
        type: "notes_dock_change",
        data: { dock }
      });
      syncChannel.postMessage({
        type: "notes_update",
        data: { notes: latestWhiteboardMarkdown || "" }
      });
    } catch (_) {}
    showToast(isPortrait ? "📱 智能板书已移至竖屏展示，横屏右栏已自动收起！" : "🖥️ 智能板书已移回横屏主台！");
  }
}

function setNotesCollapsed(collapsed, notify = false) {
  isNotesCollapsed = !!collapsed;
  localStorage.setItem("english_coach_notes_collapsed", isNotesCollapsed ? "1" : "0");
  if (workspaceContainer) {
    workspaceContainer.classList.toggle("notes-collapsed", isNotesCollapsed);
  }
  const notesToggleBtn = document.getElementById("notes-toggle-btn");
  if (notesToggleBtn) {
    notesToggleBtn.textContent = isNotesCollapsed ? "◀ 展开板书" : "▶ 折叠板书";
    notesToggleBtn.title = isNotesCollapsed ? "展开右侧智能板书栏" : "收起右侧智能板书栏 (享受更宽对话流)";
  }
  const expandNotesTab = document.getElementById("expand-notes-tab");
  if (expandNotesTab) {
    expandNotesTab.style.display = isNotesCollapsed ? "flex" : "none";
  }

  const chipShow = document.getElementById("chip-notes-show");
  const chipHide = document.getElementById("chip-notes-hide");
  if (chipShow && chipHide) {
    chipShow.classList.toggle("active", !isNotesCollapsed);
    chipHide.classList.toggle("active", isNotesCollapsed);
    const rShow = chipShow.querySelector("input");
    const rHide = chipHide.querySelector("input");
    if (rShow) rShow.checked = !isNotesCollapsed;
    if (rHide) rHide.checked = isNotesCollapsed;
  }

  if (notify) {
    showToast(isNotesCollapsed ? "已收起智能板书栏" : "已展开智能板书栏 📝");
  }
}

function syncTypingFromPortrait(data) {
  if (!data) return;
  const sentTag = document.getElementById("docked-tag-sentence");
  if (sentTag && data.sentence_index) {
    sentTag.textContent = `当前第 ${data.sentence_index} 句`;
  }
  const comboBadge = document.getElementById("typing-combo-badge");
  if (comboBadge && data.combo !== undefined) {
    comboBadge.textContent = `Combo x${data.combo} 🔥`;
    comboBadge.className = data.combo >= 10 ? "typing-combo-badge super" : (data.combo >= 3 ? "typing-combo-badge active" : "typing-combo-badge");
  }
  const wpmBadge = document.getElementById("typing-wpm-badge");
  if (wpmBadge && data.wpm !== undefined) {
    wpmBadge.textContent = `${data.wpm} WPM`;
  }
  const display = document.getElementById("typing-target-display");
  if (display && data.target && data.typed !== undefined) {
    const target = data.target;
    const typed = data.typed;
    display.innerHTML = target.split("").map((ch, idx) => {
      let cls = "char-pending";
      if (idx < typed.length) {
        cls = areTypingCharsEqual(typed[idx], ch) ? "char-correct" : "char-wrong";
      } else if (idx === typed.length) {
        cls = "char-current";
      }
      return `<span class="${cls}" data-idx="${idx}">${escapeHtml(ch)}</span>`;
    }).join("");
  }
  if (data.completed) {
    const card = document.getElementById("shadow-typing-card");
    if (card) card.classList.add("completed");
    const pill = document.getElementById("typing-status-pill");
    if (pill) {
      pill.className = "typing-status-pill success";
      pill.textContent = "✓ 竖屏跟打通关！";
    }
  }
}

function handleTypingCompletedFromSync(data) {
  if (isTypingCompleted) return;
  isTypingCompleted = true;
  const card = document.getElementById("shadow-typing-card");
  if (card) card.classList.add("completed");
  const pill = document.getElementById("typing-status-pill");
  if (pill) {
    pill.className = "typing-status-pill success";
    pill.textContent = "✓ 竖屏跟打达成！";
  }
  SoundEngine.playSuccessChime();
  ConfettiEngine.fireConfetti();
  showToast("🎉 太棒了！竖屏跟打拼写 100% 正确！");
  if (window.OdometerEngine) window.OdometerEngine.flush();

  if (isContinuousLecture) {
    setStatus("拼写完成！准备进入下一句…", "speaking");
    setTimeout(() => {
      if (!isContinuousLecture) return;
      if (currentSentenceIndex < lectureSentences.length) {
        currentSentenceIndex++;
        updateSentencePreview();
        const nextSent = lectureSentences[currentSentenceIndex - 1];
        const activeAction = getEffectiveActionMode();
        triggerSentenceAction(currentSentenceIndex, nextSent ? nextSent.text : "", activeAction);
      } else {
        stopContinuousLecture();
        showToast("🎉 本页所有句子已完成练习！");
      }
    }, 1400);
  }
}

function applyLayoutPreset(preset) {
  if (preset === "dual_screen") {
    setTypingDock("portrait", true);
    setNotesDock("landscape", true);
    setSidebarCollapsed(true, false);
    setNotesCollapsed(false, false);
    showToast("🚀 已切换为「双屏极致分工」：竖屏打字跟打，横屏宽屏对话与板书！");
  } else if (preset === "standard") {
    setTypingDock("landscape", true);
    setNotesDock("landscape", true);
    setSidebarCollapsed(false, false);
    setNotesCollapsed(false, false);
    showToast("🖥️ 已切换为「标准全能单屏」经典布局");
  } else if (preset === "focus_typing") {
    setTypingDock("landscape", true);
    setNotesDock("landscape", true);
    setSidebarCollapsed(true, false);
    setNotesCollapsed(true, false);
    showToast("⌨️ 已切换为「沉浸跟打大屏」模式");
  }
}

function initPanelLayoutManager() {
  const layoutBtn = document.getElementById("layout-manager-btn");
  const menuLayoutBtn = document.getElementById("menu-layout-btn");
  const modal = document.getElementById("layout-manager-modal");
  const closeBtn = document.getElementById("layout-modal-close-btn");
  const typingDockBtn = document.getElementById("typing-dock-btn");
  const typingUndockBtn = document.getElementById("typing-undock-btn");
  const notesDockBtn = document.getElementById("notes-dock-btn");
  const notesToggleBtn = document.getElementById("notes-toggle-btn");
  const expandNotesTab = document.getElementById("expand-notes-tab");

  const showModal = () => {
    if (modal) modal.style.display = "flex";
    if (headerMenuPopover) headerMenuPopover.style.display = "none";
  };
  const hideModal = () => {
    if (modal) modal.style.display = "none";
  };

  if (layoutBtn) layoutBtn.addEventListener("click", showModal);
  if (menuLayoutBtn) menuLayoutBtn.addEventListener("click", showModal);
  if (closeBtn) closeBtn.addEventListener("click", hideModal);
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) hideModal();
    });
  }

  if (typingDockBtn) {
    typingDockBtn.addEventListener("click", () => {
      const next = (currentTypingDock === "portrait") ? "landscape" : "portrait";
      setTypingDock(next, true);
    });
  }
  if (typingUndockBtn) {
    typingUndockBtn.addEventListener("click", () => {
      setTypingDock("landscape", true);
    });
  }

  if (notesDockBtn) {
    notesDockBtn.addEventListener("click", () => {
      const next = (currentNotesDock === "portrait") ? "landscape" : "portrait";
      setNotesDock(next, true);
    });
  }

  if (notesToggleBtn) {
    notesToggleBtn.addEventListener("click", () => {
      setNotesCollapsed(!isNotesCollapsed, true);
    });
  }
  if (expandNotesTab) {
    expandNotesTab.addEventListener("click", () => {
      setNotesCollapsed(false, true);
    });
  }

  // Presets
  const pDual = document.getElementById("preset-dual-screen");
  const pStd = document.getElementById("preset-standard");
  const pFocus = document.getElementById("preset-focus-typing");

  if (pDual) pDual.addEventListener("click", () => applyLayoutPreset("dual_screen"));
  if (pStd) pStd.addEventListener("click", () => applyLayoutPreset("standard"));
  if (pFocus) pFocus.addEventListener("click", () => applyLayoutPreset("focus_typing"));

  // Radio chips
  const chipLand = document.getElementById("chip-dock-landscape");
  const chipPort = document.getElementById("chip-dock-portrait");
  if (chipLand) chipLand.addEventListener("click", () => setTypingDock("landscape", true));
  if (chipPort) chipPort.addEventListener("click", () => setTypingDock("portrait", true));

  const chipNotesLand = document.getElementById("chip-notes-dock-landscape");
  const chipNotesPort = document.getElementById("chip-notes-dock-portrait");
  if (chipNotesLand) chipNotesLand.addEventListener("click", () => setNotesDock("landscape", true));
  if (chipNotesPort) chipNotesPort.addEventListener("click", () => setNotesDock("portrait", true));

  const chipSideShow = document.getElementById("chip-sidebar-show");
  const chipSideHide = document.getElementById("chip-sidebar-hide");
  if (chipSideShow) chipSideShow.addEventListener("click", () => setSidebarCollapsed(false, true));
  if (chipSideHide) chipSideHide.addEventListener("click", () => setSidebarCollapsed(true, true));

  const chipNoteShow = document.getElementById("chip-notes-show");
  const chipNoteHide = document.getElementById("chip-notes-hide");
  if (chipNoteShow) chipNoteShow.addEventListener("click", () => setNotesCollapsed(false, true));
  if (chipNoteHide) chipNoteHide.addEventListener("click", () => setNotesCollapsed(true, true));

  // Initialize saved states
  setTypingDock(currentTypingDock, false);
  setNotesDock(currentNotesDock, false);
  setNotesCollapsed(isNotesCollapsed, false);
}

// Start app
loadVoices();
setupHeaderSettings();
initAutoTypingToggle();
loadLectureDocuments();
connectWs();
setupDocumentUpload();
setupUrlImport();
setupAiPracticeModal();
initThemeToggle();
initResizableLayout();
initSidebarCollapse();
initPanelLayoutManager();
setupMainImageViewer();
refreshGameStatus();
initGamificationModal();
initHeaderMenu();
initHeaderProgressClickHandlers();
updateHeaderProgress();
initTypingFullscreen();
initSelectBlurOnChoice();
initModelStatusListener();
