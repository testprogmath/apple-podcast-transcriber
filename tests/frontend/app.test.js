"use strict";

const assert = require("assert");
const {
  setup, loadApp, memoryStorage, failingStorage, speechStub, css, markup,
} = require("./dom.js");

const DOCUMENT = {
  title: "Mami Chinese｜菲尔兹奖",
  hanly_available: true,
  mosaic_available: true,
  hanly_error: "",
  mosaic_error: "",
  paragraphs: [[0, 1]],
  glossary: {
    他们: { p: "tā men", en: ["they"] },
    获得: { p: "huò dé", ru: ["получать"], rs: "contextual", en: ["to obtain"] },
    了: { p: "le" },
    菲尔兹奖: { p: "fēi ěr zī jiǎng" },
    做研究: { p: "zuò yán jiū" },
    很难: { p: "hěn nán", ru: ["трудный", "тяжёлый"], rs: "bkrs", en: ["difficult"] },
  },
  sentences: [
    {
      id: 0,
      text: "他们获得了菲尔兹奖。",
      tokens: [
        { t: "他们", w: true },
        { t: "获得", w: true },
        { t: "了", w: true },
        { t: "菲尔兹奖", w: true },
        { t: "。", w: false },
      ],
    },
    {
      id: 1,
      text: "做研究很难。",
      tokens: [
        { t: "做研究", w: true },
        { t: "很难", w: true },
        { t: "。", w: false },
      ],
    },
  ],
};

const senses = (nodes) => nodes["lexeme-senses"].children.map((li) => li.textContent);

function start(options = {}) {
  const env = setup(options);
  if (options.speech) Object.assign(window, options.speech);
  const calls = [];
  const responses = options.responses || {};
  const payload = structuredClone(options.document || DOCUMENT);
  global.fetch = async (path, init) => {
    calls.push({ path, method: (init && init.method) || "GET", body: init && init.body });
    for (const [suffix, make] of Object.entries(responses)) {
      if (path.endsWith(suffix)) return make();
    }
    return { ok: true, json: async () => payload };
  };
  loadApp();
  env.calls = calls;
  return env;
}

async function settle() {
  for (let i = 0; i < 6; i += 1) await new Promise((r) => setImmediate(r));
}

const words = (body) => body.querySelectorAll(".word");
const word = (body, text) =>
  words(body).find((w) => (w.dataset.glyph || "") === text);
const sentence = (body, id) => body.querySelector(`[data-sentence="${id}"]`);

const tests = {};
const test = (name, fn) => (tests[name] = fn);

test("tokens render as keyboard-accessible buttons with ruby pinyin", async () => {
  const { body } = start();
  await settle();
  const node = word(body, "获得");
  assert.strictEqual(node.tagName, "button");
  assert.strictEqual(node.type, "button");
  assert.strictEqual(node.getAttribute("aria-pressed"), "false");
  assert.strictEqual(node.getAttribute("aria-label"), "获得 huò dé");
  const ruby = node.children[0];
  assert.strictEqual(ruby.tagName, "ruby");
  assert.strictEqual(ruby.children[1].tagName, "rt");
  assert.strictEqual(ruby.children[1].textContent, "huò dé");
  assert.strictEqual(node.textContent, "获得huò dé");
});

test("punctuation is plain text with no pinyin and no button", async () => {
  const { body } = start();
  await settle();
  assert.ok(!words(body).some((w) => w.dataset.glyph === "。"));
  assert.strictEqual(words(body).length, 6);
  assert.ok(!body.querySelectorAll("rt").some((rt) => rt.textContent === ""));
});

test("tapping a token opens the lexeme popup and selects nothing", async () => {
  const { body, nodes } = start();
  await settle();
  const node = word(body, "获得");
  node.click(body);
  assert.strictEqual(nodes.lexeme.hidden, false);
  assert.strictEqual(nodes["lexeme-glyph"].textContent, "获得");
  assert.strictEqual(nodes["lexeme-action"].textContent, "+ Add to Hanly");
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 0");
  assert.ok(!node.classes.has("picked"));
  assert.ok(node.classes.has("inspecting"));
});

test("popup always shows pinyin, and shows a known meaning", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "获得").click(body);
  assert.strictEqual(nodes["lexeme-pinyin"].textContent, "huò dé");
  assert.deepStrictEqual(senses(nodes), ["получать"]);
  assert.ok(!nodes.text.classes.has("pinyin"), "inline pinyin still off");
});

test("a token with no meaning shows an empty meaning rather than a label", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "做研究").click(body);
  assert.strictEqual(nodes["lexeme-pinyin"].textContent, "zuò yán jiū");
  assert.deepStrictEqual(senses(nodes), []);
  assert.strictEqual(nodes["lexeme-source"].textContent, "", "no source label either");
});

test("a contextual meaning is shown without a dictionary label", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "获得").click(body);
  assert.deepStrictEqual(senses(nodes), ["получать"]);
  assert.strictEqual(nodes["lexeme-source"].textContent, "");
});

test("every sense renders on its own line", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "很难").click(body);
  assert.deepStrictEqual(senses(nodes), ["трудный", "тяжёлый"]);
  assert.strictEqual(nodes["lexeme-source"].textContent, "大БКРС");
});

test("the language toggle switches the popup between Russian and English", async () => {
  const { body, nodes } = start();
  await settle();
  assert.strictEqual(nodes["language-toggle"].textContent, "RU", "Russian by default");
  word(body, "很难").click(body);
  assert.deepStrictEqual(senses(nodes), ["трудный", "тяжёлый"]);

  nodes["language-toggle"].fire("click");
  assert.strictEqual(nodes["language-toggle"].textContent, "EN");
  assert.deepStrictEqual(senses(nodes), ["difficult"], "the open popup updates in place");
  assert.strictEqual(nodes["lexeme-source"].textContent, "CC-CEDICT");

  nodes["language-toggle"].fire("click");
  assert.deepStrictEqual(senses(nodes), ["трудный", "тяжёлый"]);
});

test("a language with no entry falls back to the other rather than showing nothing", async () => {
  const { body, nodes } = start();
  await settle();
  nodes["language-toggle"].fire("click");
  word(body, "获得").click(body);
  assert.deepStrictEqual(senses(nodes), ["to obtain"]);
  nodes["lexeme-close"].fire("click");
  word(body, "他们").click(body);
  assert.deepStrictEqual(senses(nodes), ["they"]);
  nodes["language-toggle"].fire("click");
  assert.deepStrictEqual(senses(nodes), ["they"], "Russian absent, English shown instead");
});

test("the language preference is persisted and restored", async () => {
  const storage = memoryStorage();
  const first = start({ storage });
  await settle();
  first.nodes["language-toggle"].fire("click");
  assert.strictEqual(storage._store["reader.language"], "en");

  const second = start({ storage: memoryStorage({ "reader.language": "en" }) });
  await settle();
  assert.strictEqual(second.nodes["language-toggle"].textContent, "EN");
});

test("an unknown word still opens a usable popup and stays selectable", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "做研究").click(body);
  assert.strictEqual(nodes["lexeme-glyph"].textContent, "做研究");
  assert.strictEqual(nodes["lexeme-action"].textContent, "+ Add to Hanly");
  nodes["lexeme-action"].fire("click");
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 1");
});

test("the Reader carries CC-CEDICT attribution", () => {
  const html = markup();
  assert.match(html, /Dictionary data:/);
  assert.match(html, /cc-cedict/i);
  assert.match(html, /creativecommons\.org\/licenses\/by-sa\/4\.0/);
  assert.match(html, /CC BY-SA 4\.0/);
});

test("explicit Add selects the token, explicit Remove clears it", async () => {
  const { body, nodes } = start();
  await settle();
  const node = word(body, "获得");
  node.click(body);
  nodes["lexeme-action"].fire("click");
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 1");
  assert.ok(node.classes.has("picked"));
  assert.strictEqual(node.getAttribute("aria-pressed"), "true");
  assert.strictEqual(nodes.lexeme.hidden, true, "sheet closes after the action");

  node.click(body);
  assert.strictEqual(nodes["lexeme-action"].textContent, "✓ In Hanly — remove");
  nodes["lexeme-action"].fire("click");
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 0");
  assert.ok(!node.classes.has("picked"));
});

test("a selected item retains the sentence_id it was selected from", async () => {
  const { body, nodes, calls } = start({
    responses: {
      "/hanly": () => ({ ok: true, json: async () => ({ uploaded: 2, name: "D", total: 5, notes: [] }) }),
    },
  });
  await settle();
  word(body, "获得").click(body);
  nodes["lexeme-action"].fire("click");
  word(body, "很难").click(body);
  nodes["lexeme-action"].fire("click");
  nodes["hanly-counter"].fire("click");
  await nodes["basket-upload"].listeners.click[0]();
  await settle();
  const sent = JSON.parse(calls[calls.length - 1].body);
  assert.deepStrictEqual(sent.items, [
    { glyph: "获得", sentence_id: 0 },
    { glyph: "很难", sentence_id: 1 },
  ]);
});

test("re-selecting the same glyph elsewhere is deterministic: first context is kept", async () => {
  const repeated = JSON.parse(JSON.stringify(DOCUMENT));
  repeated.sentences[1].tokens.unshift({ t: "获得", w: true, p: "huò dé" });
  const { body, nodes, calls } = start({
    document: repeated,
    responses: {
      "/hanly": () => ({ ok: true, json: async () => ({ uploaded: 1, name: "D", total: 1, notes: [] }) }),
    },
  });
  await settle();
  const occurrences = words(body).filter((w) => w.dataset.glyph === "获得");
  assert.strictEqual(occurrences.length, 2);
  occurrences[0].click(body);
  nodes["lexeme-action"].fire("click");
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 1");
  occurrences[1].click(body);
  assert.strictEqual(
    nodes["lexeme-action"].textContent,
    "✓ In Hanly — remove",
    "the second occurrence reports the existing selection"
  );
  assert.ok(occurrences[1].classes.has("picked"), "both occurrences show as selected");
  nodes["hanly-counter"].fire("click");
  await nodes["basket-upload"].listeners.click[0]();
  await settle();
  const sent = JSON.parse(calls[calls.length - 1].body);
  assert.deepStrictEqual(sent.items, [{ glyph: "获得", sentence_id: 0 }], "first context wins");
});

test("pinyin toggle switches inline pinyin and persists the preference", async () => {
  const storage = memoryStorage();
  const { nodes } = start({ storage });
  await settle();
  assert.ok(!nodes.text.classes.has("pinyin"), "defaults to off");
  assert.strictEqual(nodes["pinyin-toggle"].textContent, "拼音 OFF");
  assert.strictEqual(nodes["pinyin-toggle"].getAttribute("aria-pressed"), "false");

  nodes["pinyin-toggle"].fire("click");
  assert.ok(nodes.text.classes.has("pinyin"), "inline pinyin on");
  assert.strictEqual(nodes["pinyin-toggle"].textContent, "拼音 ON");
  assert.strictEqual(nodes["pinyin-toggle"].getAttribute("aria-pressed"), "true");
  assert.strictEqual(storage._store["reader.pinyin"], "on");

  nodes["pinyin-toggle"].fire("click");
  assert.ok(!nodes.text.classes.has("pinyin"));
  assert.strictEqual(storage._store["reader.pinyin"], "off");
});

test("a stored preference is restored on load", async () => {
  const { nodes } = start({ storage: memoryStorage({ "reader.pinyin": "on" }) });
  await settle();
  assert.ok(nodes.text.classes.has("pinyin"));
  assert.strictEqual(nodes["pinyin-toggle"].textContent, "拼音 ON");
});

test("unavailable localStorage does not break the Reader", async () => {
  const { body, nodes } = start({ storage: failingStorage() });
  await settle();
  assert.strictEqual(words(body).length, 6, "document still rendered");
  assert.ok(!nodes.text.classes.has("pinyin"));
  nodes["pinyin-toggle"].fire("click");
  assert.ok(nodes.text.classes.has("pinyin"), "toggle still works in-session");
});

test("CSS hides rt when the toggle is off and reveals it when on", () => {
  const sheet = css();
  assert.match(sheet, /#text rt \{\s*display: none;/, "rt hidden by default");
  assert.match(sheet, /#text\.pinyin rt \{ display: revert; \}/, "rt shown when enabled");
  assert.ok(!/^#text \{[^}]*line-height: 2\.6/m.test(sheet), "no extra leading while off");
  assert.match(sheet, /#text\.pinyin \{ line-height: 2\.6; \}/);
});

test("tapping a token never toggles the Mandarin Mosaic sentence around it", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "获得").click(body);
  nodes["lexeme-action"].fire("click");
  assert.strictEqual(nodes["mosaic-counter"].textContent, "Mosaic · 0");
  assert.ok(!sentence(body, 0).classes.has("picked"));
});

test("sentence selection and lexical selection coexist", async () => {
  const { body, nodes } = start();
  await settle();
  sentence(body, 1).fire("click");
  word(body, "获得").click(body);
  nodes["lexeme-action"].fire("click");
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 1");
  assert.strictEqual(nodes["mosaic-counter"].textContent, "Mosaic · 1");
  assert.ok(sentence(body, 1).classes.has("picked"));
  assert.ok(word(body, "获得").classes.has("picked"));
});

test("selection survives inspecting other tokens", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "获得").click(body);
  nodes["lexeme-action"].fire("click");
  word(body, "他们").click(body);
  nodes["lexeme-close"].fire("click");
  word(body, "很难").click(body);
  nodes["lexeme-close"].fire("click");
  assert.ok(word(body, "获得").classes.has("picked"));
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 1");
});

test("scrim and Escape dismiss the popup without mutating the selection", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "获得").click(body);
  nodes.scrim.fire("click");
  assert.strictEqual(nodes.lexeme.hidden, true);
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 0");

  word(body, "获得").click(body);
  document.fire("keydown", { key: "Escape" });
  assert.strictEqual(nodes.lexeme.hidden, true);
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 0");
  assert.strictEqual(body.querySelectorAll(".word.inspecting").length, 0);
});

test("the lexeme sheet is marked up as a modal dialog", () => {
  const html = markup();
  const sheet = /<section id="lexeme"[^>]*>/.exec(html)[0];
  assert.match(sheet, /role="dialog"/);
  assert.match(sheet, /aria-modal="true"/);
  assert.match(sheet, /aria-labelledby="lexeme-glyph"/);
  assert.match(sheet, /hidden/);
  assert.match(html, /<button id="pinyin-toggle"[^>]*aria-pressed="false"/, "toggle is a button");
  assert.ok(!/onclick=/.test(html), "no inline handlers; CSP allows only external scripts");
});

test("popup moves focus to the action and returns it to the token", async () => {
  const { body, nodes } = start();
  await settle();
  const node = word(body, "获得");
  node.click(body);
  assert.strictEqual(document.activeElement, nodes["lexeme-action"]);
  nodes["lexeme-close"].fire("click");
  assert.strictEqual(document.activeElement, node, "focus returns to the token");
});

test("successful Hanly upload clears the uploaded selections", async () => {
  const { body, nodes } = start({
    responses: {
      "/hanly": () => ({
        ok: true,
        json: async () => ({ uploaded: 1, name: "D", total: 9, notes: [{ action: "created" }] }),
      }),
    },
  });
  await settle();
  word(body, "获得").click(body);
  nodes["lexeme-action"].fire("click");
  nodes["hanly-counter"].fire("click");
  await nodes["basket-upload"].listeners.click[0]();
  await settle();
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 0");
  assert.ok(!word(body, "获得").classes.has("picked"));
  assert.match(nodes.toast.textContent, /Notes: 1 added\./);
  word(body, "获得").fire("click");
  assert.strictEqual(nodes["lexeme-state"].textContent, "Learning · Hanly");
  assert.strictEqual(nodes["lexeme-knowledge"].textContent, "✓ Mark as known");
});

test("failed Hanly upload keeps every selection", async () => {
  const { body, nodes } = start({
    responses: {
      "/hanly": () => ({ ok: false, json: async () => ({ error: "Hanly connection failed." }) }),
    },
  });
  await settle();
  word(body, "获得").click(body);
  nodes["lexeme-action"].fire("click");
  nodes["hanly-counter"].fire("click");
  await nodes["basket-upload"].listeners.click[0]();
  await settle();
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 1");
  assert.ok(word(body, "获得").classes.has("picked"));
  assert.match(nodes.toast.textContent, /Your selection is kept/);
});

test("partial Mandarin Mosaic upload preserves the failed sentence", async () => {
  const { body, nodes } = start({
    responses: {
      "/mandarin-mosaic": () => ({
        ok: true,
        json: async () => ({ uploaded: 1, name: "D", failed_sentence_ids: [1], error: "rejected" }),
      }),
    },
  });
  await settle();
  sentence(body, 0).fire("click");
  sentence(body, 1).fire("click");
  nodes["mosaic-counter"].fire("click");
  await nodes["basket-upload"].listeners.click[0]();
  await settle();
  assert.strictEqual(nodes["mosaic-counter"].textContent, "Mosaic · 1");
  assert.ok(!sentence(body, 0).classes.has("picked"));
  assert.ok(sentence(body, 1).classes.has("picked"));
});

test("the header splits the podcast from the episode title", async () => {
  const { nodes } = start();
  await settle();
  assert.strictEqual(nodes["source-line"].textContent, "Mami Chinese");
  assert.strictEqual(nodes.title.textContent, "菲尔兹奖");
});

test("a title with no separator stays on one line", async () => {
  const plain = { ...DOCUMENT, title: "transcript (5).txt" };
  const { nodes } = start({ document: plain });
  await settle();
  assert.strictEqual(nodes["source-line"].textContent, "");
  assert.strictEqual(nodes.title.textContent, "transcript (5).txt");
});

test("the popup shows the sentence the word was tapped in, with the word marked", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "获得").click(body);
  assert.strictEqual(nodes["lexeme-context"].textContent, "他们获得了菲尔兹奖。");
  assert.strictEqual(nodes["lexeme-context-label"].hidden, false);
  const marked = nodes["lexeme-context"].children.filter((n) => n.classes.has("hit"));
  assert.deepStrictEqual(
    marked.map((n) => n.textContent),
    ["获得"],
    "only the tapped word is highlighted"
  );
});

test("the context follows the occurrence, not the first sentence", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "很难").click(body);
  assert.strictEqual(nodes["lexeme-context"].textContent, "做研究很难。");
});

test("the meaning label names where the meaning came from", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "获得").click(body);
  assert.match(nodes["lexeme-senses-label"].textContent, /в этом эпизоде/);
  nodes["lexeme-close"].fire("click");
  word(body, "很难").click(body);
  assert.match(nodes["lexeme-senses-label"].textContent, /из словаря/);
});

test("a word with no meaning hides the label rather than leaving it bare", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "做研究").click(body);
  assert.strictEqual(nodes["lexeme-senses-label"].hidden, true);
  assert.strictEqual(nodes["lexeme-source"].textContent, "");
});

test("basket rows carry the reading and a gloss", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "很难").click(body);
  nodes["lexeme-action"].fire("click");
  nodes["hanly-counter"].fire("click");
  assert.strictEqual(nodes["basket-title"].textContent, "Hanly vocabulary (1)");
  const row = nodes["basket-items"].children[0];
  const entry = row.children.find((n) => n.classes.has("entry"));
  assert.strictEqual(entry.textContent, "很难hěn nánтрудный");
  assert.strictEqual(row.children[row.children.length - 1].getAttribute("aria-label"), "Remove");
  assert.match(nodes["basket-note"].textContent, /added to this document's Hanly collection/);
});

test("the two popup actions form an evenly matched row", () => {
  const sheet = css();
  const row = /#lexeme-actions \{([^}]*)\}/.exec(sheet)[1];
  const button = /#lexeme-actions button \{([^}]*)\}/.exec(sheet)[1];
  const secondary = /#lexeme-actions \.secondary \{([^}]*)\}/.exec(sheet)[1];
  const primary = /\n\.primary \{([^}]*)\}/.exec(sheet)[1];

  assert.match(row, /align-items: stretch/, "both buttons share the row height");
  assert.match(row, /margin-top: 18px/, "the row owns the spacing");
  assert.match(button, /margin-top: 0/, ".primary's own margin must not offset one button");
  assert.match(button, /flex: 1 1 0/, "equal widths regardless of label length");

  const pad = (block) => Number(/padding: (\d+)px/.exec(block)[1]);
  const border = Number((/border: (\d+)px/.exec(secondary) || [0, 0])[1]);
  assert.strictEqual(
    pad(secondary) + border,
    pad(primary),
    "the outline's border is compensated so both boxes are the same height"
  );
  for (const property of [/font-size: 15px/, /font-weight: 600/]) {
    assert.match(secondary, property, "labels share the primary button's text metrics");
    assert.match(primary, property);
  }
});

test("the stylesheet defines a full dark palette", () => {
  const sheet = css();
  assert.match(sheet, /@media \(prefers-color-scheme: dark\)/);
  for (const token of ["--blue", "--green", "--bg", "--surface", "--fg", "--muted", "--border"]) {
    const light = new RegExp(`:root \\{[\\s\\S]*?${token}:`);
    const dark = new RegExp(`prefers-color-scheme: dark[\\s\\S]*?${token}:`);
    assert.match(sheet, light, `${token} missing from the light palette`);
    assert.match(sheet, dark, `${token} missing from the dark palette`);
  }
  assert.ok(!/var\(--tg-theme/.test(sheet), "the palette is explicit, not inherited from Telegram");
});

test("the layout has no fixed width that would overflow a narrow WebView", () => {
  const sheet = css();
  const widths = [...sheet.matchAll(/min-width:\s*(\d+)px/g)].map((m) => Number(m[1]));
  assert.ok(widths.every((w) => w <= 320), `min-width too large: ${widths}`);
  assert.match(sheet, /max-width:\s*100%|width:\s*100%/, "sheets stretch to the viewport");
  assert.match(sheet, /env\(safe-area-inset-bottom\)/, "bottom sheets clear the home indicator");
});

test("vocabulary saves immediately, reopens, and clears without selecting sentences", async () => {
  let next = "known";
  const { body, nodes, calls } = start({ responses: {
    "/vocabulary-state": () => ({ ok: true, json: async () => ({ glyph: "获得", vocabulary_state: next }) }),
  }});
  await settle();
  word(body, "获得").fire("click");
  assert.ok(nodes["lexeme-state"].hidden);
  assert.strictEqual(nodes["lexeme-knowledge"].textContent, "✓ I know this");
  nodes["lexeme-knowledge"].fire("click");
  await settle();
  assert.strictEqual(nodes["lexeme-state"].textContent, "✓ Known");
  assert.deepStrictEqual(JSON.parse(calls.at(-1).body), { glyph: "获得", state: "known" });
  nodes["lexeme-close"].fire("click");
  word(body, "获得").fire("click");
  assert.strictEqual(nodes["lexeme-knowledge"].textContent, "Mark as unknown");
  assert.strictEqual(nodes["mosaic-counter"].textContent.includes("0"), true);
  next = "unknown";
  nodes["lexeme-knowledge"].fire("click");
  await settle();
  assert.ok(nodes["lexeme-state"].hidden);
});

for (const prior of ["unknown", "known", "learning"]) {
  test(`Hanly basket overrides ${prior} and removal restores it`, async () => {
    const document = structuredClone(DOCUMENT);
    document.glossary["获得"].vocabulary_state = prior;
    const { body, nodes, calls } = start({ document });
    await settle();
    word(body, "获得").fire("click");
    nodes["lexeme-action"].fire("click");
    word(body, "获得").fire("click");
    assert.strictEqual(nodes["lexeme-state"].textContent, "Learning · Hanly");
    nodes["lexeme-action"].fire("click");
    word(body, "获得").fire("click");
    assert.strictEqual(nodes["lexeme-state"].textContent,
      prior === "known" ? "✓ Known" : prior === "learning" ? "Learning · Hanly" : "");
    assert.strictEqual(calls.length, 1, "basket changes do not persist knowledge");
  });
}

test("failed vocabulary save preserves state and baskets", async () => {
  const { body, nodes } = start({ responses: {
    "/vocabulary-state": () => { throw new Error("Offline"); },
  }});
  await settle();
  word(body, "获得").fire("click");
  nodes["lexeme-action"].fire("click");
  word(body, "获得").fire("click");
  const hanly = nodes["hanly-counter"].textContent;
  const mosaic = nodes["mosaic-counter"].textContent;
  nodes["lexeme-knowledge"].fire("click");
  await settle();
  assert.strictEqual(nodes["lexeme-state"].textContent, "Learning · Hanly");
  assert.strictEqual(nodes["lexeme-knowledge"].textContent, "✓ Mark as known");
  assert.strictEqual(nodes["hanly-counter"].textContent, hanly);
  assert.strictEqual(nodes["mosaic-counter"].textContent, mosaic);
  assert.match(nodes.toast.textContent, /Could not save/);
});

test("marking a basket word known keeps learning until removal; pinyin is independent", async () => {
  const { body, nodes } = start({ responses: {
    "/vocabulary-state": () => ({ ok: true, json: async () => ({ glyph: "获得", vocabulary_state: "known" }) }),
  }});
  await settle();
  word(body, "获得").fire("click");
  nodes["lexeme-action"].fire("click");
  word(body, "获得").fire("click");
  nodes["lexeme-knowledge"].click(body);
  await settle();
  assert.strictEqual(nodes["lexeme-state"].textContent, "Learning · Hanly");
  nodes["pinyin-toggle"].fire("click");
  assert.strictEqual(nodes["lexeme-state"].textContent, "Learning · Hanly");
  assert.strictEqual(nodes["mosaic-counter"].textContent, "Mosaic · 0");
  nodes["lexeme-action"].fire("click");
  word(body, "获得").fire("click");
  assert.strictEqual(nodes["lexeme-state"].textContent, "✓ Known");
});

test("late vocabulary response updates its glyph and blocks an overlapping Hanly upload", async () => {
  let finish;
  const { body, nodes, calls } = start({ responses: {
    "/vocabulary-state": () => new Promise((resolve) => { finish = resolve; }),
  }});
  await settle();
  word(body, "获得").fire("click");
  nodes["lexeme-knowledge"].fire("click");
  assert.ok(nodes["lexeme-knowledge"].disabled);
  assert.ok(nodes["lexeme-state"].hidden, "no premature saved state");
  nodes["lexeme-action"].fire("click");
  nodes["hanly-counter"].fire("click");
  nodes["basket-upload"].fire("click");
  assert.ok(!calls.some((c) => c.path.endsWith("/hanly")));
  word(body, "他们").fire("click");
  finish({ ok: true, json: async () => ({ glyph: "获得", vocabulary_state: "known" }) });
  await settle();
  assert.ok(nodes["lexeme-state"].hidden, "other glyph remains unknown");
  word(body, "获得").fire("click");
  nodes["lexeme-action"].fire("click");
  word(body, "获得").fire("click");
  assert.strictEqual(nodes["lexeme-state"].textContent, "✓ Known");
});

const translationButton = (body, id) => sentence(body, id).querySelector(".translation-action");
const translationText = (body, id) => sentence(body, id).querySelector(".translation-text");

test("translations start hidden without any translation request", async () => {
  const { body, calls } = start();
  await settle();
  assert.ok(translationText(body, 0).hidden);
  assert.strictEqual(translationButton(body, 0).getAttribute("aria-label"), "Show translation");
  assert.strictEqual(calls.length, 1);
});

for (const source of ["study", "generated"]) {
  test(`${source} translation shows, hides and reopens without another request`, async () => {
    const { body, calls } = start({ responses: {
      "/sentences/0/translation": () => ({ ok: true, json: async () => ({
        sentence_id: 0, translation: "Они получили медаль.", source,
      }) }),
    }});
    await settle();
    const button = translationButton(body, 0);
    button.click(body);
    await settle();
    assert.strictEqual(translationText(body, 0).textContent, "Они получили медаль.");
    assert.ok(!translationText(body, 0).hidden);
    assert.strictEqual(button.getAttribute("aria-expanded"), "true");
    button.click(body);
    assert.ok(translationText(body, 0).hidden);
    button.click(body);
    await settle();
    assert.ok(!translationText(body, 0).hidden);
    assert.strictEqual(calls.length, 2);
    assert.deepStrictEqual(JSON.parse(calls[1].body), { language: "ru" });
  });
}

test("translation loading is local and repeated taps coalesce", async () => {
  let finish;
  const { body, nodes, calls } = start({ responses: {
    "/sentences/0/translation": () => new Promise((resolve) => { finish = resolve; }),
  }});
  await settle();
  const button = translationButton(body, 0);
  button.click(body);
  button.fire("click");
  assert.strictEqual(button.getAttribute("aria-label"), "Translating…");
  assert.strictEqual(button.getAttribute("aria-busy"), "true");
  assert.ok(translationText(body, 0).hidden);
  assert.ok(!translationButton(body, 1).disabled);
  word(body, "获得").click(body);
  assert.ok(!nodes.lexeme.hidden);
  finish({ ok: true, json: async () => ({ sentence_id: 0, translation: "Они получили медаль.", source: "generated" }) });
  await settle();
  assert.ok(!button.disabled);
  assert.strictEqual(calls.length, 2);
});

test("several translations coexist with Hanly, Mosaic and pinyin", async () => {
  const { body, nodes } = start({ responses: {
    "/sentences/0/translation": () => ({ ok: true, json: async () => ({ sentence_id: 0, translation: "Они получили медаль.", source: "study" }) }),
    "/sentences/1/translation": () => ({ ok: true, json: async () => ({ sentence_id: 1, translation: "Исследовать трудно.", source: "generated" }) }),
  }});
  await settle();
  word(body, "获得").click(body);
  nodes["lexeme-action"].fire("click");
  sentence(body, 0).click(body);
  const mosaic = nodes["mosaic-counter"].textContent;
  const hanly = nodes["hanly-counter"].textContent;
  translationButton(body, 0).click(body);
  translationButton(body, 1).click(body);
  await settle();
  assert.ok(!translationText(body, 0).hidden && !translationText(body, 1).hidden);
  translationText(body, 0).click(body);
  nodes["pinyin-toggle"].fire("click");
  assert.ok(!translationText(body, 0).hidden);
  assert.strictEqual(nodes["mosaic-counter"].textContent, mosaic);
  assert.strictEqual(nodes["hanly-counter"].textContent, hanly);
  assert.ok(nodes.lexeme.hidden);
});

test("failed translation retries only explicitly without changing selections", async () => {
  let fails = true;
  const { body, nodes, calls } = start({ responses: {
    "/sentences/0/translation": () => fails
      ? { ok: false, json: async () => ({ error: "Unavailable" }) }
      : { ok: true, json: async () => ({ sentence_id: 0, translation: "Они получили медаль.", source: "generated" }) },
  }});
  await settle();
  sentence(body, 0).click(body);
  const selected = nodes["mosaic-counter"].textContent;
  const button = translationButton(body, 0);
  button.click(body);
  await settle();
  assert.strictEqual(button.getAttribute("aria-label"), "Translation unavailable · Retry");
  assert.ok(translationText(body, 0).hidden);
  assert.strictEqual(calls.length, 2);
  assert.strictEqual(nodes["mosaic-counter"].textContent, selected);
  fails = false;
  button.click(body);
  await settle();
  assert.ok(!translationText(body, 0).hidden);
  assert.strictEqual(calls.length, 3);
});

test("language toggle updates open translation and reuses each language cache", async () => {
  let count = 0;
  const { body, nodes, calls } = start({ responses: {
    "/sentences/0/translation": () => ({ ok: true, json: async () => ({ sentence_id: 0,
      translation: ++count === 1 ? "Они получили медаль." : "They received a medal.", source: "generated" }) }),
  }});
  await settle();
  translationButton(body, 0).click(body);
  await settle();
  nodes["language-toggle"].fire("click");
  await settle();
  assert.strictEqual(translationText(body, 0).textContent, "They received a medal.");
  assert.strictEqual(translationText(body, 0).getAttribute("lang"), "en");
  assert.deepStrictEqual(JSON.parse(calls.at(-1).body), { language: "en" });
  nodes["language-toggle"].fire("click");
  await settle();
  assert.strictEqual(translationText(body, 0).textContent, "Они получили медаль.");
  assert.strictEqual(count, 2);
});

test("late Russian response cannot replace the selected English translation", async () => {
  let finish;
  let count = 0;
  const { body, nodes } = start({ responses: {
    "/sentences/0/translation": () => ++count === 1
      ? new Promise((resolve) => { finish = resolve; })
      : { ok: true, json: async () => ({ sentence_id: 0, translation: "They received a medal.", source: "generated" }) },
  }});
  await settle();
  translationButton(body, 0).click(body);
  nodes["language-toggle"].fire("click");
  await settle();
  finish({ ok: true, json: async () => ({ sentence_id: 0, translation: "Они получили медаль.", source: "generated" }) });
  await settle();
  assert.strictEqual(translationText(body, 0).textContent, "They received a medal.");
});

function fakeSpeech(voices = []) {
  const events = {};
  const spoken = [];
  let cancelled = 0;
  return {
    spoken, events, get cancelled() { return cancelled; },
    speechSynthesis: {
      getVoices: () => voices,
      addEventListener: (name, handler) => { events[name] = handler; },
      speak: (utterance) => { spoken.push(utterance); utterance.onstart(); },
      cancel: () => { cancelled += 1; },
    },
    SpeechSynthesisUtterance: class { constructor(text) { this.text = text; } },
    addEventListener: (name, handler) => { events[name] = handler; },
  };
}

for (const [langs, expected] of [
  [["en-US", "zh-HK", "zh-TW", "zh-CN"], "zh-CN"],
  [["zh-TW", "ZH-cn"], "ZH-cn"],
  [["zh-HK", "zh-SG"], "zh-SG"],
  [["zh-HK", "zh-Hans"], "zh-Hans"],
  [["zh-HK", "cmn-TW"], "cmn-TW"],
  [["zh-HK", "zh-TW"], "zh-TW"],
  [["zh-HK"], "zh-HK"],
  [["en-US", "ru-RU"], undefined],
  [[], undefined],
]) {
  test(`speech voice metadata ranking: ${langs.join(",") || "empty"}`, async () => {
    const speech = fakeSpeech(langs.map((lang, index) => ({ lang, name: `Voice ${index}` })));
    const { body, nodes } = start({ speech });
    await settle();
    word(body, "获得").click(body);
    nodes["lexeme-pronounce"].click(body);
    assert.strictEqual(speech.spoken[0].voice?.lang, expected);
    assert.strictEqual(speech.spoken[0].lang, "zh-CN");
    assert.strictEqual(speech.spoken[0].text, "获得");
  });
}

test("unsupported speech is hidden and popup still works", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "获得").click(body);
  assert.ok(nodes["lexeme-pronounce"].hidden);
  assert.ok(!nodes.lexeme.hidden);
});

test("voiceschanged refreshes voices without autoplay", async () => {
  const voices = [];
  const speech = fakeSpeech(voices);
  const { body, nodes } = start({ speech });
  await settle();
  word(body, "获得").click(body);
  assert.ok(!nodes["lexeme-pronounce"].hidden);
  assert.strictEqual(speech.spoken.length, 0);
  voices.push({ lang: "zh-CN", name: "Any platform voice" });
  speech.events.voiceschanged();
  assert.strictEqual(speech.spoken.length, 0);
  nodes["lexeme-pronounce"].click(body);
  assert.strictEqual(speech.spoken[0].voice, voices[0]);
});

test("repeat restarts; old callbacks cannot clear current playback", async () => {
  const speech = fakeSpeech();
  const { body, nodes } = start({ speech });
  await settle();
  word(body, "获得").click(body);
  const button = nodes["lexeme-pronounce"];
  button.click(body);
  assert.ok(button.classes.has("speaking"));
  button.click(body);
  assert.strictEqual(speech.cancelled, 1);
  assert.strictEqual(speech.spoken.length, 2);
  speech.spoken[0].onerror();
  assert.ok(button.classes.has("speaking"));
  speech.spoken[1].onend();
  assert.ok(!button.classes.has("speaking"));
  nodes["lexeme-close"].fire("click");
  assert.strictEqual(speech.cancelled, 1, "idle popup close does not cancel unrelated speech");
});

test("switching word, closing popup and leaving page stop Reader speech", async () => {
  const speech = fakeSpeech();
  const { body, nodes } = start({ speech });
  await settle();
  word(body, "获得").click(body);
  nodes["lexeme-pronounce"].click(body);
  word(body, "做研究").click(body);
  assert.strictEqual(speech.cancelled, 1);
  nodes["lexeme-pronounce"].click(body);
  assert.strictEqual(speech.spoken[1].text, "做研究");
  nodes["lexeme-close"].fire("click");
  assert.strictEqual(speech.cancelled, 2);
  word(body, "获得").click(body);
  nodes["lexeme-pronounce"].click(body);
  speech.events.pagehide();
  assert.strictEqual(speech.cancelled, 3);
});

for (const glyph of ["歡迎", "辛苦了", "研究成果", "  第3届AI奖！  "]) {
  test(`speech preserves arbitrary glyph: ${glyph}`, async () => {
    const document = structuredClone(DOCUMENT);
    document.sentences[0].tokens[0] = { t: glyph, w: true };
    const speech = fakeSpeech();
    const { body, nodes } = start({ speech, document });
    await settle();
    word(body, glyph).click(body);
    nodes["lexeme-pronounce"].click(body);
    assert.strictEqual(speech.spoken[0].text, glyph.trim());
    assert.strictEqual(nodes["lexeme-pronounce"].getAttribute("aria-label"), `Pronounce ${glyph.trim()}`);
  });
}

test("speech leaves translation, baskets and pinyin independent", async () => {
  const speech = fakeSpeech();
  const { body, nodes, calls } = start({ speech, responses: {
    "/sentences/0/translation": () => ({ ok: true, json: async () => ({ sentence_id: 0, translation: "Перевод.", source: "study" }) }),
  }});
  await settle();
  word(body, "获得").click(body);
  nodes["lexeme-action"].fire("click");
  sentence(body, 0).click(body);
  translationButton(body, 0).click(body);
  await settle();
  const mosaic = nodes["mosaic-counter"].textContent;
  const hanly = nodes["hanly-counter"].textContent;
  const requests = calls.length;
  word(body, "获得").click(body);
  nodes["lexeme-pronounce"].click(body);
  nodes["pinyin-toggle"].fire("click");
  assert.strictEqual(speech.spoken.length, 1);
  nodes["lexeme-pronounce"].click(body);
  assert.ok(speech.spoken.every((utterance) => utterance.text === "获得"));
  assert.strictEqual(nodes["hanly-counter"].textContent, hanly);
  assert.strictEqual(nodes["mosaic-counter"].textContent, mosaic);
  assert.strictEqual(calls.length, requests);
  assert.ok(!nodes.lexeme.hidden && !translationText(body, 0).hidden);
});

test("speech failures clear active state without affecting the popup", async () => {
  const speech = fakeSpeech();
  speech.speechSynthesis.speak = () => { throw new Error("Platform failure"); };
  const { body, nodes } = start({ speech });
  await settle();
  word(body, "获得").click(body);
  nodes["lexeme-pronounce"].click(body);
  assert.ok(!nodes["lexeme-pronounce"].classes.has("speaking"));
  assert.strictEqual(nodes.toast.textContent, "Pronunciation unavailable");
  assert.ok(!nodes.lexeme.hidden);
});

const CANONICAL = "最近在中国，大家都在讨论两位数学家。";

const SPEECH_DOCUMENT = {
  title: "Mami Chinese｜菲尔兹奖",
  hanly_available: true,
  mosaic_available: true,
  hanly_error: "",
  mosaic_error: "",
  paragraphs: [[0, 1]],
  glossary: {
    最近: { p: "zuì jìn", ru: ["недавно"], rs: "bkrs" },
    中国: { p: "zhōng guó" },
    大家: { p: "dà jiā" },
    讨论: { p: "tǎo lùn" },
    数学家: { p: "shù xué jiā" },
    做研究: { p: "zuò yán jiū" },
    很难: { p: "hěn nán" },
  },
  sentences: [
    {
      id: 0,
      text: CANONICAL,
      tokens: [
        { t: "最近", w: true }, { t: "在", w: true }, { t: "中国", w: true }, { t: "，", w: false },
        { t: "大家", w: true }, { t: "都", w: true }, { t: "在", w: true }, { t: "讨论", w: true },
        { t: "两位", w: true }, { t: "数学家", w: true }, { t: "。", w: false },
      ],
    },
    {
      id: 1,
      text: "做研究很难。",
      tokens: [{ t: "做研究", w: true }, { t: "很难", w: true }, { t: "。", w: false }],
    },
  ],
};

const RUSSIAN = "Недавно в Китае все обсуждают двух математиков.";
const audioButton = (body, id) => sentence(body, id).querySelector(".audio-action");

/** A Reader whose window exposes the Web Speech API, on the canonical document. */
function speaking(options = {}) {
  const speech = speechStub(options.speech);
  const env = start({
    ...options,
    speech: speech.window,
    document: options.document || SPEECH_DOCUMENT,
  });
  return { ...env, speech };
}

test("every sentence carries the Mosaic, audio and translation actions in one row", async () => {
  const { body, speech } = speaking();
  await settle();
  const controls = sentence(body, 0).querySelector(".sentence-controls");
  assert.deepStrictEqual(
    controls.children.map((node) => node.className),
    ["mark", "audio-action", "translation-action"]
  );
  const audio = audioButton(body, 0);
  assert.strictEqual(audio.tagName, "button");
  assert.strictEqual(audio.type, "button");
  assert.strictEqual(audio.getAttribute("aria-label"), "Pronounce sentence");
  assert.strictEqual(audio.children[0].tagName, "svg");
  assert.strictEqual(audio.children[0].getAttribute("aria-hidden"), "true");
  assert.strictEqual(audio.textContent, "", "no wording of its own inside the transcript");
  assert.ok(!speech.spoken.length, "opening the Reader speaks nothing");
});

test("audio speaks the canonical sentence in Mandarin with punctuation intact", async () => {
  const { body, speech } = speaking();
  await settle();
  audioButton(body, 0).click(body);
  const utterance = speech.last();
  assert.strictEqual(utterance.text, CANONICAL);
  assert.strictEqual(utterance.lang, "zh-CN");
  assert.ok(utterance.text.includes("，") && utterance.text.endsWith("。"));
  for (const stray of ["zuì jìn", "shù xué jiā", "недавно", "Show translation", "文", "<", "w:"]) {
    assert.ok(!utterance.text.includes(stray), `the utterance carried ${stray}`);
  }
});

test("pinyin on or off never changes the spoken sentence", async () => {
  const { body, nodes, speech } = speaking();
  await settle();
  audioButton(body, 0).click(body);
  nodes["pinyin-toggle"].fire("click");
  assert.ok(nodes.text.classes.has("pinyin"));
  audioButton(body, 0).click(body);
  assert.deepStrictEqual(speech.spoken.map((u) => u.text), [CANONICAL, CANONICAL]);
});

test("a visible translation is never spoken and stays open afterwards", async () => {
  const { body, speech } = speaking({ responses: {
    "/sentences/0/translation": () => ({ ok: true, json: async () => ({
      sentence_id: 0, translation: RUSSIAN, source: "generated",
    }) }),
  }});
  await settle();
  translationButton(body, 0).click(body);
  await settle();
  assert.strictEqual(translationText(body, 0).textContent, RUSSIAN);
  audioButton(body, 0).click(body);
  assert.strictEqual(speech.last().text, CANONICAL);
  assert.ok(!translationText(body, 0).hidden, "translation stays visible");
  assert.strictEqual(translationButton(body, 0).getAttribute("aria-expanded"), "true");
});

test("a second sentence takes over the single Reader playback channel", async () => {
  const { body, speech } = speaking();
  await settle();
  const first = audioButton(body, 0);
  const second = audioButton(body, 1);
  first.click(body);
  assert.ok(first.classes.has("speaking"));
  assert.strictEqual(first.getAttribute("aria-label"), "Restart sentence pronunciation");
  second.click(body);
  assert.ok(!first.classes.has("speaking"), "the first control returns to idle");
  assert.strictEqual(first.getAttribute("aria-label"), "Pronounce sentence");
  assert.ok(second.classes.has("speaking"));
  assert.strictEqual(speech.queue.length, 1, "nothing waits behind the live utterance");
  assert.deepStrictEqual(speech.spoken.map((u) => u.text), [CANONICAL, "做研究很难。"]);
});

test("repeated taps restart the sentence instead of queueing it", async () => {
  const { body, speech } = speaking();
  await settle();
  const audio = audioButton(body, 0);
  audio.click(body);
  audio.click(body);
  audio.click(body);
  assert.strictEqual(speech.queue.length, 1);
  assert.strictEqual(speech.spoken.length, 3, "each tap starts the sentence again");
  assert.ok(audio.classes.has("speaking"));
});

test("the active state clears when speech ends", async () => {
  const { body, nodes, speech } = speaking();
  await settle();
  const audio = audioButton(body, 0);
  audio.click(body);
  assert.ok(audio.classes.has("speaking"));
  speech.end();
  assert.ok(!audio.classes.has("speaking"));
  assert.strictEqual(audio.getAttribute("aria-label"), "Pronounce sentence");
  assert.strictEqual(nodes.toast.textContent, "", "a clean finish says nothing");
});

test("a failed utterance clears the active state and reports it", async () => {
  const { body, nodes, speech } = speaking();
  await settle();
  const audio = audioButton(body, 0);
  audio.click(body);
  speech.fail();
  assert.ok(!audio.classes.has("speaking"));
  assert.strictEqual(nodes.toast.textContent, "Pronunciation unavailable");
  assert.ok(nodes.toast.classes.has("bad"));
});

test("a synthesizer that refuses to speak leaves the Reader usable", async () => {
  const { body, nodes } = speaking({ speech: { broken: true } });
  await settle();
  const audio = audioButton(body, 0);
  audio.click(body);
  assert.ok(!audio.classes.has("speaking"));
  assert.strictEqual(nodes.toast.textContent, "Pronunciation unavailable");
  sentence(body, 0).click(body);
  assert.strictEqual(nodes["mosaic-counter"].textContent, "Mosaic · 1");
});

test("audio never reaches Mosaic, translation, Hanly or the word popup", async () => {
  const { body, nodes, speech } = speaking();
  await settle();
  audioButton(body, 0).click(body);
  assert.strictEqual(nodes["mosaic-counter"].textContent, "Mosaic · 0");
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 0");
  assert.ok(!sentence(body, 0).classes.has("picked"));
  assert.strictEqual(nodes["lexeme-glyph"].textContent, "", "no word popup opened");
  assert.ok(translationText(body, 0).hidden, "no translation opened");
  assert.strictEqual(speech.spoken.length, 1);
});

test("the neighbouring sentence actions never start playback", async () => {
  const { body, speech } = speaking({ responses: {
    "/sentences/0/translation": () => ({ ok: true, json: async () => ({
      sentence_id: 0, translation: RUSSIAN, source: "generated",
    }) }),
  }});
  await settle();
  sentence(body, 0).click(body);
  translationButton(body, 0).click(body);
  await settle();
  word(body, "讨论").click(body);
  assert.ok(!speech.spoken.length, "only the speaker control speaks");
});

test("a sentence selected for Mosaic plays without changing the selection", async () => {
  const { body, nodes, speech } = speaking();
  await settle();
  sentence(body, 0).click(body);
  assert.ok(sentence(body, 0).classes.has("picked"));
  audioButton(body, 0).click(body);
  assert.strictEqual(speech.last().text, CANONICAL);
  assert.ok(sentence(body, 0).classes.has("picked"), "playback is not a selection");
  assert.strictEqual(nodes["mosaic-counter"].textContent, "Mosaic · 1");
  assert.strictEqual(sentence(body, 0).querySelector(".mark").getAttribute("aria-pressed"), "true");
});

for (const [expected, voices] of [
  ["Mainland", [{ name: "Yue", lang: "zh-HK" }, { name: "Taiwan", lang: "zh-TW" },
                { name: "Putonghua", lang: "cmn-Hans-CN" }, { name: "Mainland", lang: "zh_CN" }]],
  ["Putonghua", [{ name: "Yue", lang: "zh-HK" }, { name: "Taiwan", lang: "zh-TW" },
                 { name: "Putonghua", lang: "cmn-Hans-CN" }]],
  ["Taiwan", [{ name: "Yue", lang: "zh-HK" }, { name: "Taiwan", lang: "zh-TW" }]],
  ["Anna", [{ name: "Anna", lang: "zh-HK" }, { name: "Ivan", lang: "ru-RU" }]],
]) {
  test(`Mandarin voice selection picks ${expected} from its voice list`, async () => {
    const { body, speech } = speaking({ speech: { voices } });
    await settle();
    audioButton(body, 0).click(body);
    assert.strictEqual(speech.last().voice.name, expected);
  });
}

test("no Chinese voice at all still speaks zh-CN, and later voices are picked up", async () => {
  const voices = [{ name: "Ivan", lang: "ru-RU" }];
  const { body, speech } = speaking({ speech: { voices } });
  await settle();
  audioButton(body, 0).click(body);
  assert.strictEqual(speech.last().lang, "zh-CN");
  assert.strictEqual(speech.last().voice, null, "the system picks the voice");
  voices.push({ name: "Mainland", lang: "zh-CN" });
  speech.voicesArrived();
  audioButton(body, 0).click(body);
  assert.strictEqual(speech.last().voice.name, "Mainland");
});

test("leaving the page stops Reader speech", async () => {
  const { body, speech } = speaking();
  await settle();
  const audio = audioButton(body, 0);
  audio.click(body);
  global.document.hidden = true;
  global.document.fire("visibilitychange");
  assert.ok(!audio.classes.has("speaking"));
  assert.strictEqual(speech.queue.length, 0);
  global.document.hidden = false;
});

test("pasted-text documents speak arbitrary sentences whole", async () => {
  const long =
    "第一次听到这个消息的时候，我正在北京的一个小咖啡馆里看书，窗外下着雨，" +
    "朋友突然打电话来，说王虹解决了一个困扰数学界一百多年的问题。";
  const pasted = {
    ...SPEECH_DOCUMENT,
    title: "Вставленный текст",
    paragraphs: [[0]],
    sentences: [{ id: 0, text: long, tokens: [{ t: long, w: false }] }],
  };
  const { body, speech } = speaking({ document: pasted });
  await settle();
  audioButton(body, 0).click(body);
  assert.strictEqual(speech.last().text, long);
  assert.strictEqual(speech.last().text.length, long.length, "nothing is truncated");
});

test("without the Web Speech API the audio action is absent and the Reader works", async () => {
  const { body, nodes } = start();
  await settle();
  assert.strictEqual(audioButton(body, 0), null);
  assert.ok(sentence(body, 0).querySelector(".mark"), "Mosaic survives");
  assert.ok(sentence(body, 0).querySelector(".translation-action"), "translation survives");
  word(body, "获得").click(body);
  assert.ok(!nodes.lexeme.hidden, "Hanly popup survives");
  sentence(body, 1).click(body);
  assert.strictEqual(nodes["mosaic-counter"].textContent, "Mosaic · 1");
});

test("a word takes the channel from a playing sentence", async () => {
  const { body, nodes, speech } = speaking();
  await settle();
  const audio = audioButton(body, 0);
  audio.click(body);
  assert.ok(audio.classes.has("speaking"));
  word(body, "讨论").click(body);
  nodes["lexeme-pronounce"].click(body);
  assert.ok(!audio.classes.has("speaking"), "the sentence control returns to idle");
  assert.ok(nodes["lexeme-pronounce"].classes.has("speaking"));
  assert.strictEqual(speech.queue.length, 1, "one Reader utterance at a time");
  assert.deepStrictEqual(speech.spoken.map((u) => u.text), [CANONICAL, "讨论"]);
});

test("a sentence takes the channel from a playing word", async () => {
  const { body, nodes, speech } = speaking();
  await settle();
  word(body, "讨论").click(body);
  nodes["lexeme-pronounce"].click(body);
  assert.ok(nodes["lexeme-pronounce"].classes.has("speaking"));
  audioButton(body, 1).click(body);
  assert.ok(!nodes["lexeme-pronounce"].classes.has("speaking"));
  assert.ok(audioButton(body, 1).classes.has("speaking"));
  assert.strictEqual(speech.queue.length, 1);
  assert.deepStrictEqual(speech.spoken.map((u) => u.text), ["讨论", "做研究很难。"]);
});

test("looking a word up leaves a playing sentence alone", async () => {
  const { body, nodes, speech } = speaking();
  await settle();
  const audio = audioButton(body, 0);
  audio.click(body);
  word(body, "讨论").click(body);
  assert.ok(audio.classes.has("speaking"), "opening the popup is not a playback command");
  nodes["lexeme-close"].fire("click");
  assert.ok(audio.classes.has("speaking"), "closing it is not either");
  assert.strictEqual(speech.queue.length, 1);
});

test("closing a word popup still stops the word it was pronouncing", async () => {
  const { body, nodes, speech } = speaking();
  await settle();
  word(body, "讨论").click(body);
  nodes["lexeme-pronounce"].click(body);
  assert.ok(nodes["lexeme-pronounce"].classes.has("speaking"));
  nodes["lexeme-close"].fire("click");
  assert.ok(!nodes["lexeme-pronounce"].classes.has("speaking"));
  assert.strictEqual(speech.queue.length, 0);
});

test("leaving the page through pagehide stops Reader speech", async () => {
  const { body, speech } = speaking();
  await settle();
  const audio = audioButton(body, 0);
  audio.click(body);
  speech.leave();
  assert.ok(!audio.classes.has("speaking"));
  assert.strictEqual(speech.queue.length, 0);
});

test("the audio control is a mobile-sized target with a subtle active state", () => {
  const sheet = css();
  assert.ok(/\.sentence-controls button \{[^}]*width: 40px; height: 40px/.test(sheet));
  assert.ok(/\.audio-icon \{[^}]*width: 20px; height: 20px/.test(sheet));
  assert.ok(/\.audio-action\.speaking \{[^}]*var\(--blue\)/.test(sheet));
});

(async () => {
  let failed = 0;
  for (const [name, fn] of Object.entries(tests)) {
    try {
      await fn();
      console.log("  ✓ " + name);
    } catch (error) {
      failed += 1;
      console.log("  ✗ " + name);
      console.log(String(error.message).split("\n").map((l) => "      " + l).join("\n"));
    }
  }
  const total = Object.keys(tests).length;
  console.log(`\n${total - failed}/${total} frontend tests passed`);
  process.exit(failed ? 1 : 0);
})();
