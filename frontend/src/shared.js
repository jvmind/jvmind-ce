// shared.js — 纯工具函数，无业务逻辑依赖
import { t, getLang } from "../i18n/index.js";
import { ico } from './icons.js';

/**
 * Parse a bilingual backend message in "中文 / English" format and return
 * the appropriate language portion based on the current UI language.
 * Falls back to the full text if the separator is not found.
 */
export function i18nText(text) {
  if (text == null) return "";
  if (typeof text !== "string") {
    try { text = JSON.stringify(text); } catch { text = String(text); }
  }
  const idx = text.indexOf(" / ");
  if (idx === -1) return text;
  const zh = text.slice(0, idx).trim();
  const en = text.slice(idx + 3).trim();
  return getLang() === "zh" ? zh : en;
}

export function getCookie(name) {
  return document.cookie.split(";").map(v => v.trim()).find(v => v.startsWith(name + "="))?.split("=").slice(1).join("=") || "";
}

export function csrfHeaders(headers = {}) {
  const token = getCookie("csrf_token");
  return token ? { ...headers, "X-CSRF-Token": decodeURIComponent(token) } : headers;
}

export function escapeHtml(s) {
  if (typeof s !== "string") s = String(s ?? "");
  return s.replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;" }[c]));
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function formatN(v) {
  if (v >= 1000) return (v / 1000).toFixed(1) + "k";
  if (v >= 100) return v.toFixed(0);
  if (v >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

export function formatTime(t) {
  if (t >= 3600) return (t / 3600).toFixed(2) + "h";
  if (t >= 60) return (t / 60).toFixed(2) + "m";
  return t.toFixed(t < 10 ? 2 : 1) + "s";
}

export function formatEpoch(epochMs, refEpochMs) {
  const d = new Date(epochMs);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  const ref = new Date(refEpochMs != null ? refEpochMs : epochMs);
  if (d.toDateString() !== ref.toDateString()) {
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${month}-${day} ${hh}:${mm}:${ss}`;
  }
  return `${hh}:${mm}:${ss}`;
}

/**
 * Format a freed/reclaimed amount for the GC slowest-events table.
 * Positive delta = heap shrunk (Young GC reclaimed memory) → "↑ 2.1 GB".
 * Negative delta = heap grew (Full GC expanded)            → "↓ 0.0 MB".
 * Zero delta                                              → "±0 MB".
 * Auto-scales MB → GB at >=1024 MB, one decimal place.
 * Returns { sign, text } where sign is "up" | "down" | "zero".
 */
export function formatFreed(before, after) {
  const b = Number(before);
  const a = Number(after);
  if (!Number.isFinite(b) || !Number.isFinite(a)) {
    return { sign: "zero", text: "" };
  }
  const delta = b - a;
  if (Math.abs(delta) < 0.05) {
    return { sign: "zero", text: "±0 MB" };
  }
  const sign = delta > 0 ? "up" : "down";
  const abs = Math.abs(delta);
  const text = abs >= 1024
    ? `${delta > 0 ? "↑" : "↓"} ${(abs / 1024).toFixed(1)} GB`
    : `${delta > 0 ? "↑" : "↓"} ${abs.toFixed(1)} MB`;
  return { sign, text };
}

export function isValidEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value || "").trim());
}

export function money(cents, currency = "USD") {
  const value = (Number(cents || 0) / 100).toFixed(2);
  return `${currency} ${value}`;
}

/**
 * Parse a backend timestamp string into a Date.
 * Backend stores UTC strings in "YYYY-MM-DD HH:MM:SS" format WITHOUT a timezone
 * marker (semantics are UTC). ISO strings with an explicit offset/Z are honored
 * as-is. Returns null for unparseable input.
 */
export function parseServerTime(s) {
  if (!s) return null;
  const str = String(s).trim();
  if (!str) return null;
  // Already has explicit timezone (Z or ±HH:MM) or is a full ISO 'T' string → trust it.
  if (/[zZ]$/.test(str) || /[+-]\d{2}:?\d{2}$/.test(str)) {
    const d = new Date(str);
    return isNaN(d.getTime()) ? null : d;
  }
  // Marker-less "YYYY-MM-DD HH:MM:SS" (or with 'T') → interpret as UTC.
  const m = str.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
  if (m) {
    const d = new Date(Date.UTC(
      +m[1], +m[2] - 1, +m[3], +m[4], +m[5], m[6] ? +m[6] : 0
    ));
    return isNaN(d.getTime()) ? null : d;
  }
  // Date-only "YYYY-MM-DD" → midnight UTC.
  const dm = str.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (dm) {
    const d = new Date(Date.UTC(+dm[1], +dm[2] - 1, +dm[3]));
    return isNaN(d.getTime()) ? null : d;
  }
  const fallback = new Date(str);
  return isNaN(fallback.getTime()) ? null : fallback;
}

function _pad2(n) {
  return String(n).padStart(2, "0");
}

/**
 * Format a backend UTC timestamp as local "YYYY-MM-DD HH:MM" (browser timezone).
 */
export function fmtDate(s) {
  const d = parseServerTime(s);
  if (!d) return "-";
  return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())} ${_pad2(d.getHours())}:${_pad2(d.getMinutes())}`;
}

/**
 * Format a backend UTC timestamp as local date-only "YYYY-MM-DD" (browser timezone).
 */
export function fmtDateOnly(s) {
  const d = parseServerTime(s);
  if (!d) return "";
  return `${d.getFullYear()}-${_pad2(d.getMonth() + 1)}-${_pad2(d.getDate())}`;
}

export function parseSSE(block) {
  const lines = block.split(/\r?\n/);
  let event = "message", data = "";
  for (const line of lines) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).replace(/^ /, "");
  }
  if (!data) return null;
  try { return { event, data: JSON.parse(data) }; } catch { return null; }
}

export function calculateGCHealth(stats) {
  const collector = stats.collector;
  const fullCount = stats.by_category?.Full?.count || 0;
  const fullMaxPause = stats.by_category?.Full?.max_pause_ms || 0;
  const maxDurations = Object.values(stats.by_category || {}).map(x => x.max_pause_ms || 0);
  const maxPause = Math.max(...maxDurations, 0);
  const tp = stats.throughput;
  const heapPct = stats.max_heap_usage_pct || 0;
  const hasFull = fullCount > 0;
  const lowTp = tp != null && tp < 0.9;
  const highHeap = heapPct > 95;
  const veryHighHeap = heapPct >= 98;
  const isParallel = collector === "Parallel";

  let level = "good";
  if (isParallel) {
    if (fullMaxPause > 2000) {
      level = "bad";
    } else if (fullMaxPause > 1000) {
      level = "warn";
    }
  } else if (hasFull && fullCount > 3) {
    level = "bad";
  } else if (hasFull || (tp != null && tp < 0.9) || heapPct > 95) {
    level = "warn";
  } else if (lowTp || maxPause > 200) {
    level = "caution";
  }

  if (veryHighHeap) {
    level = "bad";
  }

  const d = stats.diagnosis;
  if (d && (d.leak_risk === "high" || d.oom_risk === "high")) {
    level = "bad";
  } else if (d && (d.leak_risk === "medium" || d.oom_risk === "medium")) {
    if (level === "good" || level === "caution") level = "warn";
  }
  return level;
}

export function detectReportMode() {
  const mgc = location.pathname.match(/^\/report\/([^\/]+)\/([^\/?#]+)/);
  if (mgc) return { sid: mgc[1], rid: mgc[2], type: "gc" };
  const mjs = location.pathname.match(/^\/jstack-report\/([^\/]+)\/([^\/?#]+)/);
  if (mjs) return { sid: mjs[1], rid: mjs[2], type: "jstack" };
  const mhd = location.pathname.match(/^\/heapdump-report\/([^/?#]+)/);
  if (mhd) return { rid: mhd[1], type: "heapdump" };
  const p = new URLSearchParams(location.search).get("report");
  if (p && p.includes("/")) {
    const [sid, rid] = p.split("/");
    return { sid, rid, type: "gc" };
  }
  return null;
}

export function getSelectedReports(container) {
  return Array.from(container.querySelectorAll(".report-select:checked")).map(cb => ({
    id: cb.dataset.id,
    type: cb.dataset.type,
    sessionId: cb.dataset.session,
  }));
}

export function updateReportBulkBar(container) {
  const selected = getSelectedReports(container).length;
  const total = container.querySelectorAll(".report-select").length;
  const count = container.querySelector(".report-selected-count");
  const bulkDelete = container.querySelector(".bulk-delete");
  const selectAll = container.querySelector(".report-select-all");
  if (count) count.textContent = t("reports.selected_count", { n: selected });
  if (bulkDelete) bulkDelete.disabled = selected === 0;
  if (selectAll) {
    selectAll.checked = total > 0 && selected === total;
    selectAll.indeterminate = selected > 0 && selected < total;
  }
}

export function canManageTeam(member, isOwner) {
  return isOwner && member.role !== "owner";
}

export function isReportActionTarget(target) {
  return Boolean(target.closest(".del, .report-select, .report-select-all, .bulk-delete"));
}

export function validateGCFile(file, allowedExts = [".log", ".txt", ".gc"], maxSize = 10 * 1024 * 1024) {
  const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
  if (!allowedExts.includes(ext)) {
    return { valid: false, error: "invalid_type", ext, allowedExts };
  }
  if (file.size > maxSize) {
    return { valid: false, error: "too_large", sizeMB: (file.size / 1024 / 1024).toFixed(1), maxSizeMB: maxSize / 1024 / 1024 };
  }
  return { valid: true };
}

export function calculateGCStatsClasses(stats) {
  const fullCount = stats.by_category?.Full?.count || 0;
  const maxDurations = Object.values(stats.by_category || {}).map(x => x.max_pause_ms || 0);
  const maxPause = Math.max(...maxDurations, 0);
  const pauseClass = maxPause > 500 ? "bad" : (maxPause > 200 ? "warn" : "good");
  const tpClass = (stats.throughput != null && stats.throughput < 0.95) ? "warn" : "good";
  const fullClass = fullCount > 0 ? "bad" : "good";
  return { fullCount, maxPause, pauseClass, tpClass, fullClass };
}

export function detectProvider(baseUrl) {
  if (!baseUrl) return "";
  if (/api\.openai\.com/.test(baseUrl)) return "OpenAI";
  else if (/api\.deepseek/.test(baseUrl)) return "DeepSeek";
  else if (/dashscope\.aliyuncs\.com/.test(baseUrl)) return "通义千问";
  else if (/api\.moonshot\.cn/.test(baseUrl)) return "Kimi";
  else return "";
}

export function getPendingInvite() {
  const raw = sessionStorage.getItem("pendingInvite");
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

export function consumePendingInvite() {
  const invite = getPendingInvite();
  if (invite) sessionStorage.removeItem("pendingInvite");
  return invite;
}

export function consumeLoginRedirect() {
  const redirect = sessionStorage.getItem("loginRedirect");
  if (!redirect) return "";
  sessionStorage.removeItem("loginRedirect");
  try {
    const url = new URL(redirect, location.origin);
    if (url.origin !== location.origin) return "";
    if (/^\/(report|jstack-report|heapdump-report)\//.test(url.pathname)) {
      return url.pathname + url.search + url.hash;
    }
  } catch (_) { /* ignore invalid redirect */ }
  return "";
}

export function renderReportBulkToolbar(scope, t) {
  return `
    <div class="report-bulk-bar" data-scope="${scope}">
      <label class="report-select-all-label">
        <input type="checkbox" class="report-select-all" />
        <span>${t("reports.select_all")}</span>
      </label>
      <span class="report-selected-count">${t("reports.selected_count", { n: 0 })}</span>
      <button type="button" class="bulk-delete" disabled>${t("reports.bulk_delete")}</button>
    </div>
  `;
}

export function formatHealthBanner(stats, t) {
  const collector = stats.collector || "—";
  const fullCount = stats.by_category?.Full?.count || 0;
  const fullMaxPause = stats.by_category?.Full?.max_pause_ms || 0;
  const tp = stats.throughput;
  const heapPct = stats.max_heap_usage_pct || 0;
  const hasFull = fullCount > 0;

  const level = calculateGCHealth(stats);
  const levelMeta = {
    good:    { icon: ico('circle-check'), label: t("gc.health_good") },
    caution: { icon: ico('circle-alert'), label: t("gc.health_caution") },
    warn:    { icon: ico('triangle-alert'), label: t("gc.health_warn") },
    bad:     { icon: ico('circle-x'), label: t("gc.health_bad") },
  };
  const { icon, label } = levelMeta[level] || levelMeta.good;

  // Fixed-order metrics: collector, throughput, heap, full GC, diagnosis.
  // Each metric only renders when its data is available, so the chip
  // list is stable in shape (always same slot order, just fewer chips
  // when data is missing).
  const metricText = [];
  metricText.push(escapeHtml(collector));
  if (tp != null) metricText.push(t("gc.health_detail_tp", { p: (tp*100).toFixed(1) }));
  if (heapPct > 0) metricText.push(t("gc.health_detail_heap", { p: heapPct }));
  if (hasFull) metricText.push(t("gc.health_detail_full", { n: fullCount, ms: fullMaxPause.toFixed(0) }));

  const diag = stats.diagnosis;
  if (diag) {
    if (diag.leak_risk && diag.leak_risk !== "none") {
      metricText.push(`${t("gc.diagnosis_leak_risk")}: ${t("gc.diagnosis_risk_" + diag.leak_risk)}`);
    }
    if (diag.oom_risk && diag.oom_risk !== "none") {
      metricText.push(`${t("gc.diagnosis_oom_risk")}: ${t("gc.diagnosis_risk_" + diag.oom_risk)}`);
    }
  }

  const detailHtml = metricText
    .map(m => `<span class="hb-metric">${m}</span>`)
    .join('<span class="hb-sep">·</span>');

  return `<div class="health-banner level-${level}"><span class="hb-status">${icon} ${label}</span><span class="hb-detail">${detailHtml}</span></div>`;
}
