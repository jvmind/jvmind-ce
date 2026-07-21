/**
 * Regression test: clicking a history item (in either the history body or the
 * sidebar) must NOT move it to position 1 in the sidebar.
 *
 * Pre-fix bug: ``openHeapdumpReport`` unconditionally called ``_trackOpen`` which
 * pushed the report into ``state.openHeapdumpReports``. The sidebar renders
 * session items first then history items, so the clicked report jumped from
 * its original history position to the top of the list — confusing for users
 * who then couldn't find where the report went. GC has the equivalent code
 * path but uses ``openReport(type, full, { dontTrack: true })`` for history
 * clicks, which leaves the report in the history section with an active
 * highlight.
 *
 * Post-fix: ``openHeapdumpReport`` and ``renderHeapdumpReport`` accept
 * ``dontTrack: true``; the sidebar and history-body click handlers pass it.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { state } from '../state.js';

document.body.innerHTML = `
  <div id="heapdumpSidebar">
    <div id="heapdumpSidebarList"></div>
    <button id="heapdumpSidebarToggle"></button>
    <input id="heapdumpSidebarFile" />
    <div id="heapdumpSidebarUploadZone"></div>
  </div>
  <div id="heapdumpBodyCurrent"></div>
  <div id="heapdumpBodyHistory">
    <div id="heapdumpHistoryList"></div>
    <div id="heapdumpHistoryEmpty"></div>
    <div id="heapdumpHistoryCount"></div>
  </div>
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
  <div id="heapdumpTabCount"></div>
  <div id="heapdumpTab"></div>
`;

vi.mock('../../i18n/index.js', () => ({ t: (key) => key }));

const apiCalls = [];
const fetchQueue = [];
vi.mock('../api.js', () => ({
  api: vi.fn(async (url, opts) => {
    apiCalls.push({ url, opts });
    if (fetchQueue.length) return fetchQueue.shift();
    return {};
  }),
}));

globalThis.marked = { parse: (t) => `<p>${t}</p>` };
globalThis.DOMPurify = { sanitize: (h) => h };
window.marked = globalThis.marked;
window.DOMPurify = globalThis.DOMPurify;

const heapdump = await import('../heapdump-analysis/heapdump.js');
const { refreshHeapdumpHistory, initHeapdump } = heapdump;
initHeapdump();

function makeReport(id, filename, created_at) {
  return {
    id, filename, created_at, status: 'DONE', has_ai: false,
    session_id: 'sid_test', num_objects: 100, used_heap: 1024,
  };
}

function historyItemIds() {
  return [...document.querySelectorAll('#heapdumpHistoryList .history-item')]
    .map(it => it.dataset.id);
}

function sidebarItemIds() {
  return [...document.querySelectorAll('#heapdumpSidebarList .sidebar-item')]
    .map(it => it.dataset.id);
}

describe('clicking a history item does NOT move it to the top of the sidebar', () => {
  beforeEach(() => {
    apiCalls.length = 0;
    fetchQueue.length = 0;
    state.currentSessionId = 'sid_test';
    state.openHeapdumpReports = [];
    state.heapdumpHistoryReports = [];
    state.currentHeapdumpReport = null;
    state.currentHeapdumpReportId = null;
    document.getElementById('heapdumpHistoryList').innerHTML = '';
    document.getElementById('heapdumpSidebarList').innerHTML = '';
  });

  it('sidebar: clicking the 2nd item leaves it at index 1 (the 2nd slot)', async () => {
    const reports = [
      makeReport('hd_newest', 'newest.hprof', '2024-01-03T00:00:00Z'),
      makeReport('hd_mid',     'mid.hprof',    '2024-01-02T00:00:00Z'),
      makeReport('hd_oldest',  'oldest.hprof', '2024-01-01T00:00:00Z'),
    ];
    fetchQueue.push({ reports });
    await refreshHeapdumpHistory();

    expect(state.openHeapdumpReports).toEqual([]);
    const before = sidebarItemIds();
    expect(before).toEqual(['hd_newest', 'hd_mid', 'hd_oldest']);

    fetchQueue.push(reports[1]);
    document.querySelector('#heapdumpSidebarList .sidebar-item[data-id="hd_mid"]').click();
    await new Promise(r => setTimeout(r, 20));

    // Pre-fix: hd_mid moved to index 0 (top of session section).
    // Post-fix: hd_mid stays at index 1 (the 2nd slot in history).
    expect(sidebarItemIds()).toEqual(['hd_newest', 'hd_mid', 'hd_oldest']);
    expect(state.openHeapdumpReports).toEqual([]);
    expect(state.currentHeapdumpReportId).toBe('hd_mid');
  });

  it('history body: clicking the 2nd item leaves the sidebar order unchanged', async () => {
    const reports = [
      makeReport('hd_newest', 'newest.hprof', '2024-01-03T00:00:00Z'),
      makeReport('hd_mid',     'mid.hprof',    '2024-01-02T00:00:00Z'),
      makeReport('hd_oldest',  'oldest.hprof', '2024-01-01T00:00:00Z'),
    ];
    fetchQueue.push({ reports });
    await refreshHeapdumpHistory();

    expect(historyItemIds()).toEqual(['hd_newest', 'hd_mid', 'hd_oldest']);

    fetchQueue.push(reports[1]);
    document.querySelector('#heapdumpHistoryList .history-item[data-id="hd_mid"]').click();
    await new Promise(r => setTimeout(r, 20));

    expect(sidebarItemIds()).toEqual(['hd_newest', 'hd_mid', 'hd_oldest']);
    expect(state.openHeapdumpReports).toEqual([]);
    expect(state.currentHeapdumpReportId).toBe('hd_mid');
  });
});