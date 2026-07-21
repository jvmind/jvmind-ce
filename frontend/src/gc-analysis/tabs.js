import { state } from "../state.js";
import { app } from "../app.js";
import { escapeHtml, calculateGCHealth } from "../shared.js";
import { t } from "../../i18n/index.js";
import { bindReportContext, ACTIVE_REPORT_CONTEXT_LIMIT } from "./context.js";
import { renderReport } from "./render.js";
import { renderGcSidebar, updateReportHeader } from "./sidebar.js";
import { ico } from "../icons.js";

function _isGcSidebar() {
  return !!document.querySelector('.mode-body[data-mode="gc"].sidebar-active');
}

const OPEN_REPORT_LIMIT = 8;

function _openListFor(type) {
  return type === "jstack" ? state.openJstackReports : state.openGcReports;
}

function _activeIdFor(type) {
  return type === "jstack" ? state.currentJstackReportId : state.currentReportId;
}

export function openReport(type, report, { attach = false, dontTrack = false } = {}) {
  if (!report || !report.id) return;
  const list = _openListFor(type);
  const activeId = _activeIdFor(type);
  const idx = list.findIndex(r => r.id === report.id);
  if (!dontTrack) {
    if (idx >= 0) {
      list[idx] = { id: report.id, filename: report.filename || "", report };
    } else {
      if (list.length >= OPEN_REPORT_LIMIT) {
        const victim = list.findIndex(r => r.id !== activeId);
        if (victim >= 0) list.splice(victim, 1);
      }
      list.push({ id: report.id, filename: report.filename || "", report });
    }
  }
  if (type === "jstack") {
    state.currentJstackReport = report;
    state.currentJstackReportId = report.id;
  } else {
    state.currentReport = report;
    state.currentReportId = report.id;
  }
  if (attach) bindReportContext(type, report);
  if (!_isGcSidebar()) renderReportTabs(type);
  if (type === "gc") { renderGcSidebar(); updateReportHeader("gc", report); }
  if (type === "jstack") app.renderJstackReport(report);
  else renderReport(report);
}

export function activateReportTab(type, id) {
  const list = _openListFor(type);
  const entry = list.find(r => r.id === id);
  if (!entry) return;
  if (type === "jstack") {
    state.currentJstackReport = entry.report;
    state.currentJstackReportId = id;
  } else {
    state.currentReport = entry.report;
    state.currentReportId = id;
  }
  if (!_isGcSidebar()) renderReportTabs(type);
  if (type === "gc") { renderGcSidebar(); updateReportHeader("gc", entry.report); }
  if (type === "jstack") app.renderJstackReport(entry.report);
  else renderReport(entry.report);
}

export function closeReportTab(type, id) {
  const list = _openListFor(type);
  const idx = list.findIndex(r => r.id === id);
  if (idx < 0) return;
  const wasActive = _activeIdFor(type) === id;
  list.splice(idx, 1);
  if (!wasActive) {
    if (!_isGcSidebar()) renderReportTabs(type);
    if (type === "gc") renderGcSidebar();
    return;
  }
  const next = list[idx] || list[idx - 1] || null;
  if (next) {
    activateReportTab(type, next.id);
  } else {
    if (type === "jstack") {
      state.currentJstackReport = null;
      state.currentJstackReportId = null;
      const area = document.getElementById("jstackReportArea");
      if (area) {
        area.style.display = "";
        area.innerHTML = `<div class="report-empty-state">
          <div class="es-icon">${ico('clipboard-list')}</div>
          <div class="es-title">${escapeHtml(t("jstack.empty_state_title"))}</div>
          <div class="es-hint">${escapeHtml(t("jstack.empty_state_hint"))}</div>
        </div>`;
      }
    } else {
      state.currentReport = null;
      state.currentReportId = null;
      const area = document.getElementById("gcReportArea");
      if (area) {
        area.style.display = "";
        area.innerHTML = `<div class="report-empty-state">
          <div class="es-icon">${ico('clipboard-list')}</div>
          <div class="es-title">${escapeHtml(t("gc.empty_state_title"))}</div>
          <div class="es-hint">${escapeHtml(t("gc.empty_state_hint"))}</div>
        </div>`;
      }
    }
    if (!_isGcSidebar()) renderReportTabs(type);
    if (type === "gc") {
      renderGcSidebar();
    }
  }
}

export function renderReportTabs(type) {
  const el = document.getElementById(type === "jstack" ? "jstackReportTabs" : "gcReportTabs");
  if (!el) return;
  if (document.body.classList.contains("report-only")) {
    el.style.display = "none";
    return;
  }
  const list = _openListFor(type);
  if (!list.length) {
    el.style.display = "none";
    el.innerHTML = "";
    return;
  }
  const activeId = _activeIdFor(type);
  el.style.display = "flex";
  el.innerHTML = list.map(entry => {
    const r = entry.report || {};
    const isActive = entry.id === activeId;
    const attached = state.activeReportContexts.some(c => c.report_id === entry.id);
    let healthDot = "";
    if (type === "gc" && r.stats) {
      const level = calculateGCHealth(r.stats);
      healthDot = `<span class="health-dot level-${level}"></span>`;
    }
    const name = escapeHtml(entry.filename || r.filename || t("gc.history_unnamed"));
    return `
      <div class="report-tab ${isActive ? "active" : ""}" data-id="${escapeHtml(entry.id)}" title="${name}">
        ${healthDot}
        <span class="report-tab-name">${name}</span>
        <button type="button" class="report-tab-attach ${attached ? "attached" : ""}" data-attach="${escapeHtml(entry.id)}" title="${attached ? t("reports.tab_attached") : t("reports.tab_attach")}">${ico('paperclip')}</button>
        <button type="button" class="report-tab-close" data-close="${escapeHtml(entry.id)}" title="${t("reports.tab_close")}">×</button>
      </div>
    `;
  }).join("");
  el.querySelectorAll(".report-tab").forEach(tab => {
    tab.onclick = (e) => {
      if (e.target.closest("[data-attach]") || e.target.closest("[data-close]")) return;
      activateReportTab(type, tab.dataset.id);
    };
  });
  el.querySelectorAll("[data-attach]").forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      const entry = list.find(r => r.id === btn.dataset.attach);
      if (!entry) return;
      const before = state.activeReportContexts.length;
      bindReportContext(type, entry.report);
      if (state.activeReportContexts.length === before && !state.activeReportContexts.some(c => c.report_id === entry.id)) {
        btn.innerHTML = ico('triangle-alert');
        btn.title = t("reports.attach_limit", { limit: ACTIVE_REPORT_CONTEXT_LIMIT });
        setTimeout(() => { btn.innerHTML = ico('paperclip'); renderReportTabs(type); }, 1500);
        return;
      }
      renderReportTabs(type);
    };
  });
  el.querySelectorAll("[data-close]").forEach(btn => {
    btn.onclick = (e) => {
      e.stopPropagation();
      closeReportTab(type, btn.dataset.close);
    };
  });
  const uploadZone = document.getElementById(type === "jstack" ? "jstackUploadZone" : "uploadZone");
  if (uploadZone) {
    if (list.length > 0) {
      uploadZone.classList.add("collapsed");
    } else {
      uploadZone.classList.remove("collapsed");
    }
    const toggleBtn = uploadZone.querySelector(".upload-zone-toggle");
    if (toggleBtn) {
      toggleBtn.textContent = uploadZone.classList.contains("collapsed") ? "⤢" : "⤡";
      toggleBtn.title = uploadZone.classList.contains("collapsed") ? t("gc.upload_zone_expand") : t("gc.upload_zone_collapse");
    }
  }
}
