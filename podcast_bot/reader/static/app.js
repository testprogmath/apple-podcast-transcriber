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
  selection: null,
  selectionPending: false,
  selectionBarHeld: false,
  keyboard: false,
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

/** A span pins the item to its exact source slice, so it never depends on segmentation. */
function toggleWord(glyph, sentenceId, span) {
  const index = state.words.findIndex((w) => w.glyph === glyph);
  if (index === -1) state.words.push(span ? { glyph, sentenceId, ...span } : { glyph, sentenceId });
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
  if (state.lexeme) {
    renderLexemeBody(state.lexeme.glyph);
    if (state.capabilities?.internal_save) loadContextualMeaning();
  }
  state.translationViews.forEach((refresh) => refresh());
}

/** The chosen language, falling back to the other rather than showing nothing. */
function sensesFor(glyph) {
  const entry = state.glossary[glyph] || {};
  const native = { senses: entry.ru || [], source: entry.rs || "" };
  const english = { senses: entry.en || [], source: entry.en ? (entry.es || "cc-cedict") : "" };
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
function speakText(text, handlers = {}) {
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
    utterance.lang = handlers.speechLanguage || state.capabilities?.speech_language || "zh-CN";
    const voice = utterance.lang === "nl-NL"
      ? speech.voices.find((v) => /^nl[-_]nl$/i.test(v.lang || ""))
        || speech.voices.find((v) => /^nl(?:[-_]|$)/i.test(v.lang || ""))
      : selectMandarinVoice(speech.voices);
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
  speakText(state.lexeme.glyph, {
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
  if (state.capabilities?.internal_save) {
    return speakText(sentence.text, { ...handlers, owner: "sentence" });
  }
  const source = state.audio;
  const range = sentence.audio;
  if (!source || !range || range.source !== "podcast" || !window.Audio
      || !Number.isFinite(range.start_ms) || !Number.isFinite(range.end_ms)
      || range.start_ms < 0 || range.end_ms <= range.start_ms) {
    noteFallback(source && range ? "transport" : state.audioStatus);
    return speakText(sentence.text, { ...handlers, owner: "sentence" });
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
      if (!speakText(sentence.text, { ...handlers, owner: "sentence" })) {
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
    const mediaUrl = new URL(source.url, location.href).href;
    if (audio.src !== mediaUrl || audio.error) { audio.src = mediaUrl; audio.load(); }
    if (audio.readyState >= 1) seek();
  } catch (error) { fail(); }
  return true;
}

window.addEventListener?.("pagehide", () => {
  stopOriginal();
  stopSpeaking();
  hideSelectionActions();
});
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
  const knowledge = el("lexeme-knowledge");
  knowledge.hidden = Boolean(state.capabilities?.internal_save);
  if (state.capabilities?.internal_save) {
    action.textContent = state.lexeme.saving ? "Saving…" : state.lexeme.saved ? "✓ Saved" : "☆ Save";
    action.disabled = Boolean(state.lexeme.saving);
    action.classList.remove("remove");
    el("lexeme-state").hidden = true;
    el("lexeme-remove-expression").hidden = true;
    return;
  }
  action.disabled = false;
  const picked = isPicked(state.lexeme.glyph);
  action.textContent = picked ? "✓ In Hanly — remove" : "+ Add to Hanly";
  action.classList.toggle("remove", picked);
  const glyph = state.lexeme.glyph;
  const value = effectiveVocabulary(glyph);
  const status = el("lexeme-state");
  status.textContent = value === "known" ? "✓ Known" : value === "learning" ? "Learning · Hanly" : "";
  status.hidden = value === "unknown";
  el("lexeme-remove-expression").hidden = !state.lexeme.override;
  knowledge.disabled = state.vocabularyPending.has(glyph) || state.hanlyUploading;
  knowledge.textContent = state.vocabularyPending.has(glyph) ? "Saving…"
    : persistedVocabulary(glyph) === "known" ? "Mark as unknown"
    : value === "learning" ? "✓ Mark as known" : "✓ I know this";
}

/** Highlight the tapped occurrence; Python offsets count Unicode code points. */
function renderContext(glyph, sentenceId) {
  const node = el("lexeme-context");
  node.textContent = "";
  const sentence = state.sentences.find((s) => s.id === sentenceId);
  const text = sentence ? sentence.text : "";
  el("lexeme-context-label").hidden = !text;
  const chars = Array.from(text);
  const { start, end } = state.lexeme || {};
  if (Number.isInteger(start) && Number.isInteger(end) && chars.slice(start, end).join("") === glyph) {
    node.appendChild(document.createTextNode(chars.slice(0, start).join("")));
    const hit = document.createElement("span");
    hit.className = "hit";
    hit.textContent = glyph;
    node.appendChild(hit);
    node.appendChild(document.createTextNode(chars.slice(end).join("")));
  } else {
    // Older payloads have no occurrence spans; preserve their existing highlighting.
    let rest = text;
    while (rest) {
      const at = glyph ? rest.indexOf(glyph) : -1;
      if (at === -1) { node.appendChild(document.createTextNode(rest)); break; }
      if (at > 0) node.appendChild(document.createTextNode(rest.slice(0, at)));
      const hit = document.createElement("span"); hit.className = "hit"; hit.textContent = glyph;
      node.appendChild(hit); rest = rest.slice(at + glyph.length);
    }
  }
}

/** Local evidence and contextual generation deliberately have independent requests/state. */
function lexicalRequest(span, mode = "contextual", language = state.language) {
  const controller = new AbortController();
  let timer;
  const deadline = new Promise((resolve, reject) => {
    timer = setTimeout(() => {
      reject(new Error(mode === "dictionary" ? "Dictionary unavailable." : "Meaning timed out. Please retry."));
      controller.abort();
    }, mode === "dictionary" ? 5000 : 20000);
  });
  const request = api(`/api/reader/${documentId}/lexical`, {
    method: "POST", signal: controller.signal,
    body: JSON.stringify({...spanRequest(span), target_language: language,
      ...(mode === "dictionary" ? {mode} : {})}),
  }).then((result) => {
    if (mode === "dictionary") {
      if (typeof result.dictionary?.available !== "boolean" || !Array.isArray(result.dictionary.candidates))
        throw new Error("Dictionary unavailable.");
    } else if (typeof result.meaning !== "string" || !result.meaning.trim()) {
      throw new Error("Meaning unavailable. Please retry.");
    }
    return result;
  });
  return Promise.race([request, deadline]).finally(() => clearTimeout(timer));
}

function dictionarySense(sense, showArticle) {
  const li = document.createElement("li");
  const grammar = new Set(["neuter", "masculine", "feminine", "common-gender", "countable", "uncountable"]);
  const labels = (sense.labels || []).filter(t => !grammar.has(t));
  li.textContent = [showArticle && sense.articles?.length ? sense.articles.join(" / ") : "",
    labels.length ? `(${labels.join(", ")})` : "", ...(sense.glosses || [])].filter(Boolean).join(" ");
  return li;
}

function dictionaryCandidate(candidate, detailsTarget = null) {
  const box = document.createElement("div");
  box.className = "dictionary-candidate";
  const articles = [...new Set(candidate.senses.flatMap(s => s.articles || []))];
  const singleArticle = articles.length === 1 ? articles[0] + " " : "";
  const heading = document.createElement("p");
  heading.className = "dictionary-lemma";
  heading.textContent = `${candidate.lemma !== state.lexeme.glyph ? "→ " : ""}${singleArticle}${candidate.lemma}`
    + (candidate.plural.length ? ` · ${candidate.plural.join(", ")}` : "") + ` [${candidate.pos}]`;
  box.appendChild(heading);
  const senses = document.createElement("ul");
  for (const sense of candidate.senses.slice(0, 2)) senses.appendChild(dictionarySense(sense, articles.length > 1));
  box.appendChild(senses);
  if (candidate.senses.length > 2 || candidate.diminutive.length) {
    const more = document.createElement("details"), summary = document.createElement("summary");
    summary.textContent = "More senses and forms"; more.appendChild(summary);
    const rest = document.createElement("ul");
    for (const sense of candidate.senses.slice(2)) rest.appendChild(dictionarySense(sense, articles.length > 1));
    more.appendChild(rest);
    if (candidate.diminutive.length) {
      const diminutive = document.createElement("p");
      diminutive.textContent = `Diminutive: ${candidate.diminutive.join(", ")}`; more.appendChild(diminutive);
    }
    (detailsTarget || box).appendChild(more);
  }
  return box;
}

function renderDutchDictionary() {
  const visible = state.capabilities?.local_dictionary === "dutch";
  el("lexeme-dictionary").hidden = !visible;
  const additional = el("lexeme-dictionary-more"); additional.textContent = "";
  const sources = el("lexeme-dictionary-sources");
  const links = el("lexeme-dictionary-links"); links.textContent = "";
  additional.hidden = true; sources.hidden = true;
  if (!visible) return;
  const container = el("lexeme-dictionary-content"); container.textContent = "";
  const result = state.lexeme?.dictionary;
  el("lexeme-dictionary-label").textContent = result?.is_ambiguous
    ? result.candidates.length > 1 ? `Dictionary · multiple analyses (${result.candidates.length})`
      : "Dictionary · sense-dependent articles" : "Dictionary";
  if (!result || !result.available || !result.candidates.length) {
    container.textContent = !result ? "Loading dictionary…" : !result.available
      ? "Dictionary unavailable. Contextual meaning remains available." : "No local dictionary entry. Contextual meaning may help.";
    return;
  }
  for (const candidate of result.candidates.slice(0, 2)) container.appendChild(dictionaryCandidate(candidate, additional));
  if (result.candidates.length > 2) {
    const more = document.createElement("details"), summary = document.createElement("summary");
    summary.textContent = `Other analyses (${result.candidates.length - 2})`; more.appendChild(summary);
    for (const candidate of result.candidates.slice(2)) more.appendChild(dictionaryCandidate(candidate));
    additional.appendChild(more);
  }
  additional.hidden = !additional.children.length;
  sources.hidden = false;
  for (const candidate of result.candidates) {
    const source = document.createElement("a");
    source.textContent = `${candidate.lemma} [${candidate.pos}] · Wiktionary`;
    source.href = candidate.source_url; source.target = "_blank"; source.rel = "noopener noreferrer";
    const line = document.createElement("p"); line.appendChild(source); links.appendChild(line);
  }
}

async function loadDutchDictionary() {
  const lexeme = state.lexeme;
  if (!lexeme || state.capabilities?.local_dictionary !== "dutch") return;
  try {
    const result = await lexicalRequest({sentenceId:lexeme.sentenceId,start:lexeme.start,
      end:lexeme.end,text:lexeme.glyph}, "dictionary");
    lexeme.dictionary = result.dictionary;
  } catch (error) { lexeme.dictionary = {available:false,candidates:[]}; }
  if (state.lexeme === lexeme) renderDutchDictionary();
}

function renderLexemeBody(glyph) {
  renderDutchDictionary();
  el("lexeme-retry").hidden = true;
  el("lexeme-note").textContent = "";
  el("lexeme-pinyin").hidden = !state.capabilities?.pinyin && Boolean(state.capabilities);
  if (state.capabilities?.internal_save) {
    el("lexeme-glyph").textContent = glyph;
    el("lexeme-pinyin").textContent = "";
    const list = el("lexeme-senses");
    list.textContent = "";
    const item = document.createElement("li");
    const entry = state.lexeme?.meanings?.[state.language];
    item.textContent = entry?.meaning || "Loading meaning…";
    list.appendChild(item);
    el("lexeme-senses-label").hidden = false;
    el("lexeme-senses-label").textContent = "Meaning in context";
    const dutch = state.capabilities?.local_dictionary === "dutch";
    el("lexeme-source").textContent = !dutch && entry ? "Generated contextual meaning" : "";
    el("lexeme-note").textContent = dutch ? "" : entry?.context_note || "";
    el("lexeme-context-label").textContent = "In context";
    renderContext(glyph, state.lexeme.sentenceId);
    return;
  }
  el("lexeme-context-label").textContent = "В контексте";
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
  if (!senses.length) {
    const missing = document.createElement("li");
    missing.textContent = "Нет словарного определения.";
    list.appendChild(missing);
  }
  el("lexeme-senses-label").hidden = false;
  el("lexeme-senses-label").textContent =
    !senses.length ? "Значение" : source === "compositional" ? "Составное значение"
      : source === "contextual" ? "Значение (в контексте)" : "Значение (из словаря)";
  el("lexeme-source").textContent = senses.length ? SOURCE_LABELS[source] || "" : "";
  if (state.lexeme) renderContext(glyph, state.lexeme.sentenceId);
}

function openLexeme(token, sentenceId, node) {
  stopSpeaking("word");
  hideSelectionActions();
  pronunciationButton.setAttribute("aria-label", `Pronounce ${token.t.trim()}`);
  state.lexeme = {
    glyph: token.t, sentenceId, start: token.start, end: token.end, override: token.override || 0,
    saved: (state.savedVocabulary || []).some((item) => item.sentence_id === sentenceId
      && item.source_sentence === state.sentences.find((s) => s.id === sentenceId)?.text
      && item.normalized_text === token.t.normalize("NFC").toLowerCase().trim().replace(/\s+/g," ")),
  };
  closeLexeme.origin = node;
  renderLexemeBody(token.t);
  renderLexemeAction();
  markInspecting(node);
  el("lexeme").hidden = false;
  el("scrim").hidden = false;
  el("lexeme-action").focus();
  loadDutchDictionary();
  if (state.capabilities?.internal_save) loadContextualMeaning();
}

/** Lookup is occurrence/context-owned: never put Dutch senses in the glyph glossary. */
async function loadContextualMeaning() {
  const lexeme = state.lexeme;
  const language = state.language;
  if (!lexeme || !state.capabilities?.internal_save) return;
  lexeme.meanings ||= {};
  lexeme.requests ||= {};
  if (lexeme.meanings[language]) { renderLexemeBody(lexeme.glyph); return; }
  try {
    lexeme.requests[language] ||= lexicalRequest({sentenceId:lexeme.sentenceId,
      start:lexeme.start,end:lexeme.end,text:lexeme.glyph}, "contextual", language)
      .finally(() => { delete lexeme.requests[language]; });
    const result = await lexeme.requests[language];
    lexeme.meanings[language] = result;
    // A completed save wins over an older in-flight lookup reporting unsaved.
    lexeme.saved = lexeme.saved || result.saved;
    if (state.lexeme === lexeme && state.language === language) {
      renderLexemeBody(lexeme.glyph); renderLexemeAction();
    }
  } catch (error) {
    if (state.lexeme !== lexeme || state.language !== language) return;
    el("lexeme-senses").textContent = error.message;
    el("lexeme-retry").hidden = false;
  }
}

async function saveInternal(span) {
  const sourceSentence = state.sentences.find((s) => s.id === span.sentenceId)?.text;
  const already = (state.savedVocabulary || []).some((item) => item.sentence_id === span.sentenceId
    && item.source_sentence === sourceSentence
    && item.normalized_text === span.text.normalize("NFC").toLowerCase().trim().replace(/\s+/g," "));
  const result = await api(`/api/reader/${documentId}/saved-vocabulary`, {
    method:"POST", body:JSON.stringify({...spanRequest(span), target_language:state.language}),
  });
  state.savedVocabulary ||= [];
  state.savedVocabulary = state.savedVocabulary.filter((item) => item.id !== result.item.id);
  state.savedVocabulary.push(result.item);
  toast(already ? "✓ Already saved" : "✓ Saved");
  return result;
}

async function saveInspector() {
  const lexeme = state.lexeme;
  if (!lexeme || lexeme.saving || lexeme.saved) return;
  lexeme.saving = true; renderLexemeAction();
  try {
    await saveInternal({sentenceId:lexeme.sentenceId,start:lexeme.start,end:lexeme.end,text:lexeme.glyph});
    lexeme.saved = true;
  } catch (error) { toast(error.message, true); }
  finally { lexeme.saving = false; if(state.lexeme === lexeme) renderLexemeAction(); }
}

async function saveSelectedExpression(span) {
  // Meaning is optional: provider failure must not prevent a private source-preserving save.
  try {
    await lexicalRequest(span);
  } catch (error) { /* Save without meaning; a later inspector lookup can retry. */ }
  await saveInternal(span);
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

/** True while the reader holds a native text selection, which a tap must not override. */
function selectingText() {
  const selection = window.getSelection && window.getSelection();
  return Boolean(selection && selection.rangeCount && !selection.isCollapsed);
}

/**
 * Source text only: every canonical character is a text node of the source span and
 * pinyin lives in <rt>. Words are spans, because browsers cannot start a selection
 * inside a <button>.
 */
function renderSource(sentence, source) {
  source.textContent = "";
  for (const token of sentence.tokens) {
    if (!token.w) {
      source.appendChild(document.createTextNode(token.t));
      continue;
    }
    const pinyin = (state.glossary[token.t] || {}).p || "";
    const word = document.createElement("span");
    word.className = "word";
    word.dataset.glyph = token.t;
    if (token.override) word.dataset.override = String(token.override);
    word.setAttribute("role", "button");
    if (state.keyboard) word.setAttribute("tabindex", "0");
    const picked = isPicked(token.t);
    word.classList.toggle("picked", picked);
    word.setAttribute("aria-pressed", picked ? "true" : "false");
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
      event.stopPropagation();
      // The click that ends a drag selection belongs to the selection, not the word.
      if (selectingText()) return;
      openLexeme(token, sentence.id, word);
    });
    word.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      // Keyboard activation only: Space would otherwise scroll the page.
      event.preventDefault();
      openLexeme(token, sentence.id, word);
    });
    source.appendChild(word);
  }
}

/**
 * Words are tab stops only for keyboard use: Chrome starts no drag selection on a
 * focusable element, so a pointer press makes them plain text again first.
 */
function setWordsFocusable(on) {
  if (state.keyboard === on) return;
  state.keyboard = on;
  document.querySelectorAll(".word").forEach((word) => {
    if (on) word.setAttribute("tabindex", "0");
    else word.removeAttribute("tabindex");
  });
}

function renderSentence(sentence) {
  const node = document.createElement("span");
  node.className = "sentence";
  node.dataset.sentence = String(sentence.id);
  node.id = `s-${sentence.id}`;
  const source = document.createElement("span");
  source.className = "sentence-source";
  node.appendChild(source);
  renderSource(sentence, source);
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
  if (state.capabilities?.mosaic !== false) controls.appendChild(mark);
  if ((canSpeak && state.capabilities?.speech_language) || (window.Audio && state.audio && sentence.audio)) controls.appendChild(audioControl(sentence));
  if (state.capabilities?.internal_save) {
    node.classList.add("sentence-learnable");
    const save = document.createElement("button");
    save.type = "button";
    save.className = "sentence-save";
    save.setAttribute("aria-label", "Save sentence");
    save.setAttribute("aria-pressed", "false");
    save.title = "Save sentence for Review";
    const bookmark = document.createElementNS(SVG_NS, "svg");
    bookmark.setAttribute("class", "sentence-save-icon");
    bookmark.setAttribute("viewBox", "0 0 24 24");
    bookmark.setAttribute("aria-hidden", "true");
    bookmark.setAttribute("focusable", "false");
    const outline = document.createElementNS(SVG_NS, "path");
    outline.setAttribute("d", "M6 4.5A1.5 1.5 0 0 1 7.5 3h9A1.5 1.5 0 0 1 18 4.5V21l-6-4-6 4Z");
    outline.setAttribute("stroke", "currentColor");
    outline.setAttribute("stroke-width", "1.7");
    outline.setAttribute("stroke-linejoin", "round");
    bookmark.appendChild(outline);
    save.appendChild(bookmark);
    save.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (save.disabled) return;
      save.disabled = true;
      save.setAttribute("aria-busy", "true");
      try {
        await api(`/api/reader/${documentId}/saved-sentences`, {
          method: "POST", body: JSON.stringify({sentence_id: sentence.id, target_language: state.language}),
        });
        save.setAttribute("aria-label", "Sentence saved");
        save.setAttribute("aria-pressed", "true");
        save.title = "Sentence saved for Review";
      } catch (error) { save.disabled = false; toast(error.message, true); }
      finally { save.setAttribute("aria-busy", "false"); }
    });
    controls.appendChild(save);
  }
  node.appendChild(controls);
  if (state.sourceLanguage === "nl" || /[㐀-䶿一-鿿豈-﫿]/.test(sentence.text)) {
    const translation = translationControl(sentence);
    controls.appendChild(translation.action);
    node.appendChild(translation.box);
  }
  return node;
}

const HAN_ONLY = /^[㐀-䶿一-鿿豈-﫿]+$/;
const ONE_SENTENCE = "Select Chinese text within one sentence.";

function hasClass(node, name) {
  return Boolean(node && node.nodeType === 1 && node.classList && node.classList.contains(name));
}

function closestNode(node, test) {
  for (let at = node; at; at = at.parentNode) if (test(at)) return at;
  return null;
}

const isRt = (node) => node.nodeType === 1 && String(node.tagName).toLowerCase() === "rt";

/** Resolve an element boundary to the text node next to it, as the range edge reads it. */
function leafPoint(node, offset, edge) {
  while (node && node.nodeType !== 3) {
    const children = Array.from(node.childNodes || []);
    if (!children.length) break;
    if (edge === "start") {
      if (offset < children.length) { node = children[offset]; offset = 0; }
      else { node = children[children.length - 1]; offset = pointLength(node); }
    } else if (offset > 0) {
      node = children[Math.min(offset, children.length) - 1]; offset = pointLength(node);
    } else {
      node = children[0]; offset = 0;
    }
  }
  return { node, offset };
}

function pointLength(node) {
  return node.nodeType === 3 ? node.data.length : (node.childNodes || []).length;
}

/** Source text nodes in document order: pinyin is display metadata, never source. */
function sourceLeaves(node, out = []) {
  for (const child of Array.from(node.childNodes || [])) {
    if (child.nodeType === 3) out.push(child);
    else if (!isRt(child)) sourceLeaves(child, out);
  }
  return out;
}

/** Code points of source text before a point; JS offsets are UTF-16, spans are code points. */
function sourceOffset(source, point, edge) {
  let { node, offset } = point;
  const rt = closestNode(node, isRt);
  if (rt) {
    // A handle resting on pinyin covers the whole syllable's character.
    const leaf = sourceLeaves(rt.parentNode)[0];
    if (!leaf) return null;
    node = leaf;
    offset = edge === "start" ? 0 : leaf.data.length;
  }
  let total = 0;
  for (const leaf of sourceLeaves(source)) {
    if (leaf === node) return total + Array.from(leaf.data.slice(0, offset)).length;
    if (node.nodeType === 1 && closestNode(leaf, (n) => n === node)) return total;
    total += Array.from(leaf.data).length;
  }
  return node.nodeType === 1 ? total : null;
}

/**
 * Map a native DOM Range to one canonical sentence span. Returns null when the
 * selection involves no Reader source text, or { error } when it cannot be one expression.
 */
function selectionSpan(range) {
  if (!range || range.collapsed) return null;
  const boundaryError = state.capabilities?.internal_save ? "Select source text within one sentence." : ONE_SENTENCE;
  const ends = ["start", "end"].map((edge) => {
    const point = leafPoint(range[`${edge}Container`], range[`${edge}Offset`], edge);
    const source = point.node && closestNode(point.node, (n) => hasClass(n, "sentence-source"));
    return { edge, point, source };
  });
  if (!ends[0].source && !ends[1].source) return null;
  if (ends[0].source !== ends[1].source) return { error: boundaryError };
  const source = ends[0].source;
  const holder = closestNode(source, (n) => hasClass(n, "sentence"));
  const sentence = holder && state.sentences.find((s) => String(s.id) === holder.dataset.sentence);
  if (!sentence) return null;
  const start = sourceOffset(source, ends[0].point, "start");
  const end = sourceOffset(source, ends[1].point, "end");
  if (start === null || end === null || end <= start) return null;
  const text = Array.from(sentence.text).slice(start, end).join("");
  if (state.capabilities?.internal_save) {
    const chars = Array.from(sentence.text);
    let left = start, right = end;
    while (left < right && /\s/.test(chars[left])) left++;
    while (right > left && /\s/.test(chars[right-1])) right--;
    const selected = chars.slice(left,right).join("");
    if (!selected || Array.from(selected).length > 200) return {error:"Select a word or expression within one sentence (up to 200 characters)."};
    return {sentenceId:sentence.id,start:left,end:right,text:selected};
  }
  if (!HAN_ONLY.test(text)) return { error: boundaryError };
  return { sentenceId: sentence.id, start, end, text };
}

function currentRange() {
  const selection = window.getSelection && window.getSelection();
  if (!selection || !selection.rangeCount || selection.isCollapsed) return null;
  return selection.getRangeAt(0);
}

function clearNativeSelection() {
  const selection = window.getSelection && window.getSelection();
  if (selection && selection.removeAllRanges) selection.removeAllRanges();
}

function hideSelectionActions() {
  state.selection = null;
  state.selectionBarHeld = false;
  el("selection-actions").hidden = true;
}

/** Follows the native handles; it only reads the selection and never changes it. */
function refreshSelectionActions() {
  refreshSelectionActions.scheduled = false;
  if (state.selectionPending) return;
  const range = currentRange();
  if (!range) {
    // A tap on the bar may collapse the selection before its click arrives.
    if (!state.selectionBarHeld) hideSelectionActions();
    return;
  }
  // Expressions and Hanly are Chinese lexical tools; other languages keep plain selection.
  const span = (state.capabilities?.internal_save || state.sourceLanguage === "zh") ? selectionSpan(range) : null;
  if (!span || state.lexeme || state.open) { hideSelectionActions(); return; }
  state.selection = span.error ? null : span;
  el("selection-text").textContent = span.error || span.text;
  el("selection-text").classList.toggle("bad", Boolean(span.error));
  el("selection-expression").textContent = state.capabilities?.internal_save ? "Save expression" : "Make expression";
  el("selection-hanly").hidden = !state.capabilities?.hanly;
  el("selection-text").setAttribute("lang", state.sourceLanguage || "zh");
  el("selection-expression").disabled = Boolean(span.error) || (!state.capabilities?.internal_save && Array.from(span.text).length < 2);
  el("selection-hanly").disabled = Boolean(span.error);
  el("selection-actions").hidden = false;
}

function scheduleSelectionActions() {
  if (refreshSelectionActions.scheduled) return;
  refreshSelectionActions.scheduled = true;
  if (window.requestAnimationFrame) window.requestAnimationFrame(refreshSelectionActions);
  else refreshSelectionActions();
}

function spanRequest(span) {
  return { sentence_id: span.sentenceId, start: span.start, end: span.end, text: span.text };
}

/** Replace one sentence's lexical chunks; Mosaic, translation and audio controls stay. */
function applySentenceUpdate(result) {
  Object.assign(state.glossary, result.glossary || {});
  const sentence = state.sentences.find((s) => s.id === result.sentence.id);
  if (!sentence) return null;
  sentence.tokens = result.sentence.tokens;
  const holder = document.querySelector(`[data-sentence="${sentence.id}"]`);
  const source = holder && holder.querySelector(".sentence-source");
  if (source) renderSource(sentence, source);
  return source;
}

async function runSelectionAction(action) {
  const span = state.selection;
  if (!span || state.selectionPending) return;
  state.selectionPending = true;
  el("selection-expression").disabled = true;
  el("selection-hanly").disabled = true;
  try {
    await action(span);
    clearNativeSelection();
    state.selectionPending = false;
    hideSelectionActions();
  } catch (error) {
    // The native selection is left in place so the reader can retry or adjust it.
    toast(error.message, true);
    state.selectionPending = false;
    state.selectionBarHeld = false;
    refreshSelectionActions();
  }
}

async function makeExpression(span) {
  const result = await api(`/api/reader/${documentId}/lexical-overrides`, {
    method: "POST", body: JSON.stringify(spanRequest(span)),
  });
  const source = applySentenceUpdate(result);
  const token = (state.sentences.find((s) => s.id === span.sentenceId) || { tokens: [] })
    .tokens.find((t) => t.override === result.override.id);
  const node = source && Array.from(source.querySelectorAll(".word"))
    .find((n) => n.dataset.override === String(result.override.id));
  clearNativeSelection();
  if (token && node) openLexeme(token, span.sentenceId, node);
}

async function addSelectionToHanly(span) {
  if (isPicked(span.text)) { toast(`${span.text} is already in the Hanly basket.`); return; }
  const result = await api(`/api/reader/${documentId}/selection`, {
    method: "POST", body: JSON.stringify(spanRequest(span)),
  });
  if (!state.glossary[result.glyph]) state.glossary[result.glyph] = result.entry;
  toggleWord(result.glyph, result.sentence_id, { start: result.start, end: result.end });
  toast(`Added to the Hanly basket: ${result.glyph}`);
}

async function removeExpression(event) {
  event.stopPropagation();
  if (!state.lexeme || !state.lexeme.override) return;
  const button = el("lexeme-remove-expression");
  if (button.disabled) return;
  button.disabled = true;
  try {
    const result = await api(
      `/api/reader/${documentId}/lexical-overrides/${state.lexeme.override}`, { method: "DELETE" },
    );
    if (result.sentence) applySentenceUpdate(result);
    closeLexeme();
    toast("Custom expression removed.");
  } catch (error) {
    toast(`Could not remove the custom expression. ${error.message}`, true);
  } finally {
    button.disabled = false;
  }
}

function render(data) {
  const quality = el("sentence-quality");
  quality.hidden = !data.sentence_boundaries_approximate;
  quality.textContent = data.sentence_boundaries_approximate
    ? "В исходном тексте мало знаков конца предложения. Показаны приблизительные фрагменты для чтения; слова не изменены." : "";
  stopOriginal();
  stopSpeaking();
  state.title = data.title;
  state.audio = data.audio || null;
  state.audioStatus = data.audio_status || "";
  state.audioNotice = false;
  state.sentences = data.sentences;
  state.glossary = data.glossary || {};
  state.savedVocabulary = data.saved_vocabulary || [];
  state.sourceLanguage = data.language || "zh";
  state.capabilities = data.capabilities || (state.sourceLanguage === "nl"
    ? {lexical_inspector:"contextual",speech_language:"nl-NL",pinyin:false,hanly:false,mosaic:false,internal_save:true}
    : state.sourceLanguage === "zh" ? {lexical_inspector:"chinese",speech_language:"zh-CN",pinyin:true,hanly:true,mosaic:true,internal_save:false}
    : {lexical_inspector:null,speech_language:"",pinyin:false,hanly:false,mosaic:false,internal_save:false});
  el("counters").hidden = !state.capabilities.hanly && !state.capabilities.mosaic;
  el("attribution").hidden = state.capabilities.lexical_inspector !== "chinese";
  el("text").setAttribute("lang", state.sourceLanguage);
  el("pinyin-toggle").hidden = !state.capabilities.pinyin;
  state.available = { hanly: data.hanly_available, mosaic: data.mosaic_available };
  state.reasons = { hanly: data.hanly_error, mosaic: data.mosaic_error };
  hideSelectionActions();
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
  hideSelectionActions();
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
    : "Selected sentences will be added to this document's Mandarin Mosaic pack with English translations. Missing translations will be generated before uploading.";
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
          items: sent.map((w) => (Number.isInteger(w.start)
            ? { glyph: w.glyph, sentence_id: w.sentenceId, start: w.start, end: w.end }
            : { glyph: w.glyph, sentence_id: w.sentenceId })),
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
  if (state.capabilities?.internal_save) { saveInspector(); return; }
  const { glyph, sentenceId, start, end, override } = state.lexeme;
  // A custom expression keeps its basket entry if the expression is later removed.
  toggleWord(glyph, sentenceId, override ? { start, end } : null);
  closeLexeme();
});
el("lexeme-remove-expression").addEventListener("click", removeExpression);
el("lexeme-retry").addEventListener("click", loadContextualMeaning);
el("selection-expression").addEventListener("click", () => runSelectionAction(state.capabilities?.internal_save ? saveSelectedExpression : makeExpression));
el("selection-hanly").addEventListener("click", () => runSelectionAction(addSelectionToHanly));
el("selection-cancel").addEventListener("click", () => {
  clearNativeSelection();
  hideSelectionActions();
});
const selectionBar = el("selection-actions");
selectionBar.addEventListener("pointerdown", () => { state.selectionBarHeld = true; });
selectionBar.addEventListener("pointercancel", () => { state.selectionBarHeld = false; });
document.addEventListener("pointerdown", (event) => {
  if (!closestNode(event.target, (n) => n === selectionBar)) state.selectionBarHeld = false;
});
// Desktop browsers collapse the selection on mousedown; the bar's controls must not.
selectionBar.addEventListener("mousedown", (event) => event.preventDefault());
document.addEventListener("selectionchange", scheduleSelectionActions);
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
  if (event.key === "Tab") setWordsFocusable(true);
});
document.addEventListener("pointerdown", () => setWordsFocusable(false), true);
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

if (documentId) {
  showView("document");
  api(`/api/reader/${documentId}`)
    .then(render)
    .catch((error) => {
      el("text").textContent = error.message;
      el("text").setAttribute("aria-busy", "false");
    });
} else {
  loadLibrary();
}


function showView(view) {
  stopOriginal();
  stopSpeaking();
  el("review").hidden = view !== "review";
  if (view !== "document") el("sentence-quality").hidden = true;
  for (const id of ["bar", "text", "attribution"]) el(id).hidden = view !== "document";
  el("library").hidden = view !== "library";
  el("new-document").hidden = view !== "create";
}

function openDocument(id) {
  // Real URLs preserve direct linking, reload and the browser back stack.
  location.assign(`/reader/?doc=${encodeURIComponent(id)}`);
}

async function loadLibrary() {
  showView("library");
  el("library-status").textContent = "Loading…";
  el("library-retry").hidden = true;
  try {
    const data = await api("/api/reader/documents");
    const list = el("document-list");
    list.textContent = "";
    for (const item of data.documents) {
      const card = document.createElement("a");
      card.className = "document-card";
      card.href = `/reader/?doc=${encodeURIComponent(item.id)}`;
      const title = document.createElement("strong");
      title.textContent = item.title;
      const detail = document.createElement("small");
      const date = new Date(item.last_opened || item.created);
      detail.textContent = `${item.source_label} · ${date.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
      card.append(title, detail);
      list.appendChild(card);
    }
    el("library-status").textContent = data.documents.length
      ? "Your 100 most recent documents appear here."
      : "Paste Chinese text or upload a .txt/.md file.";
  } catch (error) {
    el("library-status").textContent = error.message;
    el("library-retry").hidden = false;
  }
}

el("new-text").addEventListener("click", () => {
  showView("create");
  el("new-text-input").focus();
});
el("create-back").addEventListener("click", () => showView("library"));
el("library-retry").addEventListener("click", loadLibrary);
el("clipboard-paste").addEventListener("click", async () => {
  const button = el("clipboard-paste");
  button.disabled = true;
  try {
    if (!window.navigator || !window.navigator.clipboard || !window.navigator.clipboard.readText) {
      throw new Error("Clipboard unavailable");
    }
    const text = await window.navigator.clipboard.readText();
    el("new-text-input").value = text;
    el("create-status").textContent = "";
  } catch (error) {
    el("create-status").textContent = "Clipboard access isn't available here. Paste into the text box instead.";
  } finally {
    button.disabled = false;
    el("new-text-input").focus();
  }
});

el("create-reader").addEventListener("click", async () => {
  const button = el("create-reader");
  if (button.disabled) return;
  if (!el("new-text-input").value.trim()) {
    el("create-status").textContent = "Paste some Chinese text first.";
    return;
  }
  button.disabled = true;
  el("create-status").textContent = "Creating…";
  try {
    const input = el("new-text-input").value.trim();
    const payload = /^https?:\/\//i.test(input)
      ? { source_type: "npo", url: input }
      : { source_type: "pasted_text", text: el("new-text-input").value, title: el("new-title").value || "" };
    const result = await api("/api/reader/documents", {
      method: "POST", body: JSON.stringify(payload),
    });
    openDocument(result.id);
  } catch (error) {
    el("create-status").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

el("upload-file").addEventListener("click", () => el("file-input").click());
el("file-input").addEventListener("change", async () => {
  const file = el("file-input").files[0];
  if (!file) return;
  const button = el("upload-file");
  button.disabled = true;
  el("library-status").textContent = "Opening file…";
  try {
    if (!/\.(txt|md|markdown)$/i.test(file.name)) throw new Error("Choose a .txt, .md or .markdown file.");
    if (file.size > 512 * 1024) throw new Error("Reader files must be at most 512 KiB.");
    const bytes = new Uint8Array(await file.arrayBuffer());
    // Bounded bytes; no file blob is persisted. Server strictly decodes UTF-8.
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    const result = await api("/api/reader/documents", { method: "POST",
      body: JSON.stringify({ source_type: "file", filename: file.name, content: btoa(binary) }),
    });
    openDocument(result.id);
  } catch (error) {
    el("library-status").textContent = error.message;
  } finally {
    button.disabled = false;
    el("file-input").value = "";
  }
});

el("rename-document").addEventListener("click", () => {
  el("rename-title").value = state.title;
  el("rename-status").textContent = "";
  el("rename-panel").hidden = false;
  el("rename-title").focus();
});
el("rename-cancel").addEventListener("click", () => { el("rename-panel").hidden = true; });
el("rename-save").addEventListener("click", async () => {
  const button = el("rename-save");
  if (button.disabled) return;
  button.disabled = true;
  try {
    const result = await api(`/api/reader/documents/${documentId}`, {
      method: "PATCH", body: JSON.stringify({ title: el("rename-title").value }),
    });
    state.title = result.title;
    el("title").textContent = result.title;
    el("source-line").textContent = "";
    document.title = result.title;
    el("rename-panel").hidden = true;
  } catch (error) {
    el("rename-status").textContent = error.message;
  } finally {
    button.disabled = false;
  }
});


// Review uses the same authenticated API and speech channel as Reader.
const reviewSession = {items: [], index: 0, epoch: 0, busy: false};
function reviewButton(text, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary";
  button.textContent = text;
  button.addEventListener("click", action);
  return button;
}
async function openReview() {
  showView("review");
  const epoch = ++reviewSession.epoch;
  reviewSession.busy = false;
  el("review-card").hidden = true;
  el("review-start").hidden = true;
  el("review-status").textContent = "Loading…";
  try {
    const data = await api("/api/reader/review");
    if (epoch !== reviewSession.epoch) return;
    el("review-status").textContent = data.due ? `${data.due} due` : "Nothing due right now.";
    if (!data.due && data.next_due_at) el("review-status").textContent += ` Next: ${new Date(data.next_due_at).toLocaleString()}`;
    el("review-start").textContent = "Start Review";
    el("review-start").hidden = !data.due;
  } catch (error) {
    if (epoch !== reviewSession.epoch) return;
    el("review-status").textContent = error.message;
    el("review-start").hidden = false;
    el("review-start").textContent = "Retry Review";
  }
}
async function startReview() {
  if (reviewSession.busy) return;
  reviewSession.busy = true;
  const epoch = ++reviewSession.epoch;
  el("review-start").disabled = true;
  try {
    const data = await api("/api/reader/review", {method: "POST", body: JSON.stringify({listening: canSpeak})});
    if (epoch !== reviewSession.epoch) return;
    reviewSession.items = data.items;
    reviewSession.index = 0;
    el("review-start").hidden = true;
    renderReviewCard();
  } catch (error) {
    if (epoch === reviewSession.epoch) el("review-status").textContent = error.message;
  } finally {
    if (epoch === reviewSession.epoch) reviewSession.busy = false;
    el("review-start").disabled = false;
  }
}
function reviewContext(card, blank = false) {
  const context = document.createElement("p");
  context.className = "review-context";
  if (card.span) {
    const chars = Array.from(card.text);
    const [start, end] = card.span;
    context.appendChild(document.createTextNode(chars.slice(0, start).join("")));
    const target = document.createElement(blank ? "span" : "mark");
    target.textContent = blank ? "_____" : chars.slice(start, end).join("");
    context.appendChild(target);
    context.appendChild(document.createTextNode(chars.slice(end).join("")));
  } else context.textContent = card.text;
  return context;
}
function renderReviewCard() {
  stopSpeaking();
  const box = el("review-card");
  box.textContent = "";
  const card = reviewSession.items[reviewSession.index];
  box.hidden = !card;
  if (!card) {
    el("review-status").textContent = "Session complete. Your reviews are saved.";
    el("review-start").hidden = false;
    el("review-start").textContent = "Check due items";
    return;
  }
  el("review-status").textContent = `${reviewSession.index + 1} / ${reviewSession.items.length}`;
  const heading = document.createElement("h2");
  heading.textContent = {recognition: "Recognition", cloze: "Cloze", listening: "Listening"}[card.exercise];
  box.appendChild(heading);
  if (card.exercise === "listening") {
    box.appendChild(reviewButton("▶ Play sentence", () => {
      speakText(card.text, {owner: "review", speechLanguage: card.speech_language});
    }));
  } else {
    box.appendChild(reviewContext(card, card.exercise === "cloze"));
    if (card.exercise === "recognition" && card.kind !== "sentence") {
      const prompt = document.createElement("p");
      prompt.textContent = `What does “${card.target}” mean here?`;
      box.appendChild(prompt);
    }
  }
  const epoch = reviewSession.epoch;
  const reveal = reviewButton(card.exercise === "listening" ? "Show text" : card.kind === "sentence" ? "Show translation" : "Show answer", async () => {
    if (reveal.disabled) return;
    reveal.disabled = true;
    try {
      const answer = await api(`/api/reader/review/${card.id}/answer`, {method: "POST", body: "{}"});
      if (epoch !== reviewSession.epoch) return;
      reveal.hidden = true;
      if (card.exercise !== "recognition") {
        const old = box.querySelector(".review-context");
        if (old) old.remove();
        box.appendChild(reviewContext(card));
      }
      const meaning = document.createElement("p");
      meaning.className = "review-meaning";
      meaning.textContent = (card.target ? `${card.target} · ` : "") + answer.meaning;
      box.appendChild(meaning);
      const provenance = document.createElement("a");
      provenance.href = `/reader/?doc=${encodeURIComponent(card.document_id)}#s-${card.sentence_id}`;
      provenance.textContent = card.title;
      box.appendChild(provenance);
      const grades = document.createElement("div");
      grades.className = "review-grades";
      let pending = false;
      let chosen = null;
      for (const grade of ["Again", "Hard", "Good", "Easy"]) {
        const button = reviewButton(grade, async () => {
          if (pending || (chosen && chosen !== grade)) return;
          chosen = grade;
          pending = true;
          for (const control of grades.children) control.disabled = true;
          try {
            await api(`/api/reader/review/${card.id}/grade`, {method: "POST", body: JSON.stringify({
              grade: grade.toLowerCase(), exercise: card.exercise, interaction: card.interaction,
            })});
            if (epoch !== reviewSession.epoch) return;
            reviewSession.index++;
            renderReviewCard();
          } catch (error) {
            if (epoch !== reviewSession.epoch) return;
            el("review-status").textContent = `${error.message} Retry ${grade}, or reopen Review.`;
            button.disabled = false;
          } finally { pending = false; }
        });
        grades.appendChild(button);
      }
      box.appendChild(grades);
    } catch (error) {
      if (epoch !== reviewSession.epoch) return;
      reveal.disabled = false;
      el("review-status").textContent = error.message;
    }
  });
  box.appendChild(reveal);
}
el("review-open").addEventListener("click", openReview);
el("review-start").addEventListener("click", startReview);
el("review-back").addEventListener("click", () => {
  reviewSession.epoch++;
  reviewSession.busy = false;
  loadLibrary();
});
