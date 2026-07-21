import { state } from "../state.js";
import { escapeHtml, calculateGCHealth, fmtDate } from "../shared.js";
import { t } from "../../i18n/index.js";
import { ico } from "../icons.js";

export function enableGcSidebar() {
  const modeBody = document.querySelector('.mode-body[data-mode="gc"]');
  if (!modeBody || modeBody.classList.contains("sidebar-active")) return;
  modeBody.classList.add("sidebar-active");
  document.getElementById("gcSidebar").style.display = "flex";
  const tabs = modeBody.querySelector(".gc-tabs");
  if (tabs) tabs.style.display = "none";
  const oldUpload = document.getElementById("uploadZone");
  if (oldUpload) oldUpload.style.display = "none";
  const historyBody = document.getElementById("gcBodyHistory");
  if (historyBody) historyBody.style.display = "none";
  const oldRptTabs = document.getElementById("gcReportTabs");
  if (oldRptTabs) oldRptTabs.style.display = "none";
  // Always start expanded
  const sidebar = document.getElementById("gcSidebar");
  if (sidebar) sidebar.classList.remove("sidebar-collapsed");
  const toggleBtn = document.getElementById("gcSidebarToggle");
  if (toggleBtn) toggleBtn.textContent = "◀";
  renderGcSidebar();
  if (state.currentReport) {
    const hdr = document.getElementById("gcReportHeader");
    if (hdr) hdr.style.display = "flex";
  }
  // 显示空状态 (若无打开报告)
  if (state.openGcReports.length === 0) {
    showGcEmptyState();
  }
}

export function showGcEmptyState() {
  const area = document.getElementById("gcReportArea");
  if (!area) return;
  area.style.display = "";
  area.innerHTML = `
    <div class="report-empty-state">
      <div class="es-icon">${ico('clipboard-list')}</div>
      <div class="es-title">${escapeHtml(t("gc.empty_state_title"))}</div>
      <div class="es-hint">${escapeHtml(t("gc.empty_state_hint"))}</div>
    </div>`;
}

export function renderGcSidebar() {
  const sidebarList = document.getElementById("gcSidebarList");
  if (!sidebarList) return;
  const activeId = state.currentReportId;
  const sessionIds = new Set(state.openGcReports.map(r => r.id));
  const items = [];

  for (const r of state.openGcReports) {
    const report = r.report || {};
    const stats = report.stats;
    items.push({
      id: r.id, filename: r.filename || t("gc.history_unnamed"),
      isSession: true, hasAi: !!(report.ai_conclusion),
      health: stats ? calculateGCHealth(stats) : null,
      title: r.filename || "",
    });
  }

  const gapNeeded = items.length > 0 && (state.gcHistoryReports || []).length > 0;

  for (const h of state.gcHistoryReports || []) {
    if (sessionIds.has(h.id)) continue;
    const stats = h.stats || {};
    items.push({
      id: h.id, filename: h.filename || t("gc.history_unnamed"),
      isSession: false, hasAi: !!h.has_ai,
      health: stats ? calculateGCHealth(stats) : null,
      meta: fmtDate(h.created_at),
      title: `${h.filename || ""}`,
    });
  }

  if (!items.length) {
    sidebarList.innerHTML = `<div class="sidebar-empty" style="color:var(--text-dim);text-align:center;padding:20px;font-size:12px;">${t("gc.no_history")}</div>`;
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

export function updateReportHeader(type, report) {
  const prefix = type === "gc" ? "gc" : type === "jstack" ? "jstack" : "heapdump";
  const header = document.getElementById(`${prefix}ReportHeader`);
  const filename = document.getElementById(`${prefix}ReportFilename`);
  const aiBadge = document.getElementById(`${prefix}ReportAiBadge`);
  const attachBtn = document.getElementById(`${prefix}ReportAttachBtn`);
  if (!header) return;
  header.style.display = "flex";
  filename.textContent = report.filename || t("gc.history_unnamed");
  filename.title = report.filename || "";
  const hasAi = !!(report.ai_conclusion);
  aiBadge.style.display = hasAi ? "" : "none";
  const isAttached = state.activeReportContexts.some(c => c.report_id === report.id);
  attachBtn.classList.toggle("attached", isAttached);
}

export function toggleGcSidebar(collapse) {
  const sidebar = document.getElementById("gcSidebar");
  const toggleBtn = document.getElementById("gcSidebarToggle");
  if (!sidebar || !toggleBtn) return;
  const isCollapsed = collapse !== undefined ? collapse : !sidebar.classList.contains("sidebar-collapsed");
  sidebar.classList.toggle("sidebar-collapsed", isCollapsed);
  toggleBtn.textContent = isCollapsed ? "▶" : "◀";
  toggleBtn.title = isCollapsed ? "Expand sidebar" : "Collapse sidebar";
}
