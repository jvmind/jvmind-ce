import { state } from "../state.js";
import { app } from "../app.js";
import { csrfHeaders, escapeHtml, calculateGCStatsClasses, formatHealthBanner, formatEpoch, formatFreed, fmtDate } from "../shared.js";
import { renderMarkdown } from "../markdown.js";
import { drawCombinedChart, renderCatTable, drawPauseDistributionChart, drawCategoryPieChart } from "../charts.js";
import { t, th, getLang } from "../../i18n/index.js";
import { bindReportContext } from "./context.js";
import { feedbackWidgetHtml, bindFeedbackWidget } from "../feedback-widget.js";
import { ico } from "../icons.js";

export function appendSystemHint(html) {
  const area = document.getElementById("chatArea");
  const empty = area.querySelector(".empty");
  if (empty) empty.remove();
  const div = document.createElement("div");
  div.style.cssText = "text-align:center;color:var(--text-dim);font-size:12px;padding:6px 0;";
  div.innerHTML = html;
  area.appendChild(div);
  app.scrollToBottom();
}

// ---------- 渲染报告 ----------
let _lastAiBodyScroll = 0;

export function renderReport(report) {
  const area = document.getElementById("gcReportArea");
  area.style.display = "";
  const s = report.stats;

  const { fullCount, maxPause, pauseClass, tpClass, fullClass } = calculateGCStatsClasses(s);

  const reportUrl = `${location.origin}/report/${state.currentSessionId}/${report.id}`;
  const isReportOnly = document.body.classList.contains("report-only");
  const hasAi = !!report.ai_conclusion;

  // 健康横幅
  const banner = formatHealthBanner(s, t);

  // 内存诊断区块
  const diagHtml = (() => {
    const d = s.diagnosis;
    if (!d || !d.findings || !d.findings.length) return "";
    const lang = getLang();
    const findingsHtml = d.findings.map(f => `
      <div class="diag-finding diag-severity-${escapeHtml(String(f.severity || ""))}">
        <span class="diag-severity-tag">${t("gc.diagnosis_severity_" + f.severity)}</span>
        <div class="diag-finding-body">
          <div class="diag-finding-title">${escapeHtml(f["title_" + lang] || f.title_zh || "")}</div>
          <div class="diag-finding-detail">${escapeHtml(f["detail_" + lang] || f.detail_zh || "")}</div>
        </div>
      </div>
    `).join("");
    const recs = d["recommendations_" + lang] || d.recommendations_zh || [];
    const recsHtml = recs.length ? `
      <div class="diag-recommendations">
        <div class="diag-recs-title">${t("gc.diagnosis_recommendations")}</div>
        ${recs.map(r => `<div class="diag-rec-item">${escapeHtml(r)}</div>`).join("")}
      </div>
    ` : "";
    return `
      <div class="diagnosis-section">
        ${findingsHtml}
        ${recsHtml}
      </div>
    `;
  })();

  // AI 正文
  const aiHtml = hasAi ? renderMarkdown(report.ai_conclusion)
    : '<div style="text-align:center;padding:20px;color:var(--text-dim);font-size:13px;">' + t("gc.no_ai_conclusion") + '</div>';

  // JVM 启动参数（仅 JDK8 日志包含 CommandLine flags 时展示）
  const jvmArgsHtml = (Array.isArray(s.jvm_args) && s.jvm_args.length) ? `
    <div class="section-title">${t("gc.jvm_args")}</div>
    <div class="jvm-args-list">
      ${s.jvm_args.map(a => `<span class="jvm-arg-tag">${escapeHtml(a)}</span>`).join("")}
    </div>
  ` : "";

  const dataHtml = `
    ${banner}
    <div class="section-title">${t("gc.diagnosis_title")}</div>
    ${diagHtml}

    <div class="stat-grid">
      <div class="stat-card"><div class="label">${t("gc.collector")}</div><div class="value" style="font-size:16px;">${escapeHtml(String(s.collector ?? ""))}</div></div>
      <div class="stat-card"><div class="label">${t("gc.events_total")}</div><div class="value">${s.events_total}</div></div>
      <div class="stat-card ${fullClass}"><div class="label">${t("gc.full_gc")}</div><div class="value">${fullCount}</div></div>
      <div class="stat-card ${pauseClass}"><div class="label">${t("gc.max_pause")}</div><div class="value">${maxPause.toFixed(1)} ms</div></div>
      <div class="stat-card"><div class="label">${t("gc.total_pause")}</div><div class="value">${s.total_pause_ms.toFixed(0)} ms</div><div class="sub">${t("gc.stat_duration", { sec: s.duration_sec.toFixed(1) })}</div></div>
      <div class="stat-card ${tpClass}"><div class="label">${t("gc.throughput")}</div><div class="value">${s.throughput != null ? (s.throughput*100).toFixed(2)+"%" : "N/A"}</div></div>
      <div class="stat-card"><div class="label">${t("gc.heap_size")}</div><div class="value">${s.heap_max_mb ? s.heap_max_mb+" MB" : "N/A"}</div></div>
      <div class="stat-card"><div class="label">${t("gc.allocation_rate")}</div><div class="value">${s.avg_alloc_rate_mb_s != null ? s.avg_alloc_rate_mb_s+" MB/s" : "N/A"}</div></div>
      <div class="stat-card"><div class="label">${t("gc.events_per_min")}</div><div class="value">${s.events_per_minute != null ? s.events_per_minute : "N/A"}</div></div>
      <div class="stat-card"><div class="label">${t("gc.heap_usage")}</div><div class="value">${s.avg_heap_usage_pct != null ? s.avg_heap_usage_pct+"%" : "N/A"}</div><div class="sub">${t("gc.heap_usage_max")} ${s.max_heap_usage_pct != null ? s.max_heap_usage_pct+"%" : "N/A"}</div></div>
    </div>

    ${jvmArgsHtml}

    <div class="section-title">${t("gc.by_category")}</div>
    ${renderCatTable(s.by_category)}

    ${s.by_cause_full && Object.keys(s.by_cause_full).length > 0 ? `
    <div class="section-title">${t("gc.by_cause_full")}</div>
    ${renderCatTable(s.by_cause_full)}

    <div class="chart-row">
      <div class="chart-wrap" id="fullGcCausePieChart" style="flex:1;height:200px;"><div class="chart-toolbar"></div></div>
    </div>
    ` : ``}

    <div class="section-title">${t("gc.combined_chart")}</div>
    <div class="chart-wrap" id="combinedChartWrap" style="position:relative;">
      <div class="chart-toolbar">
        <button data-act="zoom-in" title="${t("gc.chart.zoom_in")}">＋</button>
        <button data-act="zoom-out" title="${t("gc.chart.zoom_out")}">−</button>
        <button data-act="reset" title="${t("gc.chart.zoom_reset")}">⟲</button>
        <button data-act="expand" title="${t("gc.chart.zoom_expand")}">⤢</button>
      </div>
      <div id="combinedChart" style="width:100%;height:100%;"></div>
    </div>
    ${s.series_sampled_count != null && s.series_total_stw != null && s.series_sampled_count < s.series_total_stw
      ? `<div class="chart-hint" style="font-size:11px;color:var(--text-dim);margin-top:4px;">${escapeHtml(t("gc.chart.sampling_note", { shown: s.series_sampled_count, total: s.series_total_stw }))}</div>`
      : ""}
    <div class="chart-hint" style="display:none;"></div>

    <div class="section-title">${t("gc.pause_dist")}</div>
    <div class="chart-row">
      <div class="chart-wrap" id="pauseDistChart" style="flex:1;height:200px;"><div class="chart-toolbar"></div></div>
      <div class="chart-wrap" id="catPieChart" style="flex:1;height:200px;"><div class="chart-toolbar"></div></div>
    </div>

    <div class="section-title">${t("gc.slowest_events")}</div>
    <div class="slow-table-wrap">
      <table class="slow-table">
        <thead>
          <tr>
            <th class="col-rank">${t("gc.col_rank")}</th>
            <th class="col-cat">Cat</th>
            <th class="col-id">GC#</th>
            <th class="col-time">${t("gc.col_time")}</th>
            <th class="col-cause">${t("gc.col_cause")}</th>
            <th class="col-dur">${t("gc.col_dur")}</th>
            <th class="col-freed">${t("gc.col_freed")}</th>
            <th class="col-toggle"></th>
            <th class="col-act"></th>
          </tr>
        </thead>
        <tbody>
          ${(() => {
            const maxDur = s.slowest.reduce((m, e) => Math.max(m, Number(e.dur) || 0), 0) || 1;
            return s.slowest.map((e, idx) => {
              const rank = idx + 1;
              const rankClass = rank <= 3 ? `top-${rank}` : "";
              const catSafe = escapeHtml(String(e.cat ?? ""));
              const timeText = e.abs_ms != null
                ? formatEpoch(e.abs_ms, s.start_epoch_ms)
                : (e.t != null ? `+${Number(e.t).toFixed(3)}s` : "");
              const freed = formatFreed(e.before, e.after);
              const barWidth = maxDur > 0 ? Math.max(2, Math.round((Number(e.dur) / maxDur) * 100)) : 0;
              const rawEnc = encodeURIComponent(e.raw_type || '');
              const evId = escapeHtml(String(e.id ?? ""));
              const repId = escapeHtml(String(report.id ?? ""));
              const fname = escapeHtml(report.filename || "");
              const actionCell = isReportOnly ? '' : `<button class="send-icon" data-act="send-event" data-report-id="${repId}" data-filename="${fname}" data-event-id="${evId}" data-raw-enc="${escapeHtml(rawEnc)}" title="${t("gc.send_to_agent")}">${ico('arrow-up-right')}</button>`;
              return `
              <tr class="slow-row" data-act="toggle-slow-row" data-id="${evId}">
                <td class="col-rank"><span class="rank ${rankClass}">${rank}</span></td>
                <td class="col-cat"><span class="cat-badge ${catSafe}">${catSafe}</span></td>
                <td class="col-id gc-id">GC#${evId}</td>
                <td class="col-time gc-time">${escapeHtml(timeText)}</td>
                <td class="col-cause gc-cause" title="${escapeHtml(e.cause ?? "")}">${escapeHtml(e.cause ?? "")}</td>
                <td class="col-dur">
                  <span class="dur-bar"><span class="dur-bar-fill" style="width:${barWidth}%"></span></span>
                  <span class="dur-text">${Number(e.dur).toFixed(1)}ms</span>
                </td>
                <td class="col-freed gc-freed ${freed.sign}">${escapeHtml(freed.text)}</td>
                <td class="col-toggle"><span class="caret">▶</span></td>
                <td class="col-act">${actionCell}</td>
              </tr>
              <tr class="slow-expand" data-id="${evId}">
                <td colspan="9"><pre class="expand-pre">${e.raw_type ? escapeHtml(e.raw_type) : escapeHtml(t("gc.event_raw_log"))}</pre></td>
              </tr>`;
            }).join("");
          })()}
        </tbody>
      </table>
    </div>
  `;

  // AI 折叠区（只读展示 + 发送到 Agent）
  const aiSectionHtml = `
    <div class="ai-section ${isReportOnly ? '' : (hasAi ? 'collapsed' : '')}">
      <div class="ai-header" ${isReportOnly ? '' : 'data-act="toggle-ai-collapse"'}>
        ${ico('ChevronDown', { className: 'collapse-icon' })}
        <span class="ai-title">${t("gc.ai_conclusion")}</span>
        ${isReportOnly ? '' : `<button class="btn" id="sendToAgentBtn" style="font-size:11px;padding:2px 8px;flex-shrink:0;">${t("gc.send_to_agent")}</button>`}
      </div>
      <div class="ai-body">
        <div class="ai-conclusion">${aiHtml}</div>
        ${hasAi && !isReportOnly ? feedbackWidgetHtml("gc", report.id) : ""}
      </div>
    </div>
  `;

  if (isReportOnly) {
    area.innerHTML = `
      <div style="margin-bottom:10px;display:flex;justify-content:space-between;align-items:flex-start;gap:10px;">
        <div style="min-width:0;flex:1;">
          <div style="font-weight:600;font-size:14px;">${escapeHtml(report.filename)}</div>
          <div style="color:var(--text-dim);font-size:11px;">
            ${(report.size/1024).toFixed(1)} KB · ${t("gc.report_meta_lines", { parsed: s.parsed_lines, total: s.total_lines || "?", jdk: s.jdk_version || "?" })} · ${fmtDate(report.created_at)}
          </div>
        </div>
      </div>
      <div class="report-layout">
        <div class="report-data">${dataHtml}</div>
        <div class="report-ai">${aiSectionHtml}</div>
      </div>
    `;
  } else {
    area.innerHTML = `
      <div style="margin-bottom:10px;">
        <div style="font-weight:600;font-size:14px;">${escapeHtml(report.filename)}</div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <div style="flex:1;min-width:0;color:var(--text-dim);font-size:11px;">
            ${(report.size/1024).toFixed(1)} KB · ${t("gc.report_meta_lines", { parsed: s.parsed_lines, total: s.total_lines || "?", jdk: s.jdk_version || "?" })} · ${fmtDate(report.created_at)}
          </div>
           <a href="${reportUrl}" target="_blank" rel="noopener" class="btn" id="openInNewBtn"
              style="font-size:11px;padding:4px 10px;text-decoration:none;flex-shrink:0;">${ico('link')} ${t("gc.report_url")}</a>
        </div>
      </div>
      ${dataHtml}
      ${aiSectionHtml}
    `;
  }

  // 绘制融合图表
  drawCombinedChart(s.series, s.heap_max_mb, s.start_epoch_ms);

  // 绘制分布直方图 + 类型饼图 + Full GC by cause pie chart if available
  setTimeout(() => {
    drawPauseDistributionChart(s.series);
    drawCategoryPieChart(s.by_category);
    if (s.by_cause_full && Object.keys(s.by_cause_full).length > 0) {
      drawCategoryPieChart(s.by_cause_full, "fullGcCausePieChart");
    }
  }, 80);

  // 发送到 Agent 按钮
  const sendBtn = document.getElementById("sendToAgentBtn");
  if (sendBtn) {
    sendBtn.onclick = () => sendToAgent('gc', report.id, report.filename);
  }

  // 诊断反馈 widget（飞轮采集）
  area.querySelectorAll(".report-feedback").forEach(bindFeedbackWidget);

  // 事件委托：慢事件展开 / 发送事件到 Agent / AI 折叠（替代内联 onclick，防 XSS）
  area.onclick = (ev) => {
    const sendEventBtn = ev.target.closest('[data-act="send-event"]');
    if (sendEventBtn) {
      ev.stopPropagation();
      sendToAgent(
        'gc_event',
        sendEventBtn.dataset.reportId,
        sendEventBtn.dataset.filename || '',
        sendEventBtn.dataset.eventId,
        sendEventBtn,
        sendEventBtn.dataset.rawEnc || ''
      );
      return;
    }
    const slowRow = ev.target.closest('[data-act="toggle-slow-row"]');
    if (slowRow) {
      const id = slowRow.dataset.id;
      const expandRow = area.querySelector(`tr.slow-expand[data-id="${CSS.escape(id)}"]`);
      if (expandRow) {
        const isOpen = expandRow.classList.toggle('open');
        slowRow.classList.toggle('open', isOpen);
      }
      return;
    }
    const aiHeader = ev.target.closest('[data-act="toggle-ai-collapse"]');
    if (aiHeader) {
      const section = aiHeader.closest('.ai-section');
      if (section) section.classList.toggle('collapsed');
    }
  };
}

// ---------- AI 折叠切换 ----------
window.toggleAiCollapse = (e) => {
  const section = e.currentTarget.closest('.ai-section');
  if (section) section.classList.toggle('collapsed');
};

// ---------- 发送到 Agent ----------
window.sendToAgent = (type, reportId, filename, extra, btnEl, rawTypeEnc) => {
  if (!reportId) return;

  const ctxType = type.includes('jstack') ? 'jstack' : 'gc';
  // Ensure report in state.activeReportContexts
  if (!state.activeReportContexts.some(c => c.report_id === reportId)) {
    const report = { id: reportId, filename: filename || '', file_id: '' };
    bindReportContext(ctxType, report);
  }

  let msg;
  if (type === 'gc') {
    msg = (getLang() === 'zh' ? '请分析当前 GC 报告（' : 'Please analyze the current GC report (')
      + (filename || '') + ')';
  } else if (type === 'gc_event') {
    msg = (getLang() === 'zh' ? '请分析 GC 报告 ' : 'Please analyze GC report ')
      + (filename || '') + (getLang() === 'zh' ? ' 中的事件 #' : ' event #') + extra;
    if (rawTypeEnc) {
      const rawLog = decodeURIComponent(rawTypeEnc);
      msg += '\n\n' + (getLang() === 'zh' ? '原始日志：\n```\n' : 'Raw log:\n```\n') + rawLog + '\n```';
    }
  } else if (type === 'jstack') {
    msg = (getLang() === 'zh' ? '请分析当前线程转储报告（' : 'Please analyze the current thread dump (')
      + (filename || '') + ')';
  } else if (type === 'jstack_thread') {
    msg = (getLang() === 'zh' ? '请分析线程 "' : 'Please analyze thread "')
      + extra + '"'
      + (getLang() === 'zh' ? ' 的栈帧' : ' stack trace');
  }
  if (!msg) return;

  if (btnEl) btnEl.innerHTML = ico('check');

  const chatArea = document.getElementById("chatArea");
  if (chatArea) chatArea.scrollIntoView({ behavior: 'smooth' });
  app.sendMessage(msg);
}

// ---------- 保存分析结论到报告 ----------
export async function saveToReport(type, content, btn) {
  if (!content) {
    if (btn) { btn.innerHTML = ico('triangle-alert'); setTimeout(() => { if (btn) btn.innerHTML = ico('download'); }, 2000); }
    return;
  }
  if (!state.currentSessionId) {
    if (btn) { btn.innerHTML = ico('x'); setTimeout(() => { if (btn) btn.innerHTML = ico('download'); }, 2000); }
    return;
  }

  let reports = state.activeReportContexts.filter(c => c.type === type);
  if (!reports.length) reports = state.activeReportContexts;

  if (!reports.length) {
    if (btn) {
      btn.innerHTML = getLang() === 'zh' ? ico('inbox') + ' 无打开的报告' : ico('inbox') + ' No report open';
      setTimeout(() => { if (btn) btn.textContent = t("chat.save_to_report"); }, 3000);
    }
    return;
  }

  let targetReport, targetType;
  if (reports.length === 1) {
    targetReport = reports[0];
    targetType = targetReport.type;
    if (btn) {
      const label = targetReport.filename || targetReport.report_id || '';
      btn.innerHTML = (getLang() === 'zh' ? ico('save') + ' 保存到: ' : ico('save') + ' Save to: ') + label;
    }
  } else {
    const names = reports.map((r, i) => (i + 1) + '. [' + r.type + '] ' + (r.filename || ''));
    const choice = prompt(
      (getLang() === 'zh' ? '选择要保存到的报告：\n' : 'Select report to save to:\n') + names.join('\n'),
      '1'
    );
    if (!choice) return;
    const idx = parseInt(choice) - 1;
    if (idx < 0 || idx >= reports.length) return;
    targetReport = reports[idx];
    targetType = targetReport.type;
  }

  const endpoint = targetType === 'gc'
    ? `/api/sessions/${state.currentSessionId}/gc/reports/${targetReport.report_id}/save-conclusion`
    : targetType === 'jstack'
    ? `/api/sessions/${state.currentSessionId}/jstack/reports/${targetReport.report_id}/save-conclusion`
    : `/api/heapdump-reports/${targetReport.report_id}/save-conclusion`;

  try {
    const res = await fetch(endpoint, {
      method: 'POST', credentials: 'same-origin',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ conclusion: content }),
    });
    if (res.ok) {
      if (btn) {
        const label = targetReport.filename || targetReport.report_id || '';
        btn.innerHTML = ico('check') + ' ' + label;
        setTimeout(() => { if (btn) btn.textContent = t("chat.save_to_report"); }, 3000);
      }
      // Sync local report objects + UI
      let reportObj = null;
      if (targetType === 'gc') reportObj = state.currentReport;
      else if (targetType === 'jstack') reportObj = state.currentJstackReport;
      else if (targetType === 'heapdump' && state.openHeapdumpReports) {
        const entry = state.openHeapdumpReports.find(x => x.id === targetReport.report_id);
        if (entry) {
          entry.report = entry.report || {};
          entry.report.ai_conclusion = content;
          reportObj = state.currentHeapdumpReport;
          if (reportObj && reportObj.id === targetReport.report_id) reportObj.ai_conclusion = content;
        }
      }
      if (reportObj && reportObj.id === targetReport.report_id) {
        reportObj.ai_conclusion = content;
      }
      // Update report panel AI section if visible
      let aiEl = null;
      if (targetType === 'heapdump') {
        const sec = document.getElementById('hdAiSection');
        if (sec) {
          sec.classList.remove('collapsed');
          aiEl = sec.querySelector('.hd-ai-conclusion');
          if (!aiEl) {
            const body = sec.querySelector('.hd-ai-body');
            if (body) body.innerHTML = `<div class="hd-ai-conclusion"></div>`;
            aiEl = sec.querySelector('.hd-ai-conclusion');
          }
        }
      } else {
        aiEl = document.querySelector(targetType === 'gc' ? '.ai-section .ai-conclusion' : '.ai-conclusion');
      }
      if (aiEl) aiEl.innerHTML = renderMarkdown(content);
      // Show the AI section if it was hidden
      const aiSection = targetType === 'heapdump'
        ? document.getElementById('hdAiSection')
        : document.querySelector('.ai-section');
      if (aiSection) aiSection.classList.remove('collapsed');
    } else {
      if (btn) { btn.innerHTML = ico('x'); setTimeout(() => { btn.innerHTML = ico('download'); }, 2000); }
    }
  } catch {
    if (btn) { btn.innerHTML = ico('x'); setTimeout(() => { btn.innerHTML = ico('download'); }, 2000); }
  }
}
