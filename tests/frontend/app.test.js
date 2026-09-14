"use strict";

const assert = require("assert");
const { setup, loadApp, memoryStorage, failingStorage, css, markup } = require("./dom.js");

const DOCUMENT = {
  title: "Mami Chinese｜菲尔兹奖",
  hanly_available: true,
  mosaic_available: true,
  hanly_error: "",
  mosaic_error: "",
  paragraphs: [[0, 1]],
  sentences: [
    {
      id: 0,
      text: "他们获得了菲尔兹奖。",
      tokens: [
        { t: "他们", w: true, p: "tā men" },
        { t: "获得", w: true, p: "huò dé", m: "получать", ms: "contextual" },
        { t: "了", w: true, p: "le" },
        { t: "菲尔兹奖", w: true, p: "fēi ěr zī jiǎng" },
        { t: "。", w: false },
      ],
    },
    {
      id: 1,
      text: "做研究很难。",
      tokens: [
        { t: "做研究", w: true, p: "zuò yán jiū" },
        { t: "很难", w: true, p: "hěn nán", m: "difficult", ms: "cc-cedict" },
        { t: "。", w: false },
      ],
    },
  ],
};

function start(options = {}) {
  const env = setup(options);
  const calls = [];
  const responses = options.responses || {};
  const payload = options.document || DOCUMENT;
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
  assert.strictEqual(nodes["lexeme-meaning"].textContent, "получать");
  assert.ok(!nodes.text.classes.has("pinyin"), "inline pinyin still off");
});

test("a token with no meaning shows an empty meaning rather than a label", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "做研究").click(body);
  assert.strictEqual(nodes["lexeme-pinyin"].textContent, "zuò yán jiū");
  assert.strictEqual(nodes["lexeme-meaning"].textContent, "");
  assert.strictEqual(nodes["lexeme-source"].textContent, "", "no source label either");
});

test("a contextual meaning is shown without a dictionary label", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "获得").click(body);
  assert.strictEqual(nodes["lexeme-meaning"].textContent, "получать");
  assert.strictEqual(nodes["lexeme-source"].textContent, "");
});

test("a CC-CEDICT meaning is shown and attributed", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "很难").click(body);
  assert.strictEqual(nodes["lexeme-pinyin"].textContent, "hěn nán");
  assert.strictEqual(nodes["lexeme-meaning"].textContent, "difficult");
  assert.strictEqual(nodes["lexeme-source"].textContent, "CC-CEDICT");
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
  assert.ok(!/^#text \{[^}]*line-height: 2\.5/m.test(sheet), "no extra leading while off");
  assert.match(sheet, /#text\.pinyin \{ line-height: 2\.5; \}/);
});

test("tapping a token never toggles the Mandarin Mosaic sentence around it", async () => {
  const { body, nodes } = start();
  await settle();
  word(body, "获得").click(body);
  nodes["lexeme-action"].fire("click");
  assert.strictEqual(nodes["mosaic-counter"].textContent, "Mandarin Mosaic · 0");
  assert.ok(!sentence(body, 0).classes.has("picked"));
});

test("sentence selection and lexical selection coexist", async () => {
  const { body, nodes } = start();
  await settle();
  sentence(body, 1).fire("click");
  word(body, "获得").click(body);
  nodes["lexeme-action"].fire("click");
  assert.strictEqual(nodes["hanly-counter"].textContent, "Hanly · 1");
  assert.strictEqual(nodes["mosaic-counter"].textContent, "Mandarin Mosaic · 1");
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
  assert.strictEqual(nodes["mosaic-counter"].textContent, "Mandarin Mosaic · 1");
  assert.ok(!sentence(body, 0).classes.has("picked"));
  assert.ok(sentence(body, 1).classes.has("picked"));
});

test("the layout has no fixed width that would overflow a narrow WebView", () => {
  const sheet = css();
  const widths = [...sheet.matchAll(/min-width:\s*(\d+)px/g)].map((m) => Number(m[1]));
  assert.ok(widths.every((w) => w <= 320), `min-width too large: ${widths}`);
  assert.match(sheet, /max-width:\s*100%|width:\s*100%/, "sheets stretch to the viewport");
  assert.match(sheet, /env\(safe-area-inset-bottom\)/, "bottom sheets clear the home indicator");
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
