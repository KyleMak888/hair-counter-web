const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

let MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
let MAX_SIDE = 6000;
let MAX_PIXELS = 20_000_000;
let MAX_BATCH_SIZE = 10;

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
  // Batch mode
  inputMode: "single",
  batchQueue: [],
  batchAdding: false,
  batchProcessing: false,
  batchExporting: false,
  batchId: null,
  batchViewIndex: -1,
  singleViewerState: null,
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
    MAX_BATCH_SIZE = Number(data.max_batch_size) || MAX_BATCH_SIZE;
    $("#uploadLimits").textContent = `支持 JPG、PNG、WebP、BMP，最大 ${formatBytes(MAX_UPLOAD_BYTES)}`;
    $("#batchLimits").textContent = `支持 JPG、PNG、WebP、BMP，最多 ${MAX_BATCH_SIZE} 张，最大 ${formatBytes(MAX_UPLOAD_BYTES)} / 张`;
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

function isSvip(account = state.me) {
  return account?.plan === "svip";
}

async function refreshCurrentAccount() {
  state.me = await apiJson("/api/me", { cache: "no-store" });
  updateAccountHeader();
  return state.me;
}

function applyBillingState(billing) {
  if (!state.me || !billing) return;
  state.me.plan = billing.plan || "standard";
  state.me.balance_fen = billing.balance_fen;
  if (state.me.plan === "standard") state.me.unit_price_fen = billing.unit_price_fen;
  updateAccountHeader();
}

function updateAccountHeader() {
  if (!state.me) return;
  const svip = isSvip();
  $("#standardAccountMeter").hidden = svip;
  $("#svipAccount").hidden = !svip;
  $("#accountName").textContent = state.me.display_name;
  $("#svipAccountName").textContent = state.me.display_name;
  $("#accountBalance").textContent = formatMoney(state.me.balance_fen);
  $("#accountPrice").textContent = `${formatMoney(state.me.unit_price_fen)} / 根`;
  $("#singleBillingLabel").textContent = svip ? "SVIP 专属权益" : "自动识别结果计费";
  $("#batchBillingLabel").textContent = svip ? "SVIP 专属权益" : "自动识别结果计费";
  $("#currentUnitPrice").textContent = svip ? "无限使用" : `${formatMoney(state.me.unit_price_fen)} / 根`;
  $("#batchCurrentUnitPrice").textContent = svip ? "无限使用" : `${formatMoney(state.me.unit_price_fen)} / 根`;
  $("#singleBillingRule").classList.toggle("svip", svip);
  $("#batchBillingRule").classList.toggle("svip", svip);
  $("#viewSwitch").hidden = state.me.role !== "admin";
}

function showLogin() {
  if (state.me) resetFile();
  clearBatch();
  $$("dialog[open]").forEach((dialog) => dialog.close());
  state.me = null;
  state.inputMode = "single";
  setInputMode("single");
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
  const standardAccounts = accounts.filter((account) => !isSvip(account));
  $("#adminAccountCount").textContent = accounts.filter((account) => account.active).length;
  $("#adminTotalBalance").textContent = formatMoney(standardAccounts.reduce((sum, account) => sum + account.balance_fen, 0));
  $("#adminTotalStrands").textContent = accounts.reduce((sum, account) => sum + account.total_billable_count, 0).toLocaleString("zh-CN");
  $("#adminTotalSpent").textContent = formatMoney(accounts.reduce((sum, account) => sum + account.total_spent_fen, 0));

  const accountBody = $("#adminAccountsTable");
  accountBody.innerHTML = accounts.length ? accounts.map((account) => {
    const svip = isSvip(account);
    return `<tr>
    <td>${escapeHtml(account.display_name)}</td><td>${escapeHtml(account.username)}</td>
    <td><span class="plan-tag ${svip ? "svip" : "standard"}">${svip ? "SVIP" : "按量"}</span></td>
    <td>${svip ? '<span class="svip-table-value">买断</span>' : `${formatMoney(account.unit_price_fen)} / 根`}</td>
    <td>${svip ? '<span class="svip-table-value">不限量</span>' : formatMoney(account.balance_fen)}</td>
    <td>${Number(account.total_billable_count).toLocaleString("zh-CN")}</td><td>${formatMoney(account.total_spent_fen)}</td>
    <td><span class="status-tag ${account.active ? "normal" : "partial"}">${account.active ? "启用" : "停用"}</span></td>
    <td>${formatDate(account.last_recognition_at)}</td>
    <td><button type="button" class="table-action" data-manage-account="${account.id}">管理</button></td>
  </tr>`;
  }).join("") : '<tr><td class="empty-row" colspan="10">还没有客户账号</td></tr>';

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

function updatePlanFields(form) {
  const svip = form.elements.plan?.value === "svip";
  const priceField = form.querySelector("[data-plan-price]");
  if (priceField) {
    priceField.hidden = svip;
    priceField.querySelector("input").disabled = svip;
  }
  if (form.id === "accountSettingsForm") {
    $("#balanceAdjustmentForm").hidden = svip;
  }
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
  form.elements.plan.value = account.plan || "standard";
  form.elements.active.checked = account.active;
  updatePlanFields(form);
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
  Object.assign(state, { sourceFile: null, uploadFile: null, objectUrl: null, image: null, response: null, autoItems: [], items: [], dirty: false, pendingRequestId: null, singleViewerState: null });
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
    await refreshCurrentAccount();
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
    applyBillingState(payload.billing);
    state.autoItems = payload.items.map((item) => ({ ...item, manual: false }));
    state.items = state.autoItems.map(cloneItem);
    state.dirty = false;
    setEditMode("view");
    renderAll();
    const svip = payload.billing.plan === "svip";
    $("#billingReceipt").classList.toggle("svip", svip);
    $("#billingReceiptLabel").textContent = svip ? "SVIP 权益已生效" : "本次扣费";
    $("#billingCharge").textContent = svip ? "权益免扣" : formatMoney(payload.billing.charged_amount_fen);
    $("#billingDetail").textContent = svip
      ? `${payload.billing.billable_count} 根 · 不扣余额`
      : `${payload.billing.billable_count} 根 · 余额 ${formatMoney(payload.billing.balance_fen)}`;
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

function refreshDirtyState() {
  state.dirty = globalThis.BatchExportData.hasNetManualChanges(state.autoItems, state.items);
}

function normalizedItems(items = state.items) {
  return items.map((item, index) => ({ ...item, id: index + 1 }));
}

function drawAnnotatedImage(targetCanvas, image, sourceItems) {
  const targetContext = targetCanvas.getContext("2d");
  const canvasWidth = image.naturalWidth || image.width;
  const canvasHeight = image.naturalHeight || image.height;
  targetCanvas.width = canvasWidth;
  targetCanvas.height = canvasHeight;
  targetContext.clearRect(0, 0, canvasWidth, canvasHeight);
  targetContext.drawImage(image, 0, 0, canvasWidth, canvasHeight);

  const items = normalizedItems(sourceItems);
  const scale = Math.max(0.8, Math.min(canvasWidth, canvasHeight) / 360);
  const lineWidth = Math.max(1.5, 1.7 * scale);
  const radius = Math.max(2.5, 3 * scale);
  const fontSize = Math.max(11, Math.min(22, canvasWidth / 45));
  targetContext.lineWidth = lineWidth;
  targetContext.font = `700 ${fontSize}px Inter, Arial, sans-serif`;
  targetContext.textBaseline = "middle";

  for (const item of items) {
    const [x, y, width, height] = item.bbox;
    const [centerX, centerY] = item.center;
    const color = item.manual ? "#8b5cf6" : item.partial ? "#f59e0b" : "#16a34a";
    targetContext.strokeStyle = color;
    targetContext.setLineDash(item.manual ? [5 * scale, 3 * scale] : []);
    targetContext.strokeRect(x - lineWidth / 2, y - lineWidth / 2, width + lineWidth, height + lineWidth);
    targetContext.setLineDash([]);
    targetContext.fillStyle = item.manual ? "#8b5cf6" : "#ef4444";
    targetContext.beginPath(); targetContext.arc(centerX, centerY, radius, 0, Math.PI * 2); targetContext.fill();

    const count = strandCount(item);
    const text = count > 1 ? `${item.id} ×${count}` : String(item.id);
    const metrics = targetContext.measureText(text);
    const padX = 4 * scale;
    const boxWidth = metrics.width + padX * 2;
    const boxHeight = fontSize + 5 * scale;
    const labelX = Math.max(1, Math.min(canvasWidth - boxWidth - 1, x));
    let labelY = y - boxHeight - 3 * scale;
    if (labelY < 1) labelY = Math.min(canvasHeight - boxHeight - 1, y + height + 3 * scale);
    targetContext.fillStyle = "rgba(255,255,255,.94)"; targetContext.fillRect(labelX, labelY, boxWidth, boxHeight);
    targetContext.strokeStyle = color; targetContext.lineWidth = Math.max(1, scale); targetContext.strokeRect(labelX, labelY, boxWidth, boxHeight);
    targetContext.fillStyle = "#1d4ed8"; targetContext.fillText(text, labelX + padX, labelY + boxHeight / 2 + .5);
  }
}

function drawResults() {
  if (!state.image) return;
  drawAnnotatedImage(canvas, state.image, state.items);
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

function renderAll() {
  saveActiveBatchViewer();
  drawResults();
  updateMetrics();
  updateTable();
  if (state.inputMode === "batch") {
    updateBatchSummary();
    renderBatchQueue();
  }
}

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
      refreshDirtyState();
      renderAll();
    }
    return;
  }
  const side = Math.max(9, Math.round(Math.min(canvas.width, canvas.height) * .032));
  const x = Math.max(0, Math.min(canvas.width - side, Math.round(point.x - side / 2)));
  const y = Math.max(0, Math.min(canvas.height - side, Math.round(point.y - side / 2)));
  state.items.push({ id: state.items.length + 1, bbox: [x, y, side, side], center: [Number(point.x.toFixed(2)), Number(point.y.toFixed(2))], area: side * side, contrast: 0, confidence: 1, partial: x === 0 || y === 0 || x + side >= canvas.width || y + side >= canvas.height, strand_count: 1, split_confidence: 1, manual: true });
  refreshDirtyState(); renderAll();
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
    const plan = form.elements.plan.value;
    const payload = await apiJson("/api/admin/accounts", {
      method: "POST",
      body: JSON.stringify({
        username: form.elements.username.value.trim(),
        display_name: form.elements.display_name.value.trim(),
        password: form.elements.password.value,
        unit_price_fen: plan === "standard" ? yuanToFen(form.elements.unit_price_yuan.value) : 0,
        plan,
      }),
    });
    replaceAdminAccount(payload);
    closeDialog(dialog);
    form.reset();
    updatePlanFields(form);
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
    const plan = form.elements.plan.value;
    const body = {
      display_name: form.elements.display_name.value.trim(),
      active: form.elements.active.checked,
      plan,
    };
    if (plan === "standard") {
      body.unit_price_fen = yuanToFen(form.elements.unit_price_yuan.value);
    }
    const account = await apiJson(`/api/admin/accounts/${state.managedAccountId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    replaceAdminAccount(account);
    $("#manageAccountTitle").textContent = account.display_name;
    form.elements.plan.value = account.plan;
    updatePlanFields(form);
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

/* ── Batch mode ─────────────────────────────────────── */

const batchFileInput = $("#batchFileInput");
const batchDropZone = $("#batchDropZone");
const batchAnalyzeButton = $("#batchAnalyzeButton");
const batchSensitivity = $("#batchSensitivity");
const batchContrast = $("#batchContrast");

function captureViewerState() {
  return {
    image: state.image,
    response: state.response,
    autoItems: state.autoItems,
    items: state.items,
    editMode: state.editMode,
    dirty: state.dirty,
  };
}

function applyViewerState(viewer = null) {
  const next = viewer || {
    image: null,
    response: null,
    autoItems: [],
    items: [],
    editMode: "view",
    dirty: false,
  };
  Object.assign(state, next);
}

function ensureBatchResultState(item) {
  if (!item?.response) return item;
  if (!item.autoItems) item.autoItems = item.response.items.map((entry) => ({ ...cloneItem(entry), manual: false }));
  if (!item.items) item.items = item.autoItems.map(cloneItem);
  item.dirty = globalThis.BatchExportData.hasNetManualChanges(item.autoItems, item.items);
  return item;
}

function batchResultItems(item) {
  return ensureBatchResultState(item)?.items || [];
}

function saveActiveBatchViewer() {
  if (state.inputMode !== "batch" || state.batchViewIndex < 0) return;
  const item = state.batchQueue[state.batchViewIndex];
  if (!item?.response || state.response !== item.response) return;
  item.autoItems = state.autoItems.map(cloneItem);
  item.items = state.items.map(cloneItem);
  item.dirty = state.dirty;
}

function batchViewerState(item) {
  ensureBatchResultState(item);
  return {
    image: item.image,
    response: item.response,
    autoItems: item.autoItems.map(cloneItem),
    items: item.items.map(cloneItem),
    editMode: "view",
    dirty: item.dirty,
  };
}

function setInputMode(mode) {
  if (mode !== "single" && mode !== "batch") return;
  const previousMode = state.inputMode;
  if (previousMode !== mode && mode === "batch") {
    state.singleViewerState = captureViewerState();
    const selected = state.batchQueue[state.batchViewIndex];
    applyViewerState(selected?.response ? batchViewerState(selected) : null);
  } else if (previousMode !== mode && mode === "single") {
    saveActiveBatchViewer();
    applyViewerState(state.singleViewerState);
    state.singleViewerState = null;
  }

  state.inputMode = mode;
  $$("[data-input-mode]").forEach((button) => button.classList.toggle("active", button.dataset.inputMode === mode));
  $("#singleSection").hidden = mode !== "single";
  $("#batchSection").hidden = mode !== "batch";
  $("#batchQueueCard").hidden = mode !== "batch";
  if (mode === "single") {
    if (state.response) renderAll();
    setEditMode(state.editMode);
    $("#emptyState").hidden = Boolean(state.response);
    $("#resultView").hidden = !state.response;
  } else {
    if (state.response) renderAll();
    setEditMode(state.editMode);
    $("#emptyState").hidden = state.batchQueue.length > 0;
    $("#resultView").hidden = !state.response;
  }
}

function clearBatchError() { $("#batchErrorMessage").textContent = ""; }
function showBatchError(message) { $("#batchErrorMessage").textContent = message; }

async function addBatchFiles(fileList) {
  clearBatchError();
  if (state.batchAdding) {
    showBatchError("图片正在加入队列，请稍候。");
    return;
  }
  const files = [...fileList].filter((file) => file.type.startsWith("image/"));
  if (!files.length) { showBatchError("请选择有效的图片文件。"); return; }
  if (state.batchQueue.length + files.length > MAX_BATCH_SIZE) {
    showBatchError(`批量处理最多 ${MAX_BATCH_SIZE} 张图片。`);
    return;
  }
  state.batchAdding = true;
  for (const file of files) {
    try {
      const normalized = await normalizeImage(file);
      const objectUrl = URL.createObjectURL(normalized.file);
      const image = await imageFromUrl(objectUrl);
      state.batchQueue.push({
        sourceFile: file,
        uploadFile: normalized.file,
        objectUrl,
        image,
        width: normalized.width,
        height: normalized.height,
        resized: normalized.resized,
        response: null,
        error: null,
        status: "pending",
        requestKey: requestId(),
      });
    } catch (error) {
      showBatchError(`${file.name}: ${error.message || "图片处理失败"}`);
    }
  }
  state.batchAdding = false;
  renderBatchQueue();
  updateBatchSummary();
  batchAnalyzeButton.disabled = !state.batchQueue.some((item) => item.status === "pending");
  $("#batchActions").hidden = !state.batchQueue.length;
  if (state.batchQueue.length) { $("#emptyState").hidden = true; }
}

function removeBatchItem(index) {
  if (state.batchProcessing || state.batchExporting) return;
  const item = state.batchQueue[index];
  if (item?.objectUrl) URL.revokeObjectURL(item.objectUrl);
  state.batchQueue.splice(index, 1);
  if (state.batchViewIndex === index) {
    state.batchViewIndex = -1;
    applyViewerState();
    $("#resultView").hidden = true;
  } else if (state.batchViewIndex > index) {
    state.batchViewIndex -= 1;
  }
  renderBatchQueue();
  updateBatchSummary();
  batchAnalyzeButton.disabled = !state.batchQueue.some((i) => i.status === "pending" || i.status === "error");
  $("#batchActions").hidden = !state.batchQueue.length;
  $("#emptyState").hidden = state.batchQueue.length > 0;
  $("#batchQueueCard").hidden = state.inputMode !== "batch";
}

function clearBatch() {
  if (state.batchProcessing || state.batchExporting) return;
  for (const item of state.batchQueue) { if (item.objectUrl) URL.revokeObjectURL(item.objectUrl); }
  state.batchQueue = [];
  state.batchViewIndex = -1;
  state.batchId = null;
  if (state.inputMode === "batch") applyViewerState();
  $("#batchProgress").hidden = true;
  $("#batchActions").hidden = true;
  $("#batchExportMenu").open = false;
  setBatchExportStatus("");
  clearBatchError();
  renderBatchQueue();
  updateBatchSummary();
  $("#emptyState").hidden = false;
  $("#resultView").hidden = true;
}

function batchThresholdOffset() { return -Number(batchSensitivity.value); }

function renderBatchQueue() {
  const list = $("#batchQueueList");
  $("#batchQueueCount").textContent = `${state.batchQueue.length} 项`;
  list.innerHTML = state.batchQueue.map((item, index) => {
    const count = item.response ? totalStrands(batchResultItems(item)) : 0;
    const statusMap = {
      pending: ["等待", "pending"],
      processing: ["处理中", "processing"],
      done: [`完成 · ${count} 根`, "done"],
      error: ["错误", "error"],
    };
    const [statusText, statusClass] = statusMap[item.status] || ["未知", "pending"];
    const active = state.batchViewIndex === index ? " active" : "";
    const thumb = item.objectUrl ? `<img src="${item.objectUrl}" alt="${escapeHtml(item.sourceFile.name)}" />` : "";
    const errorMsg = item.error ? `<small class="batch-item-error">${escapeHtml(item.error)}</small>` : "";
    return `<div class="batch-item ${statusClass}${active}" data-batch-view="${index}">
      <div class="batch-item-thumb">${thumb}</div>
      <div class="batch-item-info">
        <strong>${escapeHtml(item.sourceFile.name)}</strong>
        <span>${item.width} × ${item.height}${item.resized ? " · 已缩放" : ""}</span>
        ${item.response ? `<span class="batch-item-count">${count} 根 · ${item.response.billing?.plan === "svip" ? "SVIP" : formatMoney(item.response.billing?.charged_amount_fen || 0)}</span>` : ""}
        ${errorMsg}
      </div>
      <span class="batch-item-status status-tag ${statusClass}">${statusText}</span>
      ${state.batchProcessing || state.batchExporting ? "" : `<button type="button" class="icon-button batch-item-remove" data-batch-remove="${index}" title="移除">×</button>`}
    </div>`;
  }).join("");
}

function viewBatchItem(index) {
  const item = state.batchQueue[index];
  if (!item || !item.response) return;
  saveActiveBatchViewer();
  state.batchViewIndex = index;
  applyViewerState(batchViewerState(item));

  setEditMode("view");
  renderAll();
  $("#emptyState").hidden = true;
  $("#resultView").hidden = false;
  renderBatchQueue();
}

async function analyzeBatch() {
  const pending = state.batchQueue.filter((item) => item.status === "pending" || item.status === "error");
  if (!pending.length) return;
  try {
    await refreshCurrentAccount();
  } catch (error) {
    if (state.me) showBatchError(error.message || "无法刷新账户信息");
    return;
  }
  state.batchProcessing = true;
  batchAnalyzeButton.disabled = true;
  batchAnalyzeButton.classList.add("loading");
  batchAnalyzeButton.querySelector(".button-label").textContent = "批量识别中…";
  clearBatchError();
  setBatchExportStatus("");

  if (!state.batchId) state.batchId = requestId();

  const params = new URLSearchParams({
    exclude_border: $("#batchExcludeBorder").checked ? "true" : "false",
    threshold_offset: String(batchThresholdOffset()),
    min_contrast: String(batchContrast.value),
  });

  let processed = 0;
  const toProcess = pending.length;

  $("#batchProgress").hidden = false;
  $("#batchExport").hidden = true;

  for (const item of pending) {
    item.status = "processing";
    item.error = null;
    renderBatchQueue();
    updateBatchProgress(processed, toProcess);

    const form = new FormData();
    form.append("file", item.uploadFile, item.uploadFile.name);

    let networkError = null;
    let response = null;

    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        response = await fetch(`/api/count?${params}`, {
          method: "POST",
          body: form,
          headers: { "Idempotency-Key": item.requestKey },
        });
        networkError = null;
        break;
      } catch (error) {
        networkError = error;
      }
    }

    if (!response) {
      item.status = "error";
      item.error = networkError?.message || "网络连接失败";
      processed += 1;
      renderBatchQueue();
      continue;
    }

    const payload = await response.json().catch(() => ({}));

    if (response.status === 401) {
      state.batchProcessing = false;
      showLogin();
      $("#loginMessage").textContent = "登录已失效，请重新登录";
      batchAnalyzeButton.classList.remove("loading");
      batchAnalyzeButton.querySelector(".button-label").textContent = "开始批量识别";
      return;
    }

    if (!response.ok) {
      item.status = "error";
      item.error = apiError(payload, "识别失败");
      processed += 1;
      renderBatchQueue();
      if (response.status === 402) {
        const remaining = state.batchQueue.filter((q) => q.status === "pending");
        for (const remainingItem of remaining) {
          remainingItem.status = "error";
          remainingItem.error = "余额不足，已跳过";
        }
        processed = toProcess;
        showBatchError("余额不足，批量处理已停止。");
        break;
      }
      continue;
    }

    item.status = "done";
    item.response = payload;
    ensureBatchResultState(item);
    applyBillingState(payload.billing);
    processed += 1;
    renderBatchQueue();
  }

  state.batchProcessing = false;
  renderBatchQueue();
  batchAnalyzeButton.classList.remove("loading");
  batchAnalyzeButton.querySelector(".button-label").textContent = "开始批量识别";
  batchAnalyzeButton.disabled = !state.batchQueue.some((i) => i.status === "pending" || i.status === "error");
  updateBatchProgress(processed, toProcess);

  const succeeded = state.batchQueue.filter((i) => i.status === "done").length;
  const failed = state.batchQueue.filter((i) => i.status === "error").length;
  updateBatchSummary();
  if (failed > 0 && succeeded === 0) {
    showBatchError("全部图片处理失败，请检查图片格式或余额。");
  } else if (failed > 0) {
    showBatchError(`${failed} 张图片处理失败，已跳过。`);
  }
}

function updateBatchProgress(processed, total) {
  const pct = total > 0 ? Math.round((processed / total) * 100) : 0;
  $("#batchProgressFill").style.width = `${pct}%`;
  $("#batchProgressText").textContent = `${processed} / ${total}`;
}

function updateBatchSummary() {
  const completed = state.batchQueue.filter((item) => item.status === "done" && item.response);
  $("#batchSummary").hidden = completed.length === 0;
  $("#batchExport").hidden = completed.length === 0;
  $("#batchSummary").classList.remove("svip");
  if (!completed.length) return;
  const totalCount = completed.reduce(
    (sum, item) => sum + totalStrands(batchResultItems(item)),
    0,
  );
  const totalCharged = completed.reduce(
    (sum, item) => sum + (item.response.billing?.charged_amount_fen || 0),
    0,
  );
  const allSvip = completed.every((item) => item.response.billing?.plan === "svip");
  $("#batchSummary").classList.toggle("svip", allSvip);
  $("#batchTotalCount").textContent = totalCount;
  $("#batchChargeLabel").textContent = allSvip ? "本批权益" : "总扣费";
  $("#batchTotalCharge").textContent = allSvip ? "免扣" : formatMoney(totalCharged);
  $("#batchBalanceLabel").textContent = allSvip || isSvip() ? "当前权益" : "当前余额";
  $("#batchBalance").textContent = allSvip || isSvip() ? "无限使用" : formatMoney(state.me?.balance_fen);
}

function createBatchExportSnapshot() {
  saveActiveBatchViewer();
  return {
    batchId: state.batchId,
    balanceFen: state.me?.balance_fen,
    items: state.batchQueue.map((item, queueIndex) => {
      ensureBatchResultState(item);
      const initialItems = item.response ? item.autoItems.map(cloneItem) : [];
      const finalItems = item.response ? batchResultItems(item).map(cloneItem) : [];
      const resultItems = item.response ? normalizedItems(finalItems).map(cloneItem) : [];
      const dirty = item.response
        ? globalThis.BatchExportData.hasNetManualChanges(initialItems, finalItems)
        : false;
      return {
        queueIndex,
        sourceFilename: item.sourceFile?.name || `image-${queueIndex + 1}`,
        status: item.status,
        error: item.error || "",
        dirty,
        image: item.image,
        response: item.response ? {
          image_width: item.response.image_width,
          image_height: item.response.image_height,
          processing_ms: item.response.processing_ms,
          billing: item.response.billing ? { ...item.response.billing } : item.response.billing,
        } : null,
        initialItems,
        finalItems,
        resultItems,
        count: item.response ? totalStrands(resultItems) : null,
      };
    }),
  };
}

function batchJsonPayload(snapshot) {
  const completed = snapshot.items.filter((item) => item.response);
  return {
    batch_id: snapshot.batchId,
    total_count: completed.reduce((sum, item) => sum + item.count, 0),
    total_charged_fen: completed.reduce((sum, item) => sum + (item.response.billing?.charged_amount_fen || 0), 0),
    balance_fen: snapshot.balanceFen,
    items: completed.map((item, index) => ({
      index,
      filename: item.sourceFilename,
      result: {
        count: item.count,
        image_width: item.response.image_width,
        image_height: item.response.image_height,
        processing_ms: item.response.processing_ms,
        billing: item.response.billing,
        manually_edited: item.dirty,
        items: item.resultItems,
      },
    })),
  };
}

function exportBatchJson() {
  const snapshot = createBatchExportSnapshot();
  if (!snapshot.items.some((item) => item.response)) return;
  const payload = batchJsonPayload(snapshot);
  downloadBlob(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }), "hair-count-batch-result.json");
  $("#batchExportMenu").open = false;
  setBatchExportStatus("已导出批量 JSON");
}

function canvasBlob(sourceCanvas, type) {
  return new Promise((resolve, reject) => {
    sourceCanvas.toBlob((blob) => blob ? resolve(blob) : reject(new Error("标注图生成失败")), type);
  });
}

function annotatedFilename(originalFilename, index, usedNames) {
  const original = originalFilename || `image-${index + 1}`;
  const rawStem = original.replace(/\.[^.]+$/, "") || `image-${index + 1}`;
  const safeStem = rawStem
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_")
    .replace(/[. ]+$/g, "")
    .slice(0, 120) || `image-${index + 1}`;
  let suffix = 1;
  let filename = `${safeStem}-annotated.png`;
  while (usedNames.has(filename.toLowerCase())) {
    suffix += 1;
    filename = `${safeStem}-annotated-${suffix}.png`;
  }
  usedNames.add(filename.toLowerCase());
  return filename;
}

function setBatchExportStatus(message, error = false) {
  const status = $("#batchExportStatus");
  status.textContent = message;
  status.classList.toggle("error", error);
}

function setBatchExportBusy(busy, kind = "") {
  state.batchExporting = busy;
  const packageButton = $("#batchDownloadPackage");
  const imageButton = $("#batchDownloadImages");
  const hasResults = state.batchQueue.some((item) => item.status === "done" && item.response);
  packageButton.disabled = busy || !hasResults;
  packageButton.classList.toggle("loading", busy && kind === "package");
  packageButton.querySelector(".button-label").textContent = busy && kind === "package" ? "正在生成结果包…" : "下载完整结果包";
  imageButton.disabled = busy || !hasResults;
  imageButton.textContent = busy && kind === "images" ? "正在打包…" : "下载全部标注图";
  $("#batchDownloadJson").disabled = busy || !hasResults;
  $("#batchClearButton").disabled = busy;
  const menu = $("#batchExportMenu");
  menu.open = false;
  menu.classList.toggle("busy", busy);
  menu.setAttribute("aria-disabled", String(busy));
  batchAnalyzeButton.disabled = busy || state.batchProcessing
    || !state.batchQueue.some((item) => item.status === "pending" || item.status === "error");
  renderBatchQueue();
}

async function createBatchAnnotatedFiles(snapshot, folder = "", onProgress = null) {
  const completed = snapshot.items.filter((item) => item.status === "done" && item.response && item.image);
  const entries = [];
  const pathsByQueueIndex = new Map();
  const usedNames = new Set();

  for (const [index, item] of completed.entries()) {
    if (onProgress) onProgress(index + 1, completed.length);
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const exportCanvas = document.createElement("canvas");
    drawAnnotatedImage(exportCanvas, item.image, item.resultItems);
    const imageBlob = await canvasBlob(exportCanvas, "image/png");
    const filename = annotatedFilename(item.sourceFilename, index, usedNames);
    const path = `${folder}${filename}`;
    entries.push({
      name: path,
      data: new Uint8Array(await imageBlob.arrayBuffer()),
    });
    pathsByQueueIndex.set(item.queueIndex, path);
    exportCanvas.width = 0;
    exportCanvas.height = 0;
  }

  return { entries, pathsByQueueIndex };
}

function batchSpreadsheetRows(snapshot, pathsByQueueIndex) {
  return snapshot.items.map((item) => {
    const succeeded = item.status === "done" && Boolean(item.response);
    const failed = item.status === "error";
    return {
      index: item.queueIndex + 1,
      originalFilename: item.sourceFilename,
      annotatedPath: succeeded ? (pathsByQueueIndex.get(item.queueIndex) || "") : "",
      status: succeeded ? "成功" : failed ? "失败" : "未处理",
      finalCount: succeeded ? item.count : null,
      manuallyEdited: succeeded ? (item.dirty ? "是" : "否") : "",
      error: failed ? (item.error || "识别失败") : "",
    };
  });
}

async function exportBatchPackage() {
  if (state.batchExporting) return;
  const snapshot = createBatchExportSnapshot();
  const succeeded = snapshot.items.filter((item) => item.status === "done" && item.response && item.image);
  if (!succeeded.length) return;

  setBatchExportBusy(true, "package");
  setBatchExportStatus("正在准备完整结果包…");

  try {
    if (!globalThis.ZipArchive?.create) throw new Error("ZIP 组件未加载，请刷新页面后重试");
    if (!globalThis.XlsxWorkbook?.create) throw new Error("Excel 组件未加载，请刷新页面后重试");

    const images = await createBatchAnnotatedFiles(snapshot, "标注图/", (current, total) => {
      setBatchExportStatus(`正在生成标注图 ${current} / ${total}`);
    });

    setBatchExportStatus("正在生成 Excel…");
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const workbook = globalThis.XlsxWorkbook.create({
      summaryRows: batchSpreadsheetRows(snapshot, images.pathsByQueueIndex),
      markerRows: globalThis.BatchExportData.createMarkerRows(snapshot, images.pathsByQueueIndex),
    });
    const json = JSON.stringify(batchJsonPayload(snapshot), null, 2);

    setBatchExportStatus("正在打包完整结果…");
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const archive = globalThis.ZipArchive.create([
      { name: "批量结果.xlsx", data: new Uint8Array(await workbook.arrayBuffer()) },
      { name: "批量结果.json", data: new TextEncoder().encode(json) },
      ...images.entries,
    ]);
    downloadBlob(archive, "hair-count-batch-results.zip");
    setBatchExportStatus(`完整结果包已生成，共记录 ${snapshot.items.length} 张图片`);
  } catch (error) {
    setBatchExportStatus(error.message || "完整结果包生成失败", true);
  } finally {
    setBatchExportBusy(false);
  }
}

async function exportBatchImages() {
  if (state.batchExporting) return;
  const snapshot = createBatchExportSnapshot();
  const completed = snapshot.items.filter((item) => item.status === "done" && item.response && item.image);
  if (!completed.length) return;

  setBatchExportBusy(true, "images");
  setBatchExportStatus(`准备打包 ${completed.length} 张标注图…`);

  try {
    if (!globalThis.ZipArchive?.create) throw new Error("ZIP 组件未加载，请刷新页面后重试");
    const images = await createBatchAnnotatedFiles(snapshot, "", (current, total) => {
      setBatchExportStatus(`正在生成标注图 ${current} / ${total}`);
    });

    setBatchExportStatus("正在生成 ZIP 文件…");
    await new Promise((resolve) => requestAnimationFrame(resolve));
    const archive = globalThis.ZipArchive.create(images.entries);
    downloadBlob(archive, "hair-count-annotated-images.zip");
    setBatchExportStatus(`已打包 ${completed.length} 张标注图`);
  } catch (error) {
    setBatchExportStatus(error.message || "批量标注图下载失败", true);
  } finally {
    setBatchExportBusy(false);
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
  refreshDirtyState();
  renderAll();
});
$("#restoreButton").addEventListener("click", restoreAutoItems);
$("#downloadJson").addEventListener("click", exportJson);
$("#downloadImage").addEventListener("click", exportImage);
$$(".toolbar-button[data-mode]").forEach((button) => button.addEventListener("click", () => setEditMode(button.dataset.mode)));
["dragenter", "dragover"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.add("dragging"); }));
["dragleave", "drop"].forEach((eventName) => dropZone.addEventListener(eventName, (event) => { event.preventDefault(); dropZone.classList.remove("dragging"); }));
dropZone.addEventListener("drop", (event) => selectFile(event.dataTransfer.files[0]));

// Batch mode events
$$("[data-input-mode]").forEach((button) => button.addEventListener("click", () => setInputMode(button.dataset.inputMode)));
batchFileInput.addEventListener("change", () => { addBatchFiles(batchFileInput.files); batchFileInput.value = ""; });
batchAnalyzeButton.addEventListener("click", analyzeBatch);
$("#batchClearButton").addEventListener("click", clearBatch);
batchSensitivity.addEventListener("input", () => $("#batchSensitivityValue").textContent = sensitivityLabel(batchSensitivity.value));
batchContrast.addEventListener("input", () => $("#batchContrastValue").textContent = batchContrast.value);
["dragenter", "dragover"].forEach((eventName) => batchDropZone.addEventListener(eventName, (event) => { event.preventDefault(); batchDropZone.classList.add("dragging"); }));
["dragleave", "drop"].forEach((eventName) => batchDropZone.addEventListener(eventName, (event) => { event.preventDefault(); batchDropZone.classList.remove("dragging"); }));
batchDropZone.addEventListener("drop", (event) => { event.preventDefault(); addBatchFiles(event.dataTransfer.files); });
$("#batchQueueList").addEventListener("click", (event) => {
  const removeButton = event.target.closest("[data-batch-remove]");
  if (removeButton) { removeBatchItem(Number(removeButton.dataset.batchRemove)); return; }
  const viewItem = event.target.closest("[data-batch-view]");
  if (viewItem) viewBatchItem(Number(viewItem.dataset.batchView));
});
$("#batchDownloadJson").addEventListener("click", exportBatchJson);
$("#batchDownloadImages").addEventListener("click", exportBatchImages);
$("#batchDownloadPackage").addEventListener("click", exportBatchPackage);
document.addEventListener("click", (event) => {
  const menu = $("#batchExportMenu");
  if (menu.open && !menu.contains(event.target)) menu.open = false;
});

$("#loginForm").addEventListener("submit", login);
$("#logoutButton").addEventListener("click", logout);
$$("[data-app-view]").forEach((button) => button.addEventListener("click", () => setAppView(button.dataset.appView)));
$("#refreshAdminButton").addEventListener("click", loadAdmin);
$("#createAccountButton").addEventListener("click", () => {
  const dialog = $("#createAccountDialog");
  const form = $("#createAccountForm");
  form.reset();
  updatePlanFields(form);
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
$$(".plan-selector input[name='plan']").forEach((input) => input.addEventListener("change", () => updatePlanFields(input.form)));

checkHealth().then(loadSession);
