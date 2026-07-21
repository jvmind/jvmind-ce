// DOM event bindings (side effects executed at import time).
import { t } from "../../i18n/index.js";
import { app } from "../app.js";
import { state } from "../state.js";
import { analysisOpen, analysisClose } from "./panel.js";
import { uploadGCFile } from "./upload.js";
import { refreshHistory, refreshAllReportHistory, renderAllReportHistory } from "./history.js";
import { enableGcSidebar, renderGcSidebar, toggleGcSidebar } from "./sidebar.js";
import { openReport, activateReportTab, closeReportTab } from "./tabs.js";
import { api } from "../api.js";
import { bindReportContext, ACTIVE_REPORT_CONTEXT_LIMIT, removeActiveReportContextByReport, deleteReportEntries } from "./context.js";
import { ico } from "../icons.js";

document.getElementById("analysisFab").onclick = analysisOpen;
document.getElementById("gcClose").onclick = analysisClose;

// Toggle sidebar collapse
const gcToggleBtn = document.getElementById("gcSidebarToggle");
if (gcToggleBtn) gcToggleBtn.onclick = () => toggleGcSidebar();

// 模式切换（GC / 线程分析）
document.querySelectorAll(".mode-tab").forEach(tab => {
  tab.onclick = () => {
    document.querySelectorAll(".mode-tab").forEach(x => x.classList.remove("active"));
    tab.classList.add("active");
    const mode = tab.dataset.mode;
    document.querySelectorAll(".mode-body").forEach(x => x.style.display = "none");
    document.querySelector(`.mode-body[data-mode="${mode}"]`).style.display = "";
    if (mode === "gc") { enableGcSidebar(); refreshHistory(); }
    if (mode === "jstack") { app.enableJstackSidebar(); app.refreshJstackHistory(); }
    if (mode === "heapdump") { app.enableHeapdumpSidebar && app.enableHeapdumpSidebar(); app.refreshHeapdumpHistory && app.refreshHeapdumpHistory(); }
    if (mode === "reports") refreshAllReportHistory();
  };
});

// Module init: enable sidebar if GC or jstack mode is already the active mode
const initialMode = document.querySelector('.mode-tab.active')?.dataset.mode;
if (initialMode === "gc") {
  enableGcSidebar();
  refreshHistory();
} else if (initialMode === "jstack") {
  app.enableJstackSidebar();
  app.refreshJstackHistory();
}

// Tab 切换（GC）
document.querySelectorAll('.mode-body[data-mode="gc"] .gc-tabs .tab').forEach(tab => {
  tab.onclick = () => {
    const parent = tab.closest('.mode-body');
    parent.querySelectorAll('.gc-tabs .tab').forEach(x => x.classList.remove("active"));
    tab.classList.add("active");
    const sub = tab.dataset.tab;
    parent.querySelector("#gcBodyCurrent").style.display = sub === "current" ? "" : "none";
    parent.querySelector("#gcBodyHistory").style.display = sub === "history" ? "" : "none";
    if (sub === "history") refreshHistory();
  };
});

// Tab 切换（JStack）— subtabs are now hidden when sidebar is enabled.
// Kept for fallback compatibility; no-op when sidebar-active class is present.
document.querySelectorAll('.mode-body[data-mode="jstack"] .gc-tabs .tab').forEach(tab => {
  tab.onclick = () => {
    const parent = tab.closest('.mode-body');
    parent.querySelectorAll('.gc-tabs .tab').forEach(x => x.classList.remove("active"));
    tab.classList.add("active");
    const sub = tab.dataset.subtab;
    if (parent.classList.contains("sidebar-active")) return;
    parent.querySelector("#jstackBodyCurrent").style.display = sub === "current" ? "" : "none";
    parent.querySelector("#jstackBodyHistory").style.display = sub === "history" ? "" : "none";
    if (sub === "history") app.refreshJstackHistory();
  };
});

document.querySelectorAll("[data-report-filter]").forEach(btn => {
  btn.onclick = () => {
    state.reportFilter = btn.dataset.reportFilter;
    document.querySelectorAll("[data-report-filter]").forEach(x => x.classList.remove("active"));
    btn.classList.add("active");
    renderAllReportHistory();
  };
});

// ---------- 上传 ----------
const uploadZone = document.getElementById("uploadZone");
const fileInput = document.getElementById("gcFile");

uploadZone.onclick = () => fileInput.click();
uploadZone.ondragover = (e) => { e.preventDefault(); uploadZone.classList.add("drag"); };
uploadZone.ondragleave = () => uploadZone.classList.remove("drag");
uploadZone.ondrop = (e) => {
  e.preventDefault();
  uploadZone.classList.remove("drag");
  if (e.dataTransfer.files[0]) uploadGCFile(e.dataTransfer.files[0]);
};
fileInput.onchange = (e) => { if (e.target.files[0]) uploadGCFile(e.target.files[0]); };

// 上传区折叠/展开切换按钮
const uploadToggleBtn = document.createElement("button");
uploadToggleBtn.className = "upload-zone-toggle";
uploadToggleBtn.textContent = "⤢";
uploadToggleBtn.title = t("gc.upload_zone_expand");
uploadToggleBtn.onclick = (e) => {
  e.stopPropagation();
  const expanded = !uploadZone.classList.contains("collapsed");
  if (expanded) {
    uploadZone.classList.add("collapsed");
    uploadToggleBtn.textContent = "⤢";
    uploadToggleBtn.title = t("gc.upload_zone_expand");
  } else {
    uploadZone.classList.remove("collapsed");
    uploadToggleBtn.textContent = "⤡";
    uploadToggleBtn.title = t("gc.upload_zone_collapse");
  }
};
uploadZone.appendChild(uploadToggleBtn);

// ---------- GC Sidebar ----------
const gcSidebarList = document.getElementById("gcSidebarList");
if (gcSidebarList) {
gcSidebarList.addEventListener("click", async (e) => {
  const item = e.target.closest(".sidebar-item");
  if (!item) return;
  const id = item.dataset.id;
  if (!id) return;

  const action = e.target.closest("[data-action]");
  if (action) {
    if (action.dataset.action === "attach") {
      let entry = state.openGcReports.find(r => r.id === id);
      let report = entry?.report;
      if (!report) {
        const historyItem = (state.gcHistoryReports || []).find(r => r.id === id);
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
        bindReportContext("gc", report);
        if (state.activeReportContexts.length === before && !state.activeReportContexts.some(c => c.report_id === id)) {
          action.innerHTML = ico('triangle-alert');
          setTimeout(() => { action.innerHTML = ico('paperclip'); renderGcSidebar(); }, 1500);
          return;
        }
      }
      renderGcSidebar();
      return;
    }
    if (action.dataset.action === "close") {
      if (!confirm(t("gc.delete_confirm"))) return;
      await api(`/api/sessions/${state.currentSessionId}/gc/reports/${id}`, { method: "DELETE" });
      removeActiveReportContextByReport(id);
      closeReportTab("gc", id);
      await refreshHistory();
      return;
    }
    return;
  }

  // Batch checkbox click
  const checkbox = e.target.closest(".si-checkbox");
  if (checkbox) {
    if (gcBatchMode && item.classList.contains("batch-active") && !item.classList.contains("batch-excluded")) {
      checkbox.innerHTML = checkbox.innerHTML === ico('check-square') ? ico('square') : ico('check-square');
      _updateGcBatchDeleteBtn();
    }
    return;
  }

  const isSession = item.dataset.session === "true";
  if (isSession) {
    activateReportTab("gc", id);
  } else {
    const full = await api(`/api/sessions/${state.currentSessionId}/gc/reports/${id}`);
    openReport("gc", full, { dontTrack: true });
  }
});
}

const sidebarUploadZone = document.getElementById("gcSidebarUploadZone");
const sidebarFileInput = document.getElementById("gcSidebarFile");
if (sidebarUploadZone && sidebarFileInput) {
  sidebarUploadZone.onclick = () => sidebarFileInput.click();
  sidebarUploadZone.ondragover = (e) => { e.preventDefault(); sidebarUploadZone.classList.add("drag"); };
  sidebarUploadZone.ondragleave = () => sidebarUploadZone.classList.remove("drag");
  sidebarUploadZone.ondrop = (e) => {
    e.preventDefault();
    sidebarUploadZone.classList.remove("drag");
    if (e.dataTransfer.files[0]) uploadGCFile(e.dataTransfer.files[0]);
  };
  sidebarFileInput.onchange = (e) => { if (e.target.files[0]) uploadGCFile(e.target.files[0]); };
}

// ---------- GC Sidebar batch mode ----------
let gcBatchMode = false;
const gcSelectBtn = document.getElementById("gcSidebarSelectBtn");
if (gcSelectBtn) {
gcSelectBtn.onclick = () => {
  gcBatchMode = true;
  document.getElementById("gcSidebarSelectBtn").style.display = "none";
  document.getElementById("gcSidebarBatchBar").style.display = "flex";
  document.querySelectorAll("#gcSidebarList .sidebar-item").forEach(el => {
    el.classList.add("batch-active");
  });
  // Sync the Delete button to reflect current selection (0 if user just entered).
  _updateGcBatchDeleteBtn();
};
document.querySelector("#gcSidebarBatchBar .sidebar-cancel-btn").onclick = () => {
  gcBatchMode = false;
  document.getElementById("gcSidebarSelectBtn").style.display = "";
  document.getElementById("gcSidebarBatchBar").style.display = "none";
  document.querySelectorAll("#gcSidebarList .sidebar-item").forEach(el => {
    el.classList.remove("batch-active", "batch-excluded");
  });
};
document.querySelector("#gcSidebarBatchBar .sidebar-selectall-btn").onclick = () => {
  const cb = document.querySelectorAll("#gcSidebarList .sidebar-item:not(.batch-excluded) .si-checkbox");
  const allChecked = !cb.length || [...cb].every(c => c.innerHTML === ico('check-square'));
  cb.forEach(c => { c.innerHTML = allChecked ? ico('square') : ico('check-square'); });
  _updateGcBatchDeleteBtn();
};
document.querySelector("#gcSidebarBatchBar .sidebar-delete-btn").onclick = async () => {
  const checked = [...document.querySelectorAll("#gcSidebarList .sidebar-item .si-checkbox")].filter(c => c.innerHTML === ico('check-square'));
  const ids = checked.map(c => c.closest(".sidebar-item").dataset.id).filter(Boolean);
  if (!ids.length) return;
  if (!confirm(t("reports.bulk_delete_confirm", { n: ids.length }))) return;
  const entries = ids.map(id => ({ id, type: "gc", sessionId: state.currentSessionId }));
  await deleteReportEntries(entries);
  // Refresh history so history-list items removed server-side disappear from
  // the sidebar (closeReportTab only handles openGcReports entries).
  await refreshHistory();
  document.querySelector("#gcSidebarBatchBar .sidebar-cancel-btn").onclick();
};
}

function _updateGcBatchDeleteBtn() {
  const checked = document.querySelectorAll("#gcSidebarList .sidebar-item.batch-active:not(.batch-excluded) .si-checkbox");
  const selected = [...checked].filter(c => c.innerHTML === ico('check-square')).length;
  const deleteBtn = document.querySelector("#gcSidebarBatchBar .sidebar-delete-btn");
  if (deleteBtn) {
    deleteBtn.disabled = selected === 0;
    deleteBtn.innerHTML = selected ? `${ico('trash-2')} ${t("reports.bulk_delete")} (${selected})` : `${ico('trash-2')} ${t("reports.bulk_delete")}`;
  }
}

// ---------- GC Report Header buttons (removed; sidebar provides duplicate) ----------

