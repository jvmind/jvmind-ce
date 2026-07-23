import { state } from "../state.js";
import { app } from "../app.js";
import { getCookie, escapeHtml, validateGCFile, i18nText } from "../shared.js";
import { api } from "../api.js";
import { t } from "../../i18n/index.js";
import { openReport } from "./tabs.js";
import { appendSystemHint } from "./render.js";
import { refreshHistory, updateBadge, refreshAllReportHistory } from "./history.js";

const ALLOWED_GC_EXTS = [".log", ".txt", ".gc"];

export async function uploadGCFile(file) {
  if (!state.currentSessionId) {
    alert(t("chat.no_session"));
    return;
  }
  const validation = validateGCFile(file, ALLOWED_GC_EXTS);
  if (!validation.valid) {
    document.getElementById("gcError").style.display = "";
    if (validation.error === "invalid_type") {
      document.getElementById("gcError").textContent = t("gc.error_invalid_type", { ext: validation.ext, exts: ALLOWED_GC_EXTS.join(", ") });
    } else if (validation.error === "too_large") {
      document.getElementById("gcError").textContent = t("gc.error_file_too_large", { size: validation.sizeMB, max: validation.maxSizeMB });
    }
    return;
  }
  document.getElementById("gcError").style.display = "none";
  document.getElementById("gcReportArea").style.display = "none";
  document.getElementById("gcLoading").style.display = "";

  const loadingEl = document.getElementById("gcLoading");
  loadingEl.style.display = "";
  loadingEl.textContent = t("gc.uploading");

  try {
    const data = await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/sessions/${state.currentSessionId}/gc/upload`);
      xhr.withCredentials = true;
      const token = getCookie("csrf_token");
      if (token) xhr.setRequestHeader("X-CSRF-Token", decodeURIComponent(token));

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          const pct = Math.round(e.loaded / e.total * 100);
          loadingEl.textContent = t("gc.upload_progress", { pct });
        }
      };

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)); }
          catch { reject(new Error(t("gc.upload_parse_error"))); }
        } else {
          try {
            const err = JSON.parse(xhr.responseText);
            reject(new Error(i18nText(err.detail || err.message || xhr.statusText)));
          } catch {
            reject(new Error(xhr.statusText));
          }
        }
      };
      xhr.onerror = () => reject(new Error(t("gc.upload_net_error")));

      const fd = new FormData();
      fd.append("file", file);
      xhr.send(fd);
    });
    // 完整数据需重新加载（带 ai_conclusion 字段）
    const full = await api(`/api/sessions/${state.currentSessionId}/gc/reports/${data.report_id}`);
    openReport("gc", full, { attach: true });
    // 提醒 Agent: 后续可使用 file_id 工具
    appendSystemHint(t("gc.upload_success", { filename: escapeHtml(data.filename || "") }) + " " + t("gc.upload_hint", { fid: escapeHtml(data.file_id || "") }));
    refreshHistory();
    updateBadge();
    refreshAllReportHistory();
    app.updateQuotaUI();
  } catch (e) {
    document.getElementById("gcError").style.display = "";
    document.getElementById("gcError").textContent = t("gc.upload_failed", { msg: e.message });
  } finally {
    document.getElementById("gcLoading").style.display = "none";
    document.getElementById("gcFile").value = "";
  }
}
