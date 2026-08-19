// MAT 工作台（react_agent_project 风格的全屏界面）：顶部工具栏 +
// 左侧可拖拽的 Inspector（对象详情 + 后退/前进）+ 中间 icon Tab 视图。
//
// 页面：/mat/{rid}，由 mat.html 加载本模块。
import { api } from "./api.js";
import { escapeHtml } from "./shared.js";
import { t } from "../i18n/index.js";
import { ico } from "./icons.js";
import { renderObjectDetail } from "./heapdump-analysis/inspector.js";

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

const CORE_TABS = [
  { id: "overview",   icon: "layout-dashboard", labelKey: "mat.tab_overview" },
  { id: "histogram",  icon: "bar-chart-3",      labelKey: "mat.tab_histogram" },
  { id: "dominator",  icon: "git-fork",         labelKey: "mat.tab_dominator" },
  { id: "threads",    icon: "layers",           labelKey: "mat.tab_threads" },
  { id: "leak",       icon: "stethoscope",      labelKey: "mat.tab_leak" },
  { id: "oql",        icon: "search",           labelKey: "mat.tab_oql" },
  { id: "threadlocals", icon: "hash",           labelKey: "mat.tab_threadlocals" },
];

async function main() {
  if (!root) return;
  const rid = ridFromUrl();
  if (!rid) {
    root.innerHTML = `<div class="mat-empty">Invalid MAT URL</div>`;
    return;
  }

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
    <div class="mat-layout">
      <aside class="mat-inspector-wrap" id="matInspectorWrap">
        <div class="mat-insp-head">
          <h3 class="mat-insp-title">${esc(t("mat.inspector_title"))}</h3>
          <span class="mat-insp-spacer"></span>
          <button class="mat-nav-btn" id="inspBack" title="${esc(t("mat.inspector_back"))}">${ico('arrow-left')}</button>
          <button class="mat-nav-btn" id="inspFwd" title="${esc(t("mat.inspector_forward"))}">${ico('arrow-right')}</button>
        </div>
        <div class="mat-insp-open">
          <input type="number" min="0" id="inspId" placeholder="${esc(t("mat.insp_open_id"))}">
          <button class="mat-btn" id="inspGo">${esc(t("mat.insp_open"))}</button>
        </div>
        <div class="mat-insp-body" id="matInspector"><div class="mat-hint">${esc(t("mat.inspector_empty"))}</div></div>
      </aside>
      <div class="mat-resize" id="matResize" title="${esc(t("mat.inspector_resize"))}"></div>
      <main class="mat-center">
        <div class="mat-tabbar" id="matTabbar"></div>
        <div class="mat-view" id="matView"><div class="mat-empty">${esc(t("mat.loading"))}</div></div>
      </main>
    </div>`;

  // 左侧 Inspector：历史栈 + 前进/后退
  const backStack = [];
  const fwdStack = [];
  const inspEl = document.getElementById("matInspector");
  let inspDir = "out";

  async function inspectObject(id) {
    if (id == null) return;
    const cur = backStack[backStack.length - 1];
    if (cur !== id) {
      backStack.push(id);
      fwdStack.length = 0;
    }
    await renderObjectDetail(inspEl, rid, id, inspDir, (next) => {
      if (next != null) {
        backStack.push(next);
        fwdStack.length = 0;
        renderObjectDetail(inspEl, rid, next, inspDir, null);
      }
    });
    updateNavButtons();
  }
  function updateNavButtons() {
    document.getElementById("inspBack").disabled = backStack.length <= 1;
    document.getElementById("inspFwd").disabled = fwdStack.length === 0;
  }
  document.getElementById("inspBack").addEventListener("click", () => {
    if (backStack.length <= 1) return;
    fwdStack.push(backStack.pop());
    const id = backStack[backStack.length - 1];
    renderObjectDetail(inspEl, rid, id, inspDir, null);
    updateNavButtons();
  });
  document.getElementById("inspFwd").addEventListener("click", () => {
    const id = fwdStack.pop();
    if (id == null) return;
    backStack.push(id);
    renderObjectDetail(inspEl, rid, id, inspDir, null);
    updateNavButtons();
  });
  document.getElementById("inspGo").addEventListener("click", () => {
    const id = parseInt(document.getElementById("inspId").value, 10);
    if (Number.isInteger(id) && id >= 0) inspectObject(id);
  });
  document.getElementById("inspId").addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("inspGo").click();
  });

  // 可拖拽宽度
  const wrap = document.getElementById("matInspectorWrap");
  const handle = document.getElementById("matResize");
  let dragging = false;
  handle.addEventListener("mousedown", (e) => {
    dragging = true;
    document.body.style.userSelect = "none";
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    const width = Math.max(260, Math.min(window.innerWidth - 320, e.clientX));
    wrap.style.width = `${width}px`;
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.userSelect = "";
    try { localStorage.setItem("matInspectorWidth", wrap.style.width); } catch {}
  });
  const savedW = (() => { try { return localStorage.getItem("matInspectorWidth"); } catch { return null; } })();
  if (savedW) wrap.style.width = savedW;

  document.getElementById("matCopyLink").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(location.href); } catch {}
  });
  document.getElementById("matAskAi").addEventListener("click", () => {
    const qs = new URLSearchParams({ mat_rid: rid });
    window.open(`${location.origin}/?${qs.toString()}`, "_blank");
  });

  // 中间 Tab
  const tabbar = document.getElementById("matTabbar");
  const view = document.getElementById("matView");
  let active = null;

  function renderTabbar() {
    tabbar.innerHTML = CORE_TABS.map(v =>
      `<button class="mat-tab${v.id === active ? " active" : ""}" data-tab="${v.id}" title="${esc(t(v.labelKey))}">
        ${ico(v.icon)}<span>${esc(t(v.labelKey))}</span>
      </button>`
    ).join("");
    tabbar.querySelectorAll(".mat-tab").forEach(b => {
      b.addEventListener("click", () => activate(b.dataset.tab));
    });
  }
  async function activate(id) {
    active = id;
    renderTabbar();
    if (id === "overview") await renderOverview(view, rid);
    else if (id === "histogram") await renderHistogram(view, rid);
    else if (id === "dominator") await renderDominator(view, rid);
    else if (id === "threads") await renderThreads(view, rid);
    else if (id === "leak") await renderLeaks(view, rid);
    else if (id === "oql") await renderOql(view, rid);
    else if (id === "threadlocals") await renderThreadLocals(view, rid);
  }

  await activate("overview");
}

// ---------- Overview ----------

async function renderOverview(el, rid) {
  el.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
  try {
    const o = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}/overview?full=true`);
    const jvm = (o.jvmInfo || {}) || {};
    const rows = [
      [t("mat.ov_heap"), fmtBytes(o.usedHeapSize ?? o.usedHeap ?? o.totalHeap)],
      [t("mat.ov_objects"), fmtNum(o.numObjects ?? o.objects ?? "")],
      [t("mat.ov_classes"), fmtNum(o.numClasses ?? o.classes ?? "")],
      [t("mat.ov_classloaders"), fmtNum(o.numClassLoaders ?? o.classLoaders ?? "")],
      [t("mat.ov_gcroots"), fmtNum(o.numGcRoots ?? o.gcRoots ?? "")],
      [t("mat.ov_jvm"), esc(jvm.javaVersion || "")],
    ].filter(r => r[1] !== "" && r[1] !== "—" && r[1] != null);
    el.innerHTML = `<div class="mat-kv"><table class="mat-table"><tbody>` +
      rows.map(r => `<tr><td class="mat-k">${r[0]}</td><td class="mat-v">${r[1]}</td></tr>`).join("") +
      `</tbody></table></div>`;
  } catch (e) {
    el.innerHTML = `<div class="mat-error">${esc(e.message)}</div>`;
  }
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

// ---------- 支配树（按 parent 浏览 / 展开） ----------

async function renderDominator(el, rid) {
  const st = { parent: "ROOT" };
  async function load() {
    el.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
    try {
      const data = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}/dominator?parent=${encodeURIComponent(st.parent)}&top=200`);
      const rows = data.rows || [];
      const total = data.totalRows != null ? data.totalRows : rows.length;
      const crumb = st.parent === "ROOT"
        ? `<span class="mat-crumb-cur">${esc(t("mat.insp_root"))}</span>`
        : `<a class="mat-crumb" data-parent="ROOT">${esc(t("mat.insp_root"))}</a> / <span class="mat-crumb-cur">#${esc(st.parent)}</span>`;
      let html = `<div class="mat-dom-crumb">${crumb} <span class="mat-dom-count">· ${esc(fmtNum(total))}</span></div>`;
      if (!rows.length) { html += `<div class="mat-hint">${esc(t("mat.dom_empty"))}</div>`; el.innerHTML = html; return; }
      html += `<div class="mat-table-wrap"><table class="mat-table"><thead><tr>
        <th></th><th>${esc(t("mat.col_class"))}</th><th>${esc(t("mat.col_field"))}</th>
        <th class="num">${esc(t("mat.col_shallow"))}</th><th class="num">${esc(t("mat.col_retained"))}</th>
      </tr></thead><tbody>` + rows.map(r => `<tr>
        <td>${r.expandable ? `<button class="mat-dom-exp" data-parent="${esc(r.objectId)}">▸</button>` : ""}</td>
        <td class="mono">${esc(r.label || "#" + r.objectId)}</td>
        <td class="mat-dim">${esc(r.fieldName || "")}</td>
        <td class="num">${esc(fmtBytes(r.shallowBytes))}</td>
        <td class="num">${esc(fmtBytes(r.retainedBytes))}</td>
      </tr>`).join("") + `</tbody></table></div>`;
      el.innerHTML = html;
      el.querySelectorAll("[data-parent]").forEach(b => {
        b.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          st.parent = b.dataset.parent;
          await load();
        });
      });
    } catch (e) {
      el.innerHTML = `<div class="mat-error">${esc(e.message)}</div>`;
    }
  }
  await load();
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

// ---------- 线程本地 ----------

async function renderThreadLocals(el, rid) {
  el.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
  try {
    const data = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}/threadlocals?groupBy=valueClass&top=200`);
    const rows = data.rows || data.groups || [];
    if (!rows.length) { el.innerHTML = `<div class="mat-hint">${esc(t("mat.empty"))}</div>`; return; }
    const headers = data.rows ? Object.keys(rows[0]) : ["group", "count"];
    el.innerHTML = `<div class="mat-table-wrap"><table class="mat-table"><thead><tr>
      ${headers.map(h => `<th>${esc(h)}</th>`).join("")}
    </tr></thead><tbody>` + rows.map(r => `<tr>
      ${headers.map(h => `<td class="mono">${esc(r[h] == null ? "" : (h.toLowerCase().includes("retained") ? fmtBytes(r[h]) : fmtNum(r[h])))}</td>`).join("")}
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
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
    });
    const data = await resp.json();
    const taskId = data.taskId;
    if (!taskId) { el.innerHTML = `<div class="mat-error">${esc(t("mat.leaks_failed"))}</div>`; return; }
    const result = await pollTask(rid, taskId, el);
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

async function pollTask(rid, taskId, el) {
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
      <div class="mat-oql-actions"><button class="mat-btn-primary" id="matOqlRun">${esc(t("mat.oql_run"))}</button></div>
      <div id="matOqlResult"></div>
    </div>`;
  document.getElementById("matOqlRun").addEventListener("click", async () => {
    const q = document.getElementById("matOqlInput").value.trim();
    const resEl = document.getElementById("matOqlResult");
    if (!q) return;
    resEl.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
    try {
      const resp = await fetch(`/api/heapdump-reports/${encodeURIComponent(rid)}/oql?view=list&sort=shallow`, {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
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
