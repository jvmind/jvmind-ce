import { state } from "./state.js";
import { app } from "./app.js";
import { csrfHeaders, escapeHtml, isReportActionTarget, renderReportBulkToolbar, i18nText, fmtDate } from "./shared.js";
import { api } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { renderJstackCharts, drawJstackCharts } from "./charts.js";
import { t, getLang } from "../i18n/index.js";
import { feedbackWidgetHtml, bindFeedbackWidget } from "./feedback-widget.js";
import { bindReportBulkActions, deleteReportEntries, removeActiveReportContextByReport } from "./gc-analysis/context.js";
import { ico } from "./icons.js";

// ============================================================
// ============ JStack 线程分析模块 =============================
// ============================================================

// ---------- 上传 ----------
const jstackUploadZone = document.getElementById("jstackUploadZone");
const jstackFileInput = document.getElementById("jstackFile");

jstackUploadZone.onclick = () => jstackFileInput.click();
jstackUploadZone.ondragover = (e) => { e.preventDefault(); jstackUploadZone.classList.add("drag"); };
jstackUploadZone.ondragleave = () => jstackUploadZone.classList.remove("drag");
jstackUploadZone.ondrop = (e) => {
  e.preventDefault();
  jstackUploadZone.classList.remove("drag");
  if (e.dataTransfer.files[0]) uploadJstackFile(e.dataTransfer.files[0]);
};
jstackFileInput.onchange = (e) => { if (e.target.files[0]) uploadJstackFile(e.target.files[0]); };

// 上传区折叠/展开切换按钮
const jstackUploadToggleBtn = document.createElement("button");
jstackUploadToggleBtn.className = "upload-zone-toggle";
jstackUploadToggleBtn.textContent = "⤢";
jstackUploadToggleBtn.title = t("jstack.upload_zone_expand");
jstackUploadToggleBtn.onclick = (e) => {
  e.stopPropagation();
  const expanded = !jstackUploadZone.classList.contains("collapsed");
  if (expanded) {
    jstackUploadZone.classList.add("collapsed");
    jstackUploadToggleBtn.textContent = "⤢";
    jstackUploadToggleBtn.title = t("jstack.upload_zone_expand");
  } else {
    jstackUploadZone.classList.remove("collapsed");
    jstackUploadToggleBtn.textContent = "⤡";
    jstackUploadToggleBtn.title = t("jstack.upload_zone_collapse");
  }
};
jstackUploadZone.appendChild(jstackUploadToggleBtn);

const ALLOWED_JSTACK_EXTS = [".txt", ".log", ".tdump", ".jstack", ".json"];
const DEFAULT_MAX_JSTACK_SIZE = 5 * 1024 * 1024;

export async function uploadJstackFile(file) {
  if (!state.currentSessionId) { alert(t("chat.no_session")); return; }
  const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
  if (!ALLOWED_JSTACK_EXTS.includes(ext)) {
    document.getElementById("jstackError").style.display = "";
    document.getElementById("jstackError").textContent = t("jstack.error_invalid_type", { ext, exts: ALLOWED_JSTACK_EXTS.join(", ") });
    return;
  }
  // 使用从后端获取的用户套餐限制，如果没有则使用默认值
  const maxSize = window._uploadSizeLimit ? (window._uploadSizeLimit * 1024 * 1024) : DEFAULT_MAX_JSTACK_SIZE;
  if (file.size > maxSize) {
    document.getElementById("jstackError").style.display = "";
    document.getElementById("jstackError").textContent = t("jstack.error_file_too_large", { size: (file.size / 1024 / 1024).toFixed(1), max: maxSize / 1024 / 1024 });
    return;
  }
  document.getElementById("jstackError").style.display = "none";
  document.getElementById("jstackReportArea").style.display = "none";
  document.getElementById("jstackLoading").style.display = "";

  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch(`/api/sessions/${state.currentSessionId}/jstack/upload`, {
      method: "POST", credentials: "same-origin", headers: csrfHeaders(), body: fd,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(i18nText(err.detail || res.statusText));
    }
    const data = await res.json();
    const full = await api(`/api/sessions/${state.currentSessionId}/jstack/reports/${data.report_id}`);
    app.openReport("jstack", full, { attach: true });
    app.appendSystemHint(t("jstack.upload_success", { filename: escapeHtml(data.filename || "") }) + " " + t("jstack.upload_hint", { fid: escapeHtml(data.file_id || "") }));
    refreshJstackHistory();
    if (typeof renderJstackSidebar === "function") renderJstackSidebar();
    app.updateBadge();
    app.refreshAllReportHistory();
    app.updateQuotaUI();
  } catch (e) {
    document.getElementById("jstackError").style.display = "";
    document.getElementById("jstackError").textContent = t("jstack.upload_failed", { msg: e.message });
  } finally {
    document.getElementById("jstackLoading").style.display = "none";
    jstackFileInput.value = "";
  }
}

// ============================================================
// ============ JStack 侧栏 (sidebar) ===============================
// ============================================================

// JStack 健康等级：deadlock > blocked > normal
function jstackLevel(stats) {
  if (!stats) return "good";
  if ((stats.deadlock_count || 0) > 0) return "bad";
  const blocked = (stats.by_state && stats.by_state.BLOCKED) || 0;
  const blockedPct = stats.blocked_percent || 0;
  if (blocked > 0 || blockedPct > 5) return "warn";
  return "good";
}

const JSTACK_LEVEL_META = {
  good: { icon: ico('check'), label: "jstack.health_good" },
  warn: { icon: ico('circle-alert'), label: "jstack.health_warn" },
  bad:  { icon: ico('circle-x'), label: "jstack.health_bad" },
};

function formatJstackHealthBanner(stats, t) {
  const level = jstackLevel(stats);
  const meta = JSTACK_LEVEL_META[level] || JSTACK_LEVEL_META.good;
  const deadlock = stats ? (stats.deadlock_count || 0) : 0;
  const blocked = stats && stats.by_state ? (stats.by_state.BLOCKED || 0) : 0;
  const total = stats ? (stats.total_threads || 0) : 0;
  const metrics = [];
  if (total) metrics.push(`${t("jstack.stat_total_threads")}: ${total}`);
  if (deadlock) metrics.push(`${t("jstack.stat_deadlocks")}: ${deadlock}`);
  if (blocked) metrics.push(`${t("jstack.stat_blocked")}: ${blocked}`);
  if (stats && stats.overall) {
    metrics.push(`overall: ${escapeHtml(String(stats.overall))}`);
  }
  const detail = metrics.join(" · ");
  return `<div class="health-banner level-${level}"><span class="hb-status">${meta.icon} ${t(meta.label)}</span><span class="hb-detail">${detail}</span></div>`;
}

// ---------- 侧栏启用 / 折叠 ----------
export function enableJstackSidebar() {
  const modeBody = document.querySelector('.mode-body[data-mode="jstack"]');
  if (!modeBody || modeBody.classList.contains("sidebar-active")) return;
  modeBody.classList.add("sidebar-active");
  document.getElementById("jstackSidebar").style.display = "flex";
  const tabs = modeBody.querySelector(".gc-tabs");
  if (tabs) tabs.style.display = "none";
  const oldUpload = document.getElementById("jstackUploadZone");
  if (oldUpload) oldUpload.style.display = "none";
  const historyBody = document.getElementById("jstackBodyHistory");
  if (historyBody) historyBody.style.display = "none";
  const oldRptTabs = document.getElementById("jstackReportTabs");
  if (oldRptTabs) oldRptTabs.style.display = "none";
  // (jstackReportEmpty removed; sidebar upload zone is the only upload UI)
  const sidebar = document.getElementById("jstackSidebar");
  if (sidebar) sidebar.classList.remove("sidebar-collapsed");
  const toggleBtn = document.getElementById("jstackSidebarToggle");
  if (toggleBtn) toggleBtn.textContent = "◀";
  renderJstackSidebar();
  // 显示空状态 (若无打开报告)
  if (state.openJstackReports.length === 0) {
    showJstackEmptyState();
  }
}

export function showJstackEmptyState() {
  const area = document.getElementById("jstackReportArea");
  if (!area) return;
  area.style.display = "";
  area.innerHTML = `<div class="report-empty-state">
    <div class="es-icon">${ico('clipboard-list')}</div>
    <div class="es-title">${escapeHtml(t("jstack.empty_state_title"))}</div>
    <div class="es-hint">${escapeHtml(t("jstack.empty_state_hint"))}</div>
  </div>`;
}

export function toggleJstackSidebar(collapse) {
  const sidebar = document.getElementById("jstackSidebar");
  const toggleBtn = document.getElementById("jstackSidebarToggle");
  if (!sidebar || !toggleBtn) return;
  const isCollapsed = collapse !== undefined ? collapse : !sidebar.classList.contains("sidebar-collapsed");
  sidebar.classList.toggle("sidebar-collapsed", isCollapsed);
  toggleBtn.textContent = isCollapsed ? "▶" : "◀";
  toggleBtn.title = isCollapsed ? "Expand sidebar" : "Collapse sidebar";
}

// ---------- 侧栏渲染 ----------
export function renderJstackSidebar() {
  const sidebarList = document.getElementById("jstackSidebarList");
  if (!sidebarList) return;
  const activeId = state.currentJstackReportId;
  const sessionIds = new Set(state.openJstackReports.map(r => r.id));
  const items = [];

  for (const r of state.openJstackReports) {
    const report = r.report || {};
    const stats = report.stats;
    items.push({
      id: r.id, filename: r.filename || t("jstack.history_unnamed"),
      isSession: true, hasAi: !!(report.ai_conclusion),
      health: jstackLevel(stats),
      meta: stats ? `${stats.total_threads || 0} ${t("jstack.stat_total_threads")}` : "",
      title: r.filename || "",
    });
  }

  const gapNeeded = items.length > 0 && (state.jstackHistoryReports || []).length > 0;

  for (const h of state.jstackHistoryReports || []) {
    if (sessionIds.has(h.id)) continue;
    const stats = h.stats || {};
    items.push({
      id: h.id, filename: h.filename || t("jstack.history_unnamed"),
      isSession: false, hasAi: !!h.has_ai,
      health: jstackLevel(stats),
      meta: fmtDate(h.created_at),
      title: `${h.filename || ""}`,
    });
  }

  if (!items.length) {
    sidebarList.innerHTML = `<div class="sidebar-empty" style="color:var(--text-dim);text-align:center;padding:20px;font-size:12px;">${t("jstack.no_history")}</div>`;
    return;
  }

  let html = "";
  let afterSession = false;
  for (const item of items) {
    if (!item.isSession && !afterSession && gapNeeded) {
      html += '<div class="sidebar-separator"></div>';
      afterSession = true;
    }
    const isActive = item.id === activeId;
    const healthClass = item.health ? `level-${item.health}` : "";
    const isAttached = state.activeReportContexts.some(c => c.report_id === item.id);
    html += `<div class="sidebar-item${isActive ? " active" : ""}${item.hasAi ? " has-ai" : ""}" data-id="${item.id}" data-session="${String(item.isSession)}">`;
    html += `<div class="si-row1">`;
    html += `<span class="si-checkbox">${ico('square')}</span>`;
    html += `<span class="si-health ${healthClass}"></span>`;
    html += `<span class="si-filename" title="${escapeHtml(item.title)}">${escapeHtml(item.filename)}</span>`;
    html += `<span class="si-ai-badge">AI</span>`;
    html += `</div>`;
    html += `<div class="si-row2">`;
    if (item.meta) html += `<span class="si-meta">${escapeHtml(item.meta)}</span>`;
    html += `<button class="si-attach-btn${isAttached ? " attached" : ""}" data-action="attach" title="${t("reports.tab_attach")}">${ico('paperclip')}</button>`;
    html += `<button class="si-close-btn" data-action="close" title="${t("reports.tab_close")}">${ico('x')}</button>`;
    html += `</div></div>`;
  }
  sidebarList.innerHTML = html;
}

// ---------- 渲染报告 ----------
export function renderJstackReport(report) {
  const isReportOnly = document.body.classList.contains("report-only");
  const area = document.getElementById("jstackReportArea");
  area.style.display = "";
  const s = report.stats;
  const reportUrl = `${location.origin}/jstack-report/${state.currentSessionId}/${report.id}`;

  const bs = s.by_state || {};
  const blockedPct = s.blocked_percent || 0;
  const deadlockBad = s.deadlock_count > 0;
  const hasAi = !!report.ai_conclusion;

  // 健康横幅（与 GC 对齐：固定 2 行 + 固定槽位顺序）
  const bannerHtml = formatJstackHealthBanner(s, t);

  // 内存诊断区块（外观与位置对齐 GC 的内存诊断：置于顶部，复用 .diagnosis-section 样式）
  const diagHtml = (() => {
    const d = s.diagnosis;
    if (!d || !d.findings || !d.findings.length) return "";
    const lang = getLang();
    const findingsHtml = d.findings.map(f => `
      <div class="diag-finding diag-severity-${escapeHtml(String(f.severity || ""))}">
        <span class="diag-severity-tag">${t("jstack.diag_severity_" + f.severity)}</span>
        <div class="diag-finding-body">
          <div class="diag-finding-title">${escapeHtml(f["title_" + lang] || f.title_zh || "")}</div>
          <div class="diag-finding-detail">${escapeHtml(f["detail_" + lang] || f.detail_zh || "")}</div>
        </div>
      </div>
    `).join("");
    const recs = d["recommendations_" + lang] || d.recommendations_zh || [];
    const recsHtml = recs.length ? `
      <div class="diag-recommendations">
        <div class="diag-recs-title">${t("jstack.diag_recommendations")}</div>
        ${recs.map(r => `<div class="diag-rec-item">${escapeHtml(r)}</div>`).join("")}
      </div>
    ` : "";
    return `
      <div id="jstackDiagnosis" class="diagnosis-section">
        ${findingsHtml}
        ${recsHtml}
      </div>
    `;
  })();

  const stateCards = Object.entries(bs).map(([st, cnt]) => {
    const pct = ((cnt / s.total_threads) * 100).toFixed(1);
    const cls = st === "BLOCKED" ? "bad" : st === "WAITING" ? "warn" : "good";
    const stSafe = escapeHtml(String(st));
    return `<div class="stat-card ${cls}"><div class="label">${stSafe}</div><div class="value">${cnt}</div><div class="sub">${pct}%</div></div>`;
  }).join("");

  // AI 结论区块（结构对齐 GC，让独立报告页能把 AI 放到右栏）
  const aiHtml = hasAi ? renderMarkdown(report.ai_conclusion)
    : `<div style="text-align:center;padding:20px;color:var(--text-dim);font-size:13px;">${escapeHtml(t("jstack.no_ai_conclusion"))}</div>`;
  const aiSectionHtml = `
    <div class="ai-section ${isReportOnly || !hasAi ? '' : 'collapsed'}">
      <div class="ai-header" ${isReportOnly ? '' : 'data-act="toggle-ai-collapse"'}>
        ${ico('ChevronDown', { className: 'collapse-icon' })}
        <span class="ai-title">${t("jstack.ai_conclusion")}</span>
        ${isReportOnly ? '' : `<button class="btn" id="jstackSendToAgentBtn" style="font-size:11px;padding:2px 8px;flex-shrink:0;">${t("jstack.send_to_agent")}</button>`}
      </div>
      <div class="ai-body">
        <div class="ai-conclusion">${aiHtml}</div>
        ${hasAi && !isReportOnly ? feedbackWidgetHtml("jstack", report.id) : ""}
      </div>
    </div>
  `;

  const dataHtml = `
    ${bannerHtml}
    <div class="section-title">${t("jstack.diag_title")}</div>
    ${diagHtml}

    <div class="stat-grid">
      <div class="stat-card"><div class="label">${t("jstack.stat_total_threads")}</div><div class="value">${s.total_threads}</div><div class="sub">${s.daemon_count} ${t("jstack.daemon")}</div></div>
      ${s.virtual_thread_count > 0 ? `<div class="stat-card"><div class="label">${t("jstack.virtual_threads")}</div><div class="value">${s.virtual_thread_count}</div></div>` : ''}
      ${s.terminated_count > 0 ? `<div class="stat-card"><div class="label">${t("jstack.terminated")}</div><div class="value">${s.terminated_count}</div></div>` : ''}
      ${s.container_stats && s.container_stats.length > 0 ? `<div class="stat-card"><div class="label">${t("jstack.stat_containers")}</div><div class="value">${s.container_stats.length}</div></div>` : ''}

      <div class="stat-subtitle">${t("jstack.section_health")}</div>
      <div class="stat-card ${deadlockBad ? 'bad' : 'good'}"><div class="label">${t("jstack.stat_deadlocks")}</div><div class="value">${s.deadlock_count}</div></div>
      <div class="stat-card"><div class="label">${t("jstack.stat_blocked")}</div><div class="value">${bs.BLOCKED || 0}</div><div class="sub">${blockedPct}%</div></div>

      <div class="stat-subtitle">${t("jstack.section_state")}</div>
      ${stateCards}

      <div class="stat-subtitle">${t("jstack.section_stack")}</div>
      <div class="stat-card"><div class="label">${t("jstack.stat_avg_depth")}</div><div class="value">${s.avg_stack_depth}</div></div>
      <div class="stat-card"><div class="label">${t("jstack.stat_max_depth")}</div><div class="value">${s.max_stack_depth}</div><div class="sub" style="font-size:10px;">${escapeHtml(s.max_stack_thread || "")}</div></div>
    </div>

    ${s.deadlocks && s.deadlocks.length ? `
      <div class="deadlock-alert">
        <div style="display:flex;align-items:center;gap:8px;font-weight:600;font-size:13px;margin-bottom:6px;">
          <span style="font-size:16px;">${ico('ban')}</span> ${t(s.deadlock_count > 1 ? "jstack.multiple_deadlocks" : "jstack.detected_deadlock")}
        </div>
        ${s.deadlocks.map(dl => `
          <div class="deadlock-chain">
            <div class="deadlock-item">
              <span class="state-badge BLOCKED">BLOCKED</span>
              <strong>${escapeHtml(dl.thread)}</strong>
              ${t("jstack.deadlock_waiting")} <code>${escapeHtml(dl.waiting_for_desc || dl.waiting_for_addr || '?')}</code>
              <span style="color:var(--text-dim);font-size:11px;">${t("jstack.deadlock_held_by", { name: escapeHtml(dl.held_by || '?') })}</span>
            </div>
          </div>
        `).join('')}
        <div style="margin-top:4px;color:var(--text-dim);font-size:11px;">
          ${t("jstack.deadlock_hint")}
        </div>
      </div>
    ` : ''}

    ${renderJstackCharts(s)}

    <div class="section-title">${t("jstack.thread_list")} (${s.threads.length})</div>
    <div id="threadFilterBar" style="display:flex;gap:6px;margin-bottom:6px;flex-wrap:wrap;align-items:center;">
      <input id="threadSearch" type="text" placeholder="${t("jstack.search_placeholder")}" style="flex:1;min-width:120px;background:var(--bg-3);border:1px solid var(--border);border-radius:4px;padding:4px 8px;color:var(--text);font-size:12px;outline:none;" />
      <span style="color:var(--text-dim);font-size:11px;">${t("jstack.state_label")}</span>
      <span id="stateFilterAll" class="state-filter-btn active" data-state="">All</span>
      <span id="stateFilterRunnable" class="state-filter-btn" data-state="RUNNABLE" style="--badge-bg:var(--badge-green-bg);--badge-c:var(--green);">RUNNABLE</span>
      <span id="stateFilterBlocked" class="state-filter-btn" data-state="BLOCKED" style="--badge-bg:var(--badge-red-bg);--badge-c:var(--red);">BLOCKED</span>
      <span id="stateFilterWaiting" class="state-filter-btn" data-state="WAITING" style="--badge-bg:var(--badge-orange-bg);--badge-c:var(--orange);">WAITING</span>
      <span id="stateFilterTimed" class="state-filter-btn" data-state="TIMED_WAITING">TIMED_WAITING</span>
    </div>
    <div style="overflow-x:auto;">
    <table class="thread-table" id="threadTable">
      <thead><tr>
        <th style="width:24px;"></th><th>${t("jstack.thread_name")}</th><th>${t("jstack.state")}</th><th>${t("jstack.daemon")}</th><th id="sortDepthBtn" style="cursor:pointer;user-select:none;">${t("jstack.stack_depth")} <span id="sortDepthIcon">${ico('arrow-up-down')}</span></th><th>${t("jstack.top_frame")}</th><th>${t("jstack.lock_wait")}</th><th>${t("jstack.actions")}</th>
      </tr></thead>
      <tbody id="threadTableBody">
      </tbody>
    </table>
    </div>
    <div id="threadPagination" style="display:flex;justify-content:center;align-items:center;gap:8px;margin-top:6px;font-size:12px;color:var(--text-dim);"></div>

    ${s.lock_hotspots && s.lock_hotspots.length ? `
      <div class="section-title">${t("jstack.lock_hotspots")}</div>
      <div class="jstack-lock-list">
        ${s.lock_hotspots.map(h => `
          <div class="jstack-lock-item">
            <div style="display:flex;justify-content:space-between;width:100%;">
              <span><span class="id">${ico('lock')}</span> <span style="font-size:11px;">${escapeHtml(h.desc).substring(0,55)}</span></span>
              <span class="dur">${h.blocked_count} ${t("jstack.threads_waiting")}</span>
            </div>
            <div style="font-size:11px;color:var(--text-dim);">
              ${t("jstack.holder")}<strong>${h.held_by ? escapeHtml(h.held_by) : t("jstack.lock_unknown_holder")}</strong>
            </div>
            ${h.blocked_by_threads && h.blocked_by_threads.length ? `
            <div style="font-size:10px;color:var(--text-dim);padding-left:14px;line-height:1.6;">
              ${t("jstack.waiting_threads")}${h.blocked_by_threads.map(t => `<span class="waiter-thread" data-thread="${escapeHtml(t)}">${escapeHtml(t)}</span>`).join(', ')}
            </div>` : ''}
          </div>
        `).join("")}
      </div>
    ` : ""}
  `;

  if (isReportOnly) {
    area.innerHTML = `
      <div style="margin-bottom:10px;display:flex;justify-content:space-between;align-items:flex-start;gap:10px;">
        <div style="min-width:0;flex:1;">
          <div style="font-weight:600;font-size:14px;">${escapeHtml(report.filename)}</div>
          <div style="color:var(--text-dim);font-size:11px;">
            ${(report.size/1024).toFixed(1)} KB · ${t("jstack.report_meta_threads", { count: s.total_threads })} · ${fmtDate(report.created_at)}
          </div>
        </div>
      </div>
      <div class="report-layout">
        <div class="report-data">${dataHtml}</div>
        <div class="report-ai">${aiSectionHtml}</div>
      </div>
    `;
  } else {
    area.innerHTML = `
      <div style="margin-bottom:10px;">
        <div style="font-weight:600;font-size:14px;">${escapeHtml(report.filename)}</div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <div style="flex:1;min-width:0;color:var(--text-dim);font-size:11px;">
            ${(report.size/1024).toFixed(1)} KB · ${t("jstack.report_meta_threads", { count: s.total_threads })} · ${fmtDate(report.created_at)}
          </div>
          <a href="${reportUrl}" target="_blank" rel="noopener" class="btn"
             style="font-size:11px;padding:4px 10px;text-decoration:none;flex-shrink:0;">${ico('link')} ${t("jstack.report_url")}</a>
        </div>
      </div>
      ${dataHtml}
      ${aiSectionHtml}
    `;
  }

  if (!isReportOnly) {
    const sendBtn = document.getElementById("jstackSendToAgentBtn");
    if (sendBtn && report.id) {
      sendBtn.onclick = () => sendToAgent('jstack', report.id, report.filename);
    }
  }
  document.querySelectorAll(".report-feedback").forEach(bindFeedbackWidget);
  if (!isReportOnly) {
    document.querySelectorAll(".waiter-thread").forEach(el => {
      el.onclick = () => sendToAgent('jstack_thread', report.id, report.filename, el.dataset.thread);
    });
  }
  // 线程表格分页
  initThreadTable(s.threads, report);
}

export function initThreadTable(allThreads, report) {
  const pageSize = 20;
  let currentPage = 0;
  let searchText = "";
  let stateFilter = "";  // "" = all
  let sortAsc = false;

  function getFiltered() {
    let list = allThreads;
    if (searchText) {
      const raw = searchText;
      // 排除模式：以 - 或 ! 开头
      const isExclude = raw.startsWith("-") || raw.startsWith("!");
      const q = (isExclude ? raw.slice(1) : raw).toLowerCase();
      if (!q) return list;
      list = list.filter(th => {
        const inName = (th.name || "").toLowerCase().includes(q);
        const inFrame = (th.top_frame || "").toLowerCase().includes(q);
        const inFrames = (th.frames || []).some(f => f.toLowerCase().includes(q));
        const match = inName || inFrame || inFrames;
        return isExclude ? !match : match;
      });
    }
    if (stateFilter) {
      list = list.filter(th => th.state === stateFilter);
    }
    // 按栈深排序
    list.sort((a, b) => sortAsc ? a.depth - b.depth : b.depth - a.depth);
    return list;
  }

  function renderPage(page) {
    const filtered = getFiltered();
    const totalPages = Math.ceil(filtered.length / pageSize) || 1;
    currentPage = Math.max(0, Math.min(page, totalPages - 1));
    const start = currentPage * pageSize;
    const end = Math.min(start + pageSize, filtered.length);
    const pageData = filtered.slice(start, end);

    const tbody = document.getElementById("threadTableBody");
    if (!tbody) return;
    tbody.innerHTML = pageData.map((th, idx) => {
      const name = escapeHtml(th.name || "");
      const frame = escapeHtml(th.top_frame || "");
      const lockW = th.lock_waiting ? escapeHtml(th.lock_waiting).substring(0, 30) : "-";
      const rowId = "tr-" + currentPage + "-" + idx;
      const framesText = (th.frames || []).map(f => escapeHtml(f)).join("\n");
      const stateSafe = escapeHtml(String(th.state ?? ""));
      return `<tr style="cursor:pointer;" data-act="toggle-stack" data-row-id="${rowId}">
        <td><span class="expand-toggle" id="${rowId}-btn">▶</span></td>
        <td class="name" title="${name}">${name}</td>
        <td><span class="state-badge ${stateSafe}">${stateSafe}</span></td>
        <td>${th.daemon ? 'Y' : 'N'}</td>
        <td>${escapeHtml(String(th.depth ?? ""))}</td>
        <td class="top-frame" title="${frame}">${frame}</td>
        <td style="color:${th.lock_waiting ? 'var(--orange)' : 'var(--text-dim)'};font-size:10px;">${lockW}</td>
        <td>${!document.body.classList.contains("report-only") ? `<button class="analyze-btn" data-act="send-thread" data-report-id="${escapeHtml(String(report.id ?? ""))}" data-filename="${escapeHtml(report.filename || "")}" data-thread="${escapeHtml(th.name || "")}">${t("jstack.send_to_agent")}</button>` : ''}</td>
      </tr>
      <tr id="${rowId}" style="display:none;">
        <td colspan="8" class="expand-frame open"><pre class="jstack-frame-pre">${framesText}</pre></td>
      </tr>`;
    }).join("");

    // 事件委托：行展开栈帧 / 发送线程到 Agent（替代内联 onclick，防 XSS）
    tbody.onclick = (ev) => {
      const sendBtn = ev.target.closest('[data-act="send-thread"]');
      if (sendBtn) {
        ev.stopPropagation();
        sendToAgent('jstack_thread', sendBtn.dataset.reportId, sendBtn.dataset.filename || '', sendBtn.dataset.thread || '', sendBtn);
        return;
      }
      const row = ev.target.closest('[data-act="toggle-stack"]');
      if (row) {
        toggleStack(row.dataset.rowId);
      }
    };

    const pag = document.getElementById("threadPagination");
    if (pag) {
      const prevDisabled = currentPage === 0 ? 'disabled' : '';
      const nextDisabled = currentPage >= totalPages - 1 ? 'disabled' : '';
      pag.innerHTML = `
        <span style="color:var(--text-dim);font-size:11px;">${filtered.length} ${t("jstack.matching")}</span>
        <button class="btn" style="font-size:11px;padding:2px 8px;" ${prevDisabled} data-page="${currentPage - 1}">‹</button>
        <span>${currentPage + 1} / ${totalPages}</span>
        <button class="btn" style="font-size:11px;padding:2px 8px;" ${nextDisabled} data-page="${currentPage + 1}">›</button>
      `;
      pag.querySelectorAll("button[data-page]").forEach(btn => {
        btn.onclick = () => renderPage(parseInt(btn.dataset.page));
      });
    }
  }

  // 搜索
  const searchEl = document.getElementById("threadSearch");
  if (searchEl) {
    searchEl.oninput = () => {
      searchText = searchEl.value;
      renderPage(0);
    };
  }

  // 状态过滤
  document.querySelectorAll(".state-filter-btn").forEach(btn => {
    btn.onclick = () => {
      document.querySelectorAll(".state-filter-btn").forEach(x => x.classList.remove("active"));
      btn.classList.add("active");
      stateFilter = btn.dataset.state || "";
      renderPage(0);
    };
  });

  // 栈深排序
  const sortBtn = document.getElementById("sortDepthBtn");
  const sortIcon = document.getElementById("sortDepthIcon");
  if (sortBtn) {
    sortBtn.onclick = () => {
      sortAsc = !sortAsc;
      if (sortIcon) sortIcon.innerHTML = sortAsc ? ico('chevron-up') : ico('chevron-down');
      renderPage(0);
    };
  }

  renderPage(0);
}

export function toggleStack(rowId) {
  const row = document.getElementById(rowId);
  const btn = document.getElementById(rowId + "-btn");
  if (row && btn) {
    const open = row.style.display !== "table-row";
    row.style.display = open ? "table-row" : "none";
    btn.textContent = open ? "▼" : "▶";
  }
}
window.toggleStack = toggleStack;

// renderJstackCharts + drawJstackCharts + flamegraph moved to charts.js

// flamegraph moved to charts.js

// ---------- JStack 线程分析入口/历史 ----------

// ---------- 历史（侧栏接管：历史数据存到 state，侧栏渲染） ----------
export async function refreshJstackHistory() {
  if (!state.currentSessionId) return;
  try {
    const r = await api(`/api/sessions/${state.currentSessionId}/jstack/reports`);
    const list = r.reports || [];
    state.jstackHistoryReports = list;
    // mode-tab 计数按当前会话语义展示
    const jsModeCount = document.getElementById("jstackTabCount");
    if (jsModeCount) jsModeCount.textContent = list.length;
    const cnt = document.getElementById("jstackHistoryCount");
    if (cnt) cnt.textContent = list.length ? `(${list.length})` : "";
    renderJstackHistoryList(list);
    if (typeof renderJstackSidebar === "function") renderJstackSidebar();
  } catch (e) {
    console.error(e);
  }
}

function renderJstackHistoryList(reports) {
  const el = document.getElementById("jstackHistoryList");
  const empty = document.getElementById("jstackHistoryEmpty");
  if (!el) return;
  if (!reports.length) {
    el.innerHTML = "";
    if (empty) empty.style.display = "";
    return;
  }
  if (empty) empty.style.display = "none";
  el.innerHTML = renderReportBulkToolbar("jstack", t) + reports.map(r => {
    const stats = r.stats || {};
    const level = jstackLevel(stats);
    const healthDot = `<span class="health-dot level-${level}"></span>`;
    const meta = stats.total_threads ? `${stats.total_threads} ${t("jstack.stat_total_threads")}` : "";
    return `<div class="history-item" data-id="${escapeHtml(r.id)}" data-type="jstack">
      <input type="checkbox" class="report-select" data-id="${escapeHtml(r.id)}" data-type="jstack" data-session="${escapeHtml(state.currentSessionId || "")}" />
      <div class="history-content">
        <div class="row1">
          <div class="row1-left">${healthDot}<span>${escapeHtml(r.filename || t("jstack.history_unnamed"))}</span></div>
          ${r.has_ai ? '<span class="ai-tag">AI</span>' : ""}
        </div>
        <div class="row2">
          <span>${escapeHtml(meta)}</span>
          <span>${escapeHtml(fmtDate(r.created_at))} <button type="button" class="del" data-id="${escapeHtml(r.id)}">${escapeHtml(t("reports.delete"))}</button></span>
        </div>
      </div>
    </div>`;
  }).join("");
  el.querySelectorAll(".history-item").forEach(it => {
    it.onclick = async (e) => {
      if (isReportActionTarget(e.target)) return;
      const rid = it.dataset.id;
      try {
        const full = await api(`/api/sessions/${state.currentSessionId}/jstack/reports/${rid}`);
        // 切到"当前报告" tab
        const mode = document.querySelector('.mode-body[data-mode="jstack"]');
        if (mode && !mode.classList.contains("sidebar-active")) {
          mode.querySelectorAll(".gc-tabs .tab").forEach(x => x.classList.remove("active"));
          mode.querySelector('.gc-tabs .tab[data-subtab="current"]').classList.add("active");
          mode.querySelector("#jstackBodyCurrent").style.display = "";
          mode.querySelector("#jstackBodyHistory").style.display = "none";
        }
        app.openReport?.("jstack", full, { dontTrack: true });
      } catch (err) {
        alert(err.message || t("heapdump.delete_failed"));
      }
    };
  });
  el.querySelectorAll(".del").forEach(d => {
    d.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(t("jstack.delete_confirm") || t("gc.delete_confirm"))) return;
      try {
        await api(`/api/sessions/${state.currentSessionId}/jstack/reports/${d.dataset.id}`, { method: "DELETE" });
        removeActiveReportContextByReport(d.dataset.id);
        app.closeReportTab?.("jstack", d.dataset.id);
        refreshJstackHistory();
        if (typeof app.refreshAllReportHistory === "function") app.refreshAllReportHistory();
        if (typeof app.updateQuotaUI === "function") app.updateQuotaUI();
      } catch (err) {
        alert(err.message || t("heapdump.delete_failed"));
      }
    };
  });
  bindReportBulkActions(el, async (selected) => {
    await deleteReportEntries(selected);
    refreshJstackHistory();
    if (typeof app.refreshAllReportHistory === "function") app.refreshAllReportHistory();
    if (typeof app.updateQuotaUI === "function") app.updateQuotaUI();
  });
}

Object.assign(app, {
  uploadJstackFile, renderJstackReport, initThreadTable, toggleStack,
  refreshJstackHistory, enableJstackSidebar, renderJstackSidebar, toggleJstackSidebar,
});

// ============================================================
// ============ JStack 侧栏 DOM 事件绑定（import 时执行） =====
// ============================================================

// Toggle sidebar collapse
const jstackToggleBtn = document.getElementById("jstackSidebarToggle");
if (jstackToggleBtn) jstackToggleBtn.onclick = () => toggleJstackSidebar();

// Sidebar upload zone
const sidebarUploadZone = document.getElementById("jstackSidebarUploadZone");
const sidebarFileInput = document.getElementById("jstackSidebarFile");
if (sidebarUploadZone && sidebarFileInput) {
  sidebarUploadZone.onclick = () => sidebarFileInput.click();
  sidebarUploadZone.ondragover = (e) => { e.preventDefault(); sidebarUploadZone.classList.add("drag"); };
  sidebarUploadZone.ondragleave = () => sidebarUploadZone.classList.remove("drag");
  sidebarUploadZone.ondrop = (e) => {
    e.preventDefault();
    sidebarUploadZone.classList.remove("drag");
    if (e.dataTransfer.files[0]) uploadJstackFile(e.dataTransfer.files[0]);
  };
  sidebarFileInput.onchange = (e) => { if (e.target.files[0]) uploadJstackFile(e.target.files[0]); };
}

// Sidebar item click handler
const jstackSidebarList = document.getElementById("jstackSidebarList");
if (jstackSidebarList) {
jstackSidebarList.addEventListener("click", async (e) => {
  const item = e.target.closest(".sidebar-item");
  if (!item) return;
  const id = item.dataset.id;
  if (!id) return;

  const action = e.target.closest("[data-action]");
  if (action) {
    if (action.dataset.action === "attach") {
      let entry = state.openJstackReports.find(r => r.id === id);
      let report = entry?.report;
      if (!report) {
        const historyItem = (state.jstackHistoryReports || []).find(r => r.id === id);
        if (historyItem) {
          report = {
            id: historyItem.id,
            filename: historyItem.filename,
            file_id: "",
          };
        }
      }
      if (report) {
        const before = state.activeReportContexts.length;
        app.bindReportContext("jstack", report);
        if (state.activeReportContexts.length === before && !state.activeReportContexts.some(c => c.report_id === id)) {
          action.innerHTML = ico('triangle-alert');
          setTimeout(() => { action.innerHTML = ico('paperclip'); renderJstackSidebar(); }, 1500);
          return;
        }
      }
      renderJstackSidebar();
      return;
    }
    if (action.dataset.action === "close") {
      if (!confirm(t("jstack.delete_confirm"))) return;
      await api(`/api/sessions/${state.currentSessionId}/jstack/reports/${id}`, { method: "DELETE" });
      app.removeActiveReportContextByReport(id);
      await refreshJstackHistory();
      return;
    }
    return;
  }

  // Batch checkbox click
  const checkbox = e.target.closest(".si-checkbox");
  if (checkbox) {
    if (jstackBatchMode && item.classList.contains("batch-active") && !item.classList.contains("batch-excluded")) {
      checkbox.innerHTML = checkbox.innerHTML === ico('check-square') ? ico('square') : ico('check-square');
      _updateJstackBatchDeleteBtn();
    }
    return;
  }

  const isSession = item.dataset.session === "true";
  if (isSession) {
    app.activateReportTab?.("jstack", id);
  } else {
    const full = await api(`/api/sessions/${state.currentSessionId}/jstack/reports/${id}`);
    app.openReport("jstack", full, { dontTrack: true });
  }
});
}

// Batch mode for jstack
let jstackBatchMode = false;
const jstackSelectBtn = document.getElementById("jstackSidebarSelectBtn");
if (jstackSelectBtn) {
  jstackSelectBtn.onclick = () => {
    jstackBatchMode = true;
    document.getElementById("jstackSelectBtn") && (document.getElementById("jstackSelectBtn").style.display = "none");
    document.getElementById("jstackSidebarSelectBtn").style.display = "none";
    document.getElementById("jstackSidebarBatchBar").style.display = "flex";
    document.querySelectorAll("#jstackSidebarList .sidebar-item").forEach(el => {
      el.classList.add("batch-active");
    });
    _updateJstackBatchDeleteBtn();
  };
}
const jstackBatchCancelBtn = document.querySelector("#jstackSidebarBatchBar .sidebar-cancel-btn");
if (jstackBatchCancelBtn) {
  jstackBatchCancelBtn.onclick = () => {
    jstackBatchMode = false;
    document.getElementById("jstackSidebarSelectBtn").style.display = "";
    document.getElementById("jstackSidebarBatchBar").style.display = "none";
    document.querySelectorAll("#jstackSidebarList .sidebar-item").forEach(el => {
      el.classList.remove("batch-active", "batch-excluded");
    });
  };
}
const jstackSelectAllBtn = document.querySelector("#jstackSidebarBatchBar .sidebar-selectall-btn");
if (jstackSelectAllBtn) {
  jstackSelectAllBtn.onclick = () => {
    const cb = document.querySelectorAll("#jstackSidebarList .sidebar-item:not(.batch-excluded) .si-checkbox");
    const allChecked = !cb.length || [...cb].every(c => c.innerHTML === ico('check-square'));
    cb.forEach(c => { c.innerHTML = allChecked ? ico('square') : ico('check-square'); });
    _updateJstackBatchDeleteBtn();
  };
}
const jstackBatchDeleteBtn = document.querySelector("#jstackSidebarBatchBar .sidebar-delete-btn");
if (jstackBatchDeleteBtn) {
  jstackBatchDeleteBtn.onclick = async () => {
    const checked = [...document.querySelectorAll("#jstackSidebarList .sidebar-item .si-checkbox")].filter(c => c.innerHTML === ico('check-square'));
    const ids = checked.map(c => c.closest(".sidebar-item").dataset.id).filter(Boolean);
    if (!ids.length) return;
    if (!confirm(t("reports.bulk_delete_confirm", { n: ids.length }))) return;
    const entries = ids.map(id => ({ id, type: "jstack", sessionId: state.currentSessionId }));
    await app.deleteReportEntries(entries);
    // Refresh history so server-side deletes appear in the sidebar.
    // closeReportTab does not re-render the jstack sidebar, so without
    // this the deleted items would stay visible until the next render.
    await refreshJstackHistory();
    jstackBatchCancelBtn.onclick();
  };
}

function _updateJstackBatchDeleteBtn() {
  const checked = document.querySelectorAll("#jstackSidebarList .sidebar-item.batch-active:not(.batch-excluded) .si-checkbox");
  const selected = [...checked].filter(c => c.innerHTML === ico('check-square')).length;
  const deleteBtn = document.querySelector("#jstackSidebarBatchBar .sidebar-delete-btn");
  if (deleteBtn) {
    deleteBtn.disabled = selected === 0;
    deleteBtn.innerHTML = selected ? `${ico('trash-2')} ${t("reports.bulk_delete")} (${selected})` : `${ico('trash-2')} ${t("reports.bulk_delete")}`;
  }
}
