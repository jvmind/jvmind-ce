import { describe, it, expect, beforeEach } from 'vitest';

// jstack-analysis.js 在 import 时访问 #jstackUploadZone / #jstackFile 并绑定事件，
// 因此必须在 import 前准备这些元素。
document.body.innerHTML = `
  <div id="jstackUploadZone"></div>
  <input id="jstackFile" type="file" />
  <div id="jstackReportArea"></div>
  <table><tbody id="threadTableBody"></tbody></table>
  <div id="threadPagination"></div>
  <input id="threadSearch" />
  <button id="sortDepthBtn"></button>
  <span id="sortDepthIcon"></span>
`;

// 提供最小 echarts 全局，避免图表渲染路径抛错
globalThis.echarts = { init: () => ({ setOption: () => {}, resize: () => {} }) };

const mod = await import('./jstack-analysis.js');

function baseReport(diagnosis) {
  return {
    id: 'r1', filename: 'dump.txt', size: 1024, created_at: Date.now(),
    ai_conclusion: '',
    stats: {
      total_threads: 3, daemon_count: 1, deadlock_count: 0, deadlocks: [],
      blocked_percent: 0, by_state: { RUNNABLE: 3 }, avg_stack_depth: 2,
      max_stack_depth: 3, max_stack_thread: 'x', threads: [], lock_hotspots: [],
      diagnosis,
    },
  };
}

describe('jstack diagnosis rendering', () => {
  beforeEach(() => {
    document.body.classList.remove('report-only');
  });

  it('renders findings when diagnosis has issues', () => {
    mod.renderJstackReport(baseReport({
      overall: 'critical',
      findings: [{ rule: 'deadlock', severity: 'high', title_zh: '检测到死锁', title_en: 'Deadlock detected', detail_zh: '细节', detail_en: 'detail' }],
      recommendations_zh: ['建议1'], recommendations_en: ['rec1'],
    }));
    const area = document.getElementById('jstackReportArea');
    const diag = area.querySelector('#jstackDiagnosis');
    expect(diag).not.toBeNull();
    // 外观对齐 GC：复用 .diagnosis-section / .diag-finding / .diag-severity-tag 类
    expect(diag.classList.contains('diagnosis-section')).toBe(true);
    expect(diag.querySelector('.diag-finding.diag-severity-high')).not.toBeNull();
    expect(diag.querySelector('.diag-severity-tag')).not.toBeNull();
    expect(diag.querySelector('.diag-finding-title').textContent).toContain('Deadlock detected');
    expect(diag.querySelector('.diag-recommendations')).not.toBeNull();
  });

  it('positions diagnosis before the stat-grid (aligned with GC layout)', () => {
    mod.renderJstackReport(baseReport({
      overall: 'warning',
      findings: [{ rule: 'lock_contention', severity: 'medium', title_zh: '锁竞争', title_en: 'Lock contention', detail_zh: 'd', detail_en: 'd' }],
      recommendations_zh: [], recommendations_en: [],
    }));
    const area = document.getElementById('jstackReportArea');
    const diag = area.querySelector('#jstackDiagnosis');
    const grid = area.querySelector('.stat-grid');
    expect(diag).not.toBeNull();
    expect(grid).not.toBeNull();
    // DOCUMENT_POSITION_FOLLOWING(4): grid 在 diag 之后
    expect(diag.compareDocumentPosition(grid) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('does not render diagnosis block when there are no findings', () => {
    mod.renderJstackReport(baseReport({ overall: 'health', findings: [], recommendations_zh: [], recommendations_en: [] }));
    expect(document.getElementById('jstackDiagnosis')).toBeNull();
  });

  it('does not crash when diagnosis is missing (legacy report)', () => {
    expect(() => mod.renderJstackReport(baseReport(undefined))).not.toThrow();
    expect(document.getElementById('jstackDiagnosis')).toBeNull();
  });
});
