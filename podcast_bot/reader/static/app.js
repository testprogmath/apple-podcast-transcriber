"use strict";

const telegram = window.Telegram && window.Telegram.WebApp;
const documentId = new URLSearchParams(location.search).get("doc") || "";
const initData = (telegram && telegram.initData) || "";
const PINYIN_KEY = "reader.pinyin";

const state = {
  title: "",
  sentences: [],
  words: [],
  picked: new Set(),
  open: null,
  lexeme: null,
  pinyin: false,
};

const el = (id) => document.getElementById(id);
const counters = { hanly: el("hanly-counter"), mosaic: el("mosaic-counter") };

function readPreference() {
  try {
    return localStorage.getItem(PINYIN_KEY) === "on";
  } catch (error) {
    return false;
  }
}

function writePreference(on) {
  try {
    localStorage.setItem(PINYIN_KEY, on ? "on" : "off");
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
  counters.mosaic.textContent = `Mandarin Mosaic · ${state.picked.size}`;
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

function renderLexemeAction() {
  const action = el("lexeme-action");
  const picked = isPicked(state.lexeme.glyph);
  action.textContent = picked ? "✓ In Hanly — remove" : "+ Add to Hanly";
  action.classList.toggle("remove", picked);
}

function openLexeme(token, sentenceId, node) {
  state.lexeme = { glyph: token.t, sentenceId };
  closeLexeme.origin = node;
  el("lexeme-glyph").textContent = token.t;
  el("lexeme-pinyin").textContent = token.p || "";
  el("lexeme-meaning").textContent = token.m || "";
  renderLexemeAction();
  markInspecting(node);
  el("lexeme").hidden = false;
  el("scrim").hidden = false;
  el("lexeme-action").focus();
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
    const word = document.createElement("button");
    word.type = "button";
    word.className = "word";
    word.dataset.glyph = token.t;
    word.setAttribute("aria-pressed", "false");
    word.setAttribute("aria-label", token.p ? `${token.t} ${token.p}` : token.t);
    if (token.p) {
      const ruby = document.createElement("ruby");
      ruby.appendChild(document.createTextNode(token.t));
      const rt = document.createElement("rt");
      rt.textContent = token.p;
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
  node.addEventListener("click", () => toggleSentence(sentence.id));
  return node;
}

function render(data) {
  state.title = data.title;
  state.sentences = data.sentences;
  state.available = { hanly: data.hanly_available, mosaic: data.mosaic_available };
  state.reasons = { hanly: data.hanly_error, mosaic: data.mosaic_error };
  el("title").textContent = data.title;
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
  el("basket-title").textContent = hanly ? "Hanly vocabulary" : "Mandarin Mosaic sentences";
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
    const label = document.createElement("span");
    label.textContent = entry.text;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", "Remove");
    remove.addEventListener("click", () => {
      if (hanly) toggleWord(entry.key);
      else toggleSentence(entry.key);
      openBasket(kind);
    });
    row.append(label, remove);
    items.appendChild(row);
  }
  const available = state.available[kind];
  const note = hanly
    ? "Tapped words and chunks are merged into this document's Hanly collection."
    : "Complete sentences are uploaded to this document's Mandarin Mosaic pack.";
  el("basket-note").textContent = available
    ? note
    : state.reasons[kind] || "Not configured on the server.";
  const upload = el("basket-upload");
  upload.textContent = `Upload ${entries.length} to ${hanly ? "Hanly" : "Mandarin Mosaic"}`;
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
      sent.forEach((w) => paintWord(w.glyph));
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
  writePreference(state.pinyin);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") dismissTop();
});

state.pinyin = readPreference();
applyPinyin(state.pinyin);

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
