import { state } from "../state.js";
import { app } from "../app.js";
import { escapeHtml, calculateGCHealth, isReportActionTarget, renderReportBulkToolbar, fmtDate } from "../shared.js";
import { api } from "../api.js";
import { t } from "../../i18n/index.js";
import { reportScopeUrl, bindReportBulkActions, deleteReportEntries, removeActiveReportContextByReport } from "./context.js";
import { openReport, closeReportTab } from "./tabs.js";
import { openHeapdumpReport, heapdumpStatusLabel } from "../heapdump-analysis/index.js";
import { renderGcSidebar } from "./sidebar.js";
import { ico } from "../icons.js";

export async function refreshHistory() {
  if (!state.currentSessionId) return;
  try {
    const r = await api(`/api/sessions/${state.currentSessionId}/gc/reports`);
    const list = r.reports || [];
    state.gcHistoryReports = list;
    renderGcSidebar();
    const el = document.getElementById("historyList");
    const empty = document.getElementById("historyEmpty");
    document.getElementById("historyCount").textContent = list.length ? `(${list.length})` : "";
    // mode-tab 计数按当前会话语义展示，避免切会话后数字残留误导
    const gcModeCount = document.getElementById("gcTabCount");
    if (gcModeCount) gcModeCount.textContent = list.length;
    if (!list.length) {
      el.innerHTML = "";
      empty.style.display = "";
      updateBadge();
      return;
    }
    empty.style.display = "none";
    el.innerHTML = renderReportBulkToolbar("gc", t) + list.map(r => {
      const level = r.stats ? calculateGCHealth(r.stats) : null;
      const healthDot = level ? `<span class="health-dot level-${level}"></span>` : "";
      return `
      <div class="history-item" data-id="${r.id}">
        <input type="checkbox" class="report-select" data-id="${r.id}" data-type="gc" data-session="${state.currentSessionId}" />
        <div class="history-content">
          <div class="row1">
            <div class="row1-left">
              ${healthDot}<span>${escapeHtml(r.filename || t("gc.history_unnamed"))}</span>
            </div>
            ${r.has_ai ? '<span class="ai-tag">AI</span>' : ''}
          </div>
          <div class="row2">
            <span>${r.collector || "?"} · ${t("gc.history_meta", { n: r.events_total || 0, ms: (r.total_pause_ms||0).toFixed(0) })}</span>
            <span>${fmtDate(r.created_at)} <button type="button" class="del" data-id="${r.id}">${t("reports.delete")}</button></span>
          </div>
        </div>
      </div>
    `;
    }).join("");
    el.querySelectorAll(".history-item").forEach(it => {
      it.onclick = async (e) => {
        if (isReportActionTarget(e.target)) return;
        const rid = it.dataset.id;
        const full = await api(`/api/sessions/${state.currentSessionId}/gc/reports/${rid}`);
        // 切到"当前报告" tab
        const gcMode = document.querySelector('.mode-body[data-mode="gc"]');
        gcMode.querySelectorAll(".gc-tabs .tab").forEach(x => x.classList.remove("active"));
        gcMode.querySelector('.gc-tabs .tab[data-tab="current"]').classList.add("active");
        gcMode.querySelector("#gcBodyCurrent").style.display = "";
        gcMode.querySelector("#gcBodyHistory").style.display = "none";
        openReport("gc", full);
      };
    });
    el.querySelectorAll(".del").forEach(d => {
      d.onclick = async (e) => {
        e.stopPropagation();
        if (!confirm(t("gc.delete_confirm"))) return;
        await api(`/api/sessions/${state.currentSessionId}/gc/reports/${d.dataset.id}`, { method: "DELETE" });
        removeActiveReportContextByReport(d.dataset.id);
        closeReportTab("gc", d.dataset.id);
        refreshHistory();
        refreshAllReportHistory();
        app.updateQuotaUI();
      };
    });
    bindReportBulkActions(el, async (selected) => {
      await deleteReportEntries(selected);
      refreshHistory();
      refreshAllReportHistory();
      app.updateQuotaUI();
    });
    updateBadge();
  } catch (e) {
    console.error(e);
  }
}

export function updateBadge(n) {
  const b = document.getElementById("gcBadge");
  if (!b) return;
  if (n === undefined) {
    const url = reportScopeUrl();
    if (!url) { b.style.display = "none"; return; }
    api(url).then(r => updateBadge((r.reports || []).length)).catch(() => {
      if (!state.currentSessionId) { b.style.display = "none"; return; }
      Promise.all([
        api(`/api/sessions/${state.currentSessionId}/gc/reports`).then(r => (r.reports||[]).length).catch(() => 0),
        api(`/api/sessions/${state.currentSessionId}/jstack/reports`).then(r => (r.reports||[]).length).catch(() => 0),
        api(`/api/heapdump-reports?session_id=${encodeURIComponent(state.currentSessionId)}`).then(r => (r.reports||[]).length).catch(() => 0),
      ]).then(([gcCount, jstackCount, heapdumpCount]) => updateBadge(gcCount + jstackCount + heapdumpCount));
    });
    return;
  }
  if (n > 0) {
    b.style.display = "";
    b.textContent = n;
  } else {
    b.style.display = "none";
  }
}

export async function refreshAllReportHistory() {
  try {
    const url = reportScopeUrl();
    if (!url) return;
    const r = await api(url);
    state.allReports = r.reports || [];
    renderAllReportHistory();
    updateBadge(state.allReports.length);
    const all = document.getElementById("reportsTabCount");
    if (all) all.textContent = state.allReports.length;
    // 注意：gcTabCount / jstackTabCount 不在此处写入，它们由 refreshHistory /
    // refreshJstackHistory 按当前会话维度填写，避免切会话时显示作用域聚合的旧数字。
  } catch (e) {
    console.error(e);
  }
}

export function renderAllReportHistory() {
  const el = document.getElementById("allReportsList");
  const empty = document.getElementById("allReportsEmpty");
  if (!el || !empty) return;
  const list = state.allReports.filter(r => state.reportFilter === "all" || r.type === state.reportFilter || (state.reportFilter === "ai" && r.has_ai));
  if (!list.length) {
    el.innerHTML = "";
    empty.style.display = "";
    return;
  }
  empty.style.display = "none";
  el.innerHTML = renderReportBulkToolbar("all", t) + list.map(r => {
    const s = r.summary || {};
    let meta = "";
    let healthDot = "";
    if (r.type === "gc") {
      meta = `${s.collector || "?"} · ${t("gc.history_meta", { n: s.events_total || 0, ms: (s.total_pause_ms || 0).toFixed(0) })}`;
      if (r.stats) {
        const level = calculateGCHealth(r.stats);
        healthDot = `<span class="health-dot level-${level}"></span>`;
      }
    } else if (r.type === "jstack") {
      meta = t("jstack.history_meta", { n: s.total_threads || 0, blocked: s.blocked_count || 0, deadlocks: s.deadlock_count || 0 });
    } else if (r.type === "heapdump") {
      const parts = [];
      if (s.size_bytes) parts.push(`${(s.size_bytes/1024/1024).toFixed(1)} MB`);
      if (s.num_objects) parts.push(`${s.num_objects} objs`);
      meta = parts.join(" · ");
    }
    const typeLabel = r.type === "gc" ? "GC" : r.type === "jstack" ? "JStack" : "HD";
    const sessionLabel = (r.session_title && r.session_title.trim())
      ? r.session_title
      : `${t("reports.untitled_session")} · #${(r.session_id || "").slice(0, 6)}`;
    const sessionChip = r.session_id
      ? `<span class="session-chip" title="${escapeHtml(sessionLabel)}">${ico('message-square')} ${escapeHtml(sessionLabel)}</span>`
      : "";
    return `
      <div class="history-item" data-id="${r.id}" data-type="${r.type}" data-session="${r.session_id}">
        <input type="checkbox" class="report-select" data-id="${r.id}" data-type="${r.type}" data-session="${r.session_id}" />
        <div class="history-content">
          <div class="row1">
            <div class="row1-left">
              ${healthDot}<span><span class="type-tag">${typeLabel}</span>${escapeHtml(r.filename || t("gc.history_unnamed"))}</span>
            </div>
            ${r.has_ai ? '<span class="ai-tag">AI</span>' : ''}
          </div>
          ${sessionChip ? `<div class="row2">${sessionChip}</div>` : ""}
          <div class="row2">
            <span>${meta}</span>
            <span>${fmtDate(r.created_at)} <button type="button" class="del" data-id="${r.id}" data-type="${r.type}" data-session="${r.session_id}">${t("reports.delete")}</button></span>
          </div>
        </div>
      </div>
    `;
  }).join("");
  el.querySelectorAll(".history-item").forEach(it => {
    it.onclick = async (e) => {
      const sid = it.dataset.session;
      const rid = it.dataset.id;
      const type = it.dataset.type;
      if (isReportActionTarget(e.target)) return;
      if (state.currentSessionId !== sid) {
        await app.selectSession(sid);
      }
      if (type === "gc") {
        const full = await api(`/api/sessions/${sid}/gc/reports/${rid}`);
        document.querySelectorAll(".mode-tab").forEach(x => x.classList.remove("active"));
        document.querySelector('.mode-tab[data-mode="gc"]').classList.add("active");
        document.querySelectorAll(".mode-body").forEach(x => x.style.display = "none");
        const gcMode = document.querySelector('.mode-body[data-mode="gc"]');
        gcMode.style.display = "";
        gcMode.querySelectorAll(".gc-tabs .tab").forEach(x => x.classList.remove("active"));
        gcMode.querySelector('.gc-tabs .tab[data-tab="current"]').classList.add("active");
        gcMode.querySelector("#gcBodyCurrent").style.display = "";
        gcMode.querySelector("#gcBodyHistory").style.display = "none";
        openReport("gc", full);
      } else if (type === "heapdump") {
        document.querySelectorAll(".mode-tab").forEach(x => x.classList.remove("active"));
        document.querySelector('.mode-tab[data-mode="heapdump"]').classList.add("active");
        document.querySelectorAll(".mode-body").forEach(x => x.style.display = "none");
        document.querySelector('.mode-body[data-mode="heapdump"]').style.display = "";
        openHeapdumpReport(rid);
      } else {
        const full = await api(`/api/sessions/${sid}/jstack/reports/${rid}`);
        document.querySelectorAll(".mode-tab").forEach(x => x.classList.remove("active"));
        document.querySelector('.mode-tab[data-mode="jstack"]').classList.add("active");
        document.querySelectorAll(".mode-body").forEach(x => x.style.display = "none");
        const parent = document.querySelector('.mode-body[data-mode="jstack"]');
        parent.style.display = "";
        parent.querySelectorAll('.gc-tabs .tab').forEach(x => x.classList.remove("active"));
        parent.querySelector('.gc-tabs .tab[data-subtab="current"]').classList.add("active");
        parent.querySelector("#jstackBodyCurrent").style.display = "";
        parent.querySelector("#jstackBodyHistory").style.display = "none";
        openReport("jstack", full);
      }
    };
  });
  el.querySelectorAll(".del").forEach(d => {
    d.onclick = async (e) => {
      e.stopPropagation();
      if (!confirm(t("gc.delete_confirm"))) return;
      const sid = d.dataset.session;
      const rid = d.dataset.id;
      const rtype = d.dataset.type;
      try {
        if (rtype === "heapdump") {
          await api(`/api/heapdump-reports/${rid}`, { method: "DELETE" });
        } else {
          await api(`/api/sessions/${sid}/${rtype}/reports/${rid}`, { method: "DELETE" });
        }
      } catch (err) {
        alert(err.message || "Delete failed");
        return;
      }
      removeActiveReportContextByReport(rid);
      closeReportTab(rtype, rid);
      refreshAllReportHistory();
      refreshHistory();
      app.refreshJstackHistory();
      if (app.refreshHeapdumpHistory) app.refreshHeapdumpHistory();
      app.updateQuotaUI();
    };
  });
  bindReportBulkActions(el, async (selected) => {
    await deleteReportEntries(selected);
    refreshAllReportHistory();
    refreshHistory();
    app.refreshJstackHistory();
    app.updateQuotaUI();
  });
}
