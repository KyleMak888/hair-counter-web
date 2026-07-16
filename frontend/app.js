const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

let MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
let MAX_SIDE = 6000;
let MAX_PIXELS = 20_000_000;

const state = {
  me: null,
  sourceFile: null,
  uploadFile: null,
  objectUrl: null,
  image: null,
  response: null,
  autoItems: [],
  items: [],
  editMode: "view",
  dirty: false,
  requestVersion: 0,
  pendingRequestId: null,
  appView: "tool",
  adminAccounts: [],
  adminLedger: [],
  managedAccountId: null,
};

const fileInput = $("#fileInput");
const dropZone = $("#dropZone");
const fileRow = $("#fileRow");
const fileThumb = $("#fileThumb");
const fileName = $("#fileName");
const fileInfo = $("#fileInfo");
const analyzeButton = $("#analyzeButton");
const sensitivity = $("#sensitivity");
const contrast = $("#contrast");
const canvas = $("#resultCanvas");
const ctx = canvas.getContext("2d");

function formatMoney(fen) {
  return `¥${(Number(fen || 0) / 100).toFixed(2)}`;
}

function yuanToFen(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) throw new Error("请输入有效金额");
  return Math.round(number * 100);
}

function apiError(payload, fallback = "请求失败") {
  const detail = payload?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return fallback;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function requestId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function sensitivityLabel(value) {
  const number = Number(value);
  if (number <= -12) return "很严格";
  if (number < -3) return "偏严格";
  if (number <= 3) return "标准";
  if (number < 12) return "偏灵敏";
  return "很灵敏";
}

function thresholdOffset() {
  return -Number(sensitivity.value);
}

function clearError() { $("#errorMessage").textContent = ""; }
function showError(message) { $("#errorMessage").textContent = message; }

async function checkHealth() {
  const node = $("#serviceStatus");
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) throw new Error();
    const data = await response.json();
    MAX_UPLOAD_BYTES = Number(data.max_upload_bytes) || MAX_UPLOAD_BYTES;
    MAX_SIDE = Number(data.max_image_side) || MAX_SIDE;
    MAX_PIXELS = Number(data.max_image_pixels) || MAX_PIXELS;
    $("#uploadLimits").textContent = `支持 JPG、PNG、WebP、BMP，最大 ${formatBytes(MAX_UPLOAD_BYTES)}`;
    node.className = "status-pill ok";
    node.querySelector("span:last-child").textContent = "系统正常";
  } catch {
    node.className = "status-pill error";
    node.querySelector("span:last-child").textContent = "服务暂不可用";
  }
}

async function apiJson(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(url, { ...options, headers });
  const payload = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (response.status === 401) {
    showLogin();
    $("#loginMessage").textContent = "登录已失效，请重新登录";
    throw new Error(apiError(payload, "登录已失效，请重新登录"));
  }
  if (!response.ok) throw new Error(apiError(payload));
  return payload;
}

function updateAccountHeader() {
  if (!state.me) return;
  $("#accountName").textContent = state.me.display_name;
  $("#accountBalance").textContent = formatMoney(state.me.balance_fen);
  $("#accountPrice").textContent = `${formatMoney(state.me.unit_price_fen)} / 根`;
  $("#currentUnitPrice").textContent = `${formatMoney(state.me.unit_price_fen)} / 根`;
  $("#viewSwitch").hidden = state.me.role !== "admin";
}

function showLogin() {
  if (state.me) resetFile();
  $$("dialog[open]").forEach((dialog) => dialog.close());
  state.me = null;
  $("#appShell").hidden = true;
  $("#authScreen").hidden = false;
  $("#loginPassword").value = "";
  $("#loginUsername").focus();
}

function showApp(account) {
  state.me = account;
  $("#authScreen").hidden = true;
  $("#appShell").hidden = false;
  updateAccountHeader();
  setAppView("tool");
}

async function loadSession() {
  try {
    const response = await fetch("/api/me", { cache: "no-store" });
    if (!response.ok) {
      showLogin();
      return;
    }
    showApp(await response.json());
  } catch {
    showLogin();
    $("#loginMessage").textContent = "暂时无法连接服务";
  }
}

async function login(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type='submit']");
  $("#loginMessage").textContent = "";
  button.disabled = true;
  button.classList.add("loading");
  try {
    const account = await apiJson("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("#loginUsername").value.trim(),
        password: $("#loginPassword").value,
      }),
    });
    showApp(account);
  } catch (error) {
    $("#loginMessage").textContent = error.message || "登录失败";
  } finally {
    button.disabled = false;
    button.classList.remove("loading");
  }
}

async function logout() {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } finally {
    showLogin();
  }
}

function setAppView(view) {
  if (view === "admin" && state.me?.role !== "admin") return;
  state.appView = view;
  $("#toolView").hidden = view !== "tool";
  $("#adminView").hidden = view !== "admin";
  $$("[data-app-view]").forEach((button) => button.classList.toggle("active", button.dataset.appView === view));
  if (view === "admin") loadAdmin();
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}

function renderAdmin() {
  const accounts = state.adminAccounts.filter((account) => account.role === "user");
  $("#adminAccountCount").textContent = accounts.filter((account) => account.active).length;
  $("#adminTotalBalance").textContent = formatMoney(accounts.reduce((sum, account) => sum + account.balance_fen, 0));
  $("#adminTotalStrands").textContent = accounts.reduce((sum, account) => sum + account.total_billable_count, 0).toLocaleString("zh-CN");
  $("#adminTotalSpent").textContent = formatMoney(accounts.reduce((sum, account) => sum + account.total_spent_fen, 0));

  const accountBody = $("#adminAccountsTable");
  accountBody.innerHTML = accounts.length ? accounts.map((account) => `<tr>
    <td>${escapeHtml(account.display_name)}</td><td>${escapeHtml(account.username)}</td>
    <td>${formatMoney(account.unit_price_fen)} / 根</td><td>${formatMoney(account.balance_fen)}</td>
    <td>${Number(account.total_billable_count).toLocaleString("zh-CN")}</td><td>${formatMoney(account.total_spent_fen)}</td>
    <td><span class="status-tag ${account.active ? "normal" : "partial"}">${account.active ? "启用" : "停用"}</span></td>
    <td>${formatDate(account.last_recognition_at)}</td>
    <td><button type="button" class="table-action" data-manage-account="${account.id}">管理</button></td>
  </tr>`).join("") : '<tr><td class="empty-row" colspan="9">还没有客户账号</td></tr>';

  const ledgerBody = $("#adminLedgerTable");
  ledgerBody.innerHTML = state.adminLedger.length ? state.adminLedger.map((entry) => `<tr>
    <td>${formatDate(entry.created_at)}</td><td>${escapeHtml(entry.display_name)}</td>
    <td>${entry.entry_type === "charge" ? "识别扣费" : "余额调整"}</td>
    <td>${entry.billable_count ?? "—"}</td>
    <td class="${entry.amount_fen >= 0 ? "money-positive" : "money-negative"}">${entry.amount_fen >= 0 ? "+" : "−"}${formatMoney(Math.abs(entry.amount_fen))}</td>
    <td>${formatMoney(entry.balance_after_fen)}</td>
    <td>${escapeHtml(entry.admin_username || "系统")} · ${escapeHtml(entry.note)}</td>
  </tr>`).join("") : '<tr><td class="empty-row" colspan="7">还没有计费流水</td></tr>';
  $("#ledgerCount").textContent = `${state.adminLedger.length} 条`;
}

async function loadAdmin() {
  $("#adminMessage").textContent = "";
  try {
    const [accounts, ledger] = await Promise.all([
      apiJson("/api/admin/accounts", { cache: "no-store" }),
      apiJson("/api/admin/ledger?limit=100", { cache: "no-store" }),
    ]);
    state.adminAccounts = accounts;
    state.adminLedger = ledger;
    renderAdmin();
  } catch (error) {
    $("#adminMessage").textContent = error.message || "无法加载管理数据";
  }
}

function dialogMessage(dialog, message = "", success = false) {
  const node = dialog.querySelector("[data-dialog-message]");
  node.textContent = message;
  node.classList.toggle("success", success);
}

function closeDialog(dialog) {
  dialog.close();
  dialogMessage(dialog);
}

function openManagedAccount(accountId) {
  const account = state.adminAccounts.find((item) => item.id === Number(accountId));
  if (!account) return;
  state.managedAccountId = account.id;
  $("#manageUsername").textContent = account.username;
  $("#manageAccountTitle").textContent = account.display_name;
  const form = $("#accountSettingsForm");
  form.elements.display_name.value = account.display_name;
  form.elements.unit_price_yuan.value = (account.unit_price_fen / 100).toFixed(2);
  form.elements.active.checked = account.active;
  $("#balanceAdjustmentForm").reset();
  $("#passwordResetForm").reset();
  dialogMessage($("#manageAccountDialog"));
  $("#manageAccountDialog").showModal();
}

async function imageFromUrl(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("无法读取图片"));
    image.src = url;
  });
}

function targetDimensions(width, height) {
  let scale = 1;
  if (Math.max(width, height) > MAX_SIDE) scale = Math.min(scale, MAX_SIDE / Math.max(width, height));
  if (width * height > MAX_PIXELS) scale = Math.min(scale, Math.sqrt(MAX_PIXELS / (width * height)));
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)), resized: scale < 0.999 };
}

async function normalizeImage(file) {
  if (file.size > MAX_UPLOAD_BYTES) throw new Error(`原始图片不能超过 ${formatBytes(MAX_UPLOAD_BYTES)}`);
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    const url = URL.createObjectURL(file);
    try {
      const image = await imageFromUrl(url);
      bitmap = image;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  const target = targetDimensions(bitmap.width, bitmap.height);
  const offscreen = document.createElement("canvas");
  offscreen.width = target.width;
  offscreen.height = target.height;
  const offctx = offscreen.getContext("2d", { alpha: false });
  offctx.fillStyle = "#ffffff";
  offctx.fillRect(0, 0, target.width, target.height);
  offctx.drawImage(bitmap, 0, 0, target.width, target.height);
  if (typeof bitmap.close === "function") bitmap.close();

  const outputType = file.type === "image/png" ? "image/png" : "image/jpeg";
  const blob = await new Promise((resolve, reject) => {
    offscreen.toBlob((value) => value ? resolve(value) : reject(new Error("图片转换失败")), outputType, 0.94);
  });
  if (blob.size > MAX_UPLOAD_BYTES) throw new Error(`转换后的图片不能超过 ${formatBytes(MAX_UPLOAD_BYTES)}`);
  const extension = outputType === "image/png" ? ".png" : ".jpg";
  const safeName = (file.name.replace(/\.[^.]+$/, "") || "upload") + extension;
  return {
    file: new File([blob], safeName, { type: outputType, lastModified: Date.now() }),
    width: target.width,
    height: target.height,
    resized: target.resized,
  };
}

async function selectFile(file) {
  clearError();
  if (!file || !file.type.startsWith("image/")) {
    showError("请选择有效的图片文件。");
    return;
  }
  const requestVersion = ++state.requestVersion;
  let objectUrl = null;
  analyzeButton.disabled = true;
  analyzeButton.classList.remove("loading");
  analyzeButton.querySelector(".button-label").textContent = "开始识别";
  try {
    const normalized = await normalizeImage(file);
    if (requestVersion !== state.requestVersion) return;
    objectUrl = URL.createObjectURL(normalized.file);
    const image = await imageFromUrl(objectUrl);
    if (requestVersion !== state.requestVersion) {
      URL.revokeObjectURL(objectUrl);
      return;
    }
    if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
    state.sourceFile = file;
    state.uploadFile = normalized.file;
    state.objectUrl = objectUrl;
    state.image = image;
    objectUrl = null;
    state.response = null;
    state.items = [];
    state.autoItems = [];
    state.dirty = false;
    state.pendingRequestId = null;

    fileThumb.src = state.objectUrl;
    fileName.textContent = file.name;
    fileInfo.textContent = `${normalized.width} × ${normalized.height} · ${formatBytes(normalized.file.size)}${normalized.resized ? " · 已自动缩放" : ""}`;
    fileRow.hidden = false;
    dropZone.hidden = true;
    analyzeButton.disabled = false;
    $("#emptyState").hidden = false;
    $("#resultView").hidden = true;
    $("#billingReceipt").hidden = true;
  } catch (error) {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    if (requestVersion === state.requestVersion) {
      analyzeButton.disabled = !state.uploadFile;
      analyzeButton.querySelector(".button-label").textContent = state.response ? "重新识别" : "开始识别";
      showError(error.message || "图片处理失败");
    }
  }
}

function resetFile() {
  state.requestVersion += 1;
  if (state.objectUrl) URL.revokeObjectURL(state.objectUrl);
  Object.assign(state, { sourceFile: null, uploadFile: null, objectUrl: null, image: null, response: null, autoItems: [], items: [], dirty: false, pendingRequestId: null });
  fileInput.value = "";
  fileRow.hidden = true;
  dropZone.hidden = false;
  analyzeButton.disabled = true;
  analyzeButton.classList.remove("loading");
  analyzeButton.querySelector(".button-label").textContent = "开始识别";
  $("#emptyState").hidden = false;
  $("#resultView").hidden = true;
  $("#billingReceipt").hidden = true;
  clearError();
}

async function analyze() {
  if (!state.uploadFile) return;
  const requestVersion = ++state.requestVersion;
  const uploadFile = state.uploadFile;
  clearError();
  $("#billingReceipt").hidden = true;
  analyzeButton.disabled = true;
  analyzeButton.classList.add("loading");
  analyzeButton.querySelector(".button-label").textContent = "识别中…";
  const idempotencyKey = state.pendingRequestId || requestId();
  state.pendingRequestId = idempotencyKey;
  let receivedResponse = false;

  try {
    const form = new FormData();
    form.append("file", uploadFile, uploadFile.name);
    const params = new URLSearchParams({
      exclude_border: $("#excludeBorder").checked ? "true" : "false",
      threshold_offset: String(thresholdOffset()),
      min_contrast: String(contrast.value),
    });
    let response;
    let networkError;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        response = await fetch(`/api/count?${params}`, {
          method: "POST",
          body: form,
          headers: { "Idempotency-Key": idempotencyKey },
        });
        receivedResponse = true;
        break;
      } catch (error) {
        networkError = error;
      }
    }
    if (!response) throw networkError || new Error("网络连接失败");
    const payload = await response.json().catch(() => ({}));
    state.pendingRequestId = null;
    if (response.status === 401) {
      showLogin();
      $("#loginMessage").textContent = "登录已失效，请重新登录";
      throw new Error(apiError(payload, "登录已失效，请重新登录"));
    }
    if (!response.ok) throw new Error(apiError(payload, "识别请求失败"));
    if (requestVersion !== state.requestVersion) return;

    state.response = payload;
    state.me.balance_fen = payload.billing.balance_fen;
    updateAccountHeader();
    state.autoItems = payload.items.map((item) => ({ ...item, manual: false }));
    state.items = state.autoItems.map(cloneItem);
    state.dirty = false;
    setEditMode("view");
    renderAll();
    $("#billingCharge").textContent = formatMoney(payload.billing.charged_amount_fen);
    $("#billingDetail").textContent = `${payload.billing.billable_count} 根 · 余额 ${formatMoney(payload.billing.balance_fen)}`;
    $("#billingReceipt").hidden = false;
    $("#emptyState").hidden = true;
    $("#resultView").hidden = false;
  } catch (error) {
    if (requestVersion === state.requestVersion) {
      const message = receivedResponse ? error.message : "网络异常，再次点击将重试本次请求";
      showError(message || "识别失败，请稍后重试。");
    }
  } finally {
    if (requestVersion === state.requestVersion) {
      analyzeButton.disabled = !state.uploadFile;
      analyzeButton.classList.remove("loading");
      analyzeButton.querySelector(".button-label").textContent = state.response ? "重新识别" : "开始识别";
    }
  }
}

function cloneItem(item) {
  return { ...item, bbox: [...item.bbox], center: [...item.center] };
}

function strandCount(item) {
  return Math.max(1, Number(item.strand_count) || 1);
}

function totalStrands(items) {
  return items.reduce((total, item) => total + strandCount(item), 0);
}

function normalizedItems() {
  return state.items.map((item, index) => ({ ...item, id: index + 1 }));
}

function drawResults() {
  if (!state.image) return;
  canvas.width = state.image.naturalWidth;
  canvas.height = state.image.naturalHeight;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(state.image, 0, 0, canvas.width, canvas.height);

  const items = normalizedItems();
  const scale = Math.max(0.8, Math.min(canvas.width, canvas.height) / 360);
  const lineWidth = Math.max(1.5, 1.7 * scale);
  const radius = Math.max(2.5, 3 * scale);
  const fontSize = Math.max(11, Math.min(22, canvas.width / 45));
  ctx.lineWidth = lineWidth;
  ctx.font = `700 ${fontSize}px Inter, Arial, sans-serif`;
  ctx.textBaseline = "middle";

  for (const item of items) {
    const [x, y, width, height] = item.bbox;
    const [centerX, centerY] = item.center;
    const color = item.manual ? "#8b5cf6" : item.partial ? "#f59e0b" : "#16a34a";
    ctx.strokeStyle = color;
    ctx.setLineDash(item.manual ? [5 * scale, 3 * scale] : []);
    ctx.strokeRect(x - lineWidth / 2, y - lineWidth / 2, width + lineWidth, height + lineWidth);
    ctx.setLineDash([]);
    ctx.fillStyle = item.manual ? "#8b5cf6" : "#ef4444";
    ctx.beginPath(); ctx.arc(centerX, centerY, radius, 0, Math.PI * 2); ctx.fill();

    const count = strandCount(item);
    const text = count > 1 ? `${item.id} ×${count}` : String(item.id);
    const metrics = ctx.measureText(text);
    const padX = 4 * scale;
    const boxWidth = metrics.width + padX * 2;
    const boxHeight = fontSize + 5 * scale;
    const labelX = Math.max(1, Math.min(canvas.width - boxWidth - 1, x));
    let labelY = y - boxHeight - 3 * scale;
    if (labelY < 1) labelY = Math.min(canvas.height - boxHeight - 1, y + height + 3 * scale);
    ctx.fillStyle = "rgba(255,255,255,.94)"; ctx.fillRect(labelX, labelY, boxWidth, boxHeight);
    ctx.strokeStyle = color; ctx.lineWidth = Math.max(1, scale); ctx.strokeRect(labelX, labelY, boxWidth, boxHeight);
    ctx.fillStyle = "#1d4ed8"; ctx.fillText(text, labelX + padX, labelY + boxHeight / 2 + .5);
  }

}

function updateMetrics() {
  const items = normalizedItems();
  const automatic = items.filter((item) => !item.manual);
  const automaticCount = totalStrands(automatic);
  const average = automaticCount ? automatic.reduce((sum, item) => {
    const score = Number(item.confidence || 0) * Number(item.split_confidence ?? 1);
    return sum + score * strandCount(item);
  }, 0) / automaticCount : 0;
  const count = totalStrands(items);
  $("#countMetric").textContent = count;
  $("#partialMetric").textContent = items.filter((item) => item.partial).reduce((sum, item) => sum + strandCount(item), 0);
  $("#confidenceMetric").textContent = automatic.length ? `${Math.round(average * 100)}%` : "—";
  $("#processingMetric").textContent = state.response ? `${Math.round(state.response.processing_ms)} ms` : "—";
  $("#manualHint").textContent = state.dirty ? "已手动调整" : "识别结果";
  $("#itemTotalBadge").textContent = `${count} 根 / ${items.length} 簇`;
}

function updateTable() {
  const body = $("#itemsTable");
  body.innerHTML = "";
  for (const [index, item] of normalizedItems().entries()) {
    const count = strandCount(item);
    const status = item.manual ? ["手动", "manual"] : item.partial ? ["边缘", "partial"] : count > 1 ? ["毛束", "cluster"] : ["正常", "normal"];
    const score = Number(item.confidence || 0) * Number(item.split_confidence ?? 1);
    const row = document.createElement("tr");
    row.innerHTML = `<td>${item.id}</td><td>${Number(item.center[0]).toFixed(1)}, ${Number(item.center[1]).toFixed(1)}</td><td>${item.bbox[2]} × ${item.bbox[3]}</td><td><div class="strand-stepper"><button type="button" data-strand-action="decrease" data-index="${index}" aria-label="减少第 ${item.id} 簇的数量" title="减少" ${count === 1 ? "disabled" : ""}>−</button><output>${count}</output><button type="button" data-strand-action="increase" data-index="${index}" aria-label="增加第 ${item.id} 簇的数量" title="增加">＋</button></div></td><td>${item.manual ? "—" : Number(item.contrast).toFixed(1)}</td><td>${item.manual ? "—" : `${Math.round(score * 100)}%`}</td><td><span class="status-tag ${status[1]}">${status[0]}</span></td>`;
    body.appendChild(row);
  }
}

function renderAll() { drawResults(); updateMetrics(); updateTable(); }

function setEditMode(mode) {
  state.editMode = mode;
  $$(".toolbar-button[data-mode]").forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
  const wrap = $("#canvasWrap");
  wrap.classList.toggle("mode-add", mode === "add");
  wrap.classList.toggle("mode-remove", mode === "remove");
  const tip = $("#canvasTip");
  if (mode === "add") tip.textContent = "点击图片空白处添加目标";
  if (mode === "remove") tip.textContent = "点击标记可逐根减少";
  tip.classList.toggle("show", mode !== "view");
}

function canvasPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return { x: (event.clientX - rect.left) * (canvas.width / rect.width), y: (event.clientY - rect.top) * (canvas.height / rect.height) };
}

function findItemIndex(point) {
  let best = -1, bestDistance = Infinity;
  state.items.forEach((item, index) => {
    const [x, y, width, height] = item.bbox;
    const [centerX, centerY] = item.center;
    const inside = point.x >= x - 5 && point.x <= x + width + 5 && point.y >= y - 5 && point.y <= y + height + 5;
    const distance = Math.hypot(point.x - centerX, point.y - centerY);
    const threshold = Math.max(14, Math.max(width, height) * 1.35);
    if ((inside || distance <= threshold) && distance < bestDistance) { best = index; bestDistance = distance; }
  });
  return best;
}

function handleCanvasClick(event) {
  if (state.editMode === "view") return;
  const point = canvasPoint(event);
  if (state.editMode === "remove") {
    const index = findItemIndex(point);
    if (index >= 0) {
      const item = state.items[index];
      if (strandCount(item) > 1) item.strand_count = strandCount(item) - 1;
      else state.items.splice(index, 1);
      state.dirty = true;
      renderAll();
    }
    return;
  }
  const side = Math.max(9, Math.round(Math.min(canvas.width, canvas.height) * .032));
  const x = Math.max(0, Math.min(canvas.width - side, Math.round(point.x - side / 2)));
  const y = Math.max(0, Math.min(canvas.height - side, Math.round(point.y - side / 2)));
  state.items.push({ id: state.items.length + 1, bbox: [x, y, side, side], center: [Number(point.x.toFixed(2)), Number(point.y.toFixed(2))], area: side * side, contrast: 0, confidence: 1, partial: x === 0 || y === 0 || x + side >= canvas.width || y + side >= canvas.height, strand_count: 1, split_confidence: 1, manual: true });
  state.dirty = true; renderAll();
}

function restoreAutoItems() { state.items = state.autoItems.map(cloneItem); state.dirty = false; renderAll(); }

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportJson() {
  if (!state.response) return;
  const items = normalizedItems();
  const payload = { count: totalStrands(items), image_width: state.response.image_width, image_height: state.response.image_height, processing_ms: state.response.processing_ms, billing: state.response.billing, manually_edited: state.dirty, items };
  downloadBlob(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }), "hair-count-result.json");
}

function exportImage() { canvas.toBlob((blob) => blob && downloadBlob(blob, "hair-count-annotated.png"), "image/png"); }

function setFormBusy(form, busy) {
  form.querySelectorAll("button").forEach((button) => { button.disabled = busy; });
}

function replaceAdminAccount(account) {
  const index = state.adminAccounts.findIndex((item) => item.id === account.id);
  if (index >= 0) state.adminAccounts[index] = account;
  else state.adminAccounts.push(account);
  renderAdmin();
}

async function createAdminAccount(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const dialog = $("#createAccountDialog");
  setFormBusy(form, true);
  dialogMessage(dialog);
  try {
    const payload = await apiJson("/api/admin/accounts", {
      method: "POST",
      body: JSON.stringify({
        username: form.elements.username.value.trim(),
        display_name: form.elements.display_name.value.trim(),
        password: form.elements.password.value,
        unit_price_fen: yuanToFen(form.elements.unit_price_yuan.value),
      }),
    });
    replaceAdminAccount(payload);
    closeDialog(dialog);
    form.reset();
    await loadAdmin();
    $("#adminMessage").textContent = `已创建账号 ${payload.username}`;
  } catch (error) {
    dialogMessage(dialog, error.message || "创建账号失败");
  } finally {
    setFormBusy(form, false);
  }
}

async function saveAccountSettings(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const dialog = $("#manageAccountDialog");
  setFormBusy(form, true);
  dialogMessage(dialog);
  try {
    const account = await apiJson(`/api/admin/accounts/${state.managedAccountId}`, {
      method: "PATCH",
      body: JSON.stringify({
        display_name: form.elements.display_name.value.trim(),
        unit_price_fen: yuanToFen(form.elements.unit_price_yuan.value),
        active: form.elements.active.checked,
      }),
    });
    replaceAdminAccount(account);
    $("#manageAccountTitle").textContent = account.display_name;
    dialogMessage(dialog, "账号设置已保存", true);
    await loadAdmin();
  } catch (error) {
    dialogMessage(dialog, error.message || "保存失败");
  } finally {
    setFormBusy(form, false);
  }
}

async function submitBalanceAdjustment(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const dialog = $("#manageAccountDialog");
  setFormBusy(form, true);
  dialogMessage(dialog);
  try {
    const account = await apiJson(`/api/admin/accounts/${state.managedAccountId}/balance-adjustments`, {
      method: "POST",
      body: JSON.stringify({
        amount_fen: yuanToFen(form.elements.amount_yuan.value),
        note: form.elements.note.value.trim(),
      }),
    });
    replaceAdminAccount(account);
    form.reset();
    dialogMessage(dialog, `余额已更新为 ${formatMoney(account.balance_fen)}`, true);
    await loadAdmin();
  } catch (error) {
    dialogMessage(dialog, error.message || "余额调整失败");
  } finally {
    setFormBusy(form, false);
  }
}

async function submitPasswordReset(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const dialog = $("#manageAccountDialog");
  setFormBusy(form, true);
  dialogMessage(dialog);
  try {
    await apiJson(`/api/admin/accounts/${state.managedAccountId}/password`, {
      method: "POST",
      body: JSON.stringify({ password: form.elements.password.value }),
    });
    form.reset();
    dialogMessage(dialog, "密码已重置，原有登录会话已失效", true);
  } catch (error) {
    dialogMessage(dialog, error.message || "密码重置失败");
  } finally {
    setFormBusy(form, false);
  }
}

fileInput.addEventListener("change", () => selectFile(fileInput.files[0]));
$("#removeFile").addEventListener("click", resetFile);
analyzeButton.addEventListener("click", analyze);
sensitivity.addEventListener("input", () => $("#sensitivityValue").textContent = sensitivityLabel(sensitivity.value));
contrast.addEventListener("input", () => $("#contrastValue").textContent = contrast.value);
canvas.addEventListener("click", handleCanvasClick);
$("#itemsTable").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-strand-action]");
  if (!button) return;
  const item = state.items[Number(button.dataset.index)];
  if (!item) return;
  if (button.dataset.strandAction === "increase") item.strand_count = strandCount(item) + 1;
  if (button.dataset.strandAction === "decrease" && strandCount(item) > 1) item.strand_count = strandCount(item) - 1;
  state.dirty = true;
  renderAll();
});
$("#restoreButton").addEventListener("click", restoreAutoItems);
$("#downloadJson").addEventListener("click", exportJson);
$("#downloadImage").addEventListener("click", exportImage);
$$(".toolbar-button[data-mode]").forEach((button) => button.addEventListener("click", () => setEditMode(button.dataset.mode)));
["dragenter", "dragover"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.add("dragging"); }));
["dragleave", "drop"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.remove("dragging"); }));
dropZone.addEventListener("drop", (event) => selectFile(event.dataTransfer.files[0]));

$("#loginForm").addEventListener("submit", login);
$("#logoutButton").addEventListener("click", logout);
$$("[data-app-view]").forEach((button) => button.addEventListener("click", () => setAppView(button.dataset.appView)));
$("#refreshAdminButton").addEventListener("click", loadAdmin);
$("#createAccountButton").addEventListener("click", () => {
  const dialog = $("#createAccountDialog");
  $("#createAccountForm").reset();
  dialogMessage(dialog);
  dialog.showModal();
});
$("#adminAccountsTable").addEventListener("click", (event) => {
  const button = event.target.closest("[data-manage-account]");
  if (button) openManagedAccount(button.dataset.manageAccount);
});
$("#createAccountForm").addEventListener("submit", createAdminAccount);
$("#accountSettingsForm").addEventListener("submit", saveAccountSettings);
$("#balanceAdjustmentForm").addEventListener("submit", submitBalanceAdjustment);
$("#passwordResetForm").addEventListener("submit", submitPasswordReset);
$$(".dialog-close").forEach((button) => button.addEventListener("click", () => closeDialog(button.closest("dialog"))));

Promise.all([checkHealth(), loadSession()]);
