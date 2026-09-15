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
  if (node) node.classList.toggle("picked", state.picked.has(id));
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

function closeLexeme() {
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
  action.textContent = "Show translation";
  action.setAttribute("aria-expanded", "false");
  const content = document.createElement("span");
  content.className = "translation-text";
  content.id = `translation-${sentence.id}`;
  content.setAttribute("lang", "ru");
  content.setAttribute("aria-live", "polite");
  action.setAttribute("aria-controls", content.id);
  content.hidden = true;
  let translated = "";
  let loading = false;
  box.addEventListener("click", (event) => event.stopPropagation());
  action.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (loading) return;
    if (!content.hidden) {
      content.hidden = true;
      action.textContent = "Show translation";
      action.setAttribute("aria-expanded", "false");
      return;
    }
    if (!translated) {
      loading = true;
      action.disabled = true;
      action.textContent = "Translating…";
      action.setAttribute("aria-busy", "true");
      try {
        const result = await api(`/api/reader/${documentId}/sentences/${sentence.id}/translation`, {
          method: "POST", body: "{}",
        });
        if (result.sentence_id !== sentence.id || typeof result.translation !== "string" || !result.translation.trim()) {
          throw new Error("Invalid translation");
        }
        translated = result.translation;
        content.textContent = translated;
      } catch (error) {
        action.textContent = "Translation unavailable · Retry";
        return;
      } finally {
        loading = false;
        action.disabled = false;
        action.setAttribute("aria-busy", "false");
      }
    }
    content.hidden = false;
    action.textContent = "Hide translation";
    action.setAttribute("aria-expanded", "true");
  });
  box.appendChild(content);
  box.appendChild(action);
  return box;
}

function renderSentence(sentence) {
  const node = document.createElement("span");
  node.className = "sentence";
  node.dataset.sentence = String(sentence.id);
  for (const token of sentence.tokens) {
    if (!token.w) {
      node.appendChild(document.createTextNode(token.t));
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
    node.appendChild(word);
  }
  const mark = document.createElement("span");
  mark.className = "mark";
  mark.textContent = "◎";
  mark.title = "Select this sentence for Mandarin Mosaic";
  node.appendChild(mark);
  if (/[㐀-䶿一-鿿豈-﫿]/.test(sentence.text)) node.appendChild(translationControl(sentence));
  node.addEventListener("click", () => toggleSentence(sentence.id));
  return node;
}

function render(data) {
  state.title = data.title;
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
