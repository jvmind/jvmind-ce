import { api } from "../api.js";
import { csrfHeaders, escapeHtml, fmtDate, i18nText, isReportActionTarget, renderReportBulkToolbar } from "../shared.js";
import { t } from "../../i18n/index.js";
import { state } from "../state.js";
import { pollTask } from "./task-poller.js";
import { addActiveReportContext, bindReportContext, renderActiveReportContext, removeActiveReportContextByReport, deleteReportEntries, ACTIVE_REPORT_CONTEXT_LIMIT } from "../gc-analysis/context.js";
import { renderMarkdown } from "../markdown.js";
import { ico } from "../icons.js";

const CHUNK_SIZE = 8 * 1024 * 1024; // server default; overwritten by server response
const ALLOWED_EXT = [".hprof", ".hprof.gz", ".bin", ".dump", ".gz"];

let _threadsLoaded = new Set();
let _leaksLoaded = new Set();
const _leaksCache = new Map();
const _CLASSES_STATE = { top: 50, sort: "retained" };
let _POOL_LEAKS_THRESHOLD_MS = 300000;
let _SQL_ONLY_RISKY = true;

// ---------- 侧栏 (对齐 GC/jstack) ----------

export function toggleHeapdumpSidebar(collapse) {
  const sidebar = document.getElementById("heapdumpSidebar");
  const toggleBtn = document.getElementById("heapdumpSidebarToggle");
  if (!sidebar || !toggleBtn) return;
  const isCollapsed = collapse !== undefined ? collapse : !sidebar.classList.contains("sidebar-collapsed");
  sidebar.classList.toggle("sidebar-collapsed", isCollapsed);
  toggleBtn.textContent = isCollapsed ? "▶" : "◀";
  toggleBtn.title = isCollapsed ? "Expand sidebar" : "Collapse sidebar";
}

export function showHeapdumpEmptyState() {
  const area = document.getElementById("heapdumpReportArea");
  if (!area) return;
  area.style.display = "";
  area.innerHTML = `<div class="report-empty-state">
    <div class="es-icon">${ico('clipboard-list')}</div>
    <div class="es-title">${escapeHtml(t("heapdump.empty_state_title"))}</div>
    <div class="es-hint">${escapeHtml(t("heapdump.empty_state_hint"))}</div>
  </div>`;
}

export function renderHeapdumpSidebar() {
  const sidebarList = document.getElementById("heapdumpSidebarList");
  if (!sidebarList) return;
  const activeId = state.currentHeapdumpReportId;
  const sessionIds = new Set(state.openHeapdumpReports.map(r => r.id));

  const items = [];
  for (const r of state.openHeapdumpReports) {
    const report = r.report || {};
    const stats = report.stats || {};
    items.push({
      id: r.id,
      filename: r.filename || t("heapdump.history_unnamed"),
      isSession: true,
      hasAi: !!(report.ai_conclusion),
      health: heapdumpLevel(stats),
      title: r.filename || "",
      meta: fmtDate(report.created_at),
    });
  }
  for (const h of state.heapdumpHistoryReports || []) {
    if (sessionIds.has(h.id)) continue;
    items.push({
      id: h.id,
      filename: h.filename || t("heapdump.history_unnamed"),
      isSession: false,
      hasAi: !!h.has_ai,
      health: heapdumpLevelFromStatus(h),
      meta: fmtDate(h.created_at),
      title: h.filename || "",
    });
  }

  if (!items.length) {
    sidebarList.innerHTML = `<div class="sidebar-empty" style="color:var(--text-dim);text-align:center;padding:20px;font-size:12px;">${escapeHtml(t("heapdump.sidebar_empty"))}</div>`;
    return;
  }

  const gapNeeded = items.some(i => i.isSession) && items.some(i => !i.isSession);
  let html = "";
  let afterSession = false;
  for (const item of items) {
    if (!item.isSession && !afterSession && gapNeeded) {
      html += '<div class="sidebar-separator"></div>';
      afterSession = true;
    }
    const isActive = item.id === activeId;
    const healthClass = item.health ? `level-${item.health}` : "";
    const isAttached = state.activeReportContexts && state.activeReportContexts.some(c => c.type === "heapdump" && c.report_id === item.id);
    html += `<div class="sidebar-item${isActive ? " active" : ""}${item.hasAi ? " has-ai" : ""}" data-id="${escapeHtml(item.id)}" data-session="${item.isSession}">`;
    html += `<div class="si-row1">`;
    html += `<span class="si-checkbox">${ico('square')}</span>`;
    html += `<span class="si-health ${healthClass}"></span>`;
    html += `<span class="si-filename" title="${escapeHtml(item.title)}">${escapeHtml(item.filename)}</span>`;
    html += `<span class="si-ai-badge">AI</span>`;
    html += `</div>`;
    html += `<div class="si-row2">`;
    if (item.meta) html += `<span class="si-meta">${escapeHtml(item.meta)}</span>`;
    html += `<button class="si-attach-btn${isAttached ? " attached" : ""}" data-action="attach" title="${escapeHtml(t("reports.tab_attach"))}">${ico('paperclip')}</button>`;
    html += `<button class="si-close-btn" data-action="close" title="${escapeHtml(t("reports.tab_close"))}">${ico('x')}</button>`;
    html += `</div></div>`;
  }
  sidebarList.innerHTML = html;
}

function heapdumpLevel(stats) {
  if (!stats) return null;
  if (stats.oom_verdict && stats.oom_verdict !== "no_oom") return "bad";
  const leakSuspects = Array.isArray(stats.leak_suspects) ? stats.leak_suspects.length : 0;
  if (leakSuspects > 0) return "warn";
  return "good";
}

// 历史项 list 接口不含 leak_suspects / oom_verdict, 用 status 作视觉代理
function heapdumpLevelFromStatus(h) {
  if (!h?.status) return "good";
  if (h.status === "FAILED" || h.status === "CANCELLED") return "bad";
  if (h.status === "QUEUED" || h.status === "PARSING" || h.status === "CANCEL_REQUESTED") return "warn";
  return "good";  // DONE: 完成, 默认绿点 (无法区分是否有 leak, 用户点击查看详情)
}

// ---------- 关键发现 banner ----------

function renderHeapdumpBanner(stats) {
  if (!stats) return "";
  const leakSuspects = Array.isArray(stats.leak_suspects) ? stats.leak_suspects.length : 0;
  const oomVerdict = stats.oom_verdict && stats.oom_verdict !== "no_oom" ? stats.oom_verdict : null;
  if (!leakSuspects && !oomVerdict) return "";
  const parts = [];
  if (leakSuspects > 0) {
    parts.push(t("heapdump.banner_leaks", { n: leakSuspects }));
  }
  if (oomVerdict) {
    parts.push(t("heapdump.banner_oom"));
  }
  return `<div class="hd-banner danger">
    <span class="hb-status">${ico('triangle-alert')} ${escapeHtml(t("heapdump.banner_critical"))}</span>
    <span class="hb-detail">${escapeHtml(parts.join(" · "))}</span>
  </div>`;
}

// ---------- public API ----------

export function initHeapdump() {
  bindUploadZone();
  bindHeapdumpSubtabs();
  bindHeapdumpSidebarClicks();
  // listen to session switches to refresh history
  document.addEventListener("sessionChanged", refreshHeapdumpHistory);
  document.addEventListener("langchange", () => {
    if (state.currentHeapdumpReport) renderHeapdumpReport(state.currentHeapdumpReport);
    refreshHeapdumpHistory();
  });
}

function bindHeapdumpSidebarClicks() {
  const list = document.getElementById("heapdumpSidebarList");
  if (!list || list._heapdumpBound) return;
  list._heapdumpBound = true;
  list.addEventListener("click", async (e) => {
    const item = e.target.closest(".sidebar-item");
    if (!item) return;
    const id = item.dataset.id;
    if (!id) return;

    const action = e.target.closest("[data-action]");
    if (action) {
      if (action.dataset.action === "attach") {
        let entry = state.openHeapdumpReports.find(r => r.id === id);
        let report = entry?.report;
        if (!report) {
          const historyItem = (state.heapdumpHistoryReports || []).find(r => r.id === id);
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
          bindReportContext("heapdump", report);
          if (state.activeReportContexts.length === before && !state.activeReportContexts.some(c => c.type === "heapdump" && c.report_id === id)) {
            action.innerHTML = ico('triangle-alert');
            setTimeout(() => { action.innerHTML = ico('paperclip'); renderHeapdumpSidebar(); }, 1500);
            return;
          }
        }
        renderHeapdumpSidebar();
        return;
      }
      if (action.dataset.action === "close") {
        if (!confirm(t("heapdump.delete_confirm") || t("gc.delete_confirm"))) return;
        try {
          await api(`/api/heapdump-reports/${encodeURIComponent(id)}`, { method: "DELETE" });
          removeActiveReportContextByReport(id);
          closeReport(id);
          refreshHeapdumpHistory();
        } catch (e) { alert(e.message || t("heapdump.delete_failed")); }
        return;
      }
      return;
    }

    // Batch checkbox click
    const checkbox = e.target.closest(".si-checkbox");
    if (checkbox) {
      if (hdBatchMode && item.classList.contains("batch-active") && !item.classList.contains("batch-excluded")) {
        checkbox.innerHTML = checkbox.innerHTML === ico('check-square') ? ico('square') : ico('check-square');
        _updateHeapdumpBatchDeleteBtn();
      }
      return;
    }

    // Click on row → activate that report
    const entry = state.openHeapdumpReports.find(r => r.id === id);
    if (entry && entry.report) {
      state.currentHeapdumpReportId = id;
      state.currentHeapdumpReport = entry.report;
      renderHeapdumpReport(entry.report);
      renderHeapdumpSidebar();
    } else {
      openHeapdumpReport(id, { dontTrack: true });
    }
  });
}

// ---------- Heapdump Sidebar batch mode ----------
let hdBatchMode = false;
const hdSelectBtn = document.getElementById("heapdumpSidebarSelectBtn");
if (hdSelectBtn) {
  hdSelectBtn.onclick = () => {
    hdBatchMode = true;
    hdSelectBtn.style.display = "none";
    const batchBar = document.getElementById("heapdumpSidebarBatchBar");
    if (batchBar) batchBar.style.display = "flex";
    document.querySelectorAll("#heapdumpSidebarList .sidebar-item").forEach(el => {
      el.classList.add("batch-active");
    });
    _updateHeapdumpBatchDeleteBtn();
  };
}
const hdBatchCancelBtn = document.querySelector("#heapdumpSidebarBatchBar .sidebar-cancel-btn");
if (hdBatchCancelBtn) {
  hdBatchCancelBtn.onclick = () => {
    hdBatchMode = false;
    if (hdSelectBtn) hdSelectBtn.style.display = "";
    const batchBar = document.getElementById("heapdumpSidebarBatchBar");
    if (batchBar) batchBar.style.display = "none";
    document.querySelectorAll("#heapdumpSidebarList .sidebar-item").forEach(el => {
      el.classList.remove("batch-active", "batch-excluded");
    });
  };
}
const hdSelectAllBtn = document.querySelector("#heapdumpSidebarBatchBar .sidebar-selectall-btn");
if (hdSelectAllBtn) {
  hdSelectAllBtn.onclick = () => {
    const cb = document.querySelectorAll("#heapdumpSidebarList .sidebar-item:not(.batch-excluded) .si-checkbox");
    const allChecked = !cb.length || [...cb].every(c => c.innerHTML === ico('check-square'));
    cb.forEach(c => { c.innerHTML = allChecked ? ico('square') : ico('check-square'); });
    _updateHeapdumpBatchDeleteBtn();
  };
}
const hdBatchDeleteBtn = document.querySelector("#heapdumpSidebarBatchBar .sidebar-delete-btn");
if (hdBatchDeleteBtn) {
  hdBatchDeleteBtn.onclick = async () => {
    const checked = [...document.querySelectorAll("#heapdumpSidebarList .sidebar-item .si-checkbox")].filter(c => c.innerHTML === ico('check-square'));
    const ids = checked.map(c => c.closest(".sidebar-item").dataset.id).filter(Boolean);
    if (!ids.length) return;
    if (!confirm(t("reports.bulk_delete_confirm", { n: ids.length }))) return;
    const entries = ids.map(id => ({ id, type: "heapdump" }));
    await deleteReportEntries(entries);
    await refreshHeapdumpHistory();
    if (hdBatchCancelBtn) hdBatchCancelBtn.onclick();
  };
}

function _updateHeapdumpBatchDeleteBtn() {
  const checked = document.querySelectorAll("#heapdumpSidebarList .sidebar-item.batch-active:not(.batch-excluded) .si-checkbox");
  const selected = [...checked].filter(c => c.innerHTML === ico('check-square')).length;
  const deleteBtn = document.querySelector("#heapdumpSidebarBatchBar .sidebar-delete-btn");
  if (deleteBtn) {
    deleteBtn.disabled = selected === 0;
    deleteBtn.innerHTML = selected ? `${ico('trash-2')} ${t("reports.bulk_delete")} (${selected})` : `${ico('trash-2')} ${t("reports.bulk_delete")}`;
  }
}

export async function refreshHeapdumpHistory() {
  if (!state.currentSessionId) return renderHeapdumpHistoryList([]);
  try {
    const data = await api(`/api/heapdump-reports?session_id=${encodeURIComponent(state.currentSessionId)}`);
    const list = data.reports || [];
    state.heapdumpHistoryReports = list;
    renderHeapdumpSidebar();
    renderHeapdumpHistoryList(list);
    updateHeapdumpBadge(list);
  } catch (e) {
    // session might not be ready; ignore
  }
}

export async function openHeapdumpReport(reportId, opts = {}) {
  try {
    const r = await api(`/api/heapdump-reports/${encodeURIComponent(reportId)}`);
    if (opts.dontTrack) {
      state.currentHeapdumpReport = r;
      state.currentHeapdumpReportId = r.id;
      renderHeapdumpSidebar();
    } else {
      _trackOpen(r);
    }
    renderHeapdumpReport(r, opts);
    switchSubtab("current");
    // auto-start progress stream while not done
    if (r.status !== "DONE" && r.status !== "FAILED" && r.status !== "CANCELLED") {
      watchProgress(r.id);
    }
    if (opts.attach === true) attachToChat(r);
  } catch (e) {
    showError(e.message);
  }
}

// ---------- internals ----------

function _trackOpen(r) {
  const exists = state.openHeapdumpReports.find(x => x.id === r.id);
  if (exists) {
    exists.filename = r.filename;
    exists.report = r;
  } else {
    if (state.openHeapdumpReports.length >= 8) {
      const victim = state.openHeapdumpReports.findIndex(x => x.id !== state.currentHeapdumpReportId);
      if (victim >= 0) state.openHeapdumpReports.splice(victim, 1);
    }
    state.openHeapdumpReports.push({ id: r.id, filename: r.filename, report: r });
  }
  state.currentHeapdumpReport = r;
  state.currentHeapdumpReportId = r.id;
  renderHeapdumpSidebar();
  const uploadZone = document.getElementById("heapdumpUploadZone");
  if (uploadZone) {
    if (state.openHeapdumpReports.length > 0) uploadZone.classList.add("collapsed");
    else uploadZone.classList.remove("collapsed");
  }
}

export function enableHeapdumpSidebar() {
  const modeBody = document.querySelector('.mode-body[data-mode="heapdump"]');
  if (!modeBody || modeBody.classList.contains("sidebar-active")) return;
  modeBody.classList.add("sidebar-active");
  const sidebar = document.getElementById("heapdumpSidebar");
  if (sidebar) {
    sidebar.style.display = "flex";
    sidebar.classList.remove("sidebar-collapsed");
  }
  const tabs = modeBody.querySelector(".gc-tabs");
  if (tabs) tabs.style.display = "none";
  const oldUpload = document.getElementById("heapdumpUploadZone");
  if (oldUpload) oldUpload.style.display = "none";
  const historyBody = document.getElementById("heapdumpBodyHistory");
  if (historyBody) historyBody.style.display = "none";
  const toggleBtn = document.getElementById("heapdumpSidebarToggle");
  if (toggleBtn) toggleBtn.textContent = "◀";
  const toggleHandler = () => toggleHeapdumpSidebar();
  if (toggleBtn && !toggleBtn._heapdumpBound) {
    toggleBtn.addEventListener("click", toggleHandler);
    toggleBtn._heapdumpBound = true;
  }
  renderHeapdumpSidebar();
  // 显示空状态 (若无打开报告)
  if (state.openHeapdumpReports.length === 0) {
    showHeapdumpEmptyState();
  }
}

function switchSubtab(name) {
  document.querySelectorAll('#gcPanel .mode-body[data-mode="heapdump"] .tab').forEach(t => {
    t.classList.toggle("active", t.dataset.subtab === name);
  });
  const cur = document.getElementById("heapdumpBodyCurrent");
  const his = document.getElementById("heapdumpBodyHistory");
  if (cur) cur.style.display = name === "current" ? "" : "none";
  if (his) his.style.display = name === "history" ? "" : "none";
}

function bindHeapdumpSubtabs() {
  document.querySelectorAll('#gcPanel .mode-body[data-mode="heapdump"] .tab').forEach(t => {
    t.addEventListener("click", () => {
      switchSubtab(t.dataset.subtab);
      if (t.dataset.subtab === "history") refreshHeapdumpHistory();
    });
  });
}

function bindUploadZone() {
  const zones = [
    { zone: document.getElementById("heapdumpUploadZone"), input: document.getElementById("heapdumpFile") },
    { zone: document.getElementById("heapdumpSidebarUploadZone"), input: document.getElementById("heapdumpSidebarFile") },
  ];
  for (const { zone, input } of zones) {
    if (!zone || !input) continue;
    if (zone._heapdumpUploadBound) continue;
    zone._heapdumpUploadBound = true;

    zone.addEventListener("click", (e) => {
      if (e.target.closest(".upload-zone-toggle")) return;
      input.click();
    });
    input.addEventListener("change", e => {
      const f = e.target.files && e.target.files[0];
      if (f) uploadHeapdump(f);
      input.value = "";
    });
    zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("drag-over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
    zone.addEventListener("drop", e => {
      e.preventDefault();
      zone.classList.remove("drag-over");
      const f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) uploadHeapdump(f);
    });

    // 折叠/展开切换按钮（仅主区，sidebar 默认 collapsed 不加按钮）
    if (zone.id === "heapdumpUploadZone") {
      const toggleBtn = document.createElement("button");
      toggleBtn.className = "upload-zone-toggle";
      toggleBtn.type = "button";
      toggleBtn.textContent = "⤢";
      toggleBtn.title = t("gc.upload_zone_expand");
      toggleBtn.onclick = (e) => {
        e.stopPropagation();
        const expanded = !zone.classList.contains("collapsed");
        if (expanded) {
          zone.classList.add("collapsed");
          toggleBtn.textContent = "⤢";
          toggleBtn.title = t("gc.upload_zone_expand");
        } else {
          zone.classList.remove("collapsed");
          toggleBtn.textContent = "⤡";
          toggleBtn.title = t("gc.upload_zone_collapse");
        }
      };
      zone.appendChild(toggleBtn);
    }
  }
}

function _extOk(name) {
  const lower = name.toLowerCase();
  return ALLOWED_EXT.some(ext => lower.endsWith(ext));
}

async function uploadHeapdump(file) {
  if (!state.currentSessionId) {
    showError(t("chat.select_session_first"));
    return;
  }
  if (!_extOk(file.name)) {
    showError(t("gc.unsupported_format"));
    return;
  }
  hideError();
  showLoading(t("heapdump.uploading"));
  hideReportArea();
  try {
    // 1. init
    const init = await api("/api/heapdump-reports/uploads", { method: "POST" });
    const uid = init.upload_id;
    const chunkSize = init.chunk_size || CHUNK_SIZE;
    const total = file.size;
    const chunks = Math.ceil(total / chunkSize);

    // 2. PUT chunks with progress via XHR
    for (let i = 0; i < chunks; i++) {
      const start = i * chunkSize;
      const end = Math.min(start + chunkSize, total);
      const blob = file.slice(start, end);
      await uploadChunk(uid, i, blob, (i + 1) / chunks, i + 1, chunks);
    }

    // 3. complete
    showLoading(t("heapdump.parsing"));
    const comp = await api(`/api/heapdump-reports/uploads/${uid}/complete`, {
      method: "POST",
      body: JSON.stringify({ filename: file.name, session_id: state.currentSessionId }),
    });
    const rid = comp.report_id;
    hideLoading();
    await openHeapdumpReport(rid);
    refreshHeapdumpHistory();
  } catch (e) {
    hideLoading();
    showError(e.message);
  }
}

function uploadChunk(uid, idx, blob, pct, done, total) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/heapdump-reports/uploads/${uid}/chunks/${idx}`);
    xhr.withCredentials = true;
    const tok = getCookie("csrf_token");
    if (tok) xhr.setRequestHeader("X-CSRF-Token", decodeURIComponent(tok));
    xhr.upload.onprogress = e => {
      if (!e.lengthComputable) return;
      const chunkFrac = e.loaded / e.total; // 0..1 within current chunk
      const overallFrac = ((done - 1) + chunkFrac) / total; // 0..1 across all chunks
      setOverallProgress(overallFrac, t("heapdump.upload_chunk", { done, total, pct: Math.round(overallFrac * 100) }));
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else {
        let msg = t("heapdump.chunk_failed", { idx, status: xhr.status });
        try { const j = JSON.parse(xhr.responseText); msg = j.detail || j.error || msg; } catch {}
        reject(new Error(i18nText(msg)));
      }
    };
    xhr.onerror = () => reject(new Error("network error during chunk upload"));
    xhr.send(blob);
  });
}

// ---------- progress SSE ----------

let _progressEs = null;
function watchProgress(rid) {
  if (_progressEs) { try { _progressEs.close(); } catch {} _progressEs = null; }
  setOverallProgress(0, t("heapdump.phase.queued"));
  showProgress(true);
  const es = new EventSource(`/api/heapdump-reports/${rid}/progress`, { withCredentials: true });
  _progressEs = es;
    es.addEventListener("progress", e => {
      try {
        const d = JSON.parse(e.data);
        // 后端 progress 存 0-100 整数；统一为 0-1 给 fill 宽度
        let p = d.progress;
        if (p == null) p = 0;
        if (p > 1) p = p / 100;
        if (d.status === "DONE") p = 1;
        setOverallProgress(p, phaseLabel(d.status, d.phase, d.error));
        if (d.error) showError(d.error);
      } catch {}
    });
  es.addEventListener("done", async () => {
    es.close(); _progressEs = null;
    showProgress(false);
    try {
      const r = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}`);
      state.currentHeapdumpReport = r;
      if (r.status === "DONE") {
        renderHeapdumpReport(r);
        refreshHeapdumpHistory();
      } else if (r.status === "FAILED") {
        showError(`${t("heapdump.parse_failed")}: ${r.error || ""}`);
      }
    } catch {}
  });
  es.onerror = () => { es.close(); _progressEs = null; showProgress(false); };
}

export function heapdumpStatusLabel(s) {
  if (s === "DONE") return t("heapdump.status_done");
  if (s === "FAILED") return t("heapdump.status_failed");
  if (s === "PARSING") return t("heapdump.status_parsing");
  if (s === "QUEUED") return t("heapdump.status_queued");
  if (s === "CANCELLED" || s === "CANCEL_REQUESTED") return t("heapdump.status_cancelled");
  return s || "";
}

function statusLabel(s) { return heapdumpStatusLabel(s); }

function phaseLabel(status, phase, err) {
  if (err) return err;
  if (status === "QUEUED") return t("heapdump.phase.queued");
  if (status === "FAILED") return err || t("heapdump.parse_failed");
  if (phase === "stats") return t("heapdump.phase.stats");
  if (status === "PARSING" || status === "CANCEL_REQUESTED") return (phase || t("heapdump.phase.parsing"));
  if (status === "DONE") return ico('check');
  return status;
}

// ---------- rendering ----------

export function renderHeapdumpReportInto(container, report, options = {}) {
  if (!container) return;
  const isStandalone = !!options.isStandalone;

  // This function is the DONE-report renderer. Non-DONE reports in the main panel
  // are handled by `renderHeapdumpReport` (which starts SSE etc.). Standalone mode
  // uses this function for all statuses and renders static messages.

  if (report.status === "QUEUED" || report.status === "PARSING" || report.status === "CANCEL_REQUESTED") {
    container.innerHTML = `<div class="hd-empty">${escapeHtml(t("heapdump.standalone_not_ready"))}<br><span class="hd-phase">${escapeHtml(report.filename || "")}</span></div>`;
    return;
  }
  if (report.status === "FAILED") {
    const deleteBtn = isStandalone ? "" : `
      <div style="margin-top:10px;display:flex;gap:8px;justify-content:center;">
        <button class="btn hd-retry-btn" id="hdRetryDelete">${escapeHtml(t("heapdump.delete"))}</button>
      </div>`;
    container.innerHTML = `<div class="hd-empty"><div style="color:var(--red)">${escapeHtml(t("heapdump.parse_failed"))}</div>
      <div style="color:var(--text-dim);font-size:11px;margin-top:6px;">${escapeHtml(report.error || "")}</div>
      ${deleteBtn}</div>`;
    if (!isStandalone) {
      document.getElementById("hdRetryDelete")?.addEventListener("click", () => deleteReport(report.id));
    }
    return;
  }
  if (report.status === "CANCELLED") {
    container.innerHTML = `<div class="hd-empty">${escapeHtml(t("heapdump.cancelled"))}</div>`;
    return;
  }

  // DONE — render summary
  const stats = report.stats && typeof report.stats === "object" ? report.stats : safeJson(report.stats);
  const used = stats.usedHeapSize || stats.usedHeap || 0;
  const committed = stats.committedHeapSize || stats.heapSize || stats.totalHeapSize || null;
  const objects = stats.numObjects || stats.objectCount || stats.objects;
  const classes = stats.numClasses || stats.classCount || stats.classes;
  const clsloaders = stats.numClassLoaders || stats.classLoaderCount;
  const gcroots = stats.numGcRoots || stats.gcRoots;
  const sysProps = Array.isArray(stats.systemProperties) ? stats.systemProperties : [];
  const _sp = (name) => { const p = sysProps.find(x => x.name === name); return p ? p.value : ""; };
  const jvmVer = _sp("java.version");
  const javaHome = _sp("java.home");
  const osName = _sp("os.name");
  const osVer = _sp("os.version");
  const heapFmt = stats.heapFormat || "";
  const compOops = stats.useCompressedOops;
  const createdDate = stats.creationDate || stats.creationTime || "";

  const reportUrl = `${location.origin}/heapdump-report/${report.id}`;
  const openLink = isStandalone ? "" : `
    <a href="${reportUrl}" target="_blank" rel="noopener" class="btn" id="hdOpenNewBtn" style="font-size:11px;padding:4px 10px;text-decoration:none;flex-shrink:0;">${ico('link')} ${escapeHtml(t("heapdump.report_url"))}</a>`;
  const metaRow = `
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px;">
      <div style="flex:1;min-width:0;font-size:11px;color:var(--text-dim);">${escapeHtml(fmtBytes(report.size))} · ${escapeHtml(fmtDate(report.created_at))}</div>
      ${openLink}
    </div>`;

  const askAiBtn = isStandalone ? "" : `
    <button class="hd-btn-primary" id="hdAskAi">${escapeHtml(((state.activeReportContexts||[]).some(c=>c.type==="heapdump"&&c.report_id===report.id)) ? t("heapdump.ask_ai_continue") : t("heapdump.ask_ai"))}</button>`;

  const hasAi = !!report.ai_conclusion;
  const aiHtml = hasAi
    ? renderMarkdown(report.ai_conclusion)
    : `<div style="text-align:center;padding:20px;color:var(--text-dim);font-size:13px;">${escapeHtml(t("heapdump.no_ai_conclusion"))}</div>`;

  let aiSectionHtml = "";
  if (isStandalone) {
    aiSectionHtml = `
      <div class="ai-section" id="hdAiSection">
        <div class="ai-header" style="cursor:default;">
          ${ico('ChevronDown', { className: 'collapse-icon' })}
          <span class="ai-title">${escapeHtml(t("heapdump.ai_title"))}</span>
        </div>
        <div class="ai-body">
          <div class="ai-conclusion">${aiHtml}</div>
        </div>
      </div>`;
  } else {
    aiSectionHtml = `
      <div class="hd-ai-section collapsed" id="hdAiSection">
        <div class="hd-ai-header" id="hdAiHeader">
          <span>${escapeHtml(t("heapdump.ai_title"))}</span><span>▾</span>
        </div>
        <div class="hd-ai-body">
          ${report.ai_conclusion
            ? `<div class="hd-ai-conclusion">${renderMarkdown(report.ai_conclusion)}</div>`
            : `<div style="color:var(--text-dim);font-size:12px;">${escapeHtml(t("heapdump.ai_empty"))}</div>`}
        </div>
      </div>`;
  }

  const dataHtml = `
      <div class="hd-header" style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:nowrap;">
        <div style="min-width:0;flex:1;display:flex;align-items:center;gap:8px;">
          <span class="hd-status ${report.status}">${escapeHtml(statusLabel(report.status))}</span>
          <span class="filename" title="${escapeHtml(report.filename)}">${escapeHtml(report.filename)}</span>
        </div>
      </div>
      ${metaRow}
      ${renderHeapdumpBanner(stats)}

      <div class="hd-stat-grid">
        ${statCard(t("heapdump.heap_used"), fmtBytes(used), committed ? fmtBytes(committed) : "", "hd-stat-highlight")}
        ${statCard(t("heapdump.objects"), fmtNum(objects), "")}
        ${statCard(t("heapdump.classes"), fmtNum(classes), clsloaders ? t("heapdump.classloaders", { n: fmtNum(clsloaders) }) : "")}
        ${statCard(t("heapdump.gcroots"), fmtNum(gcroots), "")}
      </div>

      <div class="hd-section">
        <div class="hd-section-title" style="display:flex;align-items:center;justify-content:space-between;">
          <span>${escapeHtml(t("heapdump.section_leaks"))}</span>
          <button class="icon-btn" id="hdLeaksRefresh" title="${escapeHtml(t("heapdump.refresh_leaks"))}">${ico('refresh-cw')}</button>
        </div>
        <div class="hd-section-body" id="hdLeaks"><div class="hd-progress-inline"><span class="spin"></span>${escapeHtml(t("heapdump.leaks_running"))}</div></div>
      </div>

      <div class="hd-section" id="hdOomSection" style="display:none;">
        <div class="hd-section-title">${escapeHtml(t("heapdump.section_oom"))}</div>
        <div class="hd-section-body" id="hdOom"></div>
      </div>

      <div class="hd-section">
        <div class="hd-section-title">${escapeHtml(t("heapdump.section_pools"))}</div>
        <div class="hd-section-body" id="hdPools"><div class="hd-progress-inline"><span class="spin"></span>…</div></div>
      </div>

      <div class="hd-section">
        <div class="hd-section-title" style="display:flex;align-items:center;">
          <span style="flex:0 0 auto;">${escapeHtml(t("heapdump.section_pool_leaks"))}</span>
          <span style="flex:1"></span>
          <label style="margin-right:8px;">
            <span style="margin-right:4px;">${escapeHtml(t("heapdump.pool_leaks_threshold"))}:</span>
            <select class="hd-select" id="hdPoolLeaksThreshold">
              <option value="60000">1 min</option>
              <option value="300000" selected>5 min</option>
              <option value="600000">10 min</option>
              <option value="1800000">30 min</option>
            </select>
          </label>
          <button class="icon-btn" id="hdPoolLeaksRefresh" title="">${ico('refresh-cw')}</button>
        </div>
        <div class="hd-section-body" id="hdPoolLeaks"><div class="hd-progress-inline"><span class="spin"></span>…</div></div>
      </div>

      <div class="hd-section">
        <div class="hd-section-title" style="display:flex;align-items:center;">
          <span style="flex:0 0 auto;">${escapeHtml(t("heapdump.section_sql"))}</span>
          <span style="flex:1"></span>
          <label><input type="checkbox" id="hdSqlOnlyRisky" checked> ${escapeHtml(t("heapdump.sql_only_risky"))}</label>
        </div>
        <div class="hd-section-body" id="hdSql"><div class="hd-progress-inline"><span class="spin"></span>…</div></div>
      </div>

      <div class="hd-collapsible-wrap" id="hdThreadsWrap">
        <div class="hd-collapsible-head" id="hdThreadsHead">
          <span>${escapeHtml(t("heapdump.section_threads"))}</span>
          <span class="arrow">▾</span>
        </div>
        <div class="hd-collapsible-body" id="hdThreads"></div>
      </div>

      <div class="hd-section">
        <div class="hd-section-title">${escapeHtml(t("heapdump.section_classes"))}</div>
        <div class="hd-section-body" id="hdClasses"></div>
      </div>

      ${jvmVer || osName ? `<div class="hd-section">
        <div class="hd-section-title">${escapeHtml(t("heapdump.section_env"))}</div>
        <div class="hd-section-body">
          <table class="hd-table" style="max-width:100%;"><tbody>
            ${jvmVer ? `<tr><td style="color:var(--text-dim);white-space:nowrap;">${escapeHtml(t("heapdump.env_jvm_version"))}</td><td>${escapeHtml(jvmVer)}</td></tr>` : ""}
            ${javaHome ? `<tr><td style="color:var(--text-dim);white-space:nowrap;">${escapeHtml(t("heapdump.env_java_home"))}</td><td style="word-break:break-all;">${escapeHtml(javaHome)}</td></tr>` : ""}
            ${osName ? `<tr><td style="color:var(--text-dim);white-space:nowrap;">${escapeHtml(t("heapdump.env_os"))}</td><td>${escapeHtml(osName + " " + osVer)}</td></tr>` : ""}
            ${heapFmt ? `<tr><td style="color:var(--text-dim);white-space:nowrap;">${escapeHtml(t("heapdump.env_heap_format"))}</td><td>${escapeHtml(heapFmt)}</td></tr>` : ""}
            ${compOops != null ? `<tr><td style="color:var(--text-dim);white-space:nowrap;">${escapeHtml(t("heapdump.env_compressed_oops"))}</td><td>${escapeHtml(compOops)}</td></tr>` : ""}
            ${createdDate ? `<tr><td style="color:var(--text-dim);white-space:nowrap;">${escapeHtml(t("heapdump.env_created"))}</td><td>${escapeHtml(createdDate)}</td></tr>` : ""}
          </tbody></table>
        </div>
      </div>` : ""}

      ${askAiBtn}
      ${isStandalone ? "" : aiSectionHtml}
  `;

  container.innerHTML = isStandalone
    ? `<div class="hd-summary" data-rid="${escapeHtml(report.id)}">
         <div class="report-layout">
           <div class="report-data">${dataHtml}</div>
           <div class="report-ai">${aiSectionHtml}</div>
         </div>
       </div>`
    : `<div class="hd-summary" data-rid="${escapeHtml(report.id)}">${dataHtml}</div>`;

  // event bindings
  if (!isStandalone) {
    document.getElementById("hdAskAi")?.addEventListener("click", () => askAiAbout(report));
    document.getElementById("hdAiHeader")?.addEventListener("click", () => {
      document.getElementById("hdAiSection")?.classList.toggle("collapsed");
    });
  }
  document.getElementById("hdThreadsHead")?.addEventListener("click", () => toggleThreads(report.id));
  document.getElementById("hdLeaksRefresh")?.addEventListener("click", () => {
    _leaksCache.delete(report.id);
    _leaksLoaded.delete(report.id);
    renderLeaksInto(document.getElementById("hdLeaks"), report.id, { totalHeap: used, force: true });
  });

  renderLeaksInto(document.getElementById("hdLeaks"), report.id, { totalHeap: used });
  renderOomInto(document.getElementById("hdOom"), report.id);
  renderPoolsInto(document.getElementById("hdPools"), report.id);
  renderPoolLeaksInto(document.getElementById("hdPoolLeaks"), report.id);
  renderSqlInThreadsInto(document.getElementById("hdSql"), report.id);
  renderClassesInto(document.getElementById("hdClasses"), report.id);
  renderThreadsInto(document.getElementById("hdThreads"), report.id);
  _threadsLoaded.add(report.id);
}

function renderHeapdumpReport(report, opts = {}) {
  state.currentHeapdumpReport = report;
  state.currentHeapdumpReportId = report.id;
  if (!opts.dontTrack) _trackOpen(report);
  _threadsLoaded = new Set([...(_threadsLoaded || [])].filter(id => id === report.id));
  hideError();
  const zone = document.getElementById("heapdumpUploadZone");
  if (zone) zone.classList.add("collapsed");
  const area = document.getElementById("heapdumpReportArea");
  if (!area) return;
  area.style.display = "";
  showProgress(false);
  hideLoading();

  if (report.status === "QUEUED" || report.status === "PARSING" || report.status === "CANCEL_REQUESTED") {
    area.innerHTML = `<div class="hd-empty">${escapeHtml(phaseLabel(report.status, report.phase))}<br><span class="hd-phase">${escapeHtml(report.filename || "")}</span></div>`;
    watchProgress(report.id);
    return;
  }
  if (report.status === "FAILED") {
    area.innerHTML = `<div class="hd-empty"><div style="color:var(--red)">${escapeHtml(t("heapdump.parse_failed"))}</div>
      <div style="color:var(--text-dim);font-size:11px;margin-top:6px;">${escapeHtml(report.error || "")}</div>
      <div style="margin-top:10px;display:flex;gap:8px;justify-content:center;">
        <button class="btn hd-retry-btn" id="hdRetryDelete">${escapeHtml(t("heapdump.delete"))}</button>
      </div></div>`;
    document.getElementById("hdRetryDelete")?.addEventListener("click", () => deleteReport(report.id));
    return;
  }
  if (report.status === "CANCELLED") {
    area.innerHTML = `<div class="hd-empty">${escapeHtml(t("heapdump.cancelled"))}</div>`;
    return;
  }

  // DONE — delegate to shared renderer
  renderHeapdumpReportInto(area, report, { isStandalone: false });
}

function statCard(label, value, sub, cls = "") {
  return `<div class="hd-stat ${escapeHtml(cls)}">
    <div class="label">${escapeHtml(label)}</div>
    <div class="value">${escapeHtml(value || "—")}</div>
    ${sub ? `<div class="sub">${escapeHtml(sub)}</div>` : ""}
  </div>`;
}

// ---------- sections ----------

async function toggleThreads(rid) {
  const wrap = document.getElementById("hdThreadsWrap");
  const body = document.getElementById("hdThreads");
  if (!wrap || !body) return;
  const isOpen = !wrap.classList.contains("collapsed");
  if (isOpen) { wrap.classList.add("collapsed"); return; }
  wrap.classList.remove("collapsed");
  if (!_threadsLoaded.has(rid)) {
    body.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
    try {
      await renderThreadsInto(body, rid);
      _threadsLoaded.add(rid);
    } catch (e) {
      body.innerHTML = `<div class="hd-error-inline">${escapeHtml(e.message)}</div>`;
    }
  }
}

async function renderThreadsInto(c, rid) {
  const data = await api(`/api/heapdump-reports/${rid}/threads?top=100`);
  let rows = data.rows || [];
  rows = rows.slice().sort((a, b) => (b.retainedBytes || 0) - (a.retainedBytes || 0));
  c.innerHTML = tableHtml(
    [t("heapdump.column_name"), t("heapdump.threads_daemon"), t("heapdump.retained_short")],
    rows.map(r => [
      escapeHtml(r.name || "?"),
      r.daemon ? ico('check') : "",
      { v: r.retainedBytes || 0, cls: "num", txt: fmtBytes(r.retainedBytes) },
    ]),
    { cls: "threads-table" }
  );
}

async function renderLeaksInto(c, rid, opts = {}) {
  if (!c) return;
  // 已加载且 cache 中有渲染结果: 直接恢复 (避免切换报告时 spinner 残留)
  if (_leaksLoaded.has(rid) && !opts.force && _leaksCache.has(rid)) {
    c.innerHTML = _leaksCache.get(rid);
    return;
  }
  _leaksLoaded.add(rid);
  c.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>${escapeHtml(t("heapdump.leaks_running"))}</div>`;
  try {
    const init = await api(`/api/heapdump-reports/${rid}/leak-suspects`, { method: "POST" });
    let res = init;
    if (init.taskId && init.status !== "DONE") {
      res = await pollTask(rid, init.taskId, { intervalMs: 1500, maxTries: 600 });
    }
    const suspects = res?.suspects || res?.result?.suspects || [];
    const parts = res?.report?.parts || res?.result?.report?.parts || [];
    const totalHeap = opts.totalHeap || 0;
    if (!suspects.length) {
      const emptyHtml = `<div style="font-size:12px;color:var(--text-dim);">${escapeHtml(t("heapdump.leaks_none"))}</div>`;
      c.innerHTML = emptyHtml;
      _leaksCache.set(rid, emptyHtml);
      return;
    }
    function _htmlDesc(idx) {
      const p = parts[idx + 1];
      if (!p) return "";
      const desc = (p.result?.sections || []).find(s => s.name === "Description");
      return desc?.result?.text || "";
    }
    const html = `
      <div style="font-size:11px;color:var(--text-dim);margin-bottom:6px;">${escapeHtml(t("heapdump.leaks_found", { n: suspects.length }))}</div>
      ${suspects.map((s, i) => {
        const pct = totalHeap && s.retainedBytes ? ((s.retainedBytes / totalHeap) * 100).toFixed(1) : null;
        const htmlDesc = _htmlDesc(i);
        return `<div class="hd-leak-card">
          <div class="name">${escapeHtml(s.name || t("heapdump.suspect_title", { n: i + 1 }))}</div>
          <div class="desc">${htmlDesc || escapeHtml(s.description || "")}</div>
          <div class="meta">
            <span>${escapeHtml(t("heapdump.suspect_class"))}: <b>${escapeHtml(s.className || "")}</b></span>
            <span>${escapeHtml(t("heapdump.suspect_instances"))}: <b>${fmtNum(s.instanceCount)}</b></span>
            <span>retained: <b>${fmtBytes(s.retainedBytes)}</b></span>
            ${pct ? `<span>${escapeHtml(t("heapdump.leaks_retained_pct", { pct }))}</span>` : ""}
          </div>
        </div>`;
      }).join("")}
    `;
    c.innerHTML = html;
    _leaksCache.set(rid, html);
  } catch (e) {
    console.error("[heapdump] leaks failed", e);
    c.innerHTML = `<div class="hd-error-inline">${escapeHtml(t("heapdump.leaks_failed"))}${e.message ? `<br><span style="color:var(--text-dim);font-size:11px;">${escapeHtml(e.message)}</span>` : ""}</div>`;
    _leaksLoaded.delete(rid);
  }
}

async function renderOomInto(c, rid) {
  if (!c) return;
  try {
    const data = await api(`/api/heapdump-reports/${rid}/oom-diagnosis`);
    const sec = document.getElementById("hdOomSection");
    if (!data || data.verdict === "no_oom" || !data.verdict) {
      if (sec) sec.style.display = "none";
      return;
    }
    if (sec) sec.style.display = "";
    const threads = data.triggerThreads || [];
    const hint = data.culpritHint;
    c.innerHTML = `
      <div class="hd-leak-card">
        <div class="name">${escapeHtml(data.verdict || "—")}</div>
        <div class="meta">
          <span>${escapeHtml(t("heapdump.oom_total_heap"))}: <b>${escapeHtml(data.totalHeapHuman || data.totalHeap || "")}</b></span>
          <span>${escapeHtml(t("heapdump.oom_trigger_threads", { n: data.triggerThreadCount || 0 }))}</span>
        </div>
      </div>
      ${threads.length ? `<div style="margin-top:8px;font-size:11px;color:var(--text-dim);">${escapeHtml(t("heapdump.oom_threads_title"))}:</div>
      <table class="hd-table" style="margin-top:4px;">
        <tr><th>${escapeHtml(t("heapdump.oom_thread_name"))}</th><th>${escapeHtml(t("heapdump.oom_retained"))}</th><th>${escapeHtml(t("heapdump.oom_reason"))}</th></tr>
        ${threads.map(t => `<tr>
          <td>${escapeHtml(t.threadName || "")}</td>
          <td style="white-space:nowrap;">${escapeHtml(t.retained || "")}</td>
          <td>${escapeHtml(t.reason || "")}</td>
        </tr>`).join("")}
      </table>` : ""}
      ${hint ? `<div class="hd-leak-card" style="margin-top:8px;border-left:3px solid var(--warn, #f59e0b);">
        <div class="name">${escapeHtml(t("heapdump.oom_hint_title"))}</div>
        <div class="desc">${escapeHtml(hint.threadName || "")} — ${escapeHtml(hint.retained || "")} (${escapeHtml(String(hint.percentHeap || 0))}%)</div>
        <div class="meta"><span>${escapeHtml(hint.note || "")}</span></div>
      </div>` : ""}
    `;
  } catch (e) {
    const sec = document.getElementById("hdOomSection");
    if (sec) sec.style.display = "none";
  }
}

async function renderPoolsInto(c, rid) {
  if (!c) return;
  try {
    const raw = await api(`/api/heapdump-reports/${rid}/connection-pools`);
    const pools = unwrapList(raw, ["pools", "data", "rows", "datasources", "connections"]);
    if (!pools.length) {
      // 仅当 API 响应中根本没有已知的列表 key 时才警告;
      // 空列表 ({count:0, pools:[]}) 是合法的"无连接池"状态, 不警告
      const hasListKey = raw && typeof raw === "object" && (
        Array.isArray(raw.pools) || Array.isArray(raw.data) || Array.isArray(raw.rows) ||
        Array.isArray(raw.datasources) || Array.isArray(raw.connections)
      );
      if (!hasListKey) {
        const keys = Object.keys(raw || {}).join(",");
        console.warn("[heapdump] pools: no list key in response, keys:", keys);
      }
      c.innerHTML = `<div class="hd-empty-inline">${escapeHtml(t("heapdump.pools_none"))}</div>`;
      return;
    }
    c.innerHTML = pools.map(p => renderPoolCard(p)).join("");
  } catch (e) {
    console.error("[heapdump] pools failed", e);
    c.innerHTML = `<div class="hd-error-inline">${escapeHtml(t("heapdump.error_loading_section"))}: ${escapeHtml(e.message || "")}</div>`;
  }
}

function _kv(list, name) {
  if (!Array.isArray(list)) return "";
  const hit = list.find(e => e && String(e.name || "").toLowerCase() === String(name).toLowerCase());
  return hit ? (hit.value ?? "") : "";
}

function _kvNum(list, name) {
  const v = _kv(list, name);
  const n = Number(v);
  return isNaN(n) ? 0 : n;
}

function renderPoolCard(p) {
  let type = p.type || p.poolType || "";
  const label = p.label || "";
  if (!type) {
    const n = String(label || p.name || p.poolName || p.jndiName || p.className || p.dataSourceName || "");
    if (/druid/i.test(n)) type = "Druid";
    else if (/hikari/i.test(n)) type = "HikariCP";
    else if (/tomcat|dbcp/i.test(n)) type = "Tomcat JDBC";
    else type = "Pool";
  }
  const name = label || p.name || p.poolName || p.jndiName || p.dataSourceName || "";
  const stats = Array.isArray(p.stats) ? p.stats : [];
  const config = Array.isArray(p.config) ? p.config : [];
  const conns = p.connections || {};
  const max = _kvNum(stats, "maxActive") || _kvNum(stats, "maximumPoolSize") || _kvNum(stats, "maxSize")
    || Number(p.maximumPoolSize ?? p.max ?? p.maxPoolSize ?? p.maxActive ?? p.maxSize ?? 0) || 0;
  const active = Array.isArray(conns.active) ? conns.active.length
    : (_kvNum(stats, "activeCount") || _kvNum(stats, "active")
       || Number(p.activeConnections ?? p.active ?? p.numActive ?? p.activeCount ?? p.size ?? p.busyConnections ?? 0) || 0);
  const idle = Array.isArray(conns.idle) ? conns.idle.length
    : (conns.idleCount ?? _kvNum(stats, "idleCount") ?? _kvNum(stats, "idle")
       ?? Number(p.idleConnections ?? p.idle ?? p.numIdle ?? p.idleCount ?? p.poolingCount ?? 0)) || 0;
  const waiting = _kvNum(stats, "waitThreadCount") || _kvNum(stats, "waitingThreads") || _kvNum(stats, "pendingCount")
    || Number(p.waitingThreads ?? p.waiting ?? p.threadsAwaitingConnection ?? p.waitThreadCount ?? p.pendingCount ?? 0) || 0;
  const jdbcUrl = _kv(config, "url") || _kv(config, "jdbcUrl") || p.jdbcUrl || p.url || p.jdbcURL || "";
  const username = _kv(config, "username");
  const pct = max > 0 ? Math.min(100, Math.round((active / max) * 100)) : 0;
  const fillCls = pct >= 95 ? "high" : pct >= 80 ? "mid" : "ok";
  const waitCls = waiting > 0 ? (waiting >= 5 ? "danger" : "warn") : "";
  const secretKeys = /password|secret|token|key/i;
  const shownConfig = (config || []).filter(e => e && !secretKeys.test(String(e.name || "")));
  const configHtml = shownConfig.length ? `
    <div class="hd-pool-config">
      ${shownConfig.map(e => `<span class="k">${escapeHtml(e.name)}</span><span class="v">${escapeHtml(String(e.value ?? ""))}</span>`).join("")}
    </div>` : "";
  return `<div class="hd-pool-card">
    <div class="hd-pool-head">
      <span class="hd-pool-type">${escapeHtml(type)}</span>
      <span class="hd-pool-name">${escapeHtml(name) || "—"}</span>
    </div>
    <div class="hd-pool-stats">
      <div class="hd-pool-stat"><span class="label">${escapeHtml(t("heapdump.pools_max"))}</span><span class="val">${fmtNum(max)}</span></div>
      <div class="hd-pool-stat"><span class="label">${escapeHtml(t("heapdump.pools_active"))}</span><span class="val">${fmtNum(active)}</span></div>
      <div class="hd-pool-stat"><span class="label">${escapeHtml(t("heapdump.pools_idle"))}</span><span class="val">${fmtNum(idle)}</span></div>
      <div class="hd-pool-stat waiting ${waitCls}"><span class="label">${escapeHtml(t("heapdump.pools_waiting"))}</span><span class="val">${fmtNum(waiting)}</span></div>
    </div>
    <div class="hd-pool-bar"><div class="fill ${fillCls}" style="width:${pct}%"></div></div>
    ${jdbcUrl ? `<div class="hd-pool-jdbc"><span class="k">${escapeHtml(t("heapdump.pools_jdbc_url"))}:</span> <span class="v">${escapeHtml(jdbcUrl)}</span></div>` : ""}
    ${configHtml}
  </div>`;
}

async function renderPoolLeaksInto(c, rid, opts = {}) {
  if (!c) return;
  const thresholdMs = opts.thresholdMs ?? _POOL_LEAKS_THRESHOLD_MS;
  _POOL_LEAKS_THRESHOLD_MS = thresholdMs;

  const thresholdSel = document.getElementById("hdPoolLeaksThreshold");
  const refreshBtn = document.getElementById("hdPoolLeaksRefresh");
  if (thresholdSel) {
    thresholdSel.value = String(thresholdMs);
    thresholdSel.onchange = () => {
      _POOL_LEAKS_THRESHOLD_MS = parseInt(thresholdSel.value, 10) || 300000;
      draw({ showSpinner: true });
    };
  }
  if (refreshBtn) {
    refreshBtn.onclick = () => draw({ showSpinner: true });
  }

  let inflight = null;
  const draw = async (drawOpts = {}) => {
    const { showSpinner = true } = drawOpts;
    if (showSpinner) {
      c.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
    }
    if (inflight) { try { await inflight; } catch {} }
    const fetchPromise = (async () => {
      return await api(`/api/heapdump-reports/${rid}/pool-leaks?thresholdMs=${_POOL_LEAKS_THRESHOLD_MS}`);
    })();
    inflight = fetchPromise;
    try {
      const raw = await fetchPromise;
      const all = unwrapList(raw, ["connections", "leaks", "leakedConnections", "matches", "rows", "data", "entries"]);
      const minutes = Math.round(_POOL_LEAKS_THRESHOLD_MS / 60000);
      const suspectedCount = typeof raw.suspectedLeaks === "number" ? raw.suspectedLeaks : all.filter(c => c.suspectedLeak === true).length;
      const borrowedTotal = typeof raw.borrowedTotal === "number" ? raw.borrowedTotal : all.length;
      if (!all.length) {
        c.innerHTML = `<div class="hd-empty-inline">${escapeHtml(t("heapdump.pool_leaks_none_borrowed", { n: minutes }))}</div>`;
        return;
      }
      const summary = suspectedCount > 0
        ? `<div style="font-size:11px;color:var(--red);margin-bottom:6px;">${escapeHtml(t("heapdump.pool_leaks_summary", { s: suspectedCount, t: borrowedTotal, n: minutes }))}</div>`
        : `<div style="font-size:11px;color:var(--text-dim);margin-bottom:6px;">${escapeHtml(t("heapdump.pool_leaks_summary_clean", { t: borrowedTotal, n: minutes }))}</div>`;
      c.innerHTML = summary + all.map((it, idx) => renderPoolLeakCard(it, idx, _POOL_LEAKS_THRESHOLD_MS)).join("");
      c.querySelectorAll(".hd-stack-toggle").forEach(btn => {
        btn.addEventListener("click", () => {
          const pre = btn.parentElement.querySelector(".hd-stack-pre");
          if (!pre) return;
          const show = pre.style.display === "none";
          pre.style.display = show ? "" : "none";
          btn.textContent = show ? t("heapdump.pool_leaks_hide_stack") : t("heapdump.pool_leaks_show_stack");
        });
      });
    } catch (e) {
      console.error("[heapdump] pool-leaks failed", e);
      c.innerHTML = `<div class="hd-error-inline">${escapeHtml(t("heapdump.error_loading_section"))}: ${escapeHtml(e.message || "")}</div>`;
    } finally {
      if (inflight === fetchPromise) inflight = null;
    }
  };
  draw();
}

function renderPoolLeakCard(it, idx, thresholdMs) {
  const connLabel = it.label || it.connectionLabel || "";
  const poolType = it.pool || it.poolType || "";
  const borrowedForMs = Number(it.borrowedAgeMs ?? it.borrowedForMs ?? it.heldForMs ?? it.durationMs ?? it.elapsedMs ?? it.borrowTimeMs ?? 0) || 0;
  const isSuspect = it.suspectedLeak === true || borrowedForMs >= thresholdMs;
  const stack = normalizeStack(it.borrowStack ?? it.stackTrace ?? it.stack ?? it.stacktrace ?? it.callStack);
  const durStr = fmtDuration(borrowedForMs);
  const stackId = `hdPlStack${idx}`;
  const leftColor = isSuspect ? "var(--red)" : "var(--accent)";
  const typeBg = isSuspect ? "var(--red)" : "var(--accent)";
  const typeText = isSuspect ? ico('triangle-alert') : (poolType ? escapeHtml(poolType).slice(0,1).toUpperCase() : "C");
  return `<div class="hd-pool-card" style="border-left-color:${leftColor};">
    <div class="hd-pool-head">
      <span class="hd-pool-type" style="background:${typeBg};">${typeText}</span>
      <span class="hd-pool-name">${escapeHtml(connLabel) || `#${it.connectionId || idx+1}`}</span>
      ${isSuspect ? `<span class="hd-risk-pill">${escapeHtml(t("heapdump.pool_leaks_suspected"))}</span>` : ""}
    </div>
    <div style="font-size:11px;color:${isSuspect ? "var(--red)" : "var(--text-dim)"};margin-bottom:4px;">
      ${escapeHtml(t("heapdump.pool_leaks_borrowed_for", { dur: durStr }))}
    </div>
    ${stack.length ? `
      <button class="hd-stack-toggle" data-target="${stackId}">${escapeHtml(t("heapdump.pool_leaks_show_stack"))}</button>
      <pre class="hd-stack-pre" id="${stackId}" style="display:none;">${escapeHtml(stack.join("\n"))}</pre>
    ` : ""}
  </div>`;
}

async function renderSqlInThreadsInto(c, rid, opts = {}) {
  if (!c) return;
  const onlyRisky = opts.onlyRisky ?? _SQL_ONLY_RISKY;
  _SQL_ONLY_RISKY = onlyRisky;

  const cb = document.getElementById("hdSqlOnlyRisky");
  if (cb) {
    cb.checked = !!_SQL_ONLY_RISKY;
    cb.onchange = () => {
      _SQL_ONLY_RISKY = !!cb.checked;
      draw({ showSpinner: true });
    };
  }

  let inflight = null;
  const draw = async (drawOpts = {}) => {
    const { showSpinner = true } = drawOpts;
    if (showSpinner) {
      c.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
    }
    if (inflight) { try { await inflight; } catch {} }
    const url = `/api/heapdump-reports/${rid}/sql-in-threads?onlyRisky=${_SQL_ONLY_RISKY ? "true" : "false"}`;
    const fetchPromise = (async () => api(url))();
    inflight = fetchPromise;
    try {
      const raw = await fetchPromise;
      const items = unwrapList(raw, ["matches", "threads", "rows", "data", "entries", "statements"]);
      if (!items.length) {
        c.innerHTML = `<div class="hd-empty-inline">${escapeHtml(t("heapdump.sql_none"))}</div>`;
        return;
      }
      c.innerHTML = items.map((it, idx) => renderSqlCard(it, idx)).join("");
      c.querySelectorAll(".hd-stack-toggle").forEach(btn => {
        btn.addEventListener("click", () => {
          const pre = btn.parentElement.querySelector(".hd-stack-pre");
          if (!pre) return;
          const show = pre.style.display === "none";
          pre.style.display = show ? "" : "none";
          btn.textContent = show ? t("heapdump.sql_hide_stack") : t("heapdump.sql_show_stack");
        });
      });
    } catch (e) {
      console.error("[heapdump] sql-in-threads failed", e);
      c.innerHTML = `<div class="hd-error-inline">${escapeHtml(t("heapdump.error_loading_section"))}: ${escapeHtml(e.message || "")}</div>`;
    } finally {
      if (inflight === fetchPromise) inflight = null;
    }
  };
  draw();
}

function isRiskySql(it) {
  if (it.risky === true || it.risky === false) return !!it.risky;
  const s = String(it.state || "").toUpperCase();
  return s === "BLOCKED" || s === "RUNNABLE";
}

function renderSqlCard(it, idx) {
  const threadName = it.threadName || it.thread || it.thread_name || "?";
  const sql = String(it.sql || it.sqlText || it.query || it.sql_text || it.statement || "").trim();
  const risky = isRiskySql(it);
  const stack = normalizeStack(it.stackTrace ?? it.stack ?? it.callStack);
  const stackId = `hdSqlStack${idx}`;
  const riskyPill = risky ? `<span class="hd-risk-pill">${escapeHtml(t("heapdump.sql_risky"))}</span>` : "";
  return `<div class="hd-pool-card" style="${risky ? "border-left-color:var(--red);" : ""}">
    <div class="hd-pool-head">
      <span class="hd-pool-name">${escapeHtml(threadName)}</span>
      ${riskyPill}
    </div>
    ${sql ? `<pre class="hd-sql">${escapeHtml(sql)}</pre>` : ""}
    ${stack.length ? `
      <button class="hd-stack-toggle">${escapeHtml(t("heapdump.sql_show_stack"))}</button>
      <pre class="hd-stack-pre" id="${stackId}" style="display:none;">${escapeHtml(stack.join("\n"))}</pre>
    ` : ""}
  </div>`;
}

async function renderClassesInto(c, rid) {
  if (!c) return;
  let inflight = null;
  let lastData = null;
  const draw = async (opts = {}) => {
    const { showSpinner = true } = opts;
    if (showSpinner) {
      c.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
    }
    if (inflight) {
      try { await inflight; } catch {}
    }
    const sortParam = _CLASSES_STATE.sort === "shallowBytes" ? "shallow"
                  : _CLASSES_STATE.sort === "count" ? "count" : "retained";
    const fetchPromise = (async () => {
      const data = await api(`/api/heapdump-reports/${rid}/histogram?top=${_CLASSES_STATE.top}&sort=${sortParam}`);
      lastData = data;
      return data;
    })();
    inflight = fetchPromise;
    try {
      const data = await fetchPromise;
      const rows = data.rows || [];
      const isSorted = k => _CLASSES_STATE.sort === k;
      c.innerHTML = `
        <div class="hd-toolbar">
          <span style="font-size:11px;color:var(--text-dim);">${escapeHtml(t("heapdump.classes_top"))}:</span>
          <span class="hd-seg" id="hdTopSeg">
            ${[50, 200, 500, 1000].map(n => `<button class="hd-seg-btn${n === _CLASSES_STATE.top ? " selected" : ""}" data-n="${n}">${n}</button>`).join("")}
          </span>
        </div>
        ${tableHtml(
          [
            `<th>${escapeHtml(t("heapdump.classes_col_class"))}</th>`,
            `<th class="sortable num${isSorted("count") ? " sorted-desc" : ""}" data-sort="count">${escapeHtml(t("heapdump.classes_col_objects"))}</th>`,
            `<th class="sortable num${isSorted("shallowBytes") ? " sorted-desc" : ""}" data-sort="shallowBytes">${escapeHtml(t("heapdump.classes_col_shallow"))}</th>`,
            `<th class="sortable num${isSorted("retainedBytes") ? " sorted-desc" : ""}" data-sort="retainedBytes">${escapeHtml(t("heapdump.classes_col_retained"))}</th>`,
          ],
          rows.map(r => [
            escapeHtml(r.label || "?"),
            { v: r.count || 0, cls: "num", txt: fmtNum(r.count) },
            { v: r.shallowBytes || 0, cls: "num", txt: fmtBytes(r.shallowBytes) },
            { v: r.retainedBytes || 0, cls: "num", txt: fmtBytes(r.retainedBytes) },
          ]),
          { cls: "classes-table" }
        )}
      `;
      c.querySelectorAll(".hd-seg-btn").forEach(b => {
        b.addEventListener("click", () => {
          _CLASSES_STATE.top = parseInt(b.dataset.n, 10);
          draw({ showSpinner: true });
        });
      });
      c.querySelectorAll("th.sortable").forEach(th => {
        th.addEventListener("click", () => {
          const k = th.dataset.sort;
          if (_CLASSES_STATE.sort === k) return;
          _CLASSES_STATE.sort = k;
          draw({ showSpinner: false });
        });
      });
    } catch (e) {
      c.innerHTML = `<div class="hd-error-inline">${escapeHtml(e.message)}</div>`;
    } finally {
      if (inflight === fetchPromise) inflight = null;
    }
  };
  draw();
}

function askAiAbout(report) {
  try {
    const isAttached = (state.activeReportContexts || []).some(c => c.type === "heapdump" && c.report_id === report.id);
    if (!isAttached) attachToChat(report);
    const ta = document.getElementById("msg");
    if (ta) {
      ta.value = t("heapdump.ask_ai_prompt");
      ta.focus();
      try { ta.dispatchEvent(new Event("input", { bubbles: true })); } catch {}
    }
    const btn = document.getElementById("hdAskAi");
    if (btn) {
      btn.classList.add("attached");
      btn.textContent = t("heapdump.ask_ai_continue");
    }
  } catch (e) {
    showError(e.message);
  }
}

function fmtDuration(ms) {
  const total = Math.max(0, Math.floor((ms || 0) / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  if (m > 0) return t("heapdump.duration_minutes", { m, s });
  return t("heapdump.duration_seconds", { s });
}

function normalizeStack(v) {
  if (!v) return [];
  if (Array.isArray(v)) return v.map(x => String(x).trim()).filter(Boolean);
  const s = String(v);
  return s.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
}

function unwrapList(data, keys) {
  if (Array.isArray(data)) return data;
  if (data && typeof data === "object") {
    for (const k of keys) {
      if (Array.isArray(data[k])) return data[k];
    }
    const r = data.result;
    if (r && typeof r === "object") {
      for (const k of keys) {
        if (Array.isArray(r[k])) return r[k];
      }
      for (const k of Object.keys(r)) {
        if (Array.isArray(r[k])) return r[k];
      }
    }
    for (const k of Object.keys(data)) {
      if (Array.isArray(data[k])) return data[k];
    }
  }
  return [];
}

function tableHtml(cols, rows, opts = {}) {
  const cls = opts.cls ? ` ${opts.cls}` : "";
  return `<div class="hd-table-wrap"><table class="hd-table${cls}"><thead><tr>${
    cols.map(c => {
      if (typeof c === "string" && c.startsWith("<th")) return c;
      return `<th>${escapeHtml(typeof c === "string" ? c : c.label || c)}</th>`;
    }).join("")
  }</tr></thead><tbody>${
    rows.map(r => `<tr>${r.map(cell => {
      if (cell && typeof cell === "object") {
        const cls = cell.cls ? ` class="${cell.cls}"` : "";
        return `<td${cls}>${cell.txt != null ? cell.txt : ""}</td>`;
      }
      return `<td>${cell == null ? "" : cell}</td>`;
    }).join("")}</tr>`).join("")
  }</tbody></table></div>`;
}

// ---------- history list ----------

function renderHeapdumpHistoryList(reports) {
  const el = document.getElementById("heapdumpHistoryList");
  const empty = document.getElementById("heapdumpHistoryEmpty");
  if (!el) return;
  const cnt = document.getElementById("heapdumpHistoryCount");
  if (cnt) cnt.textContent = reports.length ? `(${reports.length})` : "";
  if (!reports.length) {
    el.innerHTML = ""; if (empty) empty.style.display = "";
    return;
  }
  if (empty) empty.style.display = "none";
  el.innerHTML = renderReportBulkToolbar("heapdump", t) + reports.map(r => {
    const meta = [];
    if (r.used_heap) meta.push(fmtBytes(r.used_heap));
    if (r.num_objects) meta.push(fmtNum(r.num_objects) + " objs");
    return `<div class="history-item" data-id="${escapeHtml(r.id)}" data-type="heapdump">
      <input type="checkbox" class="report-select" data-id="${escapeHtml(r.id)}" data-type="heapdump" data-session="${escapeHtml(r.session_id || "")}" />
      <div class="history-content">
        <div class="row1">
          <div class="row1-left">${escapeHtml(r.filename || t("heapdump.history_unnamed"))}</div>
          ${r.has_ai ? '<span class="ai-tag">AI</span>' : ""}
        </div>
        <div class="row2">
          <span>${escapeHtml(meta.join(" · "))}</span>
          <span>${escapeHtml(fmtDate(r.created_at))} <button type="button" class="del" data-id="${escapeHtml(r.id)}">${escapeHtml(t("reports.delete"))}</button></span>
        </div>
      </div>
    </div>`;
  }).join("");
  el.querySelectorAll(".history-item").forEach(it => {
    it.addEventListener("click", e => {
      if (isReportActionTarget(e.target)) return;
      openHeapdumpReport(it.dataset.id, { dontTrack: true });
    });
  });
  el.querySelectorAll(".del").forEach(b => {
    b.addEventListener("click", e => { e.stopPropagation(); deleteReport(b.dataset.id); });
  });
}

export function closeReport(rid) {
  state.openHeapdumpReports = state.openHeapdumpReports.filter(r => r.id !== rid);
  if (state.currentHeapdumpReportId === rid) {
    state.currentHeapdumpReport = null;
    state.currentHeapdumpReportId = null;
    if (state.openHeapdumpReports.length === 0) {
      showHeapdumpEmptyState();
    } else {
      document.getElementById("heapdumpReportArea").style.display = "none";
    }
  }
  renderHeapdumpSidebar();
  const uploadZone = document.getElementById("heapdumpUploadZone");
  if (uploadZone && state.openHeapdumpReports.length === 0) {
    uploadZone.classList.remove("collapsed");
  }
}

async function deleteReport(rid) {
  if (!confirm(t("heapdump.delete_confirm") || t("gc.delete_confirm"))) return;
  try {
    await api(`/api/heapdump-reports/${encodeURIComponent(rid)}`, { method: "DELETE" });
    closeReport(rid);
    refreshHeapdumpHistory();
  } catch (e) { alert(e.message || t("heapdump.delete_failed")); }
}

function attachToChat(report) {
  if (!report || !state.currentSessionId) return;
  const before = state.activeReportContexts.length;
  bindReportContext("heapdump", report);
  renderHeapdumpSidebar();
  renderActiveReportContext();
  if (state.activeReportContexts.length === before &&
      !state.activeReportContexts.some(c => c.type === "heapdump" && c.report_id === report.id)) {
    showError(t("reports.attach_limit", { limit: ACTIVE_REPORT_CONTEXT_LIMIT }));
    return;
  }
  const btn = document.getElementById("hdAskAi");
  if (btn) {
    btn.classList.add("attached");
    btn.textContent = t("heapdump.ask_ai_continue");
  }
}

function _defaultAttach(report) {
  attachToChat(report);
}

function updateHeapdumpBadge(reports) {
  const b = document.getElementById("heapdumpTabCount");
  if (b) b.textContent = reports.length;
}

// ---------- UI helpers ----------

function showLoading(text) {
  const l = document.getElementById("heapdumpLoading");
  if (l) { l.textContent = text; l.style.display = ""; }
  const z = document.getElementById("heapdumpUploadZone"); if (z) z.classList.add("collapsed");
  document.getElementById("heapdumpReportArea").style.display = "none";
}
function hideLoading() { const l = document.getElementById("heapdumpLoading"); if (l) l.style.display = "none"; }
function showError(msg) {
  const el = document.getElementById("heapdumpError"); if (!el) return;
  el.textContent = msg; el.style.display = "";
}
function hideError() { const el = document.getElementById("heapdumpError"); if (el) el.style.display = "none"; }
function hideUploadZone() {
  const z = document.getElementById("heapdumpUploadZone");
  if (z) z.classList.add("collapsed");
}
function showUploadZone() {
  const z = document.getElementById("heapdumpUploadZone");
  if (z) z.classList.remove("collapsed");
  document.getElementById("heapdumpReportArea").style.display = "none";
}
function hideReportArea() { document.getElementById("heapdumpReportArea").style.display = "none"; }
function showProgress(on) {
  const p = document.getElementById("heapdumpProgress"); if (!p) return;
  p.style.display = on ? "" : "none";
}
function setOverallProgress(pct, text) {
  const fill = document.getElementById("hdProgressFill");
  const txt = document.getElementById("hdProgressText");
  if (fill) fill.style.width = `${Math.round((pct || 0) * 100)}%`;
  if (txt) txt.textContent = text || "";
}

function safeJson(s) {
  if (!s) return {};
  if (typeof s === "object") return s;
  try { return JSON.parse(s); } catch { return {}; }
}
function fmtBytes(n) {
  if (n == null || isNaN(n)) return "—";
  n = Number(n);
  const u = ["B","KB","MB","GB","TB"]; let i = 0; let v = n;
  while (Math.abs(v) >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (u[i] === "B" ? `${Math.round(v)} B` : `${v.toFixed(1)} ${u[i]}`);
}
function fmtNum(n) {
  if (n == null || isNaN(n)) return "—";
  return Number(n).toLocaleString();
}
function getCookie(name) {
  const m = document.cookie.match(new RegExp("(?:^|; )" + name.replace(/[-.]/g, "\\$&") + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : "";
}
