// charts.js — GC & JStack 图表渲染（纯 UI，无业务副作用）
import { t } from "../i18n/index.js";
import { escapeHtml, cssVar, formatN, formatTime as _formatTime } from "./shared.js";

// 在元素上挂载 resize 监听，渲染前移除旧的 handler，避免重复绑定导致内存泄漏。
function attachResize(el, chart) {
  if (el._resizeHandler) window.removeEventListener("resize", el._resizeHandler);
  const resize = () => chart.resize();
  window.addEventListener("resize", resize);
  el._resizeHandler = resize;
}

// 用 ResizeObserver 监听容器宽度变化（侧栏折叠/展开时触发），比 window.resize 更精确
function attachResizeObserver(el, chart) {
  if (el._ro) { el._ro.disconnect(); el._ro = null; }
  if (typeof ResizeObserver === "undefined") return;
  const ro = new ResizeObserver(() => chart.resize());
  ro.observe(el);
  el._ro = ro;
}

// 格式化为 HH:MM:SS（绝对时间），跨天显示 MM-DD HH:MM:SS，回退为相对时间
function formatTime(sec, startEpochMs) {
  if (startEpochMs != null) {
    const d = new Date(startEpochMs + sec * 1000);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    const startDate = new Date(startEpochMs);
    if (d.toDateString() !== startDate.toDateString()) {
      const month = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${month}-${day} ${hh}:${mm}:${ss}`;
    }
    return `${hh}:${mm}:${ss}`;
  }
  return _formatTime(sec);
}

// ==================== GC 分类统计表 ====================
export function renderCatTable(byCat) {
  const cats = Object.keys(byCat);
  if (!cats.length) return '<div style="color:var(--text-dim);font-size:12px;">' + t("gc.cat_table_empty") + '</div>';
  let html = '<table class="cat-table"><thead><tr><th>' + t("gc.category") + '</th><th>' + t("gc.count") + '</th><th>' + t("gc.total_pause") + '</th><th>' + t("gc.avg_pause") + '</th><th>' + t("gc.max_pause") + '</th><th>' + t("gc.p95_pause") + '</th><th>' + t("gc.p99_pause") + '</th><th>' + t("gc.avg_freed") + '</th></tr></thead><tbody>';
  for (const cat of cats) {
    const s = byCat[cat];
    html += `<tr>
      <td>${escapeHtml(String(cat))}</td>
      <td>${s.count}</td>
      <td>${s.total_pause_ms.toFixed(1)}ms</td>
      <td>${s.avg_pause_ms.toFixed(1)}ms</td>
      <td>${s.max_pause_ms.toFixed(1)}ms</td>
      <td>${s.p95_pause_ms.toFixed(1)}ms</td>
      <td>${s.p99_pause_ms.toFixed(1)}ms</td>
      <td>${s.avg_freed_mb.toFixed(1)} MB</td>
    </tr>`;
  }
  return html + "</tbody></table>";
}

// ==================== GC 融合图 ====================
export function drawCombinedChart(series, heapMax, startEpochMs) {
  const el = document.getElementById("combinedChart");
  if (!el) return;
  if (!series.length) {
    el.insertAdjacentHTML("beforeend",
      '<div style="color:var(--text-dim);font-size:12px;text-align:center;padding:30px;">' + t("gc.chart_no_data") + '</div>');
    return;
  }

  const heapData = series.filter(p => p.before != null && p.before > 0);
  // 没有堆数据时，仍渲染暂停时间柱状图（ZGC / Shenandoah 可能没有堆前后值）

  const colorOf = c => ({ Young: "#79c0ff", Full: "#f85149", Mixed: "#a371f7", Remark: "#d29922", Cleanup: "#a371f7", ZGC: "#56d364", Shenandoah: "#56d364" }[c] || "#8b949e");
  const yLeftMax = Math.max(heapMax || 0, ...heapData.map(p => Math.max(p.before, p.after, p.total))) * 1.08 || 1;
  const yRightMax = Math.max(...series.map(p => p.dur)) * 1.15 || 1;
  let xMin = series[0].t;
  let xMax = series[series.length - 1].t;
  if (xMin === xMax) { xMin -= 0.5; xMax += 0.5; }

  if (el._echart) el._echart.dispose();

  let chart;
  try { chart = echarts.init(el, null, { renderer: 'canvas' }); } catch(e) { console.error('echarts init error:', e); return; }
  // _echart 引用同时存到 chart-wrap (openChartZoom 通过它找 ECharts 实例)
  const chartWrap = el.closest('.chart-wrap') || el;
  chartWrap._echart = chart;

  const hasHeap = heapData.length > 0;
  const heapSeries = hasHeap ? [{
      name: t("gc.chart.heap_before"), type: 'line',
      data: heapData.map(p => [p.t, p.before]),
      symbol: 'circle', symbolSize: 3,
      lineStyle: { color: '#79c0ff', width: 1.6 },
      itemStyle: { color: '#79c0ff' },
      areaStyle: { color: 'rgba(63,185,80,0.05)' },
      yAxisIndex: 0, z: 3, clip: true,
    }, {
      name: t("gc.chart.heap_after"), type: 'line',
      data: heapData.map(p => [p.t, p.after]),
      symbol: 'circle', symbolSize: 3,
      lineStyle: { color: '#3fb950', width: 1.6 },
      itemStyle: { color: '#3fb950' },
      yAxisIndex: 0, z: 3, clip: true,
    }] : [];

  const leftAxisName = heapMax ? `${t("gc.chart.yaxis_mb")} / ${t("gc.heap_size")} ${heapMax} MB` : t("gc.chart.yaxis_mb");

  const option = {
    backgroundColor: 'transparent',
    animation: true, animationDuration: 300,
    tooltip: {
      trigger: 'axis', confine: true, backgroundColor: '#161b22', borderColor: '#2a313c', borderWidth: 1,
      textStyle: { color: '#e6edf3', fontSize: 12 },
      formatter: (params) => {
        if (!params || params.length === 0) return '';
        // 通过 x 轴时间戳定位到原始事件 (与 legend 显示状态解耦,
        // 否则取消勾选 Pause Time 后 params 不含该系列, 会导致 tooltip 为空)
        const x = params[0].value[0];
        const raw = series.find(p => p.t === x);
        if (!raw) return '';
        const timeStr = formatTime(raw.t, startEpochMs);
        let html = `<div style="font-weight:600;margin-bottom:3px;">GC#${raw.id} · ${raw.cat} · @${timeStr}</div>`;
        // 堆信息区域始终显示（数值不可用时显示 N/A）
        html += `<hr style="border:0;border-top:1px solid var(--border);margin:3px 0;"/>`;
        const hasHeapData = raw.before != null && raw.before > 0;
        if (hasHeapData) {
          const pct = Math.max(0, Math.min(100, (raw.before - raw.after) / raw.before * 100));
          html += `<div style="display:flex;justify-content:space-between;"><span style="color:var(--text-dim);">${t("gc.chart.heap_before")}</span><span>${raw.before.toFixed(1)} MB</span></div>`;
          html += `<div style="display:flex;justify-content:space-between;"><span style="color:var(--text-dim);">${t("gc.chart.heap_after")}</span><span>${raw.after.toFixed(1)} MB</span></div>`;
          html += `<div style="display:flex;justify-content:space-between;"><span style="color:var(--text-dim);">${t("gc.chart.reclaimed")}</span><span>${(raw.before - raw.after).toFixed(1)} MB (${pct.toFixed(1)}%)</span></div>`;
          html += `<div style="display:flex;justify-content:space-between;"><span style="color:var(--text-dim);">${t("gc.heap_size")}</span><span>${raw.total.toFixed(1)} MB</span></div>`;
        } else {
          html += `<div style="display:flex;justify-content:space-between;"><span style="color:var(--text-dim);">${t("gc.chart.heap_before")}</span><span style="color:var(--text-dim);">N/A</span></div>`;
          html += `<div style="display:flex;justify-content:space-between;"><span style="color:var(--text-dim);">${t("gc.chart.heap_after")}</span><span style="color:var(--text-dim);">N/A</span></div>`;
          html += `<div style="display:flex;justify-content:space-between;"><span style="color:var(--text-dim);">${t("gc.chart.reclaimed")}</span><span style="color:var(--text-dim);">N/A</span></div>`;
          html += `<div style="display:flex;justify-content:space-between;"><span style="color:var(--text-dim);">${t("gc.heap_size")}</span><span style="color:var(--text-dim);">N/A</span></div>`;
        }
        html += `<hr style="border:0;border-top:1px solid var(--border);margin:3px 0;"/>`;
        const dc = raw.dur > 500 ? 'var(--red)' : (raw.dur > 200 ? 'var(--orange)' : 'var(--text)');
        html += `<div style="display:flex;justify-content:space-between;"><span style="color:#8b949e;">${t("gc.chart.pause_time")}</span><span style="color:${dc};">${raw.dur.toFixed(2)} ms</span></div>`;
        return html;
      },
    },
    legend: {
      data: [
        ...(hasHeap ? [t("gc.chart.heap_before"), t("gc.chart.heap_after")] : []),
        ...[...new Set(series.map(p => p.cat).filter(Boolean))],
      ],
      textStyle: { color: cssVar('--text-dim'), fontSize: 11 }, bottom: 0, icon: 'circle', itemWidth: 10, itemHeight: 10,
    },
    grid: { left: 60, right: 60, top: 38, bottom: 36 },
    xAxis: {
      type: 'value', min: xMin, max: xMax,
      axisLine: { lineStyle: { color: cssVar('--scrollbar-hover') } },
      axisTick: { lineStyle: { color: cssVar('--scrollbar-hover') } },
      axisLabel: { color: cssVar('--text-dim'), fontSize: 10, formatter: v => formatTime(v, startEpochMs) },
      splitLine: { lineStyle: { color: cssVar('--border'), type: 'dashed', width: 0.5 } },
    },
    yAxis: [{
      type: 'value', name: leftAxisName, nameLocation: 'middle', nameGap: 45,
      nameTextStyle: { color: cssVar('--primary'), fontWeight: 600, fontSize: 10 },
      min: 0, max: yLeftMax,
      axisLabel: { color: cssVar('--text-dim'), fontSize: 10, formatter: v => formatN(v) },
      splitLine: { lineStyle: { color: cssVar('--border'), width: 0.5 } },
      axisLine: { lineStyle: { color: cssVar('--scrollbar-hover') } },
    }, {
      type: 'value', name: t("gc.chart.yaxis_ms"), nameLocation: 'middle', nameGap: 45,
      nameTextStyle: { color: cssVar('--red'), fontWeight: 600, fontSize: 10 },
      min: 0, max: yRightMax,
      axisLabel: { color: cssVar('--red'), fontSize: 10, formatter: v => formatN(v) },
      splitLine: { show: false },
      axisLine: { lineStyle: { color: cssVar('--scrollbar-hover') } },
    }],
    dataZoom: [{
      type: 'inside', xAxisIndex: 0, filterMode: 'none', zoomOnMouseWheel: true, moveOnMouseMove: false,
    }],
    series: [
      ...heapSeries,
      ...[...new Set(series.map(p => p.cat).filter(Boolean))].map((cat, idx) => ({
        name: cat,
        type: 'scatter',
        symbolSize: p => Math.min(8, 3 + Math.sqrt(p[1]) * 0.3),
        data: series.filter(p => p.cat === cat).map(p => [p.t, p.dur]),
        itemStyle: { color: colorOf(cat), opacity: 0.75 },
        yAxisIndex: 1, z: 5, clip: true,
        ...(idx === 0 ? {
          markArea: {
            silent: true, itemStyle: { opacity: 0.07 },
            data: [
              [{ yAxis: 0, itemStyle: { color: '#3fb950' } }, { yAxis: 200 }],
              [{ yAxis: 200, itemStyle: { color: '#d29922' } }, { yAxis: 500 }],
              [{ yAxis: 500, itemStyle: { color: '#f85149' } }, { yAxis: yRightMax }],
            ],
          },
          markLine: {
            silent: true, symbol: 'none', lineStyle: { type: 'dashed', width: 1 },
            label: { fontSize: 9, color: '#8b949e', position: 'insideStartTop' },
            data: [
              { yAxis: 200, lineStyle: { color: '#d29922' }, label: { formatter: '200ms' } },
              { yAxis: 500, lineStyle: { color: '#f85149' }, label: { formatter: '500ms' } },
            ],
          },
        } : {}),
      })),
    ],
  };

  try { chart.setOption(option); } catch(e) { console.error('echarts setOption error:', e); return; }

  chart.getZr().on('dblclick', () => { chart.dispatchAction({ type: 'dataZoom', start: 0, end: 100 }); });

  // toolbar 是 chart-wrap 的子节点 (sibling of ECharts target div),
// 不能用 el.querySelectorAll (el 是 inner div); 改用 closest('.chart-wrap') 找外层
  const toolbarScope = el.closest('.chart-wrap') || el;
  toolbarScope.querySelectorAll('.chart-toolbar button').forEach(btn => {
    btn.onclick = () => {
      const act = btn.dataset.act;
      if (act === 'reset') {
        chart.dispatchAction({ type: 'dataZoom', startValue: xMin, endValue: xMax });
      } else if (act === 'expand') {
        openChartZoom(chartWrap);
      } else {
        const zoom = chart.getModel().getComponent('dataZoom', 0);
        if (zoom) {
          let start = zoom.get('start') || 0, end = zoom.get('end') || 100;
          const range = end - start;
          const factor = act === 'zoom-in' ? 0.5 : 1.5;
          const newRange = Math.min(Math.max(range * factor, 2), 100);
          const center = (start + end) / 2;
          start = Math.max(0, center - newRange / 2);
          end = Math.min(100, center + newRange / 2);
          if (end - start < 2) return;
          chart.dispatchAction({ type: 'dataZoom', start, end });
        }
      }
    };
  });

  attachResizeObserver(el, chart);
}

// ---------- 图表放大浮层 ----------
// 把 chart-wrap DOM 临时搬到浮层, 关闭时移回原位置; ECharts 实例状态零丢失 (zoom/hover/数据范围全保留)
export function openChartZoom(chartWrapEl) {
  // 防御: 清理任何残留的 overlay (Vite HMR / 上次未关闭 / 异常退出)
  const stale = document.getElementById('chartZoomOverlay');
  if (stale) stale.remove();
  if (!chartWrapEl) return;
  const chart = chartWrapEl._echart;
  if (!chart) return;

  // 备份并隐藏 chart-wrap 内 toolbar (放大后内部 toolbar 不再显示)
  const toolbar = chartWrapEl.querySelector('.chart-toolbar');
  const originalToolbarDisplay = toolbar ? toolbar.style.display : '';
  if (toolbar) toolbar.style.display = 'none';

  const original = { parent: chartWrapEl.parentNode, next: chartWrapEl.nextSibling };
  const originalStyle = { width: chartWrapEl.style.width, height: chartWrapEl.style.height };

  const overlay = document.createElement('div');
  overlay.id = 'chartZoomOverlay';
  overlay.className = 'chart-zoom-overlay';
  overlay.innerHTML = `
    <div class="chart-zoom-container">
      <div class="chart-zoom-header">
        <span class="chart-zoom-title">${escapeHtml(t("gc.combined_chart"))}</span>
        <button class="chart-zoom-close" title="${escapeHtml(t("gc.chart.zoom_close"))}">×</button>
      </div>
      <div class="chart-zoom-body"></div>
    </div>`;
  document.body.appendChild(overlay);

  const body = overlay.querySelector('.chart-zoom-body');
  chartWrapEl.style.width = '100%';
  chartWrapEl.style.height = '100%';
  body.appendChild(chartWrapEl);
  chart.resize();

  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    ro.disconnect();
    document.removeEventListener('keydown', escHandler);
    if (toolbar) toolbar.style.display = originalToolbarDisplay;
    chartWrapEl.style.width = originalStyle.width;
    chartWrapEl.style.height = originalStyle.height;
    if (original.parent) {
      original.parent.insertBefore(chartWrapEl, original.next);
    }
    overlay.remove();
    chart.resize();
  };
  const escHandler = (e) => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', escHandler);
  overlay.querySelector('.chart-zoom-close').onclick = close;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  const ro = new ResizeObserver(() => chart.resize());
  ro.observe(body);
}

// ==================== JStack 图表 HTML ====================
export function renderJstackCharts(s) {
  const bs = s.by_state || {};

  let html = '<div class="section-title">' + t("jstack.thread_distribution") + '</div>';
  html += '<div class="chart-wrap" id="jstackStateChart" style="height:260px;"><div class="chart-toolbar"></div></div>';

  if (s.runnable_hot_methods && s.runnable_hot_methods.length) {
    html += '<div class="section-title">' + t("jstack.runnable_hotspots") + '</div>';
    html += '<div class="chart-wrap" id="jstackRunnableChart" style="height:' + Math.max(180, s.runnable_hot_methods.length * 28) + 'px;"><div class="chart-toolbar"></div></div>';
  }

  if (s.thread_pools && s.thread_pools.length > 1) {
    const poolCount = Math.min(s.thread_pools.length, 15);
    html += '<div class="section-title">' + t("jstack.thread_pool_dist") + '</div>';
    html += '<div class="chart-wrap" id="jstackPoolChart" style="height:' + Math.max(180, poolCount * 28) + 'px;"><div class="chart-toolbar"></div></div>';
  }

  if (s.flamegraph && s.flamegraph.value > 0) {
    html += '<div class="section-title">' + t("jstack.flame_graph") + '</div>';
    html += '<div class="flamegraph-wrap"><div class="flamegraph-toolbar">';
    html += '<span id="flameTitle" style="color:var(--text-dim);font-size:11px;">' + t("jstack.flame_title_prefix") + s.flamegraph.value + '</span>';
    html += '<button id="flameResetBtn" style="display:none;">' + t("jstack.flame_reset_zoom") + '</button>';
    html += '</div><div class="flamegraph-container" id="flamegraphContainer"></div></div>';
  }

  setTimeout(() => { drawJstackCharts(s); drawFlamegraph(s.flamegraph); }, 50);
  return html;
}

// ==================== JStack 图表绘制 ====================
export function drawJstackCharts(s) {
  const bs = s.by_state || {};
  const stateColors = { RUNNABLE: "#3fb950", BLOCKED: "#f85149", WAITING: "#d29922", TIMED_WAITING: "#8b949e" };
  const isLight = document.documentElement.dataset.theme === "light";
  const labelColor = isLight ? "#1c2025" : "#e6edf3";
  const labelLineColor = isLight ? "#8b949e" : "#3a424d";

  const pieEl = document.getElementById("jstackStateChart");
  if (pieEl) {
    const chart = echarts.init(pieEl);
    const data = Object.entries(bs).map(([name, value]) => ({ name, value, itemStyle: { color: stateColors[name] || "#888" } }));
    chart.setOption({
      tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
      series: [{ type: "pie", radius: ["40%", "70%"], center: ["50%", "50%"], data, label: { color: labelColor, fontSize: 11 }, labelLine: { lineStyle: { color: labelLineColor } } }],
    });
    attachResizeObserver(pieEl, chart);
    setTimeout(() => chart.resize(), 100);
  }

  const runnableEl = document.getElementById("jstackRunnableChart");
  if (runnableEl && s.runnable_hot_methods && s.runnable_hot_methods.length) {
    const chart = echarts.init(runnableEl);
    const data = s.runnable_hot_methods.slice(0, 10).reverse();
    chart.setOption({
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: p => {
        const item = p[0];
        return `<strong>${escapeHtml(data[item.dataIndex][0])}</strong><br/>线程数: ${item.value}`;
      }},
      grid: { left: 180, right: 50, top: 10, bottom: 8 },
      xAxis: { type: "value", axisLabel: { color: cssVar('--text-dim'), fontSize: 10 }, splitLine: { lineStyle: { color: cssVar('--border'), type: "dashed" } } },
      yAxis: { type: "category", data: data.map(d => d[0]), axisLabel: { color: cssVar('--text'), fontSize: 10, width: 170, overflow: "truncate" }, axisLine: { lineStyle: { color: cssVar('--scrollbar-hover') } }, axisTick: { show: false } },
      series: [{ type: "bar", data: data.map(d => d[1]), itemStyle: { color: cssVar('--green'), borderRadius: [0, 3, 3, 0] }, barWidth: "55%", label: { show: true, position: "right", color: cssVar('--text-dim'), fontSize: 10, formatter: p => p.value } }],
    });
    attachResizeObserver(runnableEl, chart);
  }

  const poolEl = document.getElementById("jstackPoolChart");
  if (poolEl && s.thread_pools && s.thread_pools.length) {
    const chart = echarts.init(poolEl);
    const data = s.thread_pools.slice(0, 15);
    const maxVal = Math.max(...data.map(d => d.total), 1);
    chart.setOption({
      tooltip: {
        trigger: "axis", axisPointer: { type: "shadow" }, confine: true,
        formatter: p => {
          const idx = data.length - 1 - p[0].dataIndex;
          const d = data[idx];
          if (!d) return "";
          return `<strong>${escapeHtml(d.pool)}</strong><br/>总数: ${d.total}<br/><span style="color:var(--green)">RUNNABLE: ${d.RUNNABLE}</span><br/><span style="color:var(--red)">BLOCKED: ${d.BLOCKED}</span><br/><span style="color:var(--orange)">WAITING: ${d.WAITING}</span><br/><span style="color:var(--text-dim)">TIMED_WAITING: ${d.TIMED_WAITING}</span>`;
        },
      },
      grid: { left: 160, right: 50, top: 10, bottom: 8 },
      xAxis: { type: "value", max: maxVal * 1.15, axisLabel: { color: cssVar('--text-dim'), fontSize: 10 }, splitLine: { lineStyle: { color: cssVar('--border'), type: "dashed" } } },
      yAxis: { type: "category", data: data.map(d => d.pool).reverse(), axisLabel: { color: cssVar('--text'), fontSize: 10, width: 150, overflow: "truncate" }, axisLine: { lineStyle: { color: cssVar('--scrollbar-hover') } }, axisTick: { show: false } },
      series: [{ type: "bar", data: data.map(d => d.total).reverse(), itemStyle: { color: cssVar('--green'), borderRadius: [0, 3, 3, 0] }, barWidth: "55%", label: { show: true, position: "right", color: cssVar('--text-dim'), fontSize: 10, formatter: p => p.value } }],
    });
    attachResizeObserver(poolEl, chart);
  }
}

// ==================== 火焰图 ====================
// 传统火焰图（bottom-up）：入口方法在栈底，栈顶方法在上方。
// 下钻聚焦某帧时保留完整祖先链（祖先各层在下方全宽显示，聚焦子树在上方按比例放大）。
const FLAME_ROW_H = 22;
let flameOriginalRoot = null;
let flameFocusNode = null;
let _flameResizeObserver = null;

export function drawFlamegraph(rootData) {
  if (!rootData || !rootData.value) return;
  flameOriginalRoot = rootData;
  flameFocusNode = rootData;
  renderFlamegraph();

  document.getElementById("flameResetBtn").onclick = () => {
    flameFocusNode = flameOriginalRoot;
    renderFlamegraph();
  };

  // ResizeObserver：容器宽度变化时重绘，避免帧错位/溢出（对齐 Vue Flamegraph 行为）
  const container = document.getElementById("flamegraphContainer");
  if (_flameResizeObserver) { _flameResizeObserver.disconnect(); _flameResizeObserver = null; }
  if (container && typeof ResizeObserver !== "undefined") {
    _flameResizeObserver = new ResizeObserver(() => renderFlamegraph());
    _flameResizeObserver.observe(container);
  }
}

function flameMaxDepth(node, d) {
  let m = d;
  for (const c of node.children) m = Math.max(m, flameMaxDepth(c, d + 1));
  return m;
}

// 找到从 root 到 target 的祖先路径（含 target）
function flameFindPath(root, target) {
  if (root === target) return [root];
  for (const c of root.children || []) {
    const sub = flameFindPath(c, target);
    if (sub) return [root, ...sub];
  }
  return null;
}

function renderFlamegraph() {
  const container = document.getElementById("flamegraphContainer");
  if (!container || !flameOriginalRoot) return;
  const root = flameOriginalRoot;
  const focus = flameFocusNode || root;
  const cw = Math.max(container.clientWidth - 2, 200);

  // 从 root 到 focus 的路径（栈底→...→focus）
  const path = flameFindPath(root, focus) || [root];
  const pathDepth = path.length - 1;
  const subDepth = flameMaxDepth(focus, 0);
  const maxDepth = pathDepth + subDepth;

  const h = (maxDepth + 2) * FLAME_ROW_H + 4;
  container.style.height = h + "px";
  container.innerHTML = "";

  const makeFrame = (node, x, width, depth, parentValue, focused) => {
    if (width < 1) return;
    const div = document.createElement("div");
    div.className = "flame-frame";
    div.style.left = x + "px";
    // bottom-up：depth 越大越靠上
    div.style.top = ((maxDepth - depth) * FLAME_ROW_H + 2) + "px";
    div.style.width = Math.max(width, 2) + "px";
    div.style.height = FLAME_ROW_H + "px";

    const hue = 10 + (depth % 12) * 5;
    const lit = Math.max(35, 80 - depth * 4);
    if (focused) {
      div.style.backgroundColor = `hsl(${hue}, 70%, ${lit}%)`;
      div.style.color = lit < 50 ? "#e6edf3" : "#1c222b";
    } else {
      div.style.backgroundColor = `hsl(210, 12%, ${Math.max(28, 46 - depth * 2)}%)`;
      div.style.color = "#e6edf3";
    }

    if (width > 55) div.textContent = node.name.split(".").pop();

    const hasChildren = node.children && node.children.length > 0;
    if (hasChildren) {
      div.style.cursor = "pointer";
      div.addEventListener("click", (e) => {
        e.stopPropagation();
        flameFocusNode = node;
        renderFlamegraph();
      });
    }

    const totalPct = ((node.value / root.value) * 100).toFixed(1);
    const parentPct = parentValue > 0 ? ((node.value / parentValue) * 100).toFixed(1) : "100.0";
    div.title = node.name + "\n" + t("jstack.flame_tooltip", { n: node.value, total_pct: totalPct, parent_pct: parentPct });

    container.appendChild(div);
  };

  // 1) 祖先链：每层全宽显示（不含 focus 自身，focus 在子树里画）
  for (let i = 0; i < path.length - 1; i++) {
    const parentVal = i > 0 ? path[i - 1].value : path[i].value;
    makeFrame(path[i], 0, cw, i, parentVal, false);
  }

  // 2) focus 及其子树：从 pathDepth 层开始，按比例展开
  const layoutSubtree = (node, x, width, depth, parentValue) => {
    if (width < 1) return;
    makeFrame(node, x, width, depth, parentValue, true);
    let cx = x;
    for (const child of node.children || []) {
      const cwChild = (child.value / node.value) * width;
      layoutSubtree(child, cx, cwChild, depth + 1, node.value);
      cx += cwChild;
    }
  };
  layoutSubtree(focus, 0, cw, pathDepth, path.length > 1 ? path[path.length - 2].value : focus.value);

  const resetBtn = document.getElementById("flameResetBtn");
  if (resetBtn) resetBtn.style.display = focus !== root ? "" : "none";

  const titleEl = document.getElementById("flameTitle");
  if (titleEl) {
    const pct = ((focus.value / root.value) * 100).toFixed(1);
    const label = focus.name === "root" ? t("jstack.flame_label_all") : focus.name;
    titleEl.textContent = t("jstack.flame_title_format", { n: focus.value, pct, label });
  }
}

// ==================== GC 停顿分布直方图 ====================
export function drawPauseDistributionChart(series) {
  const el = document.getElementById("pauseDistChart");
  if (!el || !series.length) return;

  const buckets = [
    { label: "0-10ms", min: 0, max: 10 },
    { label: "10-50ms", min: 10, max: 50 },
    { label: "50-100ms", min: 50, max: 100 },
    { label: "100-200ms", min: 100, max: 200 },
    { label: "200-500ms", min: 200, max: 500 },
    { label: "500ms+", min: 500, max: Infinity },
  ];

  const counts = buckets.map(b => ({
    name: b.label,
    value: series.filter(p => p.dur >= b.min && p.dur < b.max).length,
  }));

  if (el._echart) el._echart.dispose();
  let chart;
  try { chart = echarts.init(el); } catch { return; }
  el._echart = chart;

  const maxColor = counts.reduce((a, b) => a.value > b.value ? a : b);
  const maxIdx = counts.indexOf(maxColor);

  chart.setOption({
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: p => `${p[0].name}<br/>事件数: ${p[0].value}` },
    grid: { left: 70, right: 20, top: 25, bottom: 50 },
    xAxis: { type: "category", data: counts.map(d => d.name), axisLabel: { color: cssVar('--text-dim'), fontSize: 10, rotate: 30, margin: 8 }, axisLine: { lineStyle: { color: cssVar('--scrollbar-hover') } } },
    yAxis: { type: "value", axisLabel: { color: cssVar('--text-dim'), fontSize: 10 }, splitLine: { lineStyle: { color: cssVar('--border'), type: "dashed" } } },
    series: [{
      type: "bar", data: counts.map((d, i) => ({
        value: d.value,
        itemStyle: { color: i === maxIdx ? '#f85149' : cssVar('--primary') },
      })),
      barWidth: "55%",
      label: { show: true, position: "top", color: cssVar('--text-dim'), fontSize: 10 },
    }],
  });
  attachResizeObserver(el, chart);
}
export function drawCategoryPieChart(byCategory, containerId = "catPieChart") {
  const el = document.getElementById(containerId);
  if (!el || !byCategory) return;

  // Common colors for categories and common causes
  const colorMap = { 
    Young: "#79c0ff", Full: "#f85149", Mixed: "#a371f7", Remark: "#d29922", Cleanup: "#a371f7", 
    InitialMark: "#8b949e", Concurrent: "#3fb950", ZGC: "#58a6ff", Shenandoah: "#bc8cff",
    "System.gc()": "#f85149", "Allocation Failure": "#79c0ff", "Ergonomics": "#a371f7", 
    "Metadata GC Threshold": "#d29922", "CMS Initial Mark": "#3fb950", "CMS Final Remark": "#58a6ff",
    "G1 Humongous Allocation": "#bc8cff", "Heap Inspection Initiated GC": "#8b949e", 
    "Heap Dump Initiated GC": "#f59e0b"
  };
  // Fallback colors if cause not predefined
  const fallbackColors = ["#79c0ff", "#f85149", "#a371f7", "#d29922", "#3fb950", "#58a6ff", "#bc8cff", "#8b949e", "#f59e0b", "#8b5cf6"];
  
  // For ZGC concurrent full GC, total_pause_ms can be 0 in logs but it's still a valid cause
  // Show even when 0 because it's better than hiding it from the table/pie chart
  const data = Object.entries(byCategory)
    .map(([name, s], index) => ({ 
      name, 
      value: s.total_pause_ms > 0 ? s.total_pause_ms : s.count,
      itemStyle: { color: colorMap[name] || fallbackColors[index % fallbackColors.length] } 
    }));

  if (!data.length) return;

  if (el._echart) el._echart.dispose();
  let chart;
  try { chart = echarts.init(el); } catch { return; }
  el._echart = chart;

  const isLight = document.documentElement.dataset.theme === "light";
  const labelColor = isLight ? "#1c2025" : "#e6edf3";
  const labelLineColor = isLight ? "#8b949e" : "#3a424d";

  chart.setOption({
    tooltip: { trigger: "item", formatter: "{b}: {c}ms ({d}%)" },
    series: [{
      type: "pie", radius: ["40%", "70%"], center: ["50%", "50%"],
      data,
      label: { color: labelColor, fontSize: 11, formatter: "{b}\n{d}%" },
      labelLine: { lineStyle: { color: labelLineColor } },
      emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: "rgba(0,0,0,0.5)" } },
    }],
  });
  attachResizeObserver(el, chart);
}
