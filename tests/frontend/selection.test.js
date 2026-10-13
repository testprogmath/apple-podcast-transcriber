"use strict";

const assert = require("assert");
const { setup, loadApp } = require("./dom.js");

const ID = "a".repeat(32);
const PINYIN = { 他们: "tā men", 公布: "gōng bù", 研究: "yán jiū", 成果: "chéng guǒ", 很好: "hěn hǎo" };

function sentence(id, text, words) {
  const chars = Array.from(text);
  const tokens = [];
  let at = 0;
  for (const piece of words) {
    const size = Array.from(piece).length;
    tokens.push({ t: piece, w: /[一-鿿]/.test(piece), start: at, end: at + size });
    at += size;
  }
  assert.strictEqual(chars.length, at, `tokens cover ${text}`);
  return { id, text, tokens };
}

function documentWith(...sentences) {
  return {
    id: ID, title: "Selection", source_type: "pasted_text", audio_status: "text",
    hanly_available: true, mosaic_available: true, hanly_error: "", mosaic_error: "",
    paragraphs: [sentences.map((s) => s.id)],
    glossary: Object.fromEntries(Object.entries(PINYIN).map(([g, p]) => [g, { p }])),
    sentences,
  };
}

const RESEARCH = sentence(0, "他们公布了新的研究成果。", ["他们", "公布", "了", "新", "的", "研究", "成果", "。"]);
const SECOND = sentence(1, "学中文没有想象中那么难。", ["学", "中文", "没有", "想象", "中", "那么", "难", "。"]);
const EMOJI = sentence(2, "🙂好的，研究成果很好，新的研究成果也很好。",
  ["🙂", "好的", "，", "研究", "成果", "很好", "，", "新", "的", "研究", "成果", "也", "很好", "。"]);
const TRADITIONAL = sentence(3, "這是傳統文化。", ["這", "是", "傳統", "文化", "。"]);

function start({ doc = documentWith(RESEARCH, SECOND, EMOJI, TRADITIONAL), respond } = {}) {
  const env = setup();
  const selection = { range: null };
  window.getSelection = () => ({
    get rangeCount() { return selection.range ? 1 : 0; },
    get isCollapsed() { return !selection.range || selection.range.collapsed; },
    getRangeAt: () => selection.range,
    removeAllRanges: () => { selection.range = null; },
  });
  const calls = [];
  global.fetch = async (path, init = {}) => {
    calls.push({ path, method: init.method || "GET", body: init.body ? JSON.parse(init.body) : null });
    const custom = respond && (await respond(path, init));
    if (custom) return custom;
    return { ok: true, json: async () => structuredClone(doc) };
  };
  loadApp();
  return { ...env, calls, selection };
}

async function settle() {
  for (let i = 0; i < 6; i += 1) await new Promise((r) => setImmediate(r));
}

const holder = (body, id) => body.querySelector(`[data-sentence="${id}"]`);
const source = (body, id) => holder(body, id).querySelector(".sentence-source");
const words = (body, id) => source(body, id).querySelectorAll(".word");
const word = (body, id, glyph, nth = 0) => words(body, id).filter((w) => w.dataset.glyph === glyph)[nth];
/** The canonical text node of a word, under <ruby> when pinyin exists. */
const glyphText = (node) => (node.childNodes[0].tagName === "ruby"
  ? node.childNodes[0].childNodes[0] : node.childNodes[0]);
const rtText = (node) => node.childNodes[0].childNodes[1].childNodes[0];
const range = (startContainer, startOffset, endContainer, endOffset) => ({
  startContainer, startOffset, endContainer, endOffset,
  collapsed: startContainer === endContainer && startOffset === endOffset,
});

function choose(env, value) {
  env.selection.range = value;
  document.fire("selectionchange", {});
}

const bar = (nodes) => ({
  hidden: nodes["selection-actions"].hidden,
  text: nodes["selection-text"].textContent,
  make: nodes["selection-expression"].disabled,
  hanly: nodes["selection-hanly"].disabled,
});
const ok = (data) => ({ ok: true, json: async () => data });

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("selection across two chunks maps to one canonical span", async () => {
  const env = start();
  await settle();
  choose(env, range(glyphText(word(env.body, 0, "研究")), 0, glyphText(word(env.body, 0, "成果")), 2));
  assert.deepStrictEqual(bar(env.nodes), { hidden: false, text: "研究成果", make: false, hanly: false });
});

test("a selection may begin and end inside lexical chunks", async () => {
  const env = start();
  await settle();
  choose(env, range(glyphText(word(env.body, 0, "公布")), 1, glyphText(word(env.body, 0, "研究")), 1));
  assert.strictEqual(bar(env.nodes).text, "布了新的研");
});

test("pinyin never becomes part of the span, even when a handle rests on it", async () => {
  const env = start();
  await settle();
  env.nodes["pinyin-toggle"].click();
  choose(env, range(rtText(word(env.body, 0, "研究")), 2, rtText(word(env.body, 0, "成果")), 3));
  assert.strictEqual(bar(env.nodes).text, "研究成果");
});

test("an element boundary resolves to the neighbouring source text", async () => {
  const env = start();
  await settle();
  const src = source(env.body, 0);
  const at = src.children.indexOf(word(env.body, 0, "研究"));
  choose(env, range(src, at, src, at + 2));
  assert.strictEqual(bar(env.nodes).text, "研究成果");
});

test("a selection ending in the visible translation is refused", async () => {
  const env = start({
    respond: (path) => (path.endsWith("/translation")
      ? ok({ sentence_id: 0, translation: "Они опубликовали новые результаты." }) : null),
  });
  await settle();
  holder(env.body, 0).querySelector(".translation-action").click(env.body);
  await settle();
  const translation = holder(env.body, 0).querySelector(".translation-text");
  assert.strictEqual(translation.hidden, false);
  choose(env, range(glyphText(word(env.body, 0, "研究")), 0, translation.childNodes[0], 3));
  assert.deepStrictEqual(bar(env.nodes),
    { hidden: false, text: "Select Chinese text within one sentence.", make: true, hanly: true });
});

test("a selection entirely outside Reader source text shows no actions", async () => {
  const env = start();
  await settle();
  const title = env.nodes.title;
  choose(env, range(title.childNodes[0], 0, title.childNodes[0], 4));
  assert.strictEqual(env.nodes["selection-actions"].hidden, true);
});

test("cross-sentence selection is refused rather than truncated", async () => {
  const env = start();
  await settle();
  choose(env, range(glyphText(word(env.body, 0, "成果")), 0, glyphText(word(env.body, 1, "中文")), 1));
  assert.deepStrictEqual(bar(env.nodes),
    { hidden: false, text: "Select Chinese text within one sentence.", make: true, hanly: true });
});

test("sentence punctuation in the selection is refused", async () => {
  const env = start();
  await settle();
  const src = source(env.body, 0);
  const stop = src.childNodes[src.childNodes.length - 1];
  choose(env, range(glyphText(word(env.body, 0, "成果")), 0, stop, 1));
  assert.strictEqual(bar(env.nodes).text, "Select Chinese text within one sentence.");
});

test("a collapsed selection is ignored and hides the actions", async () => {
  const env = start();
  await settle();
  const node = glyphText(word(env.body, 0, "研究"));
  choose(env, range(node, 0, node, 2));
  assert.strictEqual(env.nodes["selection-actions"].hidden, false);
  choose(env, range(node, 1, node, 1));
  assert.strictEqual(env.nodes["selection-actions"].hidden, true);
  choose(env, null);
  assert.strictEqual(env.nodes["selection-actions"].hidden, true);
});

test("a single character can go to Hanly but cannot become an expression", async () => {
  const env = start();
  await settle();
  const node = glyphText(word(env.body, 0, "研究"));
  choose(env, range(node, 0, node, 1));
  assert.deepStrictEqual(bar(env.nodes), { hidden: false, text: "研", make: true, hanly: false });
});

test("adjusting the native handles updates the action span", async () => {
  const env = start();
  await settle();
  const first = glyphText(word(env.body, 0, "研究"));
  choose(env, range(first, 0, first, 2));
  assert.strictEqual(bar(env.nodes).text, "研究");
  choose(env, range(first, 0, glyphText(word(env.body, 0, "成果")), 2));
  assert.strictEqual(bar(env.nodes).text, "研究成果");
});

test("emoji before the span: offsets count code points, not UTF-16 units", async () => {
  const env = start({
    respond: (path, init) => (path.endsWith("/lexical-overrides")
      ? ok({ override: { id: 7 }, sentence: { id: 2, tokens: [] }, glossary: {} }) : null),
  });
  await settle();
  choose(env, range(glyphText(word(env.body, 2, "研究")), 0, glyphText(word(env.body, 2, "成果")), 2));
  env.nodes["selection-expression"].click();
  await settle();
  const post = env.calls.find((c) => c.method === "POST");
  // "🙂好的，" is five UTF-16 units but four code points.
  assert.deepStrictEqual(post.body, { sentence_id: 2, start: 4, end: 8, text: "研究成果" });
});

test("repeated text: the second occurrence keeps its own offsets", async () => {
  const env = start();
  await settle();
  choose(env, range(glyphText(word(env.body, 2, "研究", 1)), 0, glyphText(word(env.body, 2, "成果", 1)), 2));
  env.nodes["selection-hanly"].click();
  await settle();
  const post = env.calls.find((c) => c.path.endsWith("/selection"));
  // 🙂好的，研究成果很好，新的 is thirteen code points.
  assert.deepStrictEqual(post.body, { sentence_id: 2, start: 13, end: 17, text: "研究成果" });
});

test("Traditional Chinese is selected exactly as written", async () => {
  const env = start();
  await settle();
  choose(env, range(glyphText(word(env.body, 3, "傳統")), 0, glyphText(word(env.body, 3, "文化")), 2));
  assert.strictEqual(bar(env.nodes).text, "傳統文化");
});

test("the click that ends a selection opens no popup and selects no sentence", async () => {
  const env = start();
  await settle();
  const node = word(env.body, 0, "研究");
  choose(env, range(glyphText(node), 0, glyphText(word(env.body, 0, "成果")), 2));
  node.click(env.body);
  holder(env.body, 0).click(env.body);
  assert.strictEqual(env.nodes["lexeme-glyph"].textContent, "", "no popup opened");
  assert.strictEqual(env.nodes["mosaic-counter"].textContent, "Mosaic · 0");
  assert.ok(!holder(env.body, 0).classes.has("picked"));
});

test("a short tap still opens the lexical popup, and ○ still selects for Mosaic", async () => {
  const env = start();
  await settle();
  word(env.body, 0, "研究").click(env.body);
  assert.strictEqual(env.nodes.lexeme.hidden, false);
  assert.strictEqual(env.nodes["lexeme-glyph"].textContent, "研究");
  assert.strictEqual(env.nodes["lexeme-remove-expression"].hidden, true, "automatic chunk");
  env.nodes["lexeme-close"].click();
  holder(env.body, 0).querySelector(".mark").click(env.body);
  assert.strictEqual(env.nodes["mosaic-counter"].textContent, "Mosaic · 1");
});

const CUSTOM = {
  override: { id: 9, sentence_id: 0, start: 7, end: 11 },
  sentence: {
    id: 0,
    tokens: [
      ...RESEARCH.tokens.slice(0, 5),
      { t: "研究成果", w: true, start: 7, end: 11, override: 9 },
      { t: "。", w: false, start: 11, end: 12 },
    ],
  },
  glossary: { 研究成果: { p: "yán jiū chéng guǒ", en: ["research findings"], vocabulary_state: "unknown" } },
};

test("Make expression persists the span, rerenders one chunk and opens its popup", async () => {
  const env = start({ respond: (path) => (path.endsWith("/lexical-overrides") ? ok(structuredClone(CUSTOM)) : null) });
  await settle();
  holder(env.body, 0).querySelector(".mark").click(env.body);
  choose(env, range(glyphText(word(env.body, 0, "研究")), 0, glyphText(word(env.body, 0, "成果")), 2));
  env.nodes["selection-expression"].click();
  await settle();
  assert.deepStrictEqual(words(env.body, 0).map((w) => w.dataset.glyph), ["他们", "公布", "了", "新", "的", "研究成果"]);
  assert.strictEqual(word(env.body, 0, "研究成果").dataset.override, "9");
  assert.strictEqual(env.nodes["lexeme-glyph"].textContent, "研究成果");
  assert.strictEqual(env.nodes["lexeme-pinyin"].textContent, "yán jiū chéng guǒ");
  assert.strictEqual(env.nodes["lexeme-remove-expression"].hidden, false);
  assert.strictEqual(env.selection.range, null, "native selection cleared");
  assert.strictEqual(env.nodes["selection-actions"].hidden, true);
  assert.strictEqual(env.nodes["hanly-counter"].textContent, "Hanly · 0", "no Hanly side effect");
  assert.strictEqual(env.nodes["mosaic-counter"].textContent, "Mosaic · 1");
  assert.ok(holder(env.body, 0).querySelector(".mark"), "sentence controls survive");
});

test("a failed request keeps the selection for another attempt", async () => {
  const env = start({
    respond: (path) => (path.endsWith("/lexical-overrides")
      ? { ok: false, json: async () => ({ error: "That overlaps an existing custom expression." }) } : null),
  });
  await settle();
  const value = range(glyphText(word(env.body, 0, "研究")), 0, glyphText(word(env.body, 0, "成果")), 2);
  choose(env, value);
  env.nodes["selection-expression"].click();
  await settle();
  assert.strictEqual(env.nodes.toast.textContent, "That overlaps an existing custom expression.");
  assert.strictEqual(env.selection.range, value);
  assert.deepStrictEqual(bar(env.nodes), { hidden: false, text: "研究成果", make: false, hanly: false });
});

test("direct Add to Hanly fills the basket and leaves segmentation alone", async () => {
  const env = start({
    respond: (path) => (path.endsWith("/selection")
      ? ok({ glyph: "没有想象中那么难", sentence_id: 1, start: 3, end: 11,
        entry: { p: "méi yǒu xiǎng xiàng zhōng nà me nán", meaning_source: "missing" } })
      : path.endsWith("/hanly") ? ok({ uploaded: 1, total: 1, name: "Selection", notes: [] }) : null),
  });
  await settle();
  const before = words(env.body, 1).map((w) => w.dataset.glyph);
  choose(env, range(glyphText(word(env.body, 1, "没有")), 0, glyphText(word(env.body, 1, "难")), 1));
  env.nodes["selection-hanly"].click();
  await settle();
  assert.deepStrictEqual(words(env.body, 1).map((w) => w.dataset.glyph), before);
  assert.ok(!env.calls.some((c) => c.path.includes("lexical-overrides")), "no override persisted");
  assert.strictEqual(env.nodes["hanly-counter"].textContent, "Hanly · 1");
  env.nodes["hanly-counter"].click();
  const row = env.nodes["basket-items"].children[0];
  assert.strictEqual(row.querySelector(".glyph").textContent, "没有想象中那么难");
  assert.strictEqual(row.querySelector(".reading").textContent, "méi yǒu xiǎng xiàng zhōng nà me nán");
  env.nodes["basket-upload"].click();
  await settle();
  const upload = env.calls.find((c) => c.path.endsWith("/hanly"));
  assert.deepStrictEqual(upload.body.items,
    [{ glyph: "没有想象中那么难", sentence_id: 1, start: 3, end: 11 }]);
});

test("removing a custom expression restores automatic chunks and keeps Hanly and Mosaic", async () => {
  const restored = { sentence: { id: 0, tokens: RESEARCH.tokens }, glossary: {} };
  const doc = documentWith({ ...RESEARCH, tokens: CUSTOM.sentence.tokens }, SECOND);
  doc.glossary["研究成果"] = CUSTOM.glossary["研究成果"];
  const env = start({ doc,
    respond: (path, init) => (init.method === "DELETE" ? ok(structuredClone(restored)) : null) });
  await settle();
  holder(env.body, 0).querySelector(".mark").click(env.body);
  word(env.body, 0, "研究成果").click(env.body);
  env.nodes["lexeme-action"].click();
  assert.strictEqual(env.nodes["hanly-counter"].textContent, "Hanly · 1");
  word(env.body, 0, "研究成果").click(env.body);
  env.nodes["lexeme-remove-expression"].click();
  await settle();
  const remove = env.calls.find((c) => c.method === "DELETE");
  assert.strictEqual(remove.path, `/api/reader/${ID}/lexical-overrides/9`);
  assert.deepStrictEqual(words(env.body, 0).map((w) => w.dataset.glyph), ["他们", "公布", "了", "新", "的", "研究", "成果"]);
  assert.strictEqual(env.nodes.lexeme.hidden, true);
  assert.strictEqual(env.nodes["hanly-counter"].textContent, "Hanly · 1");
  assert.strictEqual(env.nodes["mosaic-counter"].textContent, "Mosaic · 1");
  env.nodes["hanly-counter"].click();
  env.nodes["basket-upload"].click();
  await settle();
  const upload = env.calls.find((c) => c.path.endsWith("/hanly"));
  // The basket entry is pinned to its source span, so it survives the removed expression.
  assert.deepStrictEqual(upload.body.items, [{ glyph: "研究成果", sentence_id: 0, start: 7, end: 11 }]);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log("  ✓ " + name);
    } catch (error) {
      failed += 1;
      console.log("  ✗ " + name);
      console.log(String(error.message).split("\n").map((l) => "      " + l).join("\n"));
    }
  }
  console.log(`\n${tests.length - failed}/${tests.length} selection frontend tests passed`);
  process.exit(failed ? 1 : 0);
})();
