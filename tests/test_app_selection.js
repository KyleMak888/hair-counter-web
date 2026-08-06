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
  const listeners = {};
  return {
    _registry: registry,
    _listeners: listeners,
    querySelector: (sel) => get(sel),
    getElementById: (id) => get(`#${id}`),
    querySelectorAll: () => [],
    createElement: (tag) => makeFakeElement(tag),
    addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    removeEventListener: (type, fn) => { if (listeners[type]) listeners[type] = listeners[type].filter((f) => f !== fn); },
    dispatchEvent: (ev) => { (listeners[ev.type] || []).forEach((fn) => fn(ev)); return true; },
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

// ---- Layout remediation (feedback: resize rewrite) -----------------------

function readInlinePx(el, name) {
  const raw = (el.style.getPropertyValue(name) || "").trim();
  if (!raw) return null;
  const px = parseFloat(raw);
  return Number.isNaN(px) ? null : px;
}

// axis "y" drives the canvas/details divider (vertical drag); default "x" is horizontal.
function dragSplitter(sandbox, selector, from, to, axis) {
  const splitter = sandbox.document.querySelector(selector);
  const down = splitter._listeners.pointerdown[0];
  const start = axis === "y" ? { clientY: from } : { clientX: from };
  down({ ...start, pointerId: 1, preventDefault() {} });
  const move = axis === "y" ? { clientY: to } : { clientX: to };
  sandbox.document.dispatchEvent({ type: "pointermove", ...move, pointerId: 1 });
  sandbox.document.dispatchEvent({ type: "pointerup", ...move, pointerId: 1 });
}

test("control splitter drag writes --control-width in px, 1:1 with cursor", () => {
  const sandbox = buildSandbox();
  loadApp(sandbox);
  const workspace = sandbox.document.querySelector(".workspace");
  // restoreLayout defaults control to 360px; drag +60px -> 420
  dragSplitter(sandbox, "#controlSplitter", 500, 560);
  assert.equal(readInlinePx(workspace, "--control-width"), 420, "control width tracks cursor delta in px");
  assert.ok(workspace.style.getPropertyValue("--control-width").endsWith("px"));
});

test("canvas/details divider drag writes --canvas-height in px (vertical, no % jump)", () => {
  const sandbox = buildSandbox();
  // Set the real ~14px splitter height BEFORE load so restoreLayout computes correct defaults.
  sandbox.document.querySelector("#resultSplitter").offsetHeight = 14;
  loadApp(sandbox);
  const resultSplit = sandbox.document.querySelector("#resultSplit");
  // restoreLayout defaults canvas to 55% of clientHeight(800) = 440; drag +100 -> 540
  dragSplitter(sandbox, "#resultSplitter", 500, 600, "y");
  assert.equal(readInlinePx(resultSplit, "--canvas-height"), 540, "canvas height is pixel-accurate, not percentage");
  assert.ok(resultSplit.style.getPropertyValue("--canvas-height").endsWith("px"));
});

test("splitter drag is clamped to live min/max bounds", () => {
  const sandbox = buildSandbox();
  loadApp(sandbox);
  const workspace = sandbox.document.querySelector(".workspace");
  // max = min(620, 1000*0.5) = 500; drag far beyond -> clamp
  dragSplitter(sandbox, "#controlSplitter", 500, 5500);
  assert.equal(readInlinePx(workspace, "--control-width"), 500, "control width clamped to max");
});

test("restoreLayout clamps out-of-range persisted values to viewport", () => {
  const sandbox = buildSandbox();
  sandbox.document.querySelector("#resultSplitter").offsetHeight = 14;
  sandbox.localStorage.setItem("hair-counter:layout", JSON.stringify({ control: "9999px", canvas: "-50px" }));
  loadApp(sandbox);
  const workspace = sandbox.document.querySelector(".workspace");
  const resultSplit = sandbox.document.querySelector("#resultSplit");
  assert.equal(readInlinePx(workspace, "--control-width"), 500, "control clamped from 9999 to max 500");
  assert.equal(readInlinePx(resultSplit, "--canvas-height"), 200, "canvas clamped from -50 to min 200");
});

test("double-click on a splitter resets that axis to default", () => {
  const sandbox = buildSandbox();
  loadApp(sandbox);
  const workspace = sandbox.document.querySelector(".workspace");
  const splitter = sandbox.document.querySelector("#controlSplitter");
  dragSplitter(sandbox, "#controlSplitter", 500, 650); // 360 + 150 = 510 -> clamp max 500
  assert.equal(readInlinePx(workspace, "--control-width"), 500, "precondition: dragged away from default");
  splitter._listeners.dblclick[0]();
  assert.equal(readInlinePx(workspace, "--control-width"), 360, "double-click resets control to 360");
});

test("reset layout button clears storage and restores defaults", () => {
  const sandbox = buildSandbox();
  sandbox.document.querySelector("#resultSplitter").offsetHeight = 14;
  loadApp(sandbox);
  const workspace = sandbox.document.querySelector(".workspace");
  const resultSplit = sandbox.document.querySelector("#resultSplit");
  const btn = sandbox.document.querySelector("#resetLayoutButton");
  dragSplitter(sandbox, "#controlSplitter", 500, 650);   // -> clamp 500
  dragSplitter(sandbox, "#resultSplitter", 500, 800, "y"); // 440 + 300 = 740 -> clamp 626
  assert.notEqual(readInlinePx(workspace, "--control-width"), 360, "precondition: control perturbed");
  assert.notEqual(readInlinePx(resultSplit, "--canvas-height"), 440, "precondition: canvas perturbed");
  btn._listeners.click[0]();
  assert.equal(readInlinePx(workspace, "--control-width"), 360, "control restored to 360");
  assert.equal(readInlinePx(resultSplit, "--canvas-height"), 440, "canvas restored to 55% of 800 = 440");
  assert.equal(sandbox.localStorage.getItem("hair-counter:layout"), null, "layout storage cleared");
});

test("canvas fullscreen toggle adds body class and updates button label", () => {
  const sandbox = buildSandbox();
  loadApp(sandbox);
  const body = sandbox.document.body;
  const btn = sandbox.document.querySelector("#fullscreenButton");
  assert.equal(body.classList.contains("viewer-fullscreen"), false, "starts not fullscreen");
  btn._listeners.click[0]();
  assert.equal(body.classList.contains("viewer-fullscreen"), true, "click enters fullscreen");
  assert.equal(btn.textContent, "退出全屏", "button label flips to exit");
  // Esc exits fullscreen.
  sandbox.document.dispatchEvent({ type: "keydown", key: "Escape" });
  assert.equal(body.classList.contains("viewer-fullscreen"), false, "Esc exits fullscreen");
  assert.equal(btn.textContent, "全屏", "button label restored");
});
