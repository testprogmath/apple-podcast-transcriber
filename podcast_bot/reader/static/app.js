"use strict";

const telegram = window.Telegram && window.Telegram.WebApp;
const documentId = new URLSearchParams(location.search).get("doc") || "";
const initData = (telegram && telegram.initData) || "";
const PINYIN_KEY = "reader.pinyin";
const LANGUAGE_KEY = "reader.language";
const SOURCE_LABELS = { "cc-cedict": "CC-CEDICT", bkrs: "大БКРС", contextual: "" };

const state = {
  title: "",
  sentences: [],
  words: [],
  picked: new Set(),
  open: null,
  lexeme: null,
  pinyin: false,
  language: "ru",
  glossary: {},
  audio: null,
  audioStatus: "",
  audioNotice: false,
  translationViews: [],
  vocabularyPending: new Set(),
  hanlyUploading: false,
};

const el = (id) => document.getElementById(id);
const counters = { hanly: el("hanly-counter"), mosaic: el("mosaic-counter") };

function readPreference(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch (error) {
    return fallback;
  }
}

function writePreference(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (error) {
    /* A private window or blocked site data must not break reading. */
  }
}

function api(path, options) {
  return fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": initData,
      ...(options && options.headers),
    },
  }).then(async (response) => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Reader request failed.");
    return data;
  });
}

function toast(message, bad) {
  const node = el("toast");
  node.textContent = message;
  node.classList.toggle("bad", Boolean(bad));
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    node.hidden = true;
  }, bad ? 7000 : 4000);
}

function refreshCounters() {
  counters.hanly.textContent = `Hanly · ${state.words.length}`;
  counters.mosaic.textContent = `Mosaic · ${state.picked.size}`;
  counters.hanly.classList.toggle("active", state.words.length > 0);
  counters.mosaic.classList.toggle("active", state.picked.size > 0);
}

function isPicked(glyph) {
  return state.words.some((w) => w.glyph === glyph);
}

function paintWord(glyph) {
  const picked = isPicked(glyph);
  document.querySelectorAll(".word").forEach((node) => {
    if (node.dataset.glyph === glyph) {
      node.classList.toggle("picked", picked);
      node.setAttribute("aria-pressed", picked ? "true" : "false");
    }
  });
}

function toggleWord(glyph, sentenceId) {
  const index = state.words.findIndex((w) => w.glyph === glyph);
  if (index === -1) state.words.push({ glyph, sentenceId });
  else state.words.splice(index, 1);
  paintWord(glyph);
  refreshCounters();
  if (telegram && telegram.HapticFeedback) telegram.HapticFeedback.selectionChanged();
}

function toggleSentence(id) {
  if (state.picked.has(id)) state.picked.delete(id);
  else state.picked.add(id);
  const node = document.querySelector(`[data-sentence="${id}"]`);
  if (node) {
    node.classList.toggle("picked", state.picked.has(id));
    node.querySelector(".mark").setAttribute("aria-pressed", String(state.picked.has(id)));
  }
  refreshCounters();
  if (telegram && telegram.HapticFeedback) telegram.HapticFeedback.selectionChanged();
}

function applyPinyin(on) {
  state.pinyin = on;
  el("text").classList.toggle("pinyin", on);
  const toggle = el("pinyin-toggle");
  toggle.setAttribute("aria-pressed", on ? "true" : "false");
  toggle.textContent = on ? "拼音 ON" : "拼音 OFF";
}

function applyLanguage(language) {
  state.language = language;
  const toggle = el("language-toggle");
  toggle.setAttribute("aria-pressed", language === "ru" ? "true" : "false");
  toggle.textContent = language.toUpperCase();
  if (state.lexeme) renderLexemeBody(state.lexeme.glyph);
  state.translationViews.forEach((refresh) => refresh());
}

/** The chosen language, falling back to the other rather than showing nothing. */
function sensesFor(glyph) {
  const entry = state.glossary[glyph] || {};
  const native = { senses: entry.ru || [], source: entry.rs || "" };
  const english = { senses: entry.en || [], source: entry.en ? "cc-cedict" : "" };
  const first = state.language === "ru" ? native : english;
  return first.senses.length ? first : state.language === "ru" ? english : native;
}

function markInspecting(node) {
  document.querySelectorAll(".word.inspecting").forEach((n) => n.classList.remove("inspecting"));
  if (node) node.classList.add("inspecting");
}

/** Select by BCP-47 metadata, never platform-specific voice names. */
function selectMandarinVoice(voices) {
  const rank = (voice) => {
    const lang = String(voice.lang || "").toLowerCase().replace(/_/g, "-");
    if (lang === "zh-cn") return 0;
    if (/^(zh-(cn|hans|sg)(-|$)|cmn(-|$))/.test(lang)) return 1;
    if (/^zh(-|$)/.test(lang) && !/^zh-(hk|mo)(-|$)/.test(lang)) return 2;
    if (/^zh-/.test(lang)) return 3;
    return 99;
  };
  return voices.filter((voice) => rank(voice) < 99).sort((a, b) => rank(a) - rank(b))[0];
}

const speech = {
  synth: window.speechSynthesis,
  Utterance: window.SpeechSynthesisUtterance,
  voices: [],
  active: null,
  release: null,
  owner: null,
};
const pronunciationButton = el("lexeme-pronounce");
const canSpeak = !!(speech.synth && typeof speech.Utterance === "function"
  && typeof speech.synth.speak === "function" && typeof speech.synth.cancel === "function");
pronunciationButton.hidden = !canSpeak;

function refreshVoices() {
  try { speech.voices = Array.from(speech.synth.getVoices() || []); }
  catch (error) { speech.voices = []; }
}

/** Stop what the Reader is saying; given an owner, only if that control started it. */
function stopSpeaking(owner) {
  if (!speech.active || (owner && speech.owner !== owner)) return;
  const release = speech.release;
  speech.active = null;
  speech.release = null;
  speech.owner = null;
  if (release) release();
  // cancel() is global to this page's synthesizer: call it only while we own an utterance.
  try { speech.synth.cancel(); } catch (error) { /* Reading remains usable. */ }
}

/** The Reader owns one utterance at a time, whichever control asked for it. */
function speakMandarin(text, handlers = {}) {
  stopOriginal();
  const words = String(text || "").trim();
  if (!canSpeak || !words) return false;
  stopSpeaking();
  let utterance;
  let started = false;
  let reported = false;
  const unavailable = () => {
    if (!reported) toast("Pronunciation unavailable", true);
    reported = true;
  };
  const begin = () => {
    if (started || speech.active !== utterance) return;
    started = true;
    if (handlers.onStart) handlers.onStart();
  };
  const finish = (failed) => {
    if (speech.active !== utterance) return;
    speech.active = null;
    speech.release = null;
    speech.owner = null;
    if (handlers.onEnd) handlers.onEnd();
    if (failed) unavailable();
  };
  try {
    refreshVoices();
    utterance = new speech.Utterance(words);
    utterance.lang = "zh-CN";
    const voice = selectMandarinVoice(speech.voices);
    if (voice) utterance.voice = voice;
    utterance.onstart = begin;
    utterance.onend = () => finish(false);
    utterance.onerror = () => finish(true);
    speech.active = utterance;
    speech.release = () => { if (handlers.onEnd) handlers.onEnd(); };
    speech.owner = handlers.owner || null;
    speech.synth.speak(utterance);
  } catch (error) {
    if (utterance && speech.active === utterance) stopSpeaking();
    unavailable();
    return false;
  }
  begin();
  return true;
}

function pronounce(event) {
  event.stopPropagation();
  if (!state.lexeme) return;
  speakMandarin(state.lexeme.glyph, {
    owner: "word",
    onStart: () => pronunciationButton.classList.add("speaking"),
    onEnd: () => pronunciationButton.classList.remove("speaking"),
  });
}

/** Why this document has no original audio; said once, not on every tap. */
const AUDIO_NOTICES = {
  untimed: "No original audio: this episode was transcribed without timings.",
  unaligned: "No original audio: the timings do not match the transcript.",
  source: "No original audio: the episode source is unusable.",
  stale: "No original audio: the stored timings no longer match this transcript.",
  missing: "No original audio stored for this episode.",
};

function noteFallback(reason) {
  if (state.audioNotice) return;
  const message = reason === "transport"
    ? "Original audio would not play; using speech."
    : AUDIO_NOTICES[reason];
  if (!message) return;
  state.audioNotice = true;
  toast(message);
}

// One original player and the existing TTS engine share a Reader playback channel.
const original = { player: null, request: null, generation: 0 };
function stopOriginal() {
  original.generation += 1;
  const request = original.request;
  original.request = null;
  if (!request) return;
  clearTimeout(request.timeout);
  if (request.interval) window.clearInterval(request.interval);
  const audio = original.player;
  if (audio) audio.onloadedmetadata = audio.onseeked = audio.ontimeupdate = audio.onended = audio.onerror = null;
  try { audio.pause(); } catch (error) { /* Best effort on navigation. */ }
  if (request.handlers.onEnd) request.handlers.onEnd();
}

/** Original range first; TTS only when unavailable or failed before useful playback. */
function playSentenceAudio(sentence, handlers = {}) {
  stopOriginal();
  stopSpeaking();
  const source = state.audio;
  const range = sentence.audio;
  if (!source || !range || range.source !== "podcast" || !window.Audio
      || !Number.isFinite(range.start_ms) || !Number.isFinite(range.end_ms)
      || range.start_ms < 0 || range.end_ms <= range.start_ms) {
    noteFallback(source && range ? "transport" : state.audioStatus);
    return speakMandarin(sentence.text, { ...handlers, owner: "sentence" });
  }
  const generation = original.generation;
  const request = { handlers, phase: "loading-original", useful: 0, last: null, timeout: null, interval: null };
  original.request = request;
  const current = () => original.request === request && original.generation === generation;
  const start = range.start_ms / 1000;
  const end = range.end_ms / 1000;
  const fail = () => {
    if (!current()) return;
    const fallback = request.useful < Math.min(.35, (end - start) / 2);
    stopOriginal();
    if (fallback) {
      noteFallback("transport");
      if (!speakMandarin(sentence.text, { ...handlers, owner: "sentence" })) {
        if (!canSpeak) toast("Pronunciation unavailable", true);
      }
    }
  };
  try {
    const audio = original.player || (original.player = new window.Audio());
    audio.preload = "metadata";
    // No crossorigin: ordinary media playback requires neither fetch nor CORS.
    const progress = () => {
      if (!current() || request.phase !== "playing-original") return;
      const time = audio.currentTime;
      if (audio.seeking) return;
      if (time < start - .15 || time > end + .5) { fail(); return; }
      if (!audio.paused && request.last !== null) {
        request.useful += Math.max(0, Math.min(.25, time - request.last));
      }
      if (request.last === null || time > request.last) {
        clearTimeout(request.timeout);
        request.timeout = setTimeout(fail, 8000);
      }
      request.last = time;
      if (time >= end) stopOriginal();
    };
    const play = () => {
      if (!current() || request.phase !== "seeking") return;
      if (Math.abs(audio.currentTime - start) > .15) { fail(); return; }
      request.phase = "playing-original";
      request.last = audio.currentTime;
      try {
        Promise.resolve(audio.play()).catch(fail);
        if (handlers.onStart) handlers.onStart();
        if (window.setInterval) request.interval = window.setInterval(progress, 50);
      } catch (error) { fail(); }
    };
    const seek = () => {
      if (!current() || request.phase !== "loading-original") return;
      // A changed enclosure (e.g. dynamic ads) must not silently shift known timings.
      if (!Number.isFinite(audio.duration) || end > audio.duration + .05
          || Math.abs(audio.duration - source.duration) > 1) { fail(); return; }
      request.phase = "seeking";
      try {
        audio.currentTime = start;
        if (!audio.seeking) play();
      } catch (error) { fail(); }
    };
    audio.onloadedmetadata = seek;
    audio.onseeked = play;
    audio.ontimeupdate = progress;
    audio.onerror = () => { if (audio.error) fail(); };
    audio.onended = () => {
      if (!current()) return;
      if (audio.currentTime >= end - .1) stopOriginal(); else fail();
    };
    request.timeout = setTimeout(fail, 8000);
    if (audio.src !== source.url || audio.error) { audio.src = source.url; audio.load(); }
    if (audio.readyState >= 1) seek();
  } catch (error) { fail(); }
  return true;
}

window.addEventListener?.("pagehide", () => { stopOriginal(); stopSpeaking(); });
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { stopOriginal(); stopSpeaking(); }
});

if (canSpeak) {
  refreshVoices();
  speech.synth.addEventListener?.("voiceschanged", refreshVoices);
  pronunciationButton.addEventListener("click", pronounce);

}

function closeLexeme() {
  stopSpeaking("word");
  state.lexeme = null;
  el("lexeme").hidden = true;
  markInspecting(null);
  if (!state.open) el("scrim").hidden = true;
  if (closeLexeme.origin && closeLexeme.origin.focus) closeLexeme.origin.focus();
  closeLexeme.origin = null;
}

// Current Hanly selection > persisted learning/known > unknown (absence).
function persistedVocabulary(glyph) {
  return (state.glossary[glyph] || {}).vocabulary_state || "unknown";
}

function effectiveVocabulary(glyph) {
  return isPicked(glyph) ? "learning" : persistedVocabulary(glyph);
}

function saveVocabularyLocally(glyph, value) {
  state.glossary[glyph] = { ...(state.glossary[glyph] || {}), vocabulary_state: value };
}

async function changeVocabulary(event) {
  event.stopPropagation();
  if (!state.lexeme || state.hanlyUploading) return;
  const glyph = state.lexeme.glyph;
  if (state.vocabularyPending.has(glyph)) return;
  const next = persistedVocabulary(glyph) === "known" ? "unknown" : "known";
  state.vocabularyPending.add(glyph);
  renderLexemeAction();
  try {
    const result = await api(`/api/reader/${documentId}/vocabulary-state`, {
      method: "POST", body: JSON.stringify({ glyph, state: next }),
    });
    if (result.glyph !== glyph || !["known", "unknown"].includes(result.vocabulary_state)) {
      throw new Error("Vocabulary state could not be confirmed.");
    }
    // Commit UI state only after the backend confirms persistence.
    saveVocabularyLocally(glyph, result.vocabulary_state);
  } catch (error) {
    toast(`Could not save vocabulary state. ${error.message}`, true);
  } finally {
    state.vocabularyPending.delete(glyph);
    if (state.lexeme) renderLexemeAction();
  }
}

function renderLexemeAction() {
  const action = el("lexeme-action");
  const picked = isPicked(state.lexeme.glyph);
  action.textContent = picked ? "✓ In Hanly — remove" : "+ Add to Hanly";
  action.classList.toggle("remove", picked);
  const glyph = state.lexeme.glyph;
  const value = effectiveVocabulary(glyph);
  const status = el("lexeme-state");
  status.textContent = value === "known" ? "✓ Known" : value === "learning" ? "Learning · Hanly" : "";
  status.hidden = value === "unknown";
  const knowledge = el("lexeme-knowledge");
  knowledge.disabled = state.vocabularyPending.has(glyph) || state.hanlyUploading;
  knowledge.textContent = state.vocabularyPending.has(glyph) ? "Saving…"
    : persistedVocabulary(glyph) === "known" ? "Mark as unknown"
    : value === "learning" ? "✓ Mark as known" : "✓ I know this";
}

/** The sentence the word was tapped in, with every occurrence of it marked. */
function renderContext(glyph, sentenceId) {
  const node = el("lexeme-context");
  node.textContent = "";
  const sentence = state.sentences.find((s) => s.id === sentenceId);
  const text = sentence ? sentence.text : "";
  el("lexeme-context-label").hidden = !text;
  if (!text) return;
  let rest = text;
  while (rest) {
    const at = glyph ? rest.indexOf(glyph) : -1;
    if (at === -1) {
      node.appendChild(document.createTextNode(rest));
      break;
    }
    if (at > 0) node.appendChild(document.createTextNode(rest.slice(0, at)));
    const hit = document.createElement("span");
    hit.className = "hit";
    hit.textContent = glyph;
    node.appendChild(hit);
    rest = rest.slice(at + glyph.length);
  }
}

function renderLexemeBody(glyph) {
  const entry = state.glossary[glyph] || {};
  el("lexeme-glyph").textContent = glyph;
  el("lexeme-pinyin").textContent = entry.p || "";
  const list = el("lexeme-senses");
  list.textContent = "";
  const { senses, source } = sensesFor(glyph);
  for (const sense of senses) {
    const item = document.createElement("li");
    item.textContent = sense;
    list.appendChild(item);
  }
  el("lexeme-senses-label").hidden = !senses.length;
  el("lexeme-senses-label").textContent =
    source === "contextual" ? "Значение (в этом эпизоде)" : "Значение (из словаря)";
  el("lexeme-source").textContent = senses.length ? SOURCE_LABELS[source] || "" : "";
  if (state.lexeme) renderContext(glyph, state.lexeme.sentenceId);
}

function openLexeme(token, sentenceId, node) {
  stopSpeaking("word");
  pronunciationButton.setAttribute("aria-label", `Pronounce ${token.t.trim()}`);
  state.lexeme = { glyph: token.t, sentenceId };
  closeLexeme.origin = node;
  renderLexemeBody(token.t);
  renderLexemeAction();
  markInspecting(node);
  el("lexeme").hidden = false;
  el("scrim").hidden = false;
  el("lexeme-action").focus();
}

function translationControl(sentence) {
  const box = document.createElement("span");
  box.className = "sentence-translation";
  const action = document.createElement("button");
  action.type = "button";
  action.className = "translation-action";
  const icon = document.createElement("span");
  icon.className = "translation-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "文";
  const letter = document.createElement("small");
  letter.textContent = "A";
  icon.appendChild(letter);
  action.appendChild(icon);
  const label = (text) => { action.setAttribute("aria-label", text); action.title = text; };
  label("Show translation");
  action.setAttribute("aria-expanded", "false");
  const content = document.createElement("span");
  content.className = "translation-text";
  content.id = `translation-${sentence.id}`;
  content.setAttribute("lang", "ru");
  content.setAttribute("aria-live", "polite");
  action.setAttribute("aria-controls", content.id);
  content.hidden = true;
  const cache = {};
  const pending = {};
  let visible = false;
  let revision = 0;
  box.addEventListener("click", (event) => event.stopPropagation());
  async function show() {
    const language = state.language;
    const requestRevision = ++revision;
    content.hidden = true;
    content.setAttribute("lang", language);
    status.hidden = true;
    action.setAttribute("aria-expanded", String(visible));
    if (!visible) {
      label("Show translation");
      action.setAttribute("aria-busy", "false");
      return;
    }
    label("Hide translation");
    if (!cache[language]) {
      status.textContent = "Translating…";
      status.hidden = false;
      label("Translating…");
      action.setAttribute("aria-busy", "true");
      try {
        if (!pending[language]) {
          pending[language] = api(`/api/reader/${documentId}/sentences/${sentence.id}/translation`, {
            method: "POST", body: JSON.stringify({ language }),
          }).then((result) => {
            if (result.sentence_id !== sentence.id || typeof result.translation !== "string" || !result.translation.trim()) {
              throw new Error("Invalid translation");
            }
            cache[language] = result.translation;
          }).finally(() => { delete pending[language]; });
        }
        await pending[language];
      } catch (error) {
        if (requestRevision !== revision) return;
        visible = false;
        action.setAttribute("aria-expanded", "false");
        label("Translation unavailable · Retry");
        status.textContent = "Translation unavailable · Tap the icon to retry";
        return;
      } finally {
        if (requestRevision === revision) action.setAttribute("aria-busy", "false");
      }
    }
    if (requestRevision !== revision || !visible) return;
    content.textContent = cache[language];
    content.hidden = false;
    status.hidden = true;
    label("Hide translation");
  }
  action.addEventListener("click", (event) => {
    event.stopPropagation();
    if (visible && pending[state.language]) return;
    visible = !visible;
    show();
  });
  state.translationViews.push(() => { if (visible) show(); });
  const status = document.createElement("span");
  status.className = "translation-status";
  status.setAttribute("role", "status");
  status.hidden = true;
  box.appendChild(content);
  box.appendChild(status);
  return { action, box } ;
}

const SVG_NS = "http://www.w3.org/2000/svg";

function speakerIcon() {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "audio-icon");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const cone = document.createElementNS(SVG_NS, "path");
  cone.setAttribute("d", "M4 9h3.3L12 5.1v13.8L7.3 15H4z");
  cone.setAttribute("fill", "currentColor");
  const waves = document.createElementNS(SVG_NS, "path");
  waves.setAttribute("d", "M15.5 9.3a3.9 3.9 0 0 1 0 5.4M18.1 6.7a7.6 7.6 0 0 1 0 10.6");
  waves.setAttribute("fill", "none");
  waves.setAttribute("stroke", "currentColor");
  waves.setAttribute("stroke-width", "1.7");
  waves.setAttribute("stroke-linecap", "round");
  svg.append(cone, waves);
  return svg;
}

function audioControl(sentence) {
  const action = document.createElement("button");
  action.type = "button";
  action.className = "audio-action";
  action.appendChild(speakerIcon());
  const label = (text) => { action.setAttribute("aria-label", text); action.title = text; };
  label("Pronounce sentence");
  action.addEventListener("click", (event) => {
    // Playback is not a selection: it must not reach the Mosaic mark or the sentence.
    event.stopPropagation();
    playSentenceAudio(sentence, {
      onStart: () => { action.classList.add("speaking"); label("Restart sentence pronunciation"); },
      onEnd: () => { action.classList.remove("speaking"); label("Pronounce sentence"); },
    });
  });
  return action;
}

function renderSentence(sentence) {
  const node = document.createElement("span");
  node.className = "sentence";
  node.dataset.sentence = String(sentence.id);
  const source = document.createElement("span");
  source.className = "sentence-source";
  node.appendChild(source);
  for (const token of sentence.tokens) {
    if (!token.w) {
      source.appendChild(document.createTextNode(token.t));
      continue;
    }
    const pinyin = (state.glossary[token.t] || {}).p || "";
    const word = document.createElement("button");
    word.type = "button";
    word.className = "word";
    word.dataset.glyph = token.t;
    word.setAttribute("aria-pressed", "false");
    word.setAttribute("aria-label", pinyin ? `${token.t} ${pinyin}` : token.t);
    if (pinyin) {
      const ruby = document.createElement("ruby");
      ruby.appendChild(document.createTextNode(token.t));
      const rt = document.createElement("rt");
      rt.textContent = pinyin;
      ruby.appendChild(rt);
      word.appendChild(ruby);
    } else {
      word.textContent = token.t;
    }
    word.addEventListener("click", (event) => {
      // Inspecting a word must never toggle the Mandarin Mosaic sentence around it.
      event.stopPropagation();
      openLexeme(token, sentence.id, word);
    });
    source.appendChild(word);
  }
  const controls = document.createElement("span");
  controls.className = "sentence-controls";
  const mark = document.createElement("button");
  mark.type = "button";
  mark.className = "mark";
  mark.setAttribute("aria-label", "Select sentence for Mandarin Mosaic");
  mark.setAttribute("aria-pressed", "false");
  mark.title = "Select this sentence for Mandarin Mosaic";
  mark.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleSentence(sentence.id);
  });
  controls.appendChild(mark);
  if (canSpeak || (window.Audio && state.audio && sentence.audio)) controls.appendChild(audioControl(sentence));
  node.appendChild(controls);
  if (/[㐀-䶿一-鿿豈-﫿]/.test(sentence.text)) {
    const translation = translationControl(sentence);
    controls.appendChild(translation.action);
    node.appendChild(translation.box);
  }
  node.addEventListener("click", () => toggleSentence(sentence.id));
  return node;
}

function render(data) {
  stopOriginal();
  stopSpeaking();
  state.title = data.title;
  state.audio = data.audio || null;
  state.audioStatus = data.audio_status || "";
  state.audioNotice = false;
  state.sentences = data.sentences;
  state.glossary = data.glossary || {};
  state.available = { hanly: data.hanly_available, mosaic: data.mosaic_available };
  state.reasons = { hanly: data.hanly_error, mosaic: data.mosaic_error };
  const parts = String(data.title).split("｜");
  el("source-line").textContent = parts.length > 1 ? parts[0] : "";
  el("title").textContent = parts.length > 1 ? parts.slice(1).join("｜") : data.title;
  document.title = data.title;
  const byId = new Map(data.sentences.map((s) => [s.id, s]));
  const main = el("text");
  main.textContent = "";
  for (const group of data.paragraphs) {
    const paragraph = document.createElement("p");
    group.forEach((id, index) => {
      if (index > 0) paragraph.appendChild(document.createTextNode(" "));
      paragraph.appendChild(renderSentence(byId.get(id)));
    });
    main.appendChild(paragraph);
  }
  main.setAttribute("aria-busy", "false");
  applyPinyin(state.pinyin);
  refreshCounters();
}

function closeBasket() {
  state.open = null;
  el("basket").hidden = true;
  if (!state.lexeme) el("scrim").hidden = true;
}

function openBasket(kind) {
  state.open = kind;
  const hanly = kind === "hanly";
  el("basket-title").textContent = hanly
    ? `Hanly vocabulary (${state.words.length})`
    : `Mosaic sentences (${state.picked.size})`;
  const items = el("basket-items");
  items.textContent = "";
  const entries = hanly
    ? state.words.map((w) => ({ key: w.glyph, text: w.glyph }))
    : [...state.picked].sort((a, b) => a - b).map((id) => ({
        key: id,
        text: (state.sentences.find((s) => s.id === id) || {}).text || "",
      }));
  for (const entry of entries) {
    const row = document.createElement("li");
    if (state.failed && state.failed.has(entry.key)) row.className = "failed";
    const cell = document.createElement("div");
    cell.className = "entry";
    const head = document.createElement("div");
    const glyph = document.createElement("span");
    glyph.className = "glyph";
    glyph.textContent = entry.text;
    head.appendChild(glyph);
    if (hanly) {
      const info = state.glossary[entry.key] || {};
      if (info.p) {
        const reading = document.createElement("span");
        reading.className = "reading";
        reading.textContent = info.p;
        head.appendChild(reading);
      }
      const { senses } = sensesFor(entry.key);
      if (senses.length) {
        const gloss = document.createElement("div");
        gloss.className = "gloss";
        gloss.textContent = senses[0];
        cell.append(head, gloss);
      } else {
        cell.appendChild(head);
      }
    } else {
      cell.appendChild(head);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove";
    remove.textContent = "✕";
    remove.setAttribute("aria-label", "Remove");
    remove.addEventListener("click", () => {
      if (hanly) toggleWord(entry.key);
      else toggleSentence(entry.key);
      openBasket(kind);
    });
    row.append(cell, remove);
    items.appendChild(row);
  }
  const available = state.available[kind];
  const note = hanly
    ? "Selected words will be added to this document's Hanly collection."
    : "Selected sentences will be added to this document's Mandarin Mosaic pack.";
  el("basket-note").textContent = available
    ? note
    : state.reasons[kind] || "Not configured on the server.";
  const upload = el("basket-upload");
  upload.textContent = `Upload ${entries.length} to ${hanly ? "Hanly" : "Mosaic"}`;
  upload.disabled = entries.length === 0 || !available;
  el("basket").hidden = false;
  el("scrim").hidden = false;
}

function noteSummary(notes) {
  if (!notes || !notes.length) return "";
  const counted = {};
  for (const note of notes) counted[note.action] = (counted[note.action] || 0) + 1;
  const labels = {
    created: "added",
    updated: "updated",
    unchanged: "already current",
    "skipped-user-modified": "kept (yours)",
    failed: "failed",
  };
  const parts = Object.keys(labels)
    .filter((action) => counted[action])
    .map((action) => `${counted[action]} ${labels[action]}`);
  return parts.length ? `\nNotes: ${parts.join(", ")}.` : "";
}

async function upload() {
  const hanly = state.open === "hanly";
  if (hanly && state.vocabularyPending.size) {
    toast("Wait for vocabulary changes to finish saving.", true);
    return;
  }
  if (hanly) state.hanlyUploading = true;
  if (state.lexeme) renderLexemeAction();
  const button = el("basket-upload");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Uploading…";
  try {
    if (hanly) {
      const sent = state.words.slice();
      const result = await api(`/api/reader/${documentId}/hanly`, {
        method: "POST",
        body: JSON.stringify({
          items: sent.map((w) => ({ glyph: w.glyph, sentence_id: w.sentenceId })),
        }),
      });
      state.words = state.words.filter((w) => !sent.some((s) => s.glyph === w.glyph));
      sent.forEach((w) => {
        saveVocabularyLocally(w.glyph, result.vocabulary_states?.[w.glyph] || "learning");
        paintWord(w.glyph);
      });
      state.failed = null;
      closeBasket();
      toast(
        `Hanly: ${result.uploaded} uploaded to “${result.name}” (${result.total} cards).` +
          noteSummary(result.notes)
      );
    } else {
      const ids = [...state.picked].sort((a, b) => a - b);
      const result = await api(`/api/reader/${documentId}/mandarin-mosaic`, {
        method: "POST",
        body: JSON.stringify({ sentence_ids: ids }),
      });
      const failed = new Set(result.failed_sentence_ids);
      state.failed = failed;
      for (const id of ids) if (!failed.has(id)) toggleSentence(id);
      if (failed.size) {
        openBasket("mosaic");
        toast(
          `Uploaded: ${result.uploaded}\nFailed: ${failed.size}\n${result.error || "The failed sentences are still selected."}`,
          true
        );
      } else {
        closeBasket();
        toast(`Mandarin Mosaic: ${result.uploaded} sentences uploaded to “${result.name}”.`);
      }
    }
  } catch (error) {
    toast(
      `${hanly ? "Hanly" : "Mandarin Mosaic"} upload failed.\n${error.message}\nYour selection is kept.`,
      true
    );
  } finally {
    if (hanly) state.hanlyUploading = false;
    if (state.lexeme) renderLexemeAction();
    button.disabled = false;
    button.textContent = original;
    refreshCounters();
  }
}

function dismissTop() {
  if (state.lexeme) closeLexeme();
  else if (state.open) closeBasket();
}

counters.hanly.addEventListener("click", () => openBasket("hanly"));
counters.mosaic.addEventListener("click", () => openBasket("mosaic"));
el("basket-close").addEventListener("click", closeBasket);
el("basket-upload").addEventListener("click", upload);
el("lexeme-knowledge").addEventListener("click", changeVocabulary);
el("lexeme-close").addEventListener("click", closeLexeme);
el("scrim").addEventListener("click", dismissTop);
el("lexeme-action").addEventListener("click", (event) => {
  event.stopPropagation();
  const { glyph, sentenceId } = state.lexeme;
  toggleWord(glyph, sentenceId);
  closeLexeme();
});
el("pinyin-toggle").addEventListener("click", () => {
  applyPinyin(!state.pinyin);
  writePreference(PINYIN_KEY, state.pinyin ? "on" : "off");
});
el("language-toggle").addEventListener("click", () => {
  applyLanguage(state.language === "ru" ? "en" : "ru");
  writePreference(LANGUAGE_KEY, state.language);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") dismissTop();
});
for (const id of ["cedict-link", "cedict-licence"]) {
  el(id).addEventListener("click", (event) => {
    if (telegram && telegram.openLink) {
      event.preventDefault();
      telegram.openLink(event.currentTarget.href);
    }
  });
}

applyPinyin(readPreference(PINYIN_KEY, "off") === "on");
applyLanguage(readPreference(LANGUAGE_KEY, "ru") === "en" ? "en" : "ru");

if (telegram) {
  telegram.ready();
  telegram.expand();
}

api(`/api/reader/${documentId}`)
  .then(render)
  .catch((error) => {
    el("text").textContent = error.message;
    el("text").setAttribute("aria-busy", "false");
  });
