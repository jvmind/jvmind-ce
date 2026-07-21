import { state } from "./state.js";
import { app } from "./app.js";
import { escapeHtml, fmtDate, i18nText } from "./shared.js";
import { api } from "./api.js";
import { t, th } from "../i18n/index.js";
import { ico } from './icons.js';

export function resetSessionView() {
  state.currentSessionId = null;
  state.sessions = [];
  stopSessionUpdatePolling();
  hideSessionUpdateBanner();
  const chatArea = document.getElementById("chatArea");
  if (chatArea) chatArea.innerHTML = '<div class="empty"><h2>' + th("chat.start_title") + '</h2><div>' + t("chat.start_desc") + '</div></div>';
  const sessionList = document.getElementById("sessionList");
  if (sessionList) sessionList.innerHTML = "";
  const factsList = document.getElementById("factsList");
  if (factsList) factsList.innerHTML = "";
  const currentTitle = document.getElementById("currentTitle");
  if (currentTitle) currentTitle.textContent = "";
  state.currentReport = null;
  state.currentReportId = null;
  state.currentJstackReport = null;
  state.currentJstackReportId = null;
  state.currentHeapdumpReport = null;
  state.currentHeapdumpReportId = null;
  state.openGcReports = [];
  state.openJstackReports = [];
  state.openHeapdumpReports = [];
  state.allReports = [];
  const gcReportArea = document.getElementById("gcReportArea");
  if (gcReportArea) gcReportArea.style.display = "none";
  const jstackReportArea = document.getElementById("jstackReportArea");
  if (jstackReportArea) jstackReportArea.style.display = "none";
  const heapdumpReportArea = document.getElementById("heapdumpReportArea");
  if (heapdumpReportArea) heapdumpReportArea.style.display = "none";
  const allReportsList = document.getElementById("allReportsList");
  if (allReportsList) allReportsList.innerHTML = "";
  const allReportsEmpty = document.getElementById("allReportsEmpty");
  if (allReportsEmpty) allReportsEmpty.style.display = "";
  ["gcTabCount", "jstackTabCount", "heapdumpTabCount", "reportsTabCount"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = "";
  });
  const badge = document.getElementById("gcBadge");
  if (badge) badge.style.display = "none";
  if (typeof app.clearActiveReportContext === "function") app.clearActiveReportContext();
}

// 团队会话 Tab 切换
document.querySelectorAll(".session-tab").forEach(tab => {
  tab.onclick = () => {
    const nextTab = tab.dataset.tab;
    if (state.sessionTab !== nextTab) {
      state.sessionTab = nextTab;
      resetSessionView();
      app.updateBadge();
      app.refreshAllReportHistory();
    }
    document.querySelectorAll(".session-tab").forEach(t => {
      t.style.borderBottomColor = "transparent";
      t.style.color = "var(--text-dim)";
      t.style.fontWeight = "400";
    });
    tab.style.borderBottomColor = "var(--primary)";
    tab.style.color = "var(--text)";
    tab.style.fontWeight = "600";
    loadSessions();
  };
});
// apiWithAuth 是 api.js 中 api() 的别名（401 处理已由 auth.js 的 setAuthFailureHandler 统一注册）。
// 保留导出名以兼容历史调用方。
export const apiWithAuth = api;

// ---------- 团队会话更新提示（轮询 meta，约 20s）----------
const SESSION_POLL_INTERVAL_MS = 20000;
let _sessionPollTimer = null;
let _sessionBaselineUpdatedAt = "";
let _pendingUpdate = false;

function hideSessionUpdateBanner() {
  _pendingUpdate = false;
  const banner = document.getElementById("sessionUpdateBanner");
  if (banner) banner.style.display = "none";
}

function showSessionUpdateBanner() {
  const banner = document.getElementById("sessionUpdateBanner");
  if (!banner) return;
  // 流式输出中或输入框有草稿时不打断，标记待显示，待空闲再弹
  const draft = (document.getElementById("msg") || {}).value || "";
  if (state.isStreaming || draft.trim()) {
    _pendingUpdate = true;
    return;
  }
  banner.style.display = "flex";
}

export function stopSessionUpdatePolling() {
  if (_sessionPollTimer) {
    clearInterval(_sessionPollTimer);
    _sessionPollTimer = null;
  }
}

// 当前用户自己发消息后会 bump updated_at，需刷新基线避免误触发提示
export async function refreshSessionUpdateBaseline() {
  if (!state.currentSessionId) return;
  try {
    const meta = await api(`/api/sessions/${state.currentSessionId}/meta`);
    if (meta && meta.updated_at) _sessionBaselineUpdatedAt = meta.updated_at;
    hideSessionUpdateBanner();
  } catch (e) {
    // 忽略，下一周期轮询会自然纠正
  }
}

export function startSessionUpdatePolling(sid) {
  stopSessionUpdatePolling();
  _sessionPollTimer = setInterval(async () => {
    if (state.currentSessionId !== sid) { stopSessionUpdatePolling(); return; }
    // 若有待显示更新且此刻已空闲，补弹提示
    if (_pendingUpdate) { showSessionUpdateBanner(); }
    try {
      const meta = await api(`/api/sessions/${sid}/meta`);
      if (state.currentSessionId !== sid) return;
      if (meta.updated_at && _sessionBaselineUpdatedAt && meta.updated_at > _sessionBaselineUpdatedAt) {
        showSessionUpdateBanner();
      }
    } catch (e) {
      // 轮询失败静默忽略，下一周期重试
    }
  }, SESSION_POLL_INTERVAL_MS);
}

// ---------- 状态检测 ----------
export async function checkHealth() {
  document.getElementById("statusText").textContent = t("sidebar.connecting");
  try {
    const h = await apiWithAuth("/api/health");
    state.agentReady = true;
    const dot = document.getElementById("statusDot");
    dot.classList.remove("ok", "err");
    dot.classList.add("ok");
    document.getElementById("statusText").textContent = t("sidebar.status_connected");
    return true;
  } catch (e) {
    document.getElementById("statusDot").classList.add("err");
    document.getElementById("statusText").textContent = t("sidebar.status_disconnected");
    return false;
  }
}

// ---------- 会话 ----------
// 当前用户在当前组织内的删除权限（owner 或被授权 can_delete_sessions）
let _myOrgCanDeleteOthers = false;

async function _refreshMyOrgDeletePermission() {
  _myOrgCanDeleteOthers = false;
  if (!(state.sessionTab === "org" && state.currentOrg)) return;
  try {
    const det = await api(`/api/orgs/${state.currentOrg.id}`);
    const myId = state.currentUser && state.currentUser.id;
    const me = (det.members || []).find(m => m.user_id === myId);
    if (me && (me.role === "owner" || me.can_delete_sessions)) {
      _myOrgCanDeleteOthers = true;
    }
  } catch (e) {
    // 取不到权限时按最保守处理（仅能删自己创建的）
  }
}

export async function loadSessions() {
  if (!state.agentReady) {
    document.getElementById("sessionList").innerHTML =
      '<div style="color:var(--text-dim);font-size:12px;padding:12px;text-align:center;">' + t("config.no_model") + '</div>';
    return;
  }
  try {
    const url = state.sessionTab === "org" && state.currentOrg ? `/api/orgs/${state.currentOrg.id}/sessions` : "/api/sessions?personal=true";
    const r = await api(url);
    state.sessions = r.sessions || [];
    await _refreshMyOrgDeletePermission();
    if (state.currentSessionId && !state.sessions.some(s => s.id === state.currentSessionId)) {
      state.currentSessionId = null;
    }
    renderSessionList();
    if (!state.currentSessionId && state.sessions.length) {
      app.selectSession(state.sessions[0].id);
    } else if (state.sessions.length === 0) {
      const newSessionBtn = document.getElementById("newSessionBtn");
      if (newSessionBtn && newSessionBtn.disabled) {
        document.getElementById("sessionList").innerHTML = '<div style="color:var(--orange);font-size:12px;padding:12px;text-align:center;">' + t("sidebar.sessions_full") + '</div>';
      } else {
        await newSession();
      }
    }
  } catch (e) {
    console.error("loadSessions failed:", e);
  }
}

function _setActiveSessionClass(activeId) {
  const el = document.getElementById("sessionList");
  if (!el) return;
  for (const child of el.children) {
    const id = child.dataset.id;
    if (!id) continue;
    child.classList.toggle("active", id === activeId);
  }
}

export function renderSessionList() {
  const el = document.getElementById("sessionList");
  el.innerHTML = "";
  for (const s of state.sessions) {
    const item = document.createElement("div");
    item.className = "session-item" + (s.id === state.currentSessionId ? " active" : "");
    item.dataset.id = s.id;
    item.innerHTML = `
      <div class="title">${escapeHtml(s.title)}</div>
      <div class="meta">${t("sidebar.session_meta", { count: s.msg_count, time: fmtDate(s.updated_at) })}</div>
    `;
    item.onclick = () => app.selectSession(s.id);
    // 删除按钮：团队会话仅创建者、owner 或被授权成员可见
    const myId = state.currentUser && state.currentUser.id;
    const isOrgSession = state.sessionTab === "org" && s.org_id;
    const canDelete = !isOrgSession || s.user_id === myId || _myOrgCanDeleteOthers;
    if (canDelete) {
      const del = document.createElement("button");
      del.className = "session-del";
      del.innerHTML = ico('x');
      del.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm(t("chat.delete_session_confirm", { title: escapeHtml(s.title) }))) return;
        try {
          await api("/api/sessions/" + s.id, { method: "DELETE" });
        } catch (err) {
          alert(i18nText(err.message || err));
          return;
        }
        if (state.currentSessionId === s.id) {
          state.currentSessionId = null;
          document.getElementById("chatArea").innerHTML = '<div class="empty"><h2>' + th("chat.start_title") + '</h2><div>' + t("chat.start_desc") + '</div></div>';
        }
        await loadSessions();
        await app.updateQuotaUI();
      };
      item.appendChild(del);
    }
    el.appendChild(item);
  }
}

export async function selectSession(sid) {
  if (state.isStreaming) return;
  const prevSessionId = state.currentSessionId;
  state.currentSessionId = sid;
  _setActiveSessionClass(sid);
  try {
    const data = await api(`/api/sessions/${sid}`);
    document.getElementById("currentTitle").textContent = data.title;
    // 顶部删除按钮：团队会话仅创建者、owner 或被授权成员可见
    const myId = state.currentUser && state.currentUser.id;
    const canDelete = !data.org_id || data.user_id === myId || _myOrgCanDeleteOthers;
    const delBtn = document.getElementById("deleteBtn");
    if (delBtn) delBtn.style.display = canDelete ? "" : "none";
    // 团队会话：加载成员名映射用于渲染发言人
    if (data.org_id && typeof app.setAuthorNames === "function") {
      try {
        const det = await api(`/api/orgs/${data.org_id}`);
        const names = {};
        for (const m of (det.members || [])) names[m.user_id] = m.username;
        app.setAuthorNames(names);
      } catch (e) { app.setAuthorNames({}); }
    } else if (typeof app.setAuthorNames === "function") {
      app.setAuthorNames({});
    }
    app.renderMessages(data.messages || []);
    app.renderFacts(data.facts || []);
    // 记录基线 updated_at 并按需启动团队会话更新轮询
    _sessionBaselineUpdatedAt = data.updated_at || "";
    hideSessionUpdateBanner();
    if (data.org_id) startSessionUpdatePolling(sid); else stopSessionUpdatePolling();
    // 切换会话：重置并刷新分析面板（内联于此以避免打包后 monkey-patch 时序失效）
    state.currentReport = null;
    state.currentReportId = null;
    state.currentJstackReport = null;
    state.currentJstackReportId = null;
    state.currentHeapdumpReport = null;
    state.currentHeapdumpReportId = null;
    state.openGcReports = [];
    state.openJstackReports = [];
    state.openHeapdumpReports = [];
    const gcArea = document.getElementById("gcReportArea");
    if (gcArea) {
      gcArea.style.display = "";
      gcArea.innerHTML = `<div class="report-empty-state">
        <div class="es-icon">${ico('clipboard-list')}</div>
        <div class="es-title">${escapeHtml(t("gc.empty_state_title"))}</div>
        <div class="es-hint">${escapeHtml(t("gc.empty_state_hint"))}</div>
      </div>`;
    }
    const gcErr = document.getElementById("gcError");
    if (gcErr) gcErr.style.display = "none";
    const jsArea = document.getElementById("jstackReportArea");
    if (jsArea) {
      jsArea.style.display = "";
      jsArea.innerHTML = `<div class="report-empty-state">
        <div class="es-icon">${ico('clipboard-list')}</div>
        <div class="es-title">${escapeHtml(t("jstack.empty_state_title"))}</div>
        <div class="es-hint">${escapeHtml(t("jstack.empty_state_hint"))}</div>
      </div>`;
    }
    const jsErr = document.getElementById("jstackError");
    if (jsErr) jsErr.style.display = "none";
    const hdArea = document.getElementById("heapdumpReportArea");
    if (hdArea) {
      hdArea.style.display = "";
      hdArea.innerHTML = `<div class="report-empty-state">
        <div class="es-icon">${ico('clipboard-list')}</div>
        <div class="es-title">${escapeHtml(t("heapdump.empty_state_title"))}</div>
        <div class="es-hint">${escapeHtml(t("heapdump.empty_state_hint"))}</div>
      </div>`;
    }
    if (typeof app.clearActiveReportContext === "function") app.clearActiveReportContext();
    if (typeof app.renderReportTabs === "function") { app.renderReportTabs("gc"); app.renderReportTabs("jstack"); }
    if (typeof app.refreshHistory === "function") await app.refreshHistory();
    if (typeof app.refreshJstackHistory === "function") await app.refreshJstackHistory();
    if (typeof app.refreshHeapdumpHistory === "function") await app.refreshHeapdumpHistory();
    if (typeof app.refreshAllReportHistory === "function") await app.refreshAllReportHistory();
  } catch (e) {
    state.currentSessionId = prevSessionId;
    renderSessionList();
    document.getElementById("chatArea").innerHTML = '<div class="empty"><h2>' + th("chat.start_title") + '</h2><div>' + t("chat.start_desc") + '</div></div>';
    await loadSessions();
  }
}

export async function newSession() {
  const body = {};
  if (state.sessionTab === "org" && state.currentOrg) body.org_id = state.currentOrg.id;
  try {
    const r = await api("/api/sessions", { method: "POST", body: JSON.stringify(body) });
    await loadSessions();
    app.selectSession(r.id);
    await app.updateQuotaUI();
  } catch (e) {
    alert(e.message || e);
  }
}

export async function deleteSession() {
  if (!state.currentSessionId) return;
  if (!confirm(t("chat.delete_confirm"))) return;
  try {
    await api(`/api/sessions/${state.currentSessionId}`, { method: "DELETE" });
  } catch (err) {
    alert(i18nText(err.message || err));
    return;
  }
  state.currentSessionId = null;
  state.currentReport = null;
  state.currentReportId = null;
  state.currentJstackReport = null;
  state.currentJstackReportId = null;
  state.currentHeapdumpReport = null;
  state.currentHeapdumpReportId = null;
  state.openGcReports = [];
  state.openJstackReports = [];
  state.openHeapdumpReports = [];
  const gcReportArea = document.getElementById("gcReportArea");
  if (gcReportArea) gcReportArea.style.display = "none";
  const jstackReportArea = document.getElementById("jstackReportArea");
  if (jstackReportArea) jstackReportArea.style.display = "none";
  const heapdumpReportArea = document.getElementById("heapdumpReportArea");
  if (heapdumpReportArea) heapdumpReportArea.style.display = "none";
  await loadSessions();
  if (typeof app.refreshHistory === "function") await app.refreshHistory();
  if (typeof app.refreshJstackHistory === "function") await app.refreshJstackHistory();
  if (typeof app.refreshHeapdumpHistory === "function") await app.refreshHeapdumpHistory();
  if (typeof app.refreshAllReportHistory === "function") await app.refreshAllReportHistory();
  await app.updateQuotaUI();
}

export async function clearMessages() {
  if (!state.currentSessionId) return;
  if (!confirm(t("chat.clear_confirm"))) return;
  await api(`/api/sessions/${state.currentSessionId}/clear`, { method: "POST" });
  app.renderMessages([]);
}

export async function renameSession() {
  if (!state.currentSessionId) return;
  const title = prompt(t("chat.rename_prompt"));
  if (!title) return;
  await api(`/api/sessions/${state.currentSessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
  await loadSessions();
}
document.getElementById("newSessionBtn").onclick = newSession;
document.getElementById("refreshBtn").onclick = async () => {
  await loadSessions();
  await app.updateQuotaUI();
};
const _sessionUpdateBanner = document.getElementById("sessionUpdateBanner");
if (_sessionUpdateBanner) {
  _sessionUpdateBanner.onclick = async () => {
    hideSessionUpdateBanner();
    if (state.currentSessionId) await selectSession(state.currentSessionId);
  };
}
document.getElementById("deleteBtn").onclick = deleteSession;
document.getElementById("clearBtn").onclick = clearMessages;
document.getElementById("renameBtn").onclick = renameSession;
document.getElementById("sidebarToggle").onclick = () => {
  document.body.classList.toggle("sidebar-collapsed");
  const collapsed = document.body.classList.contains("sidebar-collapsed");
  document.getElementById("sidebarToggle").textContent = collapsed ? "▶" : "◀";
  document.getElementById("sidebarToggle").title = collapsed ? t("sidebar.expand") : t("sidebar.toggle");
};

Object.assign(app, { resetSessionView, apiWithAuth, checkHealth, loadSessions, renderSessionList, selectSession, newSession, deleteSession, clearMessages, renameSession, refreshSessionUpdateBaseline });
