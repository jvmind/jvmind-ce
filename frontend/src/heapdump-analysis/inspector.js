// Heapdump 对象检查（MAT 风格）：支配树浏览 + 对象 Inspector + 引用视图。
//
// 依赖后端 query-service 端点（经 /api/heapdump-reports/{rid}/... 代理）：
//   GET  /dominator?parent=ROOT|<id>  支配树子节点
//   GET  /object?id=<id>              对象详情（字段 / 静态字段 / 数组 / 容器标记）
//   GET  /array-elements?id=&top=&offset=   Object[] 元素（load-more）
//   GET  /collection-entries?id=&top=&offset= Map/Collection 条目（load-more）
//   POST /references {direction,objectId}   出/入引用详细行
//
// 本模块自包含（tableHtml/fmtBytes/fmtNum 私有实现），只依赖 shared api/escapeHtml
// 与 icons.ico、i18n t。
import { api } from "../api.js";
import { escapeHtml } from "../shared.js";
import { t } from "../../i18n/index.js";
import { ico } from "../icons.js";

const PAGE = 25;

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
function escAttr(v) {
  return escapeHtml(String(v == null ? "" : v));
}

// 每报告一份状态：支配树父栈、当前对象、引用方向
const _state = new Map();
function st(rid) {
  if (!_state.has(rid)) {
    _state.set(rid, { parent: "ROOT", objectId: null, dir: "out", objectHistory: [] });
  }
  return _state.get(rid);
}

/**
 * 渲染整个“对象检查”区块。container 为该区块的 body（已由调用方建好）。
 */
export async function renderInspectorSection(container, rid) {
  if (!container) return;
  const s = st(rid);
  container.innerHTML = `
    <div class="insp-root">
      <div class="insp-toolbar">
        <button class="insp-btn" data-act="ref-out">${ico('arrow-up-right')} ${escAttr(t("heapdump.insp_ref_out"))}</button>
        <button class="insp-btn" data-act="ref-in">${ico('arrow-down-left')} ${escAttr(t("heapdump.insp_ref_in"))}</button>
        <span class="insp-spacer"></span>
        <label class="insp-open">${escAttr(t("heapdump.insp_open_id"))} <input type="number" min="0" class="insp-id-input" placeholder="objectId"></label>
        <button class="insp-btn" data-act="open">${ico('search')} ${escAttr(t("heapdump.insp_open"))}</button>
        <button class="insp-btn" data-act="back" ${s.objectHistory.length ? "" : "disabled"}>${ico('arrow-left')} ${escAttr(t("heapdump.insp_back"))}</button>
      </div>
      <div class="insp-layout">
        <div class="insp-dominator">
          <div class="insp-pane-title">${escAttr(t("heapdump.insp_dominator"))}</div>
          <div class="insp-dom" data-dom="list"></div>
        </div>
        <div class="insp-object">
          <div class="insp-pane-title">${escAttr(t("heapdump.insp_object"))}</div>
          <div class="insp-obj" data-obj="panel"></div>
        </div>
      </div>
    </div>`;

  container.querySelector(".insp-toolbar").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    if (act === "open") {
      const input = container.querySelector(".insp-id-input");
      const id = parseInt(input.value, 10);
      if (Number.isInteger(id) && id >= 0) await inspect(container, rid, id);
    } else if (act === "ref-out") {
      s.dir = "out";
      btn.classList.add("active");
      container.querySelector('[data-act="ref-in"]').classList.remove("active");
      await renderReferences(container, rid);
    } else if (act === "ref-in") {
      s.dir = "in";
      btn.classList.add("active");
      container.querySelector('[data-act="ref-out"]').classList.remove("active");
      await renderReferences(container, rid);
    } else if (act === "back") {
      const prev = s.objectHistory.pop();
      if (prev != null) await inspect(container, rid, prev, { push: false });
    }
  });

  // 默认展示支配树 ROOT，并高亮引用方向按钮
  container.querySelector(`[data-act="${s.dir === "in" ? "ref-in" : "ref-out"}"]`).classList.add("active");
  await renderDominator(container, rid, s.parent);
  const objPanel = container.querySelector('[data-obj="panel"]');
  objPanel.innerHTML = `<div class="insp-hint">${escAttr(t("heapdump.insp_hint"))}</div>`;
}

// ---------- Dominator 树 ----------

async function renderDominator(container, rid, parent) {
  const s = st(rid);
  const list = container.querySelector('[data-dom="list"]');
  if (!list) return;
  list.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
  try {
    const data = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}/dominator?parent=${encodeURIComponent(parent)}&top=200`);
    const rows = data.rows || [];
    const total = data.totalRows != null ? data.totalRows : rows.length;
    const parentId = parent === "ROOT" ? null : parent;
    const crumbs = buildCrumbs(s, parent);

    let html = `<div class="insp-dom-crumb">${crumbs.map((c, i) => {
      if (i === crumbs.length - 1) return `<span class="insp-crumb-cur">${escAttr(c.label)}</span>`;
      return `<a class="insp-crumb" href="javascript:void(0)" data-crumb="${escAttr(c.id)}">${escAttr(c.label)}</a>`;
    }).join(" / ")}</div>`;

    if (!rows.length) {
      html += `<div class="insp-hint">${escAttr(t("heapdump.insp_dom_empty"))}</div>`;
      list.innerHTML = html;
      return;
    }

    html += `<div class="insp-dom-count">${escAttr(t("heapdump.insp_dom_count", { n: fmtNum(total) }))}</div>`;
    html += `<table class="hd-table insp-dom-table"><thead><tr>
      <th></th>
      <th>${escAttr(t("heapdump.insp_col_class"))}</th>
      <th>${escAttr(t("heapdump.insp_col_shallow"))}</th>
      <th>${escAttr(t("heapdump.insp_col_retained"))}</th>
    </tr></thead><tbody>`;

    rows.forEach((r) => {
      const expand = r.expandable
        ? `<button class="insp-dom-exp" data-parent="${escAttr(r.objectId)}" title="${escAttr(t("heapdump.insp_dom_expand"))}">▸</button>`
        : `<span class="insp-dom-exp-none"></span>`;
      html += `<tr class="insp-dom-row" data-objid="${escAttr(r.objectId)}">
        <td>${expand}</td>
        <td class="insp-dom-label" title="${escAttr((r.label || "") + (r.fieldName ? " (" + r.fieldName + ")" : ""))}">
          <span class="insp-dom-name">${escAttr(r.label || "?")}</span>
          ${r.fieldName ? `<span class="insp-dom-field">${escAttr(r.fieldName)}</span>` : ""}
        </td>
        <td class="num">${escAttr(fmtBytes(r.shallowBytes))}</td>
        <td class="num">${escAttr(fmtBytes(r.retainedBytes))}</td>
      </tr>`;
    });
    html += `</tbody></table>`;
    list.innerHTML = html;

    list.querySelectorAll(".insp-crumb").forEach((el) => {
      el.addEventListener("click", async () => {
        const id = el.dataset.crumb === "ROOT" ? "ROOT" : el.dataset.crumb;
        s.parent = id;
        await renderDominator(container, rid, id);
      });
    });
    list.querySelectorAll(".insp-dom-exp").forEach((el) => {
      el.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const id = el.dataset.parent;
        s.parent = id;
        await renderDominator(container, rid, id);
      });
    });
    list.querySelectorAll(".insp-dom-row").forEach((el) => {
      el.addEventListener("click", async () => {
        const id = parseInt(el.dataset.objid, 10);
        if (Number.isInteger(id) && id >= 0) await inspect(container, rid, id);
      });
    });
  } catch (e) {
    list.innerHTML = `<div class="hd-error-inline">${escAttr(e.message)}</div>`;
  }
}

function buildCrumbs(s, parent) {
  if (!parent || parent === "ROOT") return [{ id: "ROOT", label: t("heapdump.insp_root") }];
  // 简化为 ROOT / parent（深层面包屑需额外遍历，避免额外请求）
  return [{ id: "ROOT", label: t("heapdump.insp_root") }, { id: parent, label: "#" + parent }];
}

// ---------- 对象 Inspector ----------

/** 复用入口：在给定 container 的 [data-obj="panel"] 内渲染对象详情（两栏区块用）。 */
async function inspect(container, rid, objectId, opts = {}) {
  const s = st(rid);
  const panel = container.querySelector('[data-obj="panel"]');
  if (!panel) return;
  if (opts.push !== false) {
    if (s.objectId != null) s.objectHistory.push(s.objectId);
  }
  s.objectId = objectId;
  const backBtn = container.querySelector('[data-act="back"]');
  if (backBtn) backBtn.disabled = !(s.objectHistory.length > 0);
  await _renderObjectDetail(panel, rid, objectId, s.dir, (id) => inspect(container, rid, id));
}

/** 独立入口：把对象详情直接渲染进 container（MAT 工作台左侧 Inspector 用），
 *  引用导航在同一 container 内跳转。dir 为初始引用方向；onNavigate 可选，
 *  用于在导航到新对象时让调用方跟踪历史（back/forward）。 */
export async function renderObjectDetail(container, rid, objectId, dir = "out", onNavigate) {
  const nav = onNavigate || ((id) => renderObjectDetail(container, rid, id, dir));
  await _renderObjectDetail(container, rid, objectId, dir, nav);
}

async function _renderObjectDetail(panel, rid, objectId, dir, onOpen) {
  if (!panel) return;
  let currentDir = dir;
  panel.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
  let obj;
  try {
    obj = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}/object?id=${objectId}`);
  } catch (e) {
    panel.innerHTML = `<div class="hd-error-inline">${escAttr(e.message)}</div>`;
    return;
  }

  const kind = obj.kind || "instance";
  let html = `<div class="insp-obj-head">
    <div class="insp-obj-label" title="${escAttr(obj.label || "")}">${escAttr(obj.label || "#" + objectId)}</div>
    <div class="insp-obj-meta">
      <span class="insp-obj-type">${escAttr(obj.type || "?")}</span>
      <span class="insp-obj-addr">${escAttr(obj.address || "")}</span>
      ${obj.shallowBytes != null ? `<span>${escAttr(t("heapdump.insp_shallow"))}: ${escAttr(fmtBytes(obj.shallowBytes))}</span>` : ""}
      ${obj.retainedBytes != null ? `<span>${escAttr(t("heapdump.insp_retained"))}: ${escAttr(fmtBytes(obj.retainedBytes))}</span>` : ""}
      ${obj.gcRoot ? `<span class="insp-gcroot">${escAttr(t("heapdump.insp_gcroot"))}</span>` : ""}
    </div>
    ${obj.value != null ? `<div class="insp-obj-value">${escAttr(obj.value)}</div>` : ""}
  </div>`;

  if (kind === "class") {
    if (obj.instances != null) html += `<div class="insp-obj-line">${escAttr(t("heapdump.insp_instances", { n: fmtNum(obj.instances) }))}</div>`;
    if (obj.superclass) html += `<div class="insp-obj-line">${escAttr(t("heapdump.insp_superclass"))}: ${escAttr(obj.superclass)}</div>`;
  }

  if (obj.fields && obj.fields.length) {
    html += fieldTable(t("heapdump.insp_fields"), obj.fields);
  }
  if (obj.staticFields && obj.staticFields.length) {
    html += fieldTable(t("heapdump.insp_static_fields"), obj.staticFields);
  }

  // 数组元素 / 容器条目
  if (kind === "primitiveArray" && obj.elements && obj.elements.items) {
    html += `<div class="insp-block"><div class="insp-block-title">${escAttr(t("heapdump.insp_elements", { n: fmtNum(obj.elements.total) }))}</div>
      <div class="insp-arrayvals">${obj.elements.items.map((v) => `<code class="insp-arr-val">${escAttr(v)}</code>`).join("")}</div>
      ${obj.elements.truncated ? `<div class="insp-hint">${escAttr(t("heapdump.insp_truncated"))}</div>` : ""}</div>`;
  }
  if (kind === "objectArray" || (obj.hasArrayElements && obj.length > 0)) {
    html += `<div class="insp-block"><div class="insp-block-title">${escAttr(t("heapdump.insp_array_elems", { n: fmtNum(obj.length) }))}</div>
      <div data-arr="body"></div></div>`;
  }
  if (obj.hasCollection) {
    html += `<div class="insp-block"><div class="insp-block-title">${escAttr(t("heapdump.insp_collection", { n: obj.size != null ? fmtNum(obj.size) : "?" }))}</div>
      <div data-coll="body"></div></div>`;
  }

  // 引用
  html += `<div class="insp-block"><div class="insp-block-title">${escAttr(t("heapdump.insp_references"))}</div>
    <div class="insp-ref-toolbar">
      <button class="insp-btn small ${currentDir === "out" ? "active" : ""}" data-ref="out">${escAttr(t("heapdump.insp_ref_out"))}</button>
      <button class="insp-btn small ${currentDir === "in" ? "active" : ""}" data-ref="in">${escAttr(t("heapdump.insp_ref_in"))}</button>
    </div>
    <div data-ref="body"></div></div>`;

  panel.innerHTML = html;

  panel.querySelectorAll("[data-ref]").forEach((b) => {
    b.addEventListener("click", async () => {
      currentDir = b.dataset.ref;
      panel.querySelectorAll("[data-ref]").forEach((x) => x.classList.toggle("active", x === b));
      await renderReferencesBody(panel, rid, objectId, currentDir, onOpen);
    });
  });

  // 字段中的引用 → 点开新对象
  bindFieldRefs(panel, onOpen);

  // 数组元素 / 容器条目懒加载
  const arrBody = panel.querySelector('[data-arr="body"]');
  if (arrBody) await renderArrayElements(panel, rid, objectId, 0, onOpen);
  const collBody = panel.querySelector('[data-coll="body"]');
  if (collBody) await renderCollectionEntries(panel, rid, objectId, 0, onOpen);

  await renderReferencesBody(panel, rid, objectId, currentDir, onOpen);
}

function fieldTable(title, fields) {
  let html = `<div class="insp-block"><div class="insp-block-title">${escAttr(title)}</div>
    <table class="hd-table insp-field-table"><thead><tr>
      <th>${escAttr(t("heapdump.insp_col_field"))}</th>
      <th>${escAttr(t("heapdump.insp_col_type"))}</th>
      <th>${escAttr(t("heapdump.insp_col_value"))}</th>
    </tr></thead><tbody>`;
  fields.forEach((f) => {
    const isRef = !!f.isRef && f.refId != null;
    const val = isRef
      ? `<a class="insp-ref" href="javascript:void(0)" data-refid="${escAttr(f.refId)}" title="${escAttr(f.targetClass || "")}">${escAttr(f.value || "#" + f.refId)}</a>`
      : `<span class="insp-val">${escAttr(f.value == null ? "" : f.value)}</span>`;
    html += `<tr>
      <td class="insp-fname">${escAttr(f.name || "")}</td>
      <td class="insp-ftype" title="${escAttr(f.type || "")}">${escAttr(f.type || "")}</td>
      <td>${val}</td>
    </tr>`;
  });
  html += `</tbody></table></div>`;
  return html;
}

function bindFieldRefs(panel, onOpen) {
  panel.querySelectorAll("a.insp-ref[data-refid]").forEach((a) => {
    a.addEventListener("click", async () => {
      const id = parseInt(a.dataset.refid, 10);
      if (Number.isInteger(id) && id >= 0) await onOpen(id);
    });
  });
}

// ---------- 数组元素（load-more） ----------

async function renderArrayElements(panel, rid, objectId, offset, onOpen) {
  const body = panel.querySelector('[data-arr="body"]');
  if (!body) return;
  const key = `arr-${objectId}-${offset}`;
  body.dataset.key = key;
  body.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
  try {
    const data = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}/array-elements?id=${objectId}&top=${PAGE}&offset=${offset}`);
    if (body.dataset.key !== key) return;
    const rows = data.rows || [];
    const html = arrayTableHtml(rows, onOpen);
    const more = data.cursor ? `<button class="insp-btn small" data-more>${escAttr(t("heapdump.insp_load_more"))}</button>` : "";
    body.innerHTML = html + (more ? `<div class="insp-more">${more}</div>` : "");
    bindFieldRefs(body, onOpen);
    body.querySelector("[data-more]")?.addEventListener("click", async () => {
      await renderArrayElements(panel, rid, objectId, offset + PAGE, onOpen);
    });
  } catch (e) {
    if (body.dataset.key === key) body.innerHTML = `<div class="hd-error-inline">${escAttr(e.message)}</div>`;
  }
}

// ---------- 容器条目（load-more） ----------

async function renderCollectionEntries(panel, rid, objectId, offset, onOpen) {
  const body = panel.querySelector('[data-coll="body"]');
  if (!body) return;
  const key = `coll-${objectId}-${offset}`;
  body.dataset.key = key;
  body.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
  try {
    const data = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}/collection-entries?id=${objectId}&top=${PAGE}&offset=${offset}`);
    if (body.dataset.key !== key) return;
    const rows = data.rows || [];
    const open = (id) => onOpen(id);
    let html = "";
    if (data.kind === "map-entries") {
      html = `<table class="hd-table insp-ref-table"><thead><tr>
        <th>${escAttr(t("heapdump.insp_col_key"))}</th><th>${escAttr(t("heapdump.insp_col_value"))}</th>
      </tr></thead><tbody>` + rows.map((r) => {
        const cell = (c) => c && c.isNull ? `<span class="insp-hint">null</span>`
          : `<a class="insp-ref" href="javascript:void(0)" data-refid="${escAttr(c.objectId)}">${escAttr(c.label || "#" + c.objectId)}</a>`;
        return `<tr><td>${cell(r.key)}</td><td>${cell(r.value)}</td></tr>`;
      }).join("") + `</tbody></table>`;
    } else {
      html = arrayTableHtml(rows, open);
    }
    const more = data.cursor ? `<button class="insp-btn small" data-more>${escAttr(t("heapdump.insp_load_more"))}</button>` : "";
    body.innerHTML = html + (more ? `<div class="insp-more">${more}</div>` : "");
    bindFieldRefs(body, open);
    body.querySelector("[data-more]")?.addEventListener("click", async () => {
      await renderCollectionEntries(panel, rid, objectId, offset + PAGE, onOpen);
    });
  } catch (e) {
    if (body.dataset.key === key) body.innerHTML = `<div class="hd-error-inline">${escAttr(e.message)}</div>`;
  }
}

function arrayTableHtml(rows, open) {
  const bodyRows = rows.map((r) => {
    const label = r.isNull
      ? `<span class="insp-hint">null</span>`
      : `<a class="insp-ref" href="javascript:void(0)" data-refid="${escAttr(r.objectId)}" title="${escAttr(r.className || "")}">${escAttr(r.label || "#" + r.objectId)}</a>`;
    return `<tr><td>${label}</td><td class="insp-ftype">${escAttr(r.className || "")}</td></tr>`;
  }).join("");
  return `<table class="hd-table insp-ref-table"><thead><tr>
    <th>${escAttr(t("heapdump.insp_col_object"))}</th><th>${escAttr(t("heapdump.insp_col_type"))}</th>
  </tr></thead><tbody>${bodyRows}</tbody></table>`;
}

// ---------- 引用视图 ----------

async function renderReferences(container, rid) {
  const s = st(rid);
  const objPanel = container.querySelector('[data-obj="panel"]');
  if (s.objectId == null) {
    if (objPanel) objPanel.innerHTML = `<div class="insp-hint">${escAttr(t("heapdump.insp_ref_noselect"))}</div>`;
    return;
  }
  await renderReferencesBody(objPanel, rid, s.objectId, s.dir, (id) => inspect(container, rid, id));
}

async function renderReferencesBody(panel, rid, objectId, direction, onOpen) {
  const body = panel.querySelector('[data-ref="body"]');
  if (!body) return;
  body.innerHTML = `<div class="hd-progress-inline"><span class="spin"></span>…</div>`;
  try {
    const data = await api(`/api/heapdump-reports/${encodeURIComponent(rid)}/references?top=100&offset=0`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ direction, objectId }),
    });
    const rows = data.rows || [];
    if (!rows.length) {
      body.innerHTML = `<div class="insp-hint">${escAttr(t("heapdump.insp_ref_empty"))}</div>`;
      return;
    }
    body.innerHTML = `<table class="hd-table insp-ref-table"><thead><tr>
      <th>${escAttr(t("heapdump.insp_col_field"))}</th>
      <th>${escAttr(t("heapdump.insp_col_object"))}</th>
      <th>${escAttr(t("heapdump.insp_col_shallow"))}</th>
      <th>${escAttr(t("heapdump.insp_col_retained"))}</th>
    </tr></thead><tbody>` + rows.map((r) => {
      const label = r.isNull ? `<span class="insp-hint">null</span>`
        : `<a class="insp-ref" href="javascript:void(0)" data-refid="${escAttr(r.objectId)}" title="${escAttr(r.className || "")}">${escAttr(r.label || "#" + r.objectId)}</a>`;
      return `<tr>
        <td class="insp-fname">${escAttr(r.fieldName || "")}</td>
        <td>${label}</td>
        <td class="num">${escAttr(fmtBytes(r.shallowBytes))}</td>
        <td class="num">${escAttr(fmtBytes(r.retainedBytes))}</td>
      </tr>`;
    }).join("") + `</tbody></table>`;
    bindFieldRefs(body, onOpen);
  } catch (e) {
    body.innerHTML = `<div class="hd-error-inline">${escAttr(e.message)}</div>`;
  }
}
