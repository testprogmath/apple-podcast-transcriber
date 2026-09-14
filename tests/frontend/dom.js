"use strict";

const fs = require("fs");
const path = require("path");

const STATIC = path.join(__dirname, "..", "..", "podcast_bot", "reader", "static");

class Node {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.classes = new Set();
    this.attrs = {};
    this.listeners = {};
    this.hidden = false;
    this.disabled = false;
    this.focused = false;
    this._text = "";
  }

  get className() {
    return [...this.classes].join(" ");
  }

  set className(value) {
    this.classes = new Set(String(value).split(" ").filter(Boolean));
  }

  get classList() {
    const classes = this.classes;
    return {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
      toggle: (name, on) =>
        on === undefined
          ? classes.has(name)
            ? classes.delete(name)
            : classes.add(name)
          : on
            ? classes.add(name)
            : classes.delete(name),
    };
  }

  get textContent() {
    return this.children.length ? this.children.map((c) => c.textContent).join("") : this._text;
  }

  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  append(...nodes) {
    nodes.forEach((n) => this.children.push(n));
  }

  setAttribute(name, value) {
    this.attrs[name] = String(value);
  }

  getAttribute(name) {
    return name in this.attrs ? this.attrs[name] : null;
  }

  focus() {
    document.activeElement = this;
    this.focused = true;
  }

  addEventListener(name, handler) {
    (this.listeners[name] ||= []).push(handler);
  }

  fire(name, event = {}) {
    const propagating = { stopPropagation() {}, ...event };
    let stopped = false;
    const wrapped = { ...propagating, stopPropagation: () => (stopped = true) };
    (this.listeners[name] || []).forEach((handler) => handler(wrapped));
    return stopped;
  }

  /** Click that bubbles to ancestors unless a handler calls stopPropagation. */
  click(root) {
    const chain = ancestors(root || document.body, this);
    let stopped = false;
    for (const node of chain.reverse()) {
      if (stopped) break;
      stopped = node.fire("click");
    }
  }

  descendants(out = []) {
    for (const child of this.children) {
      out.push(child);
      child.descendants(out);
    }
    return out;
  }

  matches(selector) {
    if (selector.startsWith(".")) {
      return selector
        .slice(1)
        .split(".")
        .every((c) => this.classes.has(c));
    }
    const attr = /^\[([\w-]+)="([^"]*)"\]$/.exec(selector);
    if (attr) {
      const key = attr[1].startsWith("data-") ? attr[1].slice(5) : attr[1];
      return (attr[1].startsWith("data-") ? this.dataset[key] : this.attrs[attr[1]]) === attr[2];
    }
    return this.tagName === selector;
  }

  querySelectorAll(selector) {
    return this.descendants().filter((n) => n.matches(selector));
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

function ancestors(root, target, trail = []) {
  if (root === target) return [...trail, root];
  for (const child of root.children) {
    const found = ancestors(child, target, [...trail, root]);
    if (found) return found;
  }
  return null;
}

function failingStorage() {
  return {
    getItem() {
      throw new Error("site data blocked");
    },
    setItem() {
      throw new Error("site data blocked");
    },
  };
}

function memoryStorage(initial = {}) {
  const store = { ...initial };
  return {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = String(v);
    },
    _store: store,
  };
}

/** Build a document whose element ids come from the real index.html. */
function setup({ storage = memoryStorage(), telegram = null, search = "?doc=" + "a".repeat(32) } = {}) {
  const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");
  const ids = [...html.matchAll(/id="([\w-]+)"/g)].map((m) => m[1]);
  const nodes = {};
  const body = new Node("body");
  for (const id of ids) {
    const node = new Node("div");
    node.attrs.id = id;
    nodes[id] = node;
    body.appendChild(node);
  }
  const documentListeners = {};
  global.document = {
    body,
    activeElement: null,
    title: "",
    getElementById: (id) => nodes[id],
    createElement: (tag) => new Node(tag),
    createTextNode: (text) => {
      const n = new Node("#text");
      n._text = text;
      return n;
    },
    querySelectorAll: (s) => body.querySelectorAll(s),
    querySelector: (s) => body.querySelector(s),
    addEventListener: (name, handler) => ((documentListeners[name] ||= []).push(handler)),
    fire: (name, event) => (documentListeners[name] || []).forEach((h) => h(event)),
  };
  global.window = telegram ? { Telegram: { WebApp: telegram } } : {};
  global.location = { search };
  global.localStorage = storage;
  global.URLSearchParams = URLSearchParams;
  global.setTimeout = () => 0;
  global.clearTimeout = () => {};
  return { nodes, body, storage };
}

function loadApp() {
  const file = path.join(STATIC, "app.js");
  delete require.cache[require.resolve(file)];
  require(file);
}

function css() {
  return fs.readFileSync(path.join(STATIC, "app.css"), "utf8");
}

function markup() {
  return fs.readFileSync(path.join(STATIC, "index.html"), "utf8");
}

module.exports = { Node, setup, loadApp, memoryStorage, failingStorage, css, markup, STATIC };
