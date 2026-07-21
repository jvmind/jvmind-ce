/**
 * Regression test for the heapdump sidebar meta-loss bug.
 *
 * Pre-fix: renderHeapdumpSidebar() built session items (from
 * state.openHeapdumpReports) without the `meta` field. Only history items
 * (state.heapdumpHistoryReports) had `meta: fmtDate(h.created_at)`. When
 * the user clicked a historical report, openHeapdumpReport → _trackOpen
 * moved it to openHeapdumpReports, and the next renderHeapdumpSidebar
 * call lost the time.
 *
 * Post-fix: session items also carry `meta: fmtDate(report.created_at)`,
 * so the date persists across the history → open transition.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { state } from '../state.js';

// heapdump.js touches many DOM nodes at module-import time via initHeapdump
// (which references bindUploadZone, bindHeapdumpSubtabs, etc.). Provide every
// node those helpers look up. We only need renderHeapdumpSidebar in this
// suite, but importing the module triggers initHeapdump unless we stub it.
document.body.innerHTML = `
  <div id="heapdumpSidebar">
    <div id="heapdumpSidebarList"></div>
    <button id="heapdumpSidebarToggle"></button>
    <input id="heapdumpSidebarFile" />
    <div id="heapdumpSidebarUploadZone"></div>
  </div>
  <div id="heapdumpBodyCurrent"></div>
  <div id="heapdumpBodyHistory"></div>
  <button id="heapdumpSubtabCurrent"></button>
  <button id="heapdumpSubtabHistory"></button>
  <div id="heapdumpUploadZone">
    <input id="heapdumpFile" />
  </div>
  <div id="heapdumpReportArea"></div>
  <div id="heapdumpEmptyState"></div>
  <div id="heapdumpLoading" style="display:none"></div>
  <div id="heapdumpError"></div>
  <div id="heapdumpBanner"></div>
  <div id="heapdumpAiSection"></div>
  <div id="heapdumpAiConclusion"></div>
  <div id="heapdumpAiStatus"></div>
  <button id="hdAttachBtn"></button>
  <button id="hdAttachOk"></button>
  <button id="hdAskAi"></button>
  <button id="hdSaveToReport"></button>
  <button id="hdDeleteBtn"></button>
  <button id="hdRefreshBtn"></button>
  <select id="hdSaveToReportTarget"></select>
  <div id="hdPoolsSection"></div>
  <div id="hdPoolLeakSection"></div>
  <div id="hdSqlSection"></div>
  <div id="hdThreadsSection"></div>
  <div id="hdClassesSection"></div>
  <div id="hdHealthSection"></div>
  <div id="hdOverviewSection"></div>
  <div id="hdTriageSection"></div>
  <div id="hdHistogramSection"></div>
  <div id="hdDominatorSection"></div>
  <div id="hdEnvSection"></div>
  <div id="hdErrorSection"></div>
`;

// Stub the i18n module so `t(key)` returns the key (deterministic in tests).
vi.mock('../../i18n/index.js', () => ({
  t: (key) => key,
}));

// Stub api() so openHeapdumpReport's HTTP call doesn't fire.
vi.mock('../api.js', () => ({
  api: vi.fn(async () => ({ reports: [] })),
}));

// heapdump.js imports renderMarkdown which imports marked + DOMPurify globals.
globalThis.marked = { parse: (t) => `<p>${t}</p>` };
globalThis.DOMPurify = { sanitize: (h) => h };
window.marked = globalThis.marked;
window.DOMPurify = globalThis.DOMPurify;

const heapdump = await import('../heapdump-analysis/heapdump.js');
const { renderHeapdumpSidebar } = heapdump;

function getSidebarList() {
  return document.getElementById('heapdumpSidebarList');
}

function getRenderedMeta(itemId) {
  const el = getSidebarList().querySelector(`.sidebar-item[data-id="${itemId}"] .si-meta`);
  return el ? el.textContent : null;
}

describe('renderHeapdumpSidebar meta field', () => {
  beforeEach(() => {
    state.currentHeapdumpReportId = null;
    state.currentHeapdumpReport = null;
    state.openHeapdumpReports = [];
    state.heapdumpHistoryReports = [];
    state.activeReportContexts = [];
    state.currentSessionId = 'sid_test';
    getSidebarList().innerHTML = '';
  });

  it('renders meta (formatted date) for historical reports', () => {
    state.heapdumpHistoryReports = [{
      id: 'hd_1',
      filename: 'app.hprof',
      created_at: '2024-05-10T08:30:00Z',
      status: 'DONE',
      has_ai: false,
    }];
    renderHeapdumpSidebar();
    expect(getRenderedMeta('hd_1')).not.toBeNull();
    expect(getRenderedMeta('hd_1')).not.toBe('');
    // Asia/Shanghai is UTC+8 → 2024-05-10 16:30
    expect(getRenderedMeta('hd_1')).toMatch(/2024-05-10\s+16:30/);
  });

  it('preserves meta after a historical report transitions to open', () => {
    // Stage 1: report sits in history
    state.heapdumpHistoryReports = [{
      id: 'hd_2',
      filename: 'app.hprof',
      created_at: '2024-05-10T08:30:00Z',
      status: 'DONE',
      has_ai: false,
    }];
    renderHeapdumpSidebar();
    const historyMeta = getRenderedMeta('hd_2');
    expect(historyMeta).not.toBeNull();

    // Stage 2: user clicks → report moves to openHeapdumpReports (mimicking
    // the _trackOpen call inside openHeapdumpReport). The sidebar re-renders
    // and the report must STILL carry the formatted date.
    state.openHeapdumpReports = [{
      id: 'hd_2',
      filename: 'app.hprof',
      report: {
        id: 'hd_2',
        filename: 'app.hprof',
        created_at: '2024-05-10T08:30:00Z',
        stats: {},
        ai_conclusion: '',
      },
    }];
    state.heapdumpHistoryReports = [];
    renderHeapdumpSidebar();
    const sessionMeta = getRenderedMeta('hd_2');
    expect(sessionMeta).not.toBeNull();
    expect(sessionMeta).toBe(historyMeta);
  });
});