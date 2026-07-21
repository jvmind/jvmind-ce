import { state } from "../state.js";
import { escapeHtml, getSelectedReports, updateReportBulkBar } from "../shared.js";
import { api } from "../api.js";
import { t } from "../../i18n/index.js";
import { closeReportTab, renderReportTabs } from "./tabs.js";
import { closeReport as closeHeapdumpReport } from "../heapdump-analysis/index.js";

export const ACTIVE_REPORT_CONTEXT_LIMIT = 5;

export function reportScopeUrl() {
  if (state.sessionTab === "org") {
    if (!state.currentOrg) return "";
    return `/api/me/reports?org_id=${encodeURIComponent(state.currentOrg.id)}`;
  }
  return "/api/me/reports?personal=true";
}

export function addActiveReportContext(ctx) {
  const key = `${ctx.type}:${ctx.session_id}:${ctx.report_id}`;
  state.activeReportContexts = state.activeReportContexts.filter(x => `${x.type}:${x.session_id}:${x.report_id}` !== key);
  if (state.activeReportContexts.length >= ACTIVE_REPORT_CONTEXT_LIMIT) {
    return;
  }
  state.activeReportContexts.push(ctx);
  renderActiveReportContext();
}

export function removeActiveReportContext(index) {
  state.activeReportContexts.splice(index, 1);
  renderActiveReportContext();
}

export function clearActiveReportContext() {
  state.activeReportContexts = [];
  renderActiveReportContext();
}

export function removeActiveReportContextByReport(report_id) {
  state.activeReportContexts = state.activeReportContexts.filter(ctx => ctx.report_id !== report_id);
  renderActiveReportContext();
}

export function bindReportBulkActions(container, onDelete) {
  const selectAll = container.querySelector(".report-select-all");
  if (selectAll) {
    selectAll.onchange = () => {
      container.querySelectorAll(".report-select").forEach(cb => {
        cb.checked = selectAll.checked;
      });
      updateReportBulkBar(container);
    };
  }
  container.querySelectorAll(".report-select").forEach(cb => {
    cb.onclick = e => e.stopPropagation();
    cb.onchange = () => updateReportBulkBar(container);
  });
  const bulkDelete = container.querySelector(".bulk-delete");
  if (bulkDelete) {
    bulkDelete.onclick = async (e) => {
      e.stopPropagation();
      const selected = getSelectedReports(container);
      if (!selected.length) return;
      if (!confirm(t("reports.bulk_delete_confirm", { n: selected.length }))) return;
      await onDelete(selected);
    };
  }
  updateReportBulkBar(container);
}

export async function deleteReportEntries(entries) {
  for (const item of entries) {
    if (item.type === "heapdump") {
      await api(`/api/heapdump-reports/${item.id}`, { method: "DELETE" });
      // Heapdump lives in a separate module; static import is safe here
      // because the heapdump module only references context.js symbols
      // inside function bodies (no top-level evaluation dependency).
      closeHeapdumpReport(item.id);
    } else {
      await api(`/api/sessions/${item.sessionId}/${item.type}/reports/${item.id}`, { method: "DELETE" });
      closeReportTab(item.type, item.id);
    }
    removeActiveReportContextByReport(item.id);
  }
}

export function renderActiveReportContext() {
  const el = document.getElementById("activeReportContext");
  if (!el) return;
  _syncReportTabsAttachState();
  if (!state.activeReportContexts.length) {
    el.style.display = "none";
    el.innerHTML = "";
    return;
  }
  el.style.display = "flex";
  const used = state.activeReportContexts.length;
  const limit = ACTIVE_REPORT_CONTEXT_LIMIT;
  const reachLimit = used >= limit;
  const quotaCls = reachLimit ? "report-context-quota full" : "report-context-quota";
  el.innerHTML = `
    <span class="report-context-label">${t("context.label")}</span>
    <span class="${quotaCls}" title="${t("context.quota_tip", { limit })}">${t("context.quota", { used, limit })}</span>
    <div class="report-context-list">${state.activeReportContexts.map((ctx, i) => {
      const label = ctx.type === "gc" ? "GC" : ctx.type === "heapdump" ? "Heap" : "JStack";
      return `<span class="report-context-item"><strong>R${i + 1}</strong> · <strong>${label}</strong> · ${escapeHtml(ctx.filename || ctx.report_id)} <button type="button" data-index="${i}" title="${t("context.remove")}">×</button></span>`;
    }).join("")}</div>
    <button type="button" data-clear="1" title="${t("context.clear")}">${t("context.clear")}</button>
  `;
  el.querySelectorAll("button[data-index]").forEach(btn => {
    btn.onclick = () => removeActiveReportContext(Number(btn.dataset.index));
  });
  const clearBtn = el.querySelector("button[data-clear]");
  if (clearBtn) clearBtn.onclick = clearActiveReportContext;
}

export function bindReportContext(type, report) {
  if (!report || !state.currentSessionId) return;
  addActiveReportContext({
    type,
    session_id: state.currentSessionId,
    report_id: report.id,
    file_id: report.file_id || "",
    filename: report.filename || "",
  });
}

function _syncReportTabsAttachState() {
  if (state.openGcReports.length) {
    const el = document.getElementById("gcReportTabs");
    if (el && el.style.display !== "none") renderReportTabs("gc");
  }
  if (state.openJstackReports.length) {
    const el = document.getElementById("jstackReportTabs");
    if (el && el.style.display !== "none") renderReportTabs("jstack");
  }
  // Update sidebar attach buttons
  if (document.querySelector('.mode-body[data-mode="gc"].sidebar-active')) {
    document.querySelectorAll("#gcSidebarList .si-attach-btn").forEach(btn => {
      const item = btn.closest(".sidebar-item");
      if (!item) return;
      const isAttached = state.activeReportContexts.some(c => c.report_id === item.dataset.id);
      btn.classList.toggle("attached", isAttached);
    });
  }
  if (document.querySelector('.mode-body[data-mode="heapdump"].sidebar-active')) {
    document.querySelectorAll("#heapdumpSidebarList .si-attach-btn").forEach(btn => {
      const item = btn.closest(".sidebar-item");
      if (!item) return;
      const isAttached = state.activeReportContexts.some(c => c.report_id === item.dataset.id);
      btn.classList.toggle("attached", isAttached);
    });
  }
}
