// jsdom-free verification of feedback ③ (three-pane resizable layout + selection linkage).
// Loads the real frontend/app.js inside a minimal stubbed DOM/Canvas context and
// exercises the selection-linkage code paths without a browser.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

const APP_SOURCE = fs.readFileSync(require("node:path").join(__dirname, "..", "frontend", "app.js"), "utf8");

// ---- Minimal fake DOM ----------------------------------------------------

function makeClassList() {
  const set = new Set();
  return {
    add: (...c) => c.forEach((x) => set.add(x)),
    remove: (...c) => c.forEach((x) => set.delete(x)),
    toggle: (c, force) => {
      const want = force === undefined ? !set.has(c) : force;
      if (want) set.add(c); else set.delete(c);
      return want;
    },
    contains: (c) => set.has(c),
    _set: set,
  };
}

function makeStyleObject() {
  const map = new Map();
  return {
    setProperty: (k, v) => map.set(k, String(v)),
    getPropertyValue: (k) => (map.has(k) ? map.get(k) : ""),
    _map: map,
  };
}

function makeFake2DContext() {
  const calls = { strokeRects: [], usedSelectionColor: false };
  return {
    _calls: calls,
    lineWidth: 1, strokeStyle: "", fillStyle: "", font: "", textBaseline: "", globalAlpha: 1,
    clearRect() {}, drawImage() {}, beginPath() {}, arc() {}, fill() {}, fillRect() {},
    fillText() {}, save() {}, restore() {}, setLineDash() {},
    measureText: (t) => ({ width: String(t).length * 7 }),
    strokeRect(x, y, w, h) {
      calls.strokeRects.push({ x, y, w, h, strokeStyle: this.strokeStyle });
      if (this.strokeStyle === "#2563eb") calls.usedSelectionColor = true;
    },
  };
}

function makeFakeElement(tag = "div") {
  const target = {
    tagName: tag,
    children: [],
    dataset: {},
    classList: makeClassList(),
    style: makeStyleObject(),
    _attrs: {},
    _textContent: "",
    _innerHTML: "",
    offsetWidth: 360,
    offsetHeight: 600,
    clientWidth: 1000,
    clientHeight: 800,
    width: 600,
    height: 400,
    hidden: false,
    value: "",
    files: [],
    disabled: false,
    className: "",
    _listeners: {},
    _ctx: null,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 600, height: 400, right: 600, bottom: 400 }),
    addEventListener: (type, fn) => { (target._listeners[type] ||= []).push(fn); },
    removeEventListener: (type, fn) => { if (target._listeners[type]) target._listeners[type] = target._listeners[type].filter((f) => f !== fn); },
    dispatchEvent: () => true,
    appendChild: (c) => { target.children.push(c); return c; },
    removeChild: (c) => { const i = target.children.indexOf(c); if (i >= 0) target.children.splice(i, 1); },
    remove: () => {},
    setAttribute: (k, v) => { target._attrs[k] = String(v); },
    getAttribute: (k) => target._attrs[k],
    querySelector: () => makeFakeElement(),
    querySelectorAll: () => [],
    closest: () => null,
    setPointerCapture: () => {},
    releasePointerCapture: () => {},
    scrollIntoView: () => { target._scrolled = true; },
    getContext: () => (target._ctx ||= makeFake2DContext()),
    focus: () => {},
    click: () => {},
  };
  const proxy = new Proxy(target, {
    get(t, prop) {
      if (prop === "innerHTML") return t._innerHTML;
      if (prop === "textContent") return t._textContent;
      if (prop in t) return t[prop];
      if (prop === "previousElementSibling" || prop === "nextElementSibling" || prop === "parentElement") {
        return makeFakeElement();
      }
      // Unknown property: return a chainable no-op so arbitrary method calls never throw.
      return () => proxy;
    },
    set(t, prop, value) {
      if (prop === "innerHTML") {
        t._innerHTML = String(value);
        // Assigning markup (even empty) replaces child nodes, matching real DOM.
        t.children.length = 0;
        return true;
      }
      if (prop === "textContent") {
        t._textContent = String(value);
        t.children.length = 0;
        return true;
      }
      t[prop] = value;
      return true;
    },
  });
  return proxy;
}

function makeFakeDocument() {
  const registry = new Map();
  const get = (selector) => {
    if (!registry.has(selector)) registry.set(selector, makeFakeElement());
    return registry.get(selector);
  };
  return {
    _registry: registry,
    querySelector: (sel) => get(sel),
    getElementById: (id) => get(`#${id}`),
    querySelectorAll: () => [],
    createElement: (tag) => makeFakeElement(tag),
    addEventListener: () => {},
    removeEventListener: () => {},
    body: makeFakeElement("body"),
    documentElement: makeFakeElement("html"),
    activeElement: null,
    title: "",
  };
}

// ---- Sandbox -------------------------------------------------------------

function loadApp(sandbox) {
  const hook = "\n;globalThis.__APP__ = { state, selectItem, handleCanvasClick, drawAnnotatedImage, updateTable, renderAll, findItemIndex, normalizedItems, $, canvas };\n";
  vm.runInNewContext(APP_SOURCE + hook, sandbox);
  return sandbox.__APP__;
}

function buildSandbox() {
  const store = new Map();
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    fetch: () => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}), text: () => Promise.resolve("") }),
    URL,
    Blob,
    FormData,
    Headers,
    Image: class { constructor() { this.width = 0; this.height = 0; } },
    navigator: { userAgent: "node" },
    window: { addEventListener: () => {}, removeEventListener: () => {}, matchMedia: () => ({ matches: false, addEventListener: () => {} }) },
    requestAnimationFrame: (fn) => setTimeout(fn, 0),
    document: makeFakeDocument(),
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
  };
  sandbox.window.localStorage = sandbox.localStorage;
  return sandbox;
}

function sampleItems() {
  const base = { strand_count: 1, confidence: 0.8, split_confidence: 0.5, contrast: 0.5, partial: false, manual: false, adjusted: false };
  return [
    { ...base, bbox: [90, 90, 20, 20], center: [100, 100] },
    { ...base, strand_count: 2, bbox: [200, 200, 20, 20], center: [210, 210] },
    { ...base, bbox: [300, 300, 20, 20], center: [310, 310] },
  ];
}

// ---- Tests ---------------------------------------------------------------

test("app.js loads without throwing under the stubbed DOM", () => {
  const sandbox = buildSandbox();
  let loadError = null;
  const onReject = (e) => { loadError = e; };
  process.on("unhandledRejection", onReject);
  let app;
  try {
    app = loadApp(sandbox);
  } finally {
    process.off("unhandledRejection", onReject);
  }
  assert.ok(app, "app symbol export should be present");
  assert.ok(typeof app.selectItem === "function");
  assert.equal(loadError, null, `async load path should not reject (got: ${loadError && loadError.message})`);
});

test("updateTable renders rows with dataset.index and selected class", () => {
  const sandbox = buildSandbox();
  const app = loadApp(sandbox);
  app.state.image = { naturalWidth: 600, naturalHeight: 400, width: 600, height: 400 };
  app.state.items = sampleItems();
  app.state.selectedIndex = 1;
  app.renderAll();

  const table = app.$("#itemsTable");
  assert.equal(table.children.length, 3, "three rows rendered");
  table.children.forEach((row, i) => {
    assert.equal(Number(row.dataset.index), i, "row carries its item index");
  });
  assert.equal(table.children[1].classList.contains("selected"), true, "selected row flagged");
  assert.equal(table.children[0].classList.contains("selected"), false, "non-selected row not flagged");
});

test("selectItem updates shared state and re-renders the selected row", () => {
  const sandbox = buildSandbox();
  const app = loadApp(sandbox);
  app.state.image = { naturalWidth: 600, naturalHeight: 400, width: 600, height: 400 };
  app.state.items = sampleItems();
  app.renderAll();
  assert.equal(app.state.selectedIndex, -1);

  app.selectItem(2);
  assert.equal(app.state.selectedIndex, 2);
  const table = app.$("#itemsTable");
  assert.equal(table.children[2].classList.contains("selected"), true);
});

test("drawAnnotatedImage draws the blue selection ring around the selected cluster", () => {
  const sandbox = buildSandbox();
  const app = loadApp(sandbox);
  app.state.image = { naturalWidth: 600, naturalHeight: 400, width: 600, height: 400 };
  app.state.items = sampleItems();
  app.state.selectedIndex = 1;
  const ctx = app.canvas.getContext("2d");
  ctx._calls.usedSelectionColor = false;
  app.drawAnnotatedImage(app.canvas, app.state.image, app.state.items);
  assert.equal(ctx._calls.usedSelectionColor, true, "selection ring (#2563eb) must be stroked");
  // The ring is drawn as the last strokeRect for the selected item's padded box.
  const rings = ctx._calls.strokeRects.filter((r) => r.strokeStyle === "#2563eb");
  assert.equal(rings.length, 1, "exactly one selection ring for the selected cluster");
});

test("canvas click in view mode selects the nearest cluster", () => {
  const sandbox = buildSandbox();
  const app = loadApp(sandbox);
  app.state.image = { naturalWidth: 600, naturalHeight: 400, width: 600, height: 400 };
  app.state.items = sampleItems();
  app.state.editMode = "view";
  // canvasPoint maps 1:1 because rect width/height == canvas width/height == 600/400.
  app.handleCanvasClick({ clientX: 210, clientY: 210, preventDefault() {} });
  assert.equal(app.state.selectedIndex, 1, "click on cluster center selects it");
});

test("canvas click on empty space clears the current selection", () => {
  const sandbox = buildSandbox();
  const app = loadApp(sandbox);
  app.state.image = { naturalWidth: 600, naturalHeight: 400, width: 600, height: 400 };
  app.state.items = sampleItems();
  app.state.editMode = "view";
  app.state.selectedIndex = 1;
  // Click somewhere far from every cluster center.
  app.handleCanvasClick({ clientX: 5, clientY: 5, preventDefault() {} });
  assert.equal(app.state.selectedIndex, -1, "empty click clears selection");
});

test("clicking a detail row selects the corresponding cluster (reverse linkage)", () => {
  const sandbox = buildSandbox();
  const app = loadApp(sandbox);
  app.state.image = { naturalWidth: 600, naturalHeight: 400, width: 600, height: 400 };
  app.state.items = sampleItems();
  app.renderAll();
  const table = app.$("#itemsTable");
  const handler = table._listeners.click[0];
  assert.ok(handler, "itemsTable click handler is bound at load");
  handler({
    target: {
      closest: (sel) => (sel === "button[data-strand-action]" ? null : sel === "tr[data-index]" ? { dataset: { index: "1" } } : null),
    },
  });
  assert.equal(app.state.selectedIndex, 1, "clicking the row selects its cluster");
});

test("selection state resets safely when items shrink (renderAll guard)", () => {
  const sandbox = buildSandbox();
  const app = loadApp(sandbox);
  app.state.image = { naturalWidth: 600, naturalHeight: 400, width: 600, height: 400 };
  app.state.items = sampleItems();
  app.state.selectedIndex = 2;
  // Simulate switching image / clearing items without resetting selectedIndex first.
  app.state.items = sampleItems().slice(0, 1);
  app.renderAll();
  assert.equal(app.state.selectedIndex, -1, "stale selection is cleared by renderAll guard");
  const table = app.$("#itemsTable");
  assert.equal(table.children.length, 1);
});
