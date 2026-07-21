import { t, th, setLang, getLang } from "../i18n/index.js";
import { escapeHtml, detectReportMode, consumeLoginRedirect, consumePendingInvite } from "./shared.js";
import { openConfig, setOnSaved } from "./config-dialog.js";
import { drawJstackCharts, drawCombinedChart } from "./charts.js";
import { api } from "./api.js";
import { state } from "./state.js";
import { app } from "./app.js";
import { ico, initIcoIcons } from './icons.js';

window.t = t;
window.setLang = setLang;
window.getLang = getLang;
// Expose state for browser-console debugging (侧栏圆点排查用)
window._state = state;

// Feature modules self-register their functions onto `app`.
// Order matters: sessions must load before gc-analysis so that gc's
// selectSession override runs after sessions registers app.selectSession.
import { selectSession } from "./sessions.js";
import "./sessions.js";
import "./messages.js";
import "./gc-analysis.js";
import "./jstack-analysis.js";
import { initHeapdump, refreshHeapdumpHistory, openHeapdumpReport, enableHeapdumpSidebar, renderHeapdumpSidebar, toggleHeapdumpSidebar } from "./heapdump-analysis/index.js";
window.openHeapdumpReport = openHeapdumpReport;
app.refreshHeapdumpHistory = refreshHeapdumpHistory;
app.enableHeapdumpSidebar = enableHeapdumpSidebar;
app.renderHeapdumpSidebar = renderHeapdumpSidebar;
app.toggleHeapdumpSidebar = toggleHeapdumpSidebar;
window.switchToChatTab = function switchToChatTab() {
  const panel = document.getElementById("gcPanel");
  if (panel) panel.classList.remove("open");
  const input = document.getElementById("msg");
  if (input) { input.focus(); }
};

app.enterReportOnlyMode = enterReportOnlyMode;
app.initApp = initApp;
app.processPendingInvite = processPendingInvite;
app.applyI18n = applyI18n;
app.selectSession = selectSession;

// ---------- 主题 ----------
(function initTheme() {
  const saved = localStorage.getItem("theme") || "dark";
  if (saved === "light") {
    document.documentElement.setAttribute("data-theme", "light");
    document.getElementById("sbThemeToggle").innerHTML = ico('sun');
  }
})();

  document.getElementById("sbThemeToggle").onclick = () => {
  const isLight = document.documentElement.getAttribute("data-theme") === "light";
  document.documentElement.setAttribute("data-theme", isLight ? "" : "light");
  localStorage.setItem("theme", isLight ? "dark" : "light");
  document.getElementById("sbThemeToggle").innerHTML = isLight ? ico('moon') : ico('sun');
  // 重建图表适配新主题
  setTimeout(() => {
    if (typeof drawJstackCharts === 'function' && state.currentJstackReport) {
      drawJstackCharts(state.currentJstackReport.stats);
    }
    if (typeof drawCombinedChart === 'function' && state.currentReport) {
      const s = state.currentReport.stats;
      drawCombinedChart(s.series, s.heap_max_mb, s.start_epoch_ms);
    }
  }, 80);
};
// ============ 模型配置对话框（逻辑已迁移到 config-dialog.js） ============
document.getElementById("sbConfigBtn").onclick = () => openConfig(state.currentUser, api);
setOnSaved(async (data) => {
  state.llmConfigured = true;
  await app.updateConfigPrompt();
  if (!data.agent_ready) {
    alert(t("config.agent_not_ready", { msg: data.init_error }));
  }
  await app.checkHealth();
  if (state.agentReady) await app.loadSessions();
  const badge = document.getElementById("modelLabel");
  if (badge && data.config) {
    const cfg = data.config;
    const model = cfg.openai_model || "";
    badge.onclick = null;
    badge.style.cursor = "";
    if (model) {
      const fromUrl = cfg.openai_base_url ? new URL(cfg.openai_base_url).hostname.replace(/^api\.|\.com$/g, "") : "";
      badge.title = cfg.openai_base_url || "";
      badge.innerHTML = fromUrl ? `<span class="provider">${escapeHtml(fromUrl)}</span> · ${escapeHtml(model)}` : escapeHtml(model);
    } else {
      badge.innerHTML = `<span style="color:var(--text-dim);font-size:12px;">⚙️ ${t("config.no_model")}</span>`;
      badge.onclick = () => document.getElementById("sbConfigBtn")?.click();
      badge.style.cursor = "pointer";
    }
  }
});

// ============================================================
// 独立报告页模式：URL 形如 /report/{sid}/{rid} 或 ?report=sid/rid
// ============================================================

async function enterReportOnlyMode(reportMode) {
  const { sid, rid, type } = reportMode;
  document.body.classList.add("report-only");
  document.title = type === "jstack" ? "线程分析报告 · 独立视图" : "GC 报告 · 独立视图";
  state.currentSessionId = sid;

  // 打开 panel + 切到对应 mode
  document.getElementById("gcPanel").classList.add("open");
  document.querySelectorAll(".mode-tab").forEach(x => x.classList.remove("active"));
  document.querySelector(`.mode-tab[data-mode="${type}"]`).classList.add("active");
  document.querySelectorAll(".mode-body").forEach(x => x.style.display = "none");
  document.querySelector(`.mode-body[data-mode="${type}"]`).style.display = "";

  if (type === "jstack") {
    state.currentJstackReportId = rid;
    try {
      const full = await api(`/api/sessions/${sid}/jstack/reports/${rid}`);
      state.currentJstackReport = full;
      app.renderJstackReport(full);
    } catch (e) {
      document.querySelector('#jstackBodyCurrent').innerHTML =
        `<div style="color:var(--red);padding:30px;text-align:center;">${ico('x')} 加载报告失败：${escapeHtml(e.message)}</div>`;
    }
  } else {
    state.currentReportId = rid;
    try {
      const full = await api(`/api/sessions/${sid}/gc/reports/${rid}`);
      state.currentReport = full;
      app.renderReport(full);
    } catch (e) {
      document.getElementById("gcBodyCurrent").innerHTML =
        `<div style="color:var(--red);padding:30px;text-align:center;">${ico('x')} 加载报告失败：${escapeHtml(e.message)}</div>`;
    }
  }
}

// ---------- 初始化 ----------
async function initApp() {
  await app.checkHealth();
  try {
    const cfg = await api("/api/config");
    const model = cfg.openai_model || "";
    const hasBuiltIn = cfg.use_built_in && !!cfg.note;
    state.llmConfigured = !cfg.use_built_in && !!cfg.openai_base_url && !!model;
    const badge = document.getElementById("modelLabel");
    if (badge) {
      if (hasBuiltIn && model) {
        badge.title = cfg.openai_base_url || "";
        badge.innerHTML = `<span class="provider">内置 · ${escapeHtml(model)}</span>`;
      } else if (model) {
        const fromUrl = cfg.openai_base_url ? new URL(cfg.openai_base_url).hostname.replace(/^api\.|\.com$/g, "") : "";
        badge.title = cfg.openai_base_url || "";
        badge.innerHTML = fromUrl ? `<span class="provider">${escapeHtml(fromUrl)}</span> · ${escapeHtml(model)}` : escapeHtml(model);
      } else {
        badge.innerHTML = `<span style="color:var(--text-dim);font-size:12px;">⚙️ ${t("config.no_model")}</span>`;
        badge.onclick = () => document.getElementById("sbConfigBtn")?.click();
        badge.style.cursor = "pointer";
      }
    }
  } catch {}
  await app.loadSessions();
  await app.updateConfigPrompt();
  initHeapdump();
  // 加载公共设置
  try {
    const res = await fetch("/api/settings/public", { credentials: "same-origin" });
    if (res.ok) {
      const settings = await res.json();
      const ta = document.getElementById("msg");
      if (ta && settings.max_input_length) ta.maxLength = settings.max_input_length;
    }
  } catch {}
  applyI18n();
}

/** 处理来自邮件邀请链接 `#/join?org=X&token=Y` 的团队加入请求 */
async function processPendingInvite() {
  const invite = consumePendingInvite();
  if (!invite) return;
  if (invite.email && state.currentUser) {
    const currentEmail = (state.currentUser.email || state.currentUser.username || "").trim().toLowerCase();
    if (currentEmail !== invite.email.trim().toLowerCase()) {
      alert(t("team.invite.wrong_account", { email: invite.email }));
      return;
    }
  }
  try {
    await api(`/api/orgs/${invite.orgId}/join`, {
      method: "POST",
      body: JSON.stringify({ token: invite.token }),
    });
    alert(t("team.invite.accepted"));
    await app.loadOrgInfo();
  } catch (e) {
    alert(e.message);
  }
}

(async () => {
  applyI18n();

  // 初始化同步隐藏所有界面，直到认证完成
  (function() {
    const m = document.getElementById("loginMask");
    const a = document.getElementById("appContent");
    if (m) m.classList.remove("open");
    if (a) a.style.display = "none";
  })();

  // 检测团队邀请哈希 #/join?org=X&token=Y
  const hash = window.location.hash;
  if (hash.startsWith("#/join")) {
    const params = new URLSearchParams(hash.split("?")[1] || "");
    const orgId = params.get("org");
    const token = params.get("token");
    const email = params.get("email") || "";
    if (orgId && token) {
      sessionStorage.setItem("pendingInvite", JSON.stringify({ orgId, token, email }));
    }
    history.replaceState(null, "", window.location.pathname + window.location.search);
  }

  const reportMode = detectReportMode();
  if (reportMode) {
    // 报告独立页：先检查登录，未登录则显示登录窗，登录后自动加载报告
  const user = await app.checkAuth();
  if (user) {
    await app.showAuthUI(user);
    await enterReportOnlyMode(reportMode);
  } else {
    // 保存目标 URL，登录后跳回
    sessionStorage.setItem("loginRedirect", location.href);
    await app.showLoginUI();
  }
  return;
}
// 先检查登录状态
const user = await app.checkAuth();
if (user) {
  await app.showAuthUI(user);
  await initApp();
  // 登录后检查是否有报告页重定向（从 report 模式跳转过来的）
	  const redirect = consumeLoginRedirect();
	  if (redirect) location.href = redirect;
	  // 处理团队邀请
	  await processPendingInvite();
	} else {
	  await app.showLoginUI();
	}
	initIcoIcons();
	})();

// ---------- i18n DOM 渲染 ----------
function applyI18n() {
  const demo = window._demoQuota || {};
  const demoVars = {
    uploads: demo.uploads != null ? demo.uploads : 50,
    calls: demo.calls != null ? demo.calls : 50,
    sessions: demo.sessions != null ? demo.sessions : 10,
  };
  document.querySelectorAll("[data-i18n]").forEach(el => {
    if (el.tagName === "TITLE") { el.textContent = t(el.dataset.i18n); return; }
    const key = el.dataset.i18n;
    let vars = {};
    if (key && key.includes("upload_zone_hint")) vars = { size: window._uploadSizeLimit || 50 };
    else if (key && key.startsWith("demo.notice_")) vars = demoVars;
    try { el.innerHTML = th(key, vars); } catch { el.textContent = t(key, vars); }
  });
  document.querySelectorAll("[data-i18n-title]").forEach(el => {
    el.title = t(el.dataset.i18nTitle).replace(/[\u{1F300}-\u{1F9FF}\u{2600}-\u{27BF}]/gu, '').trim();
  });
  document.querySelectorAll("[data-i18n-aria-label]").forEach(el => {
    el.setAttribute("aria-label", t(el.dataset.i18nAriaLabel).replace(/[\u{1F300}-\u{1F9FF}\u{2600}-\u{27BF}]/gu, '').trim());
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder).replace(/[\u{1F300}-\u{1F9FF}\u{2600}-\u{27BF}]/gu, '').trim();
  });
}

// ---------- 语言切换 ----------
(function initLang() {
  window.langToggleClick = () => setLang(getLang() === "zh" ? "en" : "zh");

  function updateLangBtn() {
    const btn = document.getElementById("sbLangToggle");
    if (!btn) return;
    const cur = getLang();
	    btn.innerHTML = (cur === "zh" ? "EN" : "CN") + ' <span data-ico="Globe"></span>';
	    initIcoIcons();
  }

  updateLangBtn();
  const btn = document.getElementById("sbLangToggle");
  if (btn) btn.onclick = window.langToggleClick;

  window.addEventListener("langchange", () => {
    updateLangBtn();
    applyI18n();
    if (state.currentUser) app.checkHealth();
    // 刷新聊天区空状态文本
    const emptyEl = document.querySelector("#chatArea > .empty");
    if (emptyEl) {
      const h2 = emptyEl.querySelector("h2");
      const div = emptyEl.querySelector("div");
      if (h2 && !h2.hasAttribute("data-i18n")) {
        const isStart = h2.innerHTML.includes(ico('message-square')) || h2.textContent.includes(t("chat.start_title").replace(/[💬\s]/g,""));
        h2.textContent = t(isStart ? "chat.start_title" : "chat.empty_title");
        if (div) div.textContent = t(isStart ? "chat.start_desc" : "chat.empty_desc");
      }
    }
    if (typeof state.currentReport !== "undefined" && state.currentReport) app.renderReport(state.currentReport);
    if (typeof state.currentJstackReport !== "undefined" && state.currentJstackReport) app.renderJstackReport(state.currentJstackReport);
    if (typeof state.currentUser !== "undefined" && state.currentUser) app.loadSessions();
    // 刷新配额区（侧边栏额度标签 + demo 横幅）随语言切换更新
    if (typeof state.currentUser !== "undefined" && state.currentUser && app.updateQuotaUI) app.updateQuotaUI();
  });
})();
