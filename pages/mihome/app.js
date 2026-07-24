"use strict";

/**
 * AstrBot Plugin Page 相对端点约定：
 * GET  status
 * POST auth/start
 * GET  auth/status
 * POST auth/logout
 * GET  devices
 * POST devices/sync
 * POST devices/mappings
 * GET  devices/status?alias=...
 * GET  scenes
 * POST scenes/sync
 * GET  tools
 * POST tools
 * GET  diagnostics
 * POST diagnostics/check
 */
const ENDPOINTS = Object.freeze({
  status: "status",
  authStart: "auth/start",
  authStatus: "auth/status",
  authLogout: "auth/logout",
  devices: "devices",
  devicesSync: "devices/sync",
  deviceMappings: "devices/mappings",
  deviceStatus: "devices/status",
  scenes: "scenes",
  scenesSync: "scenes/sync",
  tools: "tools",
  diagnostics: "diagnostics",
  diagnosticsCheck: "diagnostics/check",
});

const DEVICE_CATEGORIES = Object.freeze([
  "无类别",
  "空调类别",
  "净化器类别",
  "风扇类别",
  "蒸煮锅类别",
  "空气炸锅类别",
  "温湿度计类别",
  "体脂秤类别",
  "扫地机类别",
  "热水器类别",
  "路由器类别",
  "音箱类别",
  "开关类别",
  "门磁传感器类别",
  "燃气传感器类别",
]);

const VIEW_META = Object.freeze({
  overview: ["概览", "查看账号、同步与运行健康状态"],
  devices: ["设备管理", "同步设备并配置聊天中使用的别名与类别"],
  scenes: ["场景与 Tool", "管理场景缓存与大模型 Tool 权限"],
  diagnostics: ["诊断", "检查账号、网络、缓存与配置健康状态"],
});

const ICON_PATHS = Object.freeze({
  device: "M7 2h10a3 3 0 0 1 3 3v14a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V5a3 3 0 0 1 3-3Zm0 2a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V5a1 1 0 0 0-1-1H7Z",
  scene: "M12 2 3 7v10l9 5 9-5V7l-9-5Zm0 2.3L18.7 8 12 11.7 5.3 8 12 4.3Z",
  check: "m9.4 16.6-4-4L4 14l5.4 5.4L20 8.8 18.6 7.4 9.4 16.6Z",
  cloud: "M7.5 18A5.5 5.5 0 0 1 7 7.03 7 7 0 0 1 20.8 9.5 4.5 4.5 0 0 1 19.5 18h-12Zm0-2h12a2.5 2.5 0 0 0 .15-5A5 5 0 0 0 9.9 8.6 3.5 3.5 0 0 0 7.5 16Z",
  mapping: "M7 7h11l-3-3 1.4-1.4L21.8 8l-5.4 5.4L15 12l3-3H7V7Zm10 10H6l3 3-1.4 1.4L2.2 16l5.4-5.4L9 12l-3 3h11v2Z",
  network: "M12 3C7.95 3 4.3 4.65 1.65 7.3l1.4 1.4A12.65 12.65 0 0 1 12 5c3.5 0 6.65 1.4 8.95 3.7l1.4-1.4A14.65 14.65 0 0 0 12 3Zm0 5a9.6 9.6 0 0 0-6.85 2.85l1.4 1.4A7.65 7.65 0 0 1 12 10c2.15 0 4.1.85 5.45 2.25l1.4-1.4A9.6 9.6 0 0 0 12 8Zm0 5c-1.3 0-2.45.5-3.35 1.35l1.4 1.45A2.75 2.75 0 0 1 12 15c.75 0 1.4.3 1.95.8l1.4-1.45A4.8 4.8 0 0 0 12 13Zm0 5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z",
  cache: "M12 2c5 0 9 1.8 9 4v12c0 2.2-4 4-9 4s-9-1.8-9-4V6c0-2.2 4-4 9-4Zm0 2C7.6 4 5 5.5 5 6s2.6 2 7 2 7-1.5 7-2-2.6-2-7-2Zm7 5.2C17.3 10.3 14.8 11 12 11s-5.3-.7-7-1.8V12c0 .5 2.6 2 7 2s7-1.5 7-2V9.2Zm0 6C17.3 16.3 14.8 17 12 17s-5.3-.7-7-1.8V18c0 .5 2.6 2 7 2s7-1.5 7-2v-2.8Z",
});

const bridge = window.AstrBotPluginPage;
const state = {
  connected: false,
  currentView: "overview",
  status: {},
  categories: [...DEVICE_CATEGORIES],
  devices: [],
  originalMappings: new Map(),
  scenes: [],
  sceneCacheTime: "",
  tools: {
    enable_readonly_tool: false,
    scene_tool: { enable: false, admin_only: true },
    control_tool: { enable: false, admin_only: true, allowed_devices: [] },
  },
  originalTools: "",
  diagnostics: {},
  loaded: {
    devices: false,
    scenes: false,
    tools: false,
    diagnostics: false,
  },
  authTimer: null,
  dialogResolver: null,
  previousFocus: null,
  drawerPreviousFocus: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function text(value, fallback = "") {
  if (value === null || value === undefined) return fallback;
  return String(value);
}

function bool(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (value === 1 || value === "1" || value === "true") return true;
  if (value === 0 || value === "0" || value === "false") return false;
  return fallback;
}

function redact(value) {
  return text(value)
    .replace(/(authorization|token|cookie|passToken|serviceToken)\s*[:=]\s*[^\s,;]+/gi, "$1=[已隐藏]")
    .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]+/gi, "Bearer [已隐藏]")
    .replace(/[A-Za-z0-9+/=_-]{48,}/g, "[已隐藏]");
}

function formatTime(value, fallback = "尚未记录") {
  const raw = text(value).trim();
  if (!raw) return fallback;
  const date = new Date(raw.includes("T") ? raw : raw.replace(" ", "T"));
  if (Number.isNaN(date.getTime())) return raw;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function createElement(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = text(content);
  return node;
}

function createIcon(path, className) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  if (className) svg.setAttribute("class", className);
  const shape = document.createElementNS("http://www.w3.org/2000/svg", "path");
  shape.setAttribute("d", path);
  svg.append(shape);
  return svg;
}

function setText(selector, value) {
  const node = $(selector);
  if (node) node.textContent = text(value);
}

function setButtonLoading(button, loading) {
  if (!button) return;
  button.disabled = Boolean(loading);
  button.classList.toggle("is-loading", Boolean(loading));
  button.setAttribute("aria-busy", String(Boolean(loading)));
}

function unwrapResponse(raw) {
  if (!raw || typeof raw !== "object") return raw;
  if (raw.success === false || raw.ok === false) {
    throw new Error(text(raw.message || raw.error, "请求未成功"));
  }
  if ((raw.success === true || raw.ok === true) && Object.prototype.hasOwnProperty.call(raw, "data")) {
    return raw.data;
  }
  return raw;
}

async function apiGet(endpoint, params = {}) {
  if (!bridge || typeof bridge.apiGet !== "function") {
    throw new Error("AstrBot Plugin Page Bridge 不可用");
  }
  return unwrapResponse(await bridge.apiGet(endpoint, params));
}

async function apiPost(endpoint, payload = {}) {
  if (!bridge || typeof bridge.apiPost !== "function") {
    throw new Error("AstrBot Plugin Page Bridge 不可用");
  }
  return unwrapResponse(await bridge.apiPost(endpoint, payload));
}

function showToast(title, message = "", kind = "success", duration = 3800) {
  const region = $("#toast-region");
  const toast = createElement("div", `toast${kind === "error" ? " is-error" : kind === "warning" ? " is-warning" : ""}`);
  toast.setAttribute("role", kind === "error" ? "alert" : "status");
  toast.append(createElement("span", "toast-dot"));
  const copy = createElement("div");
  copy.append(createElement("strong", "", title));
  if (message) copy.append(createElement("span", "", redact(message)));
  toast.append(copy);
  region.append(toast);
  window.setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(-5px)";
    window.setTimeout(() => toast.remove(), 180);
  }, duration);
}

function getErrorMessage(error, fallback = "请求失败，请稍后重试") {
  if (!error) return fallback;
  return redact(error.message || error.error || error.detail || error);
}

function applyTheme(context = {}) {
  if (typeof context.isDark === "boolean") {
    document.documentElement.dataset.theme = context.isDark ? "dark" : "light";
    return;
  }
  const requested = text(context.theme || context.colorScheme || "").toLowerCase();
  if (requested === "dark" || requested === "light") {
    document.documentElement.dataset.theme = requested;
    return;
  }
  // Bridge SDK 会预先同步 data-theme；只有脱离 AstrBot 预览时才回退到系统主题。
  if (!["dark", "light"].includes(document.documentElement.dataset.theme)) {
    const dark = window.matchMedia
      && window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }
}

function setConnection(connected, label) {
  state.connected = connected;
  const dot = $("#connection-dot");
  dot.classList.toggle("is-online", connected);
  dot.classList.toggle("is-offline", !connected);
  setText("#connection-label", label || (connected ? "已连接 AstrBot" : "连接不可用"));
}

function setTopStatus(kind, label) {
  const node = $("#top-status");
  node.className = `status-pill is-${kind}`;
  const labelNode = node.querySelector("span:last-child");
  if (labelNode) labelNode.textContent = label;
}

function navigate(view) {
  if (!VIEW_META[view]) return;
  state.currentView = view;
  $$(".nav-item").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $$("[data-view-panel]").forEach((panel) => {
    const active = panel.dataset.viewPanel === view;
    panel.hidden = !active;
    panel.classList.toggle("is-active", active);
  });
  const [title, subtitle] = VIEW_META[view];
  setText("#page-title", title);
  setText("#page-subtitle", subtitle);
  updateMappingDock();
  window.scrollTo({ top: 0, behavior: "smooth" });
  loadView(view, false);
}

async function loadView(view, force) {
  try {
    if (view === "overview") {
      const tasks = [loadStatus()];
      if (force || !state.loaded.devices) tasks.push(loadDevices(false));
      if (force || !state.loaded.scenes) tasks.push(loadScenes(false));
      if (force || !state.loaded.diagnostics) tasks.push(loadDiagnostics(false));
      await Promise.allSettled(tasks);
    } else if (view === "devices" && (force || !state.loaded.devices)) {
      await loadDevices(false);
    } else if (view === "scenes") {
      const tasks = [];
      if (force || !state.loaded.devices) tasks.push(loadDevices(false));
      if (force || !state.loaded.scenes) tasks.push(loadScenes(false));
      const toolsDirty = state.loaded.tools && serializedTools() !== state.originalTools;
      if (force || !state.loaded.tools || !toolsDirty) tasks.push(loadTools());
      await Promise.allSettled(tasks);
    } else if (view === "diagnostics" && (force || !state.loaded.diagnostics)) {
      await loadDiagnostics(false);
    }
  } catch (error) {
    showToast("页面刷新失败", getErrorMessage(error), "error");
  }
}

function isLoggedIn(payload = state.status) {
  const account = payload.auth && typeof payload.auth === "object"
    ? payload.auth
    : payload.account && typeof payload.account === "object"
      ? payload.account
      : payload;
  return bool(
    account.logged_in ?? account.authenticated ?? account.api_available ?? account.available,
    false,
  );
}

function isLoginRunning(payload = state.status) {
  const account = payload.auth && typeof payload.auth === "object"
    ? payload.auth
    : payload.account && typeof payload.account === "object"
      ? payload.account
      : payload;
  // auth/status 的 running 是最新轮询值，优先于概览中可能残留的旧字段。
  return bool(account.running ?? account.login_in_progress ?? account.auth_in_progress, false);
}

async function loadStatus() {
  const payload = await apiGet(ENDPOINTS.status);
  state.status = payload && typeof payload === "object" ? payload : {};
  renderStatus();
  return state.status;
}

function renderStatus() {
  const account = state.status.auth && typeof state.status.auth === "object"
    ? state.status.auth
    : state.status.account && typeof state.status.account === "object"
      ? state.status.account
      : state.status;
  const summary = state.status.summary && typeof state.status.summary === "object"
    ? state.status.summary
    : {};
  if (!state.sceneCacheTime && summary.scene_cache_updated_at) {
    state.sceneCacheTime = text(summary.scene_cache_updated_at).trim();
  }
  const loggedIn = isLoggedIn();
  const running = isLoginRunning();
  const loginError = redact(account.last_login_error || account.login_error || "");

  if (running) {
    setTopStatus("warning", "等待扫码");
    setText("#metric-account", "登录进行中");
  } else if (loggedIn) {
    setTopStatus("success", "服务正常");
    setText("#metric-account", "已授权");
  } else {
    setTopStatus(loginError ? "danger" : "warning", loginError ? "授权异常" : "未登录");
    setText("#metric-account", "未授权");
  }

  setText("#metric-login-time", formatTime(account.last_login_at, loggedIn ? "授权有效" : "等待扫码登录"));
  setText("#account-title", running ? "等待扫码确认" : loggedIn ? "账号已安全授权" : "尚未连接米家账号");
  setText(
    "#account-description",
    loginError || (running
      ? "请使用米家 App 扫描二维码，并在手机中确认授权。"
      : loggedIn
        ? `最近登录：${formatTime(account.last_login_at)}`
        : "扫码授权后即可同步设备与场景。"),
  );

  const badge = $("#account-badge");
  badge.className = `status-badge is-${running ? "warning" : loggedIn ? "success" : loginError ? "danger" : "neutral"}`;
  badge.textContent = running ? "授权中" : loggedIn ? "已登录" : loginError ? "异常" : "未登录";

  $("#hero-login").hidden = loggedIn && !running;
  $("#account-login").hidden = loggedIn && !running;
  $("#account-login").disabled = running;
  $("#account-login").textContent = running ? "等待扫码确认" : "开始扫码登录";
  $("#account-logout").hidden = !loggedIn || running;
  if (running) {
    $("#auth-panel").hidden = false;
    scheduleAuthPoll();
  } else if (loggedIn) {
    $("#auth-panel").hidden = true;
    clearAuthPoll();
  }

  renderRecentStatus(account, summary);
  renderOverviewMetrics();
}

function renderRecentStatus(account, summary = {}) {
  const list = $("#recent-status-list");
  list.replaceChildren();
  const items = [
    ["最近登录", formatTime(account.last_login_at)],
    ["共享设备", account.last_shared_error ? redact(account.last_shared_error) : "未发现异常"],
    ["场景同步", account.last_scene_error
      ? redact(account.last_scene_error)
      : formatTime(account.scene_cache_updated_at ?? summary.scene_cache_updated_at, "尚未同步")],
  ];
  items.forEach(([label, value]) => {
    const row = createElement("div");
    row.append(createElement("dt", "", label), createElement("dd", "", value));
    list.append(row);
  });
}

function normalizeQrImage(value) {
  const raw = text(value).trim();
  if (/^data:image\/(?:png|jpeg|webp|svg\+xml);base64,[A-Za-z0-9+/=\s]+$/i.test(raw)) return raw;
  if (/^[A-Za-z0-9+/=\s]{128,}$/.test(raw)) return `data:image/png;base64,${raw.replace(/\s/g, "")}`;
  return "";
}

function renderAuthPayload(payload = {}) {
  $("#auth-panel").hidden = false;
  const image = $("#auth-qr-image");
  const placeholder = $("#auth-qr-placeholder");
  const qrImage = normalizeQrImage(payload.qr_image || payload.qr_image_data || payload.qrcode);
  if (qrImage) {
    image.src = qrImage;
    image.hidden = false;
    placeholder.hidden = true;
  } else {
    image.removeAttribute("src");
    image.hidden = true;
    placeholder.hidden = false;
  }

  setText(
    "#auth-progress",
    redact(payload.message || payload.status_text || (
      isLoginRunning(payload) ? "等待扫码与手机确认…" : "二维码生成后，请在米家 App 中确认授权。"
    )),
  );
}

async function startLogin() {
  const buttons = [$("#hero-login"), $("#account-login")];
  buttons.forEach((button) => setButtonLoading(button, true));
  try {
    const payload = await apiPost(ENDPOINTS.authStart, {});
    renderAuthPayload(payload && typeof payload === "object" ? payload : {});
    showToast("登录流程已启动", "请使用米家 App 扫码并确认授权");
    scheduleAuthPoll(true);
  } catch (error) {
    showToast("无法启动登录", getErrorMessage(error), "error");
  } finally {
    buttons.forEach((button) => setButtonLoading(button, false));
  }
}

function clearAuthPoll() {
  if (state.authTimer) window.clearTimeout(state.authTimer);
  state.authTimer = null;
}

function scheduleAuthPoll(immediate = false) {
  clearAuthPoll();
  state.authTimer = window.setTimeout(pollAuthStatus, immediate ? 300 : 1800);
}

async function pollAuthStatus() {
  try {
    const payload = await apiGet(ENDPOINTS.authStatus);
    const auth = payload && typeof payload === "object" ? payload : {};
    renderAuthPayload(auth);
    state.status = {
      ...state.status,
      auth: {
        ...(state.status.auth && typeof state.status.auth === "object" ? state.status.auth : {}),
        ...auth,
      },
    };
    renderStatus();
    if (isLoggedIn(auth) && !isLoginRunning(auth)) {
      clearAuthPoll();
      $("#auth-panel").hidden = true;
      showToast("米家账号登录成功", "现在可以同步设备与场景");
      await Promise.allSettled([loadDevices(false), loadScenes(false)]);
      return;
    }
    if (auth.last_login_error || auth.error) {
      clearAuthPoll();
      showToast("登录未完成", redact(auth.last_login_error || auth.error), "error");
      return;
    }
    if (isLoginRunning(auth)) scheduleAuthPoll();
  } catch (error) {
    clearAuthPoll();
    showToast("登录状态读取失败", getErrorMessage(error), "error");
  }
}

async function logout() {
  const confirmed = await openDialog({
    title: "退出米家账号？",
    message: "这会清除插件保存的米家登录凭证与本地账号状态，并保留设备映射配置。",
    summary: [
      "登录凭证将从插件数据目录移除",
      "需要重新扫码后才能同步设备与场景",
    ],
    confirmLabel: "确认退出并清除",
    danger: true,
  });
  if (!confirmed) return;

  const button = $("#account-logout");
  setButtonLoading(button, true);
  try {
    await apiPost(ENDPOINTS.authLogout, { confirm: "退出登录" });
    clearAuthPoll();
    state.status = {};
    state.devices = [];
    state.scenes = [];
    state.originalMappings.clear();
    renderStatus();
    renderDevices();
    renderScenes();
    showToast("已退出米家账号", "本地登录凭证已清除");
    await loadStatus();
  } catch (error) {
    showToast("退出失败", getErrorMessage(error), "error");
  } finally {
    setButtonLoading(button, false);
  }
}

function normalizeDevices(payload) {
  const root = payload && typeof payload === "object" ? payload : {};
  const rawDevices = Array.isArray(root) ? root : Array.isArray(root.devices) ? root.devices : Array.isArray(root.items) ? root.items : [];
  const deviceMap = root.device_map && typeof root.device_map === "object" ? root.device_map : {};
  const categoryMap = root.device_category_map && typeof root.device_category_map === "object" ? root.device_category_map : {};
  const mappingsByDid = new Map();

  const providedCategories = Array.isArray(root.categories)
    ? root.categories.map((item) => text(item).trim()).filter(Boolean)
    : [];
  if (providedCategories.length) {
    state.categories = Array.from(new Set([
      ...(providedCategories.includes("无类别") ? [] : ["无类别"]),
      ...providedCategories,
    ]));
  }

  Object.entries(deviceMap).forEach(([alias, did]) => {
    if (!text(alias).trim() || !text(did).trim()) return;
    const key = text(did).trim();
    const list = mappingsByDid.get(key) || [];
    list.push({
      alias: text(alias).trim(),
      category: text(categoryMap[alias], "无类别").trim() || "无类别",
    });
    mappingsByDid.set(key, list);
  });
  if (Array.isArray(root.mappings)) {
    root.mappings.forEach((item) => {
      if (!item || !text(item.did).trim() || !text(item.alias).trim()) return;
      const key = text(item.did).trim();
      const list = mappingsByDid.get(key) || [];
      if (!list.some((mapping) => mapping.alias === text(item.alias).trim())) {
        list.push({
          alias: text(item.alias).trim(),
          category: text(item.category, "无类别").trim() || "无类别",
        });
      }
      mappingsByDid.set(key, list);
    });
  }

  return rawDevices
    .filter((item) => item && text(item.did).trim())
    .map((item) => {
      const did = text(item.did).trim();
      const embeddedMappings = Array.isArray(item.mappings)
        ? item.mappings
          .filter((mapping) => mapping && text(mapping.alias).trim())
          .map((mapping) => ({
            alias: text(mapping.alias).trim(),
            category: text(mapping.category, "无类别").trim() || "无类别",
          }))
        : [];
      const allMappings = embeddedMappings.length ? embeddedMappings : mappingsByDid.get(did) || [];
      const explicitAlias = text(item.alias).trim();
      const primary = explicitAlias
        ? allMappings.find((mapping) => mapping.alias === explicitAlias) || {
          alias: explicitAlias,
          category: text(item.category, "无类别").trim() || "无类别",
        }
        : allMappings[0] || { alias: "", category: "无类别" };
      const legacyMappings = allMappings.filter((mapping) => mapping.alias !== primary.alias);
      const category = text(item.category ?? primary.category ?? "无类别").trim() || "无类别";
      return {
        did,
        cloudName: text(item.cloud_name ?? item.name, "未命名设备").trim() || "未命名设备",
        model: text(item.model).trim(),
        alias: primary.alias,
        category: state.categories.includes(category) ? category : "无类别",
        legacyMappings,
        source: text(item.source ?? item.ownership).toLowerCase(),
        shared: bool(item.shared ?? item.is_shared, false) || text(item.source).toLowerCase() === "shared",
        profile: text(
          item.profile
          ?? item.profile_source
          ?? item.matched_by
          ?? (item.profile_matched ? "型号画像已匹配" : ""),
        ).trim(),
        missingFromCloud: bool(item.missing_from_cloud, false),
        online: item.online === undefined ? null : bool(item.online),
      };
    });
}

async function loadDevices(sync = false) {
  const payload = sync
    ? await apiPost(ENDPOINTS.devicesSync, {})
    : await apiGet(ENDPOINTS.devices);
  const normalized = normalizeDevices(payload);
  state.devices = normalized;
  state.loaded.devices = true;
  snapshotMappings();
  renderDevices();
  if (state.loaded.tools) renderControlAllowlist();
  renderOverviewMetrics();
  return normalized;
}

function snapshotMappings() {
  state.originalMappings = new Map(
    state.devices.map((device) => [
      device.did,
      { alias: device.alias.trim(), category: device.category || "无类别" },
    ]),
  );
}

function getDuplicateAliases() {
  const counts = new Map();
  state.devices.forEach((device) => {
    [device.alias, ...device.legacyMappings.map((mapping) => mapping.alias)].forEach((rawAlias) => {
      const alias = text(rawAlias).trim();
      if (alias) counts.set(alias, (counts.get(alias) || 0) + 1);
    });
  });
  return new Set(Array.from(counts).filter(([, count]) => count > 1).map(([alias]) => alias));
}

function getMappingChanges() {
  return state.devices.filter((device) => {
    const original = state.originalMappings.get(device.did) || { alias: "", category: "无类别" };
    return device.alias.trim() !== original.alias || device.category !== original.category;
  });
}

function renderOverviewMetrics() {
  const summary = state.status.summary && typeof state.status.summary === "object"
    ? state.status.summary
    : {};
  const deviceCount = state.devices.length || Number(summary.cloud_device_count) || 0;
  const mappedCount = state.devices.length ? collectMappings().length : Number(summary.configured_alias_count) || 0;
  const sceneCount = state.scenes.length || Number(summary.scene_count) || 0;
  const sceneTime = state.sceneCacheTime || text(summary.scene_cache_updated_at).trim();
  setText("#metric-devices", deviceCount ? `${deviceCount} 台` : "—");
  setText("#metric-mapped", deviceCount ? `${mappedCount} 个别名已配置` : "等待设备同步");
  setText("#metric-scenes", sceneCount ? `${sceneCount} 个` : "—");
  setText("#metric-scene-time", sceneTime ? `更新于 ${formatTime(sceneTime)}` : "尚未同步");
  const count = $("#nav-device-count");
  count.textContent = String(state.devices.length);
  count.hidden = !state.devices.length;
}

function createDeviceItem(device, duplicateAliases) {
  const item = createElement("article", "device-item");
  item.dataset.did = device.did;

  const identity = createElement("div", "device-identity");
  const avatar = createElement("span", "device-avatar");
  avatar.append(createIcon(ICON_PATHS.device));
  const name = createElement("div", "device-name");
  name.append(createElement("strong", "", device.cloudName));
  name.append(createElement("span", "", `${device.model || "未知型号"} · DID ${device.did}`));
  const tags = createElement("div", "device-tags");
  if (device.shared) tags.append(createElement("span", "mini-tag is-shared", "共享设备"));
  if (device.profile) tags.append(createElement("span", "mini-tag is-profile", device.profile));
  if (device.legacyMappings.length) {
    const legacyTag = createElement("span", "mini-tag is-warning", `另保留 ${device.legacyMappings.length} 个旧别名`);
    legacyTag.title = device.legacyMappings.map((mapping) => mapping.alias).join("、");
    tags.append(legacyTag);
  }
  if (device.missingFromCloud) tags.append(createElement("span", "mini-tag is-warning", "当前云端列表未发现"));
  if (device.online !== null) tags.append(createElement("span", "mini-tag", device.online ? "云端在线" : "状态未知"));
  if (tags.childElementCount) name.append(tags);
  identity.append(avatar, name);

  const aliasField = createElement("div", "device-field");
  const aliasLabel = createElement("label", "", "设备别名");
  const aliasInput = document.createElement("input");
  aliasInput.type = "text";
  aliasInput.maxLength = 48;
  aliasInput.placeholder = "例如：客厅 空调";
  aliasInput.value = device.alias;
  aliasInput.dataset.deviceAlias = device.did;
  aliasInput.setAttribute("aria-label", `${device.cloudName}的设备别名`);
  if (duplicateAliases.has(device.alias.trim()) && device.alias.trim()) {
    aliasInput.classList.add("is-invalid");
    aliasInput.setAttribute("aria-invalid", "true");
    aliasInput.title = "设备别名不能重复";
  }
  aliasField.append(aliasLabel, aliasInput);

  const categoryField = createElement("div", "device-field");
  const categoryLabel = createElement("label", "", "设备类别");
  const categorySelect = document.createElement("select");
  categorySelect.dataset.deviceCategory = device.did;
  categorySelect.setAttribute("aria-label", `${device.cloudName}的设备类别`);
  state.categories.forEach((category) => {
    const option = document.createElement("option");
    option.value = category;
    option.textContent = category;
    option.selected = device.category === category;
    categorySelect.append(option);
  });
  categoryField.append(categoryLabel, categorySelect);

  const action = createElement("div", "device-action");
  const statusButton = createElement("button", "button button-secondary", "查看状态");
  statusButton.type = "button";
  statusButton.dataset.viewDeviceStatus = device.did;
  statusButton.disabled = !device.alias.trim();
  statusButton.title = device.alias.trim() ? "只读查看设备实时状态" : "请先填写设备别名";
  action.append(statusButton);

  item.append(identity, aliasField, categoryField, action);
  return item;
}

function renderDevices() {
  const query = text($("#device-search").value).trim().toLowerCase();
  const filter = $("#device-filter").value;
  const duplicateAliases = getDuplicateAliases();
  const filtered = state.devices.filter((device) => {
    const matchesQuery = !query || [
      device.cloudName,
      device.alias,
      device.did,
      device.model,
    ].some((value) => text(value).toLowerCase().includes(query));
    const matchesFilter = filter === "all"
      || (filter === "mapped" && device.alias.trim())
      || (filter === "unmapped" && !device.alias.trim())
      || (filter === "shared" && device.shared);
    return matchesQuery && matchesFilter;
  });

  const list = $("#device-list");
  list.replaceChildren(...filtered.map((device) => createDeviceItem(device, duplicateAliases)));
  $("#device-empty").hidden = filtered.length > 0;

  const mapped = state.devices.filter((device) => device.alias.trim()).length;
  setText(
    "#device-summary",
    state.devices.length
      ? `显示 ${filtered.length} / ${state.devices.length} 台 · ${mapped} 台已映射`
      : "尚未读取设备",
  );
  updateMappingDock();
}

function updateDeviceValidation() {
  const duplicates = getDuplicateAliases();
  $$("[data-device-alias]").forEach((input) => {
    const invalid = duplicates.has(input.value.trim()) && input.value.trim();
    input.classList.toggle("is-invalid", Boolean(invalid));
    if (invalid) {
      input.setAttribute("aria-invalid", "true");
      input.title = "设备别名不能重复";
    } else {
      input.removeAttribute("aria-invalid");
      input.removeAttribute("title");
    }
    const item = input.closest(".device-item");
    const button = item && $("[data-view-device-status]", item);
    if (button) {
      button.disabled = !input.value.trim();
      button.title = input.value.trim() ? "只读查看设备实时状态" : "请先填写设备别名";
    }
  });
  updateMappingDock();
}

function updateMappingDock() {
  const changes = getMappingChanges();
  const hasDuplicates = getDuplicateAliases().size > 0;
  const visible = state.currentView === "devices" && changes.length > 0;
  const dock = $("#mapping-save-dock");
  const topButton = $("#save-device-mappings");
  dock.hidden = !visible;
  topButton.hidden = !visible;
  topButton.disabled = hasDuplicates;
  setText("#mapping-change-count", `${changes.length} 项待保存`);
  setText("#save-device-mappings-label", `保存 ${changes.length}`);
}

function setMappingSaveLoading(loading) {
  setButtonLoading($("#review-mappings"), loading);
  setButtonLoading($("#save-device-mappings"), loading);
}

function mappingSummary(changes) {
  return changes.map((device) => {
    const original = state.originalMappings.get(device.did) || { alias: "", category: "无类别" };
    const before = original.alias ? `${original.alias} / ${original.category}` : "未映射";
    const after = device.alias.trim() ? `${device.alias.trim()} / ${device.category}` : "移除映射";
    const preserved = device.legacyMappings.length
      ? `；另保留旧别名 ${device.legacyMappings.map((mapping) => mapping.alias).join("、")}`
      : "";
    return `${device.cloudName}：${before} → ${after}${preserved}`;
  });
}

function collectMappings() {
  const rows = [];
  state.devices.forEach((device) => {
    if (device.alias.trim()) {
      rows.push({
        alias: device.alias.trim(),
        did: device.did,
        category: device.category || "无类别",
      });
    }
    device.legacyMappings.forEach((mapping) => {
      if (!text(mapping.alias).trim() || text(mapping.alias).trim() === device.alias.trim()) return;
      rows.push({
        alias: text(mapping.alias).trim(),
        did: device.did,
        category: state.categories.includes(mapping.category) ? mapping.category : "无类别",
      });
    });
  });
  return rows;
}

function formatServerMappingChanges(changes, fallback) {
  if (!changes || typeof changes !== "object") return fallback;
  const lines = [];
  const added = Array.isArray(changes.added) ? changes.added : [];
  const removed = Array.isArray(changes.removed) ? changes.removed : [];
  const changed = Array.isArray(changes.changed) ? changes.changed : [];
  added.forEach((item) => lines.push(
    `新增：${text(item.alias)} → DID ${text(item.did)}（${text(item.category, "无类别")}）`,
  ));
  removed.forEach((item) => lines.push(
    `移除：${text(item.alias)}（DID ${text(item.did)}）`,
  ));
  changed.forEach((item) => lines.push(
    `修改：${text(item.alias)}，${text(item.before && item.before.category, "无类别")} → ${text(item.after && item.after.category, "无类别")}`,
  ));
  const preserved = Array.isArray(changes.preserved_orphan_categories)
    ? changes.preserved_orphan_categories
    : [];
  if (preserved.length) lines.push(`安全保留 ${preserved.length} 个旧版孤立类别项`);
  return lines.length ? lines : fallback;
}

async function reviewAndSaveMappings() {
  const duplicates = getDuplicateAliases();
  if (duplicates.size) {
    showToast("存在重复别名", `请修改：${Array.from(duplicates).join("、")}`, "error");
    const firstInvalid = $(".device-field input.is-invalid");
    if (firstInvalid) firstInvalid.focus();
    return;
  }

  const changes = getMappingChanges();
  if (!changes.length) {
    showToast("没有待保存的修改", "", "warning");
    return;
  }

  setMappingSaveLoading(true);
  try {
    const mappings = collectMappings();
    const preview = await apiPost(ENDPOINTS.deviceMappings, { mappings });
    setMappingSaveLoading(false);
    const confirmed = await openDialog({
      title: "保存设备映射？",
      message: "以下修改已通过后端校验。确认后才会写入插件配置，聊天命令与只读 Tool 将使用新的映射。",
      summary: formatServerMappingChanges(preview && preview.changes, mappingSummary(changes)),
      confirmLabel: "确认保存",
    });
    if (!confirmed) return;
    setMappingSaveLoading(true);
    await apiPost(ENDPOINTS.deviceMappings, { mappings, confirm: true });
    state.devices.forEach((device) => { device.alias = device.alias.trim(); });
    snapshotMappings();
    renderDevices();
    renderOverviewMetrics();
    if (state.loaded.tools) renderControlAllowlist();
    showToast("设备映射已保存", `共保留 ${mappings.length} 条映射`);
  } catch (error) {
    showToast("设备映射保存失败", getErrorMessage(error), "error");
  } finally {
    setMappingSaveLoading(false);
  }
}

function resetMappings() {
  state.devices.forEach((device) => {
    const original = state.originalMappings.get(device.did);
    if (!original) return;
    device.alias = original.alias;
    device.category = original.category;
  });
  renderDevices();
  showToast("已撤销未保存修改", "", "warning");
}

async function syncDevices() {
  if (getMappingChanges().length) {
    const discard = await openDialog({
      title: "放弃未保存的设备修改？",
      message: "同步云端设备会重新载入当前映射，尚未保存的别名或类别修改将被丢弃。",
      summary: mappingSummary(getMappingChanges()),
      confirmLabel: "放弃修改并同步",
      danger: true,
    });
    if (!discard) return;
  }
  const button = $("#sync-devices");
  setButtonLoading(button, true);
  try {
    await loadDevices(true);
    showToast("设备同步完成", `已读取 ${state.devices.length} 台云端设备`);
  } catch (error) {
    showToast("设备同步失败", getErrorMessage(error), "error");
  } finally {
    setButtonLoading(button, false);
  }
}

function findDevice(did) {
  return state.devices.find((device) => device.did === text(did));
}

function closeDeviceDetail() {
  $("#device-detail-layer").hidden = true;
  document.body.style.overflow = "";
  if (state.drawerPreviousFocus && typeof state.drawerPreviousFocus.focus === "function") {
    state.drawerPreviousFocus.focus();
  }
}

function appendMeta(container, label, value) {
  const row = createElement("div");
  row.append(createElement("dt", "", label), createElement("dd", "", value));
  container.append(row);
}

function normalizeStateEntries(payload) {
  const root = payload && typeof payload === "object" ? payload : {};
  const raw = root.states ?? root.status ?? root.properties ?? root.data;
  if (Array.isArray(raw)) {
    return raw.map((item, index) => {
      if (item && typeof item === "object") {
        return [
          text(item.label ?? item.name ?? item.key, `状态 ${index + 1}`),
          text(item.display_value ?? item.value, "—"),
        ];
      }
      return [`状态 ${index + 1}`, text(item, "—")];
    });
  }
  if (raw && typeof raw === "object") {
    return Object.entries(raw).map(([key, value]) => [
      key,
      value && typeof value === "object"
        ? text(value.display_value ?? value.value ?? value.text, "—")
        : text(value, "—"),
    ]);
  }
  const statusText = text(root.text ?? root.status_text ?? root.message ?? (typeof payload === "string" ? payload : "")).trim();
  if (!statusText) return [];
  const lines = statusText.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (lines.length <= 1) return [["状态", statusText]];
  return lines.map((line, index) => {
    const separator = line.search(/[：:]/);
    if (separator > 0) {
      return [line.slice(0, separator).replace(/^[-•]\s*/, ""), line.slice(separator + 1).trim() || "—"];
    }
    return [index === 0 ? "设备信息" : `状态 ${index}`, line.replace(/^[-•]\s*/, "")];
  });
}

async function showDeviceStatus(device) {
  if (!device || !device.alias.trim()) {
    showToast("请先配置设备别名", "只读状态接口仅允许访问已映射设备", "warning");
    return;
  }
  state.drawerPreviousFocus = document.activeElement;
  const layer = $("#device-detail-layer");
  layer.hidden = false;
  document.body.style.overflow = "hidden";
  setText("#device-detail-title", device.alias);
  setText("#device-detail-subtitle", `${device.cloudName} · 实时只读状态`);
  $("#device-detail-loading").hidden = false;
  $("#device-detail-content").hidden = true;
  $("#device-detail-error").hidden = true;
  $(".device-drawer .icon-button").focus();

  try {
    const payload = await apiGet(ENDPOINTS.deviceStatus, { alias: device.alias.trim() });
    const root = payload && typeof payload === "object" ? payload : {};
    const entries = normalizeStateEntries(payload);
    const meta = $("#device-detail-meta");
    meta.replaceChildren();
    appendMeta(meta, "设备别名", device.alias);
    appendMeta(meta, "云端名称", device.cloudName);
    appendMeta(meta, "设备型号", device.model || "未知");
    appendMeta(meta, "读取时间", formatTime(root.updated_at ?? root.read_at ?? root.timestamp, "刚刚"));

    const grid = $("#device-state-grid");
    grid.replaceChildren();
    entries.forEach(([label, value]) => {
      const item = createElement("div", "state-item");
      item.append(createElement("span", "", redact(label)), createElement("strong", "", redact(value)));
      grid.append(item);
    });
    if (!entries.length) {
      const item = createElement("div", "state-item");
      item.append(createElement("span", "", "读取结果"), createElement("strong", "", "设备未返回可展示状态"));
      grid.append(item);
    }
    $("#device-detail-loading").hidden = true;
    $("#device-detail-content").hidden = false;
  } catch (error) {
    $("#device-detail-loading").hidden = true;
    $("#device-detail-error").hidden = false;
    setText("#device-detail-error-message", getErrorMessage(error, "无法读取设备状态，请稍后重试。"));
  }
}

function normalizeScenes(payload) {
  const root = payload && typeof payload === "object" ? payload : {};
  const raw = Array.isArray(root) ? root : Array.isArray(root.scenes) ? root.scenes : Array.isArray(root.items) ? root.items : [];
  const summary = state.status.summary && typeof state.status.summary === "object" ? state.status.summary : {};
  state.sceneCacheTime = text(
    root.cache_updated_at
    ?? root.scene_cache_updated_at
    ?? root.updated_at
    ?? summary.scene_cache_updated_at,
  ).trim();
  return raw.map((item, index) => ({
    id: text(item && (item.scene_id ?? item.id), `scene-${index + 1}`),
    name: text(item && (item.scene_name ?? item.name), "未命名场景"),
    homeName: text(item && (item.home_name ?? item.home), "未知家庭"),
    homeId: text(item && item.home_id).trim(),
  }));
}

async function loadScenes(sync = false) {
  const payload = sync
    ? await apiPost(ENDPOINTS.scenesSync, {})
    : await apiGet(ENDPOINTS.scenes);
  state.scenes = normalizeScenes(payload);
  state.loaded.scenes = true;
  renderScenes();
  renderOverviewMetrics();
  return state.scenes;
}

function renderScenes() {
  const list = $("#scene-list");
  list.replaceChildren();
  state.scenes.forEach((scene) => {
    const item = createElement("div", "scene-item");
    const symbol = createElement("span", "scene-symbol");
    symbol.append(createIcon(ICON_PATHS.scene));
    const copy = createElement("div");
    copy.append(createElement("strong", "", scene.name));
    const home = scene.homeId ? `${scene.homeName} · ${scene.homeId}` : scene.homeName;
    copy.append(createElement("span", "", `${home} · scene_id ${scene.id}`));
    item.append(symbol, copy);
    list.append(item);
  });
  $("#scene-empty").hidden = state.scenes.length > 0;
  setText("#scene-cache-time", state.sceneCacheTime ? `更新于 ${formatTime(state.sceneCacheTime)}` : "尚未同步");
}

async function syncScenes() {
  const button = $("#sync-scenes");
  setButtonLoading(button, true);
  try {
    await loadScenes(true);
    showToast("场景目录已同步", `已缓存 ${state.scenes.length} 个场景`);
  } catch (error) {
    showToast("场景同步失败", getErrorMessage(error), "error");
  } finally {
    setButtonLoading(button, false);
  }
}

function normalizeTools(payload) {
  const root = payload && typeof payload === "object" ? payload : {};
  const scene = root.scene_tool && typeof root.scene_tool === "object" ? root.scene_tool : {};
  const control = root.control_tool && typeof root.control_tool === "object" ? root.control_tool : {};
  const allowed = Array.isArray(control.allowed_devices)
    ? Array.from(new Set(control.allowed_devices.map((item) => text(item).trim()).filter(Boolean)))
    : [];
  return {
    enable_readonly_tool: bool(root.enable_readonly_tool, false),
    scene_tool: {
      enable: bool(scene.enable ?? root.enable_scene_tool, false),
      admin_only: bool(scene.admin_only ?? root.scene_tool_admin_only, true),
    },
    control_tool: {
      enable: bool(control.enable, false),
      admin_only: bool(control.admin_only, true),
      allowed_devices: allowed,
    },
  };
}

function serializedTools(tools = state.tools) {
  return JSON.stringify({
    enable_readonly_tool: Boolean(tools.enable_readonly_tool),
    scene_tool: {
      enable: Boolean(tools.scene_tool.enable),
      admin_only: Boolean(tools.scene_tool.admin_only),
    },
    control_tool: {
      enable: Boolean(tools.control_tool.enable),
      admin_only: Boolean(tools.control_tool.admin_only),
      allowed_devices: [...tools.control_tool.allowed_devices].sort((a, b) => a.localeCompare(b, "zh-CN")),
    },
  });
}

async function loadTools() {
  const payload = await apiGet(ENDPOINTS.tools);
  state.tools = normalizeTools(payload);
  state.loaded.tools = true;
  state.originalTools = serializedTools();
  renderTools();
  return state.tools;
}

function readToolsFromForm() {
  const allowedDevices = $$("[data-control-alias]:checked")
    .map((input) => text(input.dataset.controlAlias).trim())
    .filter(Boolean);
  state.tools = {
    enable_readonly_tool: $("#tool-readonly").checked,
    scene_tool: {
      enable: $("#tool-scenes").checked,
      admin_only: $("#tool-admin-only").checked,
    },
    control_tool: {
      enable: $("#tool-control").checked,
      admin_only: $("#tool-control-admin-only").checked,
      allowed_devices: Array.from(new Set(allowedDevices)),
    },
  };
  updateToolState();
}

function renderTools() {
  $("#tool-readonly").checked = Boolean(state.tools.enable_readonly_tool);
  $("#tool-scenes").checked = Boolean(state.tools.scene_tool.enable);
  $("#tool-admin-only").checked = Boolean(state.tools.scene_tool.admin_only);
  $("#tool-control").checked = Boolean(state.tools.control_tool.enable);
  $("#tool-control-admin-only").checked = Boolean(state.tools.control_tool.admin_only);
  renderControlAllowlist();
  updateToolState();
}

function availableControlAliases() {
  const aliases = [];
  state.devices.forEach((device) => {
    [device.alias, ...device.legacyMappings.map((mapping) => mapping.alias)].forEach((rawAlias) => {
      const alias = text(rawAlias).trim();
      if (alias && !aliases.includes(alias)) aliases.push(alias);
    });
  });
  return aliases.sort((a, b) => a.localeCompare(b, "zh-CN"));
}

function renderControlAllowlist() {
  const container = $("#control-allowlist");
  if (!container) return;
  const selected = new Set(state.tools.control_tool.allowed_devices);
  const available = availableControlAliases();
  const allAliases = [...available];
  selected.forEach((alias) => {
    if (!allAliases.includes(alias)) allAliases.push(alias);
  });
  allAliases.sort((a, b) => a.localeCompare(b, "zh-CN"));
  container.replaceChildren();

  allAliases.forEach((alias) => {
    const mapped = available.includes(alias);
    const label = createElement("label", `allowlist-option${mapped ? "" : " is-stale"}`);
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = selected.has(alias);
    input.dataset.controlAlias = alias;
    const marker = createElement("span", "allowlist-check");
    const copy = createElement("span", "allowlist-name", alias);
    if (!mapped) copy.append(createElement("small", "", "映射已失效，请取消勾选"));
    label.append(input, marker, copy);
    container.append(label);
  });

  $("#control-allowlist-empty").hidden = allAliases.length > 0;
  setText("#control-allowlist-count", `${selected.size} 台`);
}

function updateToolState() {
  const dirty = serializedTools() !== state.originalTools;
  const sceneRisky = state.tools.scene_tool.enable && !state.tools.scene_tool.admin_only;
  const controlRisky = state.tools.control_tool.enable && !state.tools.control_tool.admin_only;
  $("#save-tools").disabled = !dirty;
  $("#tool-risk-note").hidden = !sceneRisky;
  $("#control-risk-note").hidden = !controlRisky;
  $("#control-allowlist-panel").classList.toggle("is-active", state.tools.control_tool.enable);
  setText("#control-allowlist-count", `${state.tools.control_tool.allowed_devices.length} 台`);
  const badge = $("#tool-save-state");
  badge.className = `status-badge is-${dirty ? "warning" : "neutral"}`;
  badge.textContent = dirty ? "有未保存修改" : "已保存";
}

async function saveTools() {
  const sceneRisky = state.tools.scene_tool.enable && !state.tools.scene_tool.admin_only;
  const controlEnabled = state.tools.control_tool.enable;
  const controlRisky = controlEnabled && !state.tools.control_tool.admin_only;
  const risky = sceneRisky || controlEnabled;
  const summary = [
    `设备只读 Tool：${state.tools.enable_readonly_tool ? "开启" : "关闭"}`,
    `场景 LLM Tool：${state.tools.scene_tool.enable ? "开启" : "关闭"}`,
    `场景 Tool 权限：${state.tools.scene_tool.admin_only ? "仅管理员" : "所有可调用用户"}`,
    `设备控制 Tool：${controlEnabled ? "开启" : "关闭"}`,
    `设备控制权限：${state.tools.control_tool.admin_only ? "仅 AstrBot 管理员" : "所有可调用用户"}`,
    `设备控制白名单：${state.tools.control_tool.allowed_devices.length
      ? state.tools.control_tool.allowed_devices.join("、")
      : "未配置"}`,
  ];
  if (sceneRisky) summary.push("风险提示：当前场景 Tool 未限制为仅管理员调用");
  if (controlRisky) summary.push("高风险提示：当前设备控制 Tool 未限制为仅管理员调用");

  const confirmed = await openDialog({
    title: controlEnabled ? "确认启用设备控制 Tool？" : sceneRisky ? "确认开放场景 Tool？" : "保存 Tool 权限？",
    message: controlEnabled
      ? "设备控制 Tool 可以改变真实家居状态。请确认管理员权限和设备白名单均符合预期。"
      : sceneRisky
        ? "场景可能触发真实家居动作。允许普通用户调用会增加误操作风险，请再次确认。"
      : "权限修改会立即影响大模型可以使用的米家能力。",
    summary,
    confirmLabel: risky ? "理解风险并保存" : "确认保存",
    danger: risky,
  });
  if (!confirmed) return;

  const button = $("#save-tools");
  setButtonLoading(button, true);
  try {
    const saved = await apiPost(ENDPOINTS.tools, {
      ...state.tools,
      ...(sceneRisky ? { confirm_public_scene_tool: true } : {}),
      ...(controlEnabled ? { confirm_control_tool: true } : {}),
      ...(controlRisky ? { confirm_public_control_tool: true } : {}),
    });
    state.tools = normalizeTools(saved);
    state.originalTools = serializedTools();
    renderTools();
    showToast("Tool 权限已保存", "新的权限设置已写入插件配置并生效");
  } catch (error) {
    showToast("Tool 权限保存失败", getErrorMessage(error), "error");
  } finally {
    setButtonLoading(button, false);
  }
}

function defaultChecks() {
  const account = state.status.auth && typeof state.status.auth === "object"
    ? state.status.auth
    : state.status.account && typeof state.status.account === "object"
      ? state.status.account
      : state.status;
  return [
    {
      key: "account",
      title: "米家账号",
      status: isLoggedIn() ? "ok" : account.last_login_error ? "error" : "warn",
      message: isLoggedIn() ? "登录凭证当前可用" : redact(account.last_login_error || "尚未完成扫码授权"),
      icon: "cloud",
    },
    {
      key: "devices",
      title: "设备映射",
      status: state.devices.some((device) => device.alias.trim()) ? "ok" : "warn",
      message: state.devices.length
        ? `${state.devices.filter((device) => device.alias.trim()).length} / ${state.devices.length} 台已映射`
        : "尚未同步云端设备",
      icon: "mapping",
    },
    {
      key: "scenes",
      title: "场景缓存",
      status: state.scenes.length ? "ok" : "warn",
      message: state.scenes.length ? `${state.scenes.length} 个场景已缓存` : "尚无场景缓存",
      icon: "cache",
    },
    {
      key: "network",
      title: "云端连接",
      status: account.last_login_error ? "error" : isLoggedIn() ? "ok" : "warn",
      message: account.last_login_error ? redact(account.last_login_error) : isLoggedIn() ? "未发现云端连接异常" : "登录后可检查云端连接",
      icon: "network",
    },
  ];
}

function diagnosticStatus(value, explicitOk) {
  const status = text(value).toLowerCase();
  if (status === "ok" || status === "success") return "ok";
  if (status === "warn" || status === "warning" || status === "info") return "warn";
  if (status === "error" || status === "failed" || status === "failure") return "error";
  return bool(explicitOk, false) ? "ok" : "warn";
}

function normalizeDiagnostics(payload) {
  const root = payload && typeof payload === "object" ? payload : {};
  let checks = [];
  if (Array.isArray(root.checks)) {
    checks = root.checks.map((item, index) => ({
      key: text(item.key ?? item.code, `check-${index}`),
      title: text(item.title ?? item.name, `检查项 ${index + 1}`),
      status: diagnosticStatus(item.status ?? item.level, item.ok),
      message: redact(item.message ?? item.detail ?? ""),
      icon: text(item.icon, "check"),
    }));
  } else if (root.checks && typeof root.checks === "object") {
    checks = Object.entries(root.checks).map(([key, item]) => ({
      key,
      title: text(item && (item.title ?? item.name), key),
      status: diagnosticStatus(item && (item.status ?? item.level), item && item.ok),
      message: redact(item && (item.message ?? item.detail)),
      icon: text(item && item.icon, "check"),
    }));
  }
  if (!checks.length) checks = defaultChecks();

  const issues = Array.isArray(root.issues)
    ? root.issues.map((item) => ({
      title: redact(item && typeof item === "object" ? item.title ?? item.type : "运行异常"),
      message: redact(item && typeof item === "object" ? item.message ?? item.detail : item),
    })).filter((item) => item.message)
    : [];

  const account = state.status.auth && typeof state.status.auth === "object"
    ? state.status.auth
    : state.status.account && typeof state.status.account === "object"
      ? state.status.account
      : state.status;
  [
    ["登录异常", account.last_login_error],
    ["共享设备异常", account.last_shared_error],
    ["场景异常", account.last_scene_error],
  ].forEach(([title, message]) => {
    if (message && !issues.some((issue) => issue.message === redact(message))) {
      issues.push({ title, message: redact(message) });
    }
  });
  checks.filter((check) => check.status === "warn" || check.status === "error").forEach((check) => {
    if (check.message && !issues.some((issue) => issue.message === check.message)) {
      issues.push({ title: check.title, message: check.message });
    }
  });

  const explicitScore = Number(root.score ?? root.health_score);
  const calculated = checks.length
    ? Math.round(checks.reduce((sum, check) => sum + (check.status === "ok" ? 1 : check.status === "warn" ? .55 : 0), 0) / checks.length * 100)
    : 0;
  return {
    checks,
    issues,
    score: Number.isFinite(explicitScore) ? Math.max(0, Math.min(100, explicitScore)) : calculated,
    checkedAt: text(root.checked_at ?? root.updated_at).trim(),
    message: redact(
      root.message
      ?? (root.summary && typeof root.summary === "object" ? root.summary.message : root.summary)
      ?? "",
    ),
  };
}

async function loadDiagnostics(run = false) {
  const payload = run
    ? await apiPost(ENDPOINTS.diagnosticsCheck, {})
    : await apiGet(ENDPOINTS.diagnostics);
  state.diagnostics = normalizeDiagnostics(payload);
  state.loaded.diagnostics = true;
  renderDiagnostics();
  return state.diagnostics;
}

function renderDiagnostics() {
  const diagnosis = state.diagnostics.checks ? state.diagnostics : normalizeDiagnostics({});
  const score = diagnosis.score;
  setText("#health-score", score ? String(Math.round(score)) : "—");
  setText("#health-title", score >= 85 ? "运行状态良好" : score >= 60 ? "有项目需要关注" : score ? "发现需要处理的问题" : "等待检查");
  setText(
    "#health-description",
    diagnosis.message || (
      score >= 85
        ? "关键组件目前没有发现明显异常。"
        : score
          ? "请查看下方检查项与最近异常。"
          : "运行一次健康检查，确认关键组件是否可用。"
    ),
  );
  setText("#diagnostic-time", diagnosis.checkedAt ? `检查于 ${formatTime(diagnosis.checkedAt)}` : "尚未运行");

  const progress = $(".health-progress");
  const circumference = 119.38;
  progress.style.strokeDashoffset = String(circumference * (1 - score / 100));
  progress.style.stroke = score >= 85 ? "var(--green)" : score >= 60 ? "var(--amber)" : "var(--red)";

  const grid = $("#diagnostic-grid");
  grid.replaceChildren();
  diagnosis.checks.forEach((check) => {
    const card = createElement("article", `check-card is-${check.status}`);
    const head = createElement("div", "check-head");
    const icon = createElement("span", "check-icon");
    icon.append(createIcon(ICON_PATHS[check.icon] || ICON_PATHS.check));
    head.append(icon, createElement("span", "check-mark"));
    card.append(head, createElement("strong", "", check.title), createElement("p", "", check.message || "未返回详细信息"));
    grid.append(card);
  });

  const issues = $("#issue-list");
  issues.replaceChildren();
  if (!diagnosis.issues.length) {
    issues.append(createElement("div", "issue-empty", "暂无异常记录"));
  } else {
    diagnosis.issues.forEach((issue) => {
      const row = createElement("div", "issue-item");
      row.append(createElement("strong", "", issue.title || "运行异常"), createElement("span", "", issue.message));
      issues.append(row);
    });
  }

  setText("#metric-health", score >= 85 ? "良好" : score >= 60 ? "需关注" : score ? "有异常" : "待检查");
  setText("#metric-health-note", score ? `健康评分 ${Math.round(score)}` : "可运行健康检查");
  $("#health-ring").dataset.score = String(score);
}

async function runDiagnostics() {
  const button = $("#run-diagnostics");
  setButtonLoading(button, true);
  try {
    await loadDiagnostics(true);
    const issueCount = state.diagnostics.issues.length;
    showToast(
      "健康检查已完成",
      issueCount ? `发现 ${issueCount} 项需要关注的信息` : "未发现明显异常",
      issueCount ? "warning" : "success",
    );
  } catch (error) {
    showToast("健康检查失败", getErrorMessage(error), "error");
  } finally {
    setButtonLoading(button, false);
  }
}

function openDialog(options) {
  if (state.dialogResolver) state.dialogResolver(false);
  const layer = $("#app-dialog");
  const summary = $("#dialog-summary");
  const icon = $("#dialog-icon");
  const confirm = $("#dialog-confirm");
  const cancel = $("#dialog-cancel");

  setText("#dialog-title", options.title || "确认操作");
  setText("#dialog-message", options.message || "");
  confirm.textContent = options.confirmLabel || "确认";
  cancel.textContent = options.cancelLabel || "取消";
  icon.classList.toggle("is-danger", Boolean(options.danger));
  confirm.className = `button button-primary${options.danger ? " is-danger" : ""}`;

  summary.replaceChildren();
  const items = Array.isArray(options.summary) ? options.summary : [];
  items.forEach((item) => summary.append(createElement("li", "", redact(item))));
  summary.hidden = items.length === 0;

  state.previousFocus = document.activeElement;
  layer.hidden = false;
  document.body.style.overflow = "hidden";
  window.setTimeout(() => confirm.focus(), 0);
  return new Promise((resolve) => {
    state.dialogResolver = resolve;
  });
}

function closeDialog(result) {
  const layer = $("#app-dialog");
  if (layer.hidden) return;
  layer.hidden = true;
  document.body.style.overflow = "";
  const resolver = state.dialogResolver;
  state.dialogResolver = null;
  if (resolver) resolver(Boolean(result));
  if (state.previousFocus && typeof state.previousFocus.focus === "function") {
    state.previousFocus.focus();
  }
}

function bindEvents() {
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.view)));
  $$("[data-go-view]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.goView)));
  $("#hero-login").addEventListener("click", startLogin);
  $("#account-login").addEventListener("click", startLogin);
  $("#account-logout").addEventListener("click", logout);
  $("#sync-devices").addEventListener("click", syncDevices);
  $("#sync-scenes").addEventListener("click", syncScenes);
  $("#review-mappings").addEventListener("click", reviewAndSaveMappings);
  $("#save-device-mappings").addEventListener("click", reviewAndSaveMappings);
  $("#reset-mappings").addEventListener("click", resetMappings);
  $("#save-tools").addEventListener("click", saveTools);
  $("#run-diagnostics").addEventListener("click", runDiagnostics);
  $("#device-search").addEventListener("input", renderDevices);
  $("#device-filter").addEventListener("change", renderDevices);
  $("#tool-readonly").addEventListener("change", readToolsFromForm);
  $("#tool-scenes").addEventListener("change", readToolsFromForm);
  $("#tool-admin-only").addEventListener("change", readToolsFromForm);
  $("#tool-control").addEventListener("change", readToolsFromForm);
  $("#tool-control-admin-only").addEventListener("change", readToolsFromForm);
  $("#control-allowlist").addEventListener("change", (event) => {
    if (event.target.matches("[data-control-alias]")) readToolsFromForm();
  });

  $("#device-list").addEventListener("input", (event) => {
    const input = event.target.closest("[data-device-alias]");
    if (!input) return;
    const device = findDevice(input.dataset.deviceAlias);
    if (device) device.alias = input.value;
    updateDeviceValidation();
  });
  $("#device-list").addEventListener("change", (event) => {
    const select = event.target.closest("[data-device-category]");
    if (!select) return;
    const device = findDevice(select.dataset.deviceCategory);
    if (device) device.category = select.value;
    updateMappingDock();
  });
  $("#device-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-view-device-status]");
    if (!button) return;
    showDeviceStatus(findDevice(button.dataset.viewDeviceStatus));
  });
  $$("[data-close-device-detail]").forEach((button) => button.addEventListener("click", closeDeviceDetail));

  $("#dialog-cancel").addEventListener("click", () => closeDialog(false));
  $("#dialog-backdrop").addEventListener("click", () => closeDialog(false));
  $("#dialog-confirm").addEventListener("click", () => closeDialog(true));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!$("#app-dialog").hidden) closeDialog(false);
    else if (!$("#device-detail-layer").hidden) closeDeviceDetail();
  });

  $("#refresh-current").addEventListener("click", async () => {
    const button = $("#refresh-current");
    if (state.currentView === "devices" && getMappingChanges().length) {
      const discard = await openDialog({
        title: "放弃未保存的设备修改？",
        message: "刷新会重新读取后端配置，当前尚未保存的别名与类别修改将被丢弃。",
        summary: mappingSummary(getMappingChanges()),
        confirmLabel: "放弃修改并刷新",
        danger: true,
      });
      if (!discard) return;
    }
    if (state.currentView === "scenes" && serializedTools() !== state.originalTools) {
      const discard = await openDialog({
        title: "放弃未保存的权限修改？",
        message: "刷新会重新读取 Tool 权限，当前修改将被丢弃。",
        confirmLabel: "放弃修改并刷新",
        danger: true,
      });
      if (!discard) return;
    }
    button.classList.add("is-loading");
    button.disabled = true;
    try {
      await loadView(state.currentView, true);
      showToast("页面已刷新");
    } catch (error) {
      showToast("刷新失败", getErrorMessage(error), "error");
    } finally {
      button.classList.remove("is-loading");
      button.disabled = false;
    }
  });
}

async function initialize() {
  bindEvents();
  applyTheme({});

  if (!bridge) {
    setConnection(false, "Bridge 不可用");
    setTopStatus("danger", "无法连接");
    showToast("无法连接 AstrBot", "请从 AstrBot 插件详情页打开此页面", "error", 7000);
    return;
  }

  try {
    const initialContext = typeof bridge.ready === "function"
      ? await bridge.ready()
      : (typeof bridge.getContext === "function" ? bridge.getContext() : {});
    applyTheme(initialContext || {});
    if (typeof bridge.onContext === "function") {
      bridge.onContext((context) => applyTheme(context || {}));
    }
    setConnection(true, "已连接 AstrBot");
    await Promise.allSettled([
      loadStatus(),
      loadDevices(false),
      loadScenes(false),
      loadTools(),
      loadDiagnostics(false),
    ]);
    renderOverviewMetrics();
  } catch (error) {
    setConnection(false, "后端连接失败");
    setTopStatus("danger", "连接失败");
    showToast("插件页面初始化失败", getErrorMessage(error), "error", 7000);
  }
}

initialize();
