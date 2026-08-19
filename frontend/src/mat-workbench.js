// MAT 工作台（react_agent_project 风格的全屏界面）：顶部工具栏 +
// 左侧 Inspector（支配树 + 对象检查）+ 右侧 Tab 视图（直方图/线程/泄漏/OQL）。
//
// 页面：/mat/{rid}，由 mat.html 加载本模块。
import { api } from "./api.js";
import { escapeHtml } from "./shared.js";
import { t } from "../i18n/index.js";
import { ico } from "./icons.js";
import { renderInspectorSection } from "./heapdump-analysis/inspector.js";

const root = document.getElementById("matApp");

function ridFromUrl() {
  const m = location.pathname.match(/\/mat\/([^/?#]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

function fmtBytes(n) {
  if (n == null || isNaN(n)) return "—";
  n = Number(n);
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (Math.abs(v) >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return u[i] === "B" ? `${Math.round(v)} B` : `${v.toFixed(1)} ${u[i]}`;
}
function fmtNum(n) {
  if (n == null || isNaN(n)) return "—";
  return Number(n).toLocaleString();
}
function esc(v) {
  return escapeHtml(String(v == null ? "" : v));
}

async function main() {
  const rid = ridFromUrl();
  if (!rid) {
    root.innerHTML = `<div class="mat-empty">Invalid MAT URL</div>`;
    return;
  }

  // 取报告元信息（filename），失败不阻塞工作台
  let filename = "";
  try {
    const r = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}`);
    filename = r.filename || "";
  } catch { /* ignore */ }

  document.title = t("mat.title");

  root.innerHTML = `
    <div class="mat-toolbar">
      <span class="mat-brand">${ico('flask-conical')}<span>JVMind · MAT</span></span>
      <span class="mat-chip" title="${esc(rid + (filename ? " · " + filename : ""))}">
        <span class="mat-rid">${esc(rid)}</span>
        ${filename ? `<span class="mat-sep">/</span><span class="mat-filename">${esc(filename)}</span>` : ""}
      </span>
      <span class="mat-spacer"></span>
      <button class="mat-btn" id="matCopyLink">${ico('link')} ${esc(t("mat.copy_link"))}</button>
      <button class="mat-btn-primary" id="matAskAi">${ico('bot')} ${esc(t("mat.ask_ai"))}</button>
    </div>
    <div class="mat-body">
      <div class="mat-views">
        <div class="mat-tabs" id="matTabs"></div>
        <div class="mat-view" id="matView"></div>
      </div>
      <div class="mat-inspector" id="matInspector"></div>
    </div>`;

  document.getElementById("matCopyLink").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(location.href); } catch {}
  });
  document.getElementById("matAskAi").addEventListener("click", () => {
    const qs = new URLSearchParams({ mat_rid: rid });
    window.open(`${location.origin}/?${qs.toString()}`, "_blank");
  });

  // 左侧 Inspector（支配树 + 对象检查）
  renderInspectorSection(document.getElementById("matInspector"), rid);

  // 右侧 Tab 视图
  const views = [
    { id: "histogram", label: t("mat.tab_histogram") },
    { id: "threads", label: t("mat.tab_threads") },
    { id: "leaks", label: t("mat.tab_leaks") },
    { id: "oql", label: t("mat.tab_oql") },
  ];
  const tabsEl = document.getElementById("matTabs");
  const viewEl = document.getElementById("matView");
  let active = null;

  function renderTabBar() {
    tabsEl.innerHTML = views.map(v =>
      `<button class="mat-tab${v.id === active ? " active" : ""}" data-tab="${v.id}">${esc(v.label)}</button>`
    ).join("");
    tabsEl.querySelectorAll(".mat-tab").forEach(b => {
      b.addEventListener("click", () => activate(b.dataset.tab));
    });
  }

  async function activate(id) {
    active = id;
    renderTabBar();
    if (id === "histogram") await renderHistogram(viewEl, rid);
    else if (id === "threads") await renderThreads(viewEl, rid);
    else if (id === "leaks") await renderLeaks(viewEl, rid);
    else if (id === "oql") await renderOql(viewEl, rid);
  }

  await activate("histogram");
}

// ---------- 直方图 ----------

async function renderHistogram(el, rid) {
  el.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
  try {
    const data = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}/histogram?top=200&sort=retained`);
    const rows = data.rows || [];
    if (!rows.length) { el.innerHTML = `<div class="mat-hint">${esc(t("mat.empty"))}</div>`; return; }
    el.innerHTML = `<div class="mat-table-wrap"><table class="mat-table"><thead><tr>
      <th>${esc(t("mat.col_class"))}</th>
      <th class="num">${esc(t("mat.col_objects"))}</th>
      <th class="num">${esc(t("mat.col_shallow"))}</th>
      <th class="num">${esc(t("mat.col_retained"))}</th>
    </tr></thead><tbody>` + rows.map(r => `<tr>
      <td class="mono">${esc(r.label || "?")}</td>
      <td class="num">${esc(fmtNum(r.count))}</td>
      <td class="num">${esc(fmtBytes(r.shallowBytes))}</td>
      <td class="num">${esc(fmtBytes(r.retainedBytes))}</td>
    </tr>`).join("") + `</tbody></table></div>`;
  } catch (e) {
    el.innerHTML = `<div class="mat-error">${esc(e.message)}</div>`;
  }
}

// ---------- 线程 ----------

async function renderThreads(el, rid) {
  el.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
  try {
    const data = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}/threads?top=200`);
    const threads = data.threads || data.rows || [];
    if (!threads.length) { el.innerHTML = `<div class="mat-hint">${esc(t("mat.empty"))}</div>`; return; }
    el.innerHTML = `<div class="mat-table-wrap"><table class="mat-table"><thead><tr>
      <th>${esc(t("mat.col_thread"))}</th>
      <th class="num">${esc(t("mat.col_retained"))}</th>
      <th class="num">${esc(t("mat.col_objects"))}</th>
    </tr></thead><tbody>` + threads.map(th => `<tr>
      <td class="mono">${esc(th.label || th.name || th.threadName || "?")}</td>
      <td class="num">${esc(fmtBytes(th.retainedBytes ?? th.retained))}</td>
      <td class="num">${esc(fmtNum(th.objects ?? th.count ?? ""))}</td>
    </tr>`).join("") + `</tbody></table></div>`;
  } catch (e) {
    el.innerHTML = `<div class="mat-error">${esc(e.message)}</div>`;
  }
}

// ---------- 泄漏（异步任务） ----------

async function renderLeaks(el, rid) {
  el.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>${esc(t("mat.leaks_running"))}</div>`;
  try {
    const resp = await fetch(`/api/heapdump-reports/${encodeURIComponent(rid)}/leak-suspects`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
    });
    const data = await resp.json();
    const taskId = data.taskId;
    if (!taskId) {
      el.innerHTML = `<div class="mat-error">${esc(t("mat.leaks_failed"))}</div>`;
      return;
    }
    const result = await pollTask(rid, taskId, el, t);
    if (!result) return;
    const suspects = result.suspects || [];
    if (!suspects.length) { el.innerHTML = `<div class="mat-hint">${esc(t("mat.leaks_none"))}</div>`; return; }
    el.innerHTML = `<div class="mat-hint">${esc(t("mat.leaks_found", { n: suspects.length }))}</div>` +
      suspects.map(s => `<div class="mat-leak">
        <div class="mat-leak-name">${esc(s.className || s.description || "?")}</div>
        <div class="mat-leak-meta">${esc(fmtBytes(s.retainedBytes))} · ${esc(s.instances != null ? s.instances + " instances" : "")}</div>
      </div>`).join("");
  } catch (e) {
    el.innerHTML = `<div class="mat-error">${esc(e.message)}</div>`;
  }
}

async function pollTask(rid, taskId, el, t) {
  for (let i = 0; i < 60; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const resp = await fetch(`/api/heapdump-reports/${encodeURIComponent(rid)}/tasks/${taskId}`, { credentials: "same-origin" });
    const st = await resp.json();
    if (st.status === "DONE") return st.result;
    if (st.status === "FAILED" || st.status === "CANCELLED") return null;
    if (el) el.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>${esc(t("mat.leaks_running"))}</div>`;
  }
  return null;
}

// ---------- OQL ----------

async function renderOql(el, rid) {
  el.innerHTML = `
    <div class="mat-oql">
      <textarea id="matOqlInput" placeholder="${esc(t("mat.oql_placeholder"))}" rows="3"></textarea>
      <div class="mat-oql-actions">
        <button class="mat-btn-primary" id="matOqlRun">${esc(t("mat.oql_run"))}</button>
      </div>
      <div id="matOqlResult"></div>
    </div>`;
  document.getElementById("matOqlRun").addEventListener("click", async () => {
    const q = document.getElementById("matOqlInput").value.trim();
    const resEl = document.getElementById("matOqlResult");
    if (!q) return;
    resEl.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
    try {
      const resp = await fetch(`/api/heapdump-reports/${encodeURIComponent(rid)}/oql?view=list&sort=shallow`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ q, limit: 100 }),
      });
      const data = await resp.json();
      if (!resp.ok) { resEl.innerHTML = `<div class="mat-error">${esc(data.detail || data.error || resp.status)}</div>`; return; }
      const rows = data.rows || [];
      if (!rows.length) { resEl.innerHTML = `<div class="mat-hint">${esc(t("mat.empty"))}</div>`; return; }
      resEl.innerHTML = `<div class="mat-table-wrap"><table class="mat-table"><thead><tr><th>#</th><th>${esc(t("mat.col_object"))}</th><th class="num">${esc(t("mat.col_shallow"))}</th><th class="num">${esc(t("mat.col_retained"))}</th></tr></thead><tbody>` +
        rows.map((r, i) => `<tr>
          <td class="num">${i + 1}</td>
          <td class="mono">${esc(r.label || r.objectLabel || "#" + r.objectId)}</td>
          <td class="num">${esc(fmtBytes(r.shallowBytes ?? r.shallow))}</td>
          <td class="num">${esc(fmtBytes(r.retainedBytes ?? r.retained))}</td>
        </tr>`).join("") + `</tbody></table></div>`;
    } catch (e) {
      resEl.innerHTML = `<div class="mat-error">${esc(e.message)}</div>`;
    }
  });
}

main();
