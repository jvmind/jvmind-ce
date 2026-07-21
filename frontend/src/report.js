import { t, setLang } from "../i18n/index.js";
import { escapeHtml, detectReportMode } from "./shared.js";
import { api } from "./api.js";
import { state } from "./state.js";
import { renderReport } from "./gc-analysis.js";
import { renderJstackReport } from "./jstack-analysis.js";
import { renderHeapdumpReportInto } from "./heapdump-analysis/heapdump.js";

// 语言由 i18n/index.js 模块加载时 detectLang() 自动初始化（读 localStorage["jvmind_lang"] → fallback navigator.language）

const reportMode = detectReportMode();
if (!reportMode) {
  document.body.innerHTML = `<div style="padding:40px;text-align:center;">Invalid report URL</div>`;
} else {
  const { sid, rid, type } = reportMode;
  if (type !== "heapdump") {
    state.currentSessionId = sid;
  }
  const titleKey = type === "jstack" ? "report.title_jstack"
                  : type === "heapdump" ? "report.title_heapdump"
                  : "report.title_gc";
  document.title = t(titleKey);

  const bodyEl = document.getElementById(
    type === "jstack" ? "jstackBodyCurrent"
    : type === "heapdump" ? "heapdumpBodyCurrent"
    : "gcBodyCurrent"
  );
  const loadingEl = document.getElementById(
    type === "jstack" ? "jstackLoading"
    : type === "heapdump" ? "heapdumpLoading"
    : "gcLoading"
  );
  const errorEl = document.getElementById(
    type === "jstack" ? "jstackError"
    : type === "heapdump" ? "heapdumpError"
    : "gcError"
  );
  const reportArea = document.getElementById(
    type === "jstack" ? "jstackReportArea"
    : type === "heapdump" ? "heapdumpReportArea"
    : "gcReportArea"
  );

  document.querySelectorAll(".mode-body").forEach(el => el.style.display = "none");
  const modeBody = document.querySelector(`.mode-body[data-mode="${type}"]`);
  if (modeBody) modeBody.style.display = "";

  bodyEl.style.display = "";
  loadingEl.style.display = "";

  (async () => {
    try {
      let full;
      if (type === "heapdump") {
        full = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}`);
      } else {
        const seg = type === "jstack" ? "jstack" : "gc";
        full = await api(`/api/sessions/${sid}/${seg}/reports/${rid}`);
      }
      loadingEl.style.display = "none";
      reportArea.style.display = "";
      if (type === "jstack") {
        state.currentJstackReport = full;
        state.currentJstackReportId = rid;
        renderJstackReport(full);
      } else if (type === "heapdump") {
        state.currentHeapdumpReport = full;
        state.currentHeapdumpReportId = rid;
        renderHeapdumpReportInto(reportArea, full, { isStandalone: true });
      } else {
        state.currentReport = full;
        state.currentReportId = rid;
        renderReport(full);
      }
    } catch (e) {
      loadingEl.style.display = "none";
      errorEl.style.display = "";
      errorEl.textContent = t("report.load_failed", { msg: e.message });
    }
  })();
}
