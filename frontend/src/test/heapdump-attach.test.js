/**
 * Regression test: clicking the attach button on a history item (sidebar
 * item with data-session="false") must attach the report to chat context,
 * even when the item is NOT in state.openHeapdumpReports.
 *
 * Pre-fix bug (regression from commit 5e2b885): the sidebar attach handler
 * only attached if the report was in state.openHeapdumpReports. After the
 * dontTrack fix, history clicks no longer push to that list, so the
 * attach button became a no-op for history items.
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
  <div id="activeReportContext"></div>
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

function makeHistoryItem(id, filename, created_at) {
  return {
    id, filename, created_at, status: 'DONE', has_ai: false,
    session_id: 'sid_test', num_objects: 100, used_heap: 1024,
  };
}

describe('attach button on history items', () => {
  beforeEach(() => {
    apiCalls.length = 0;
    fetchQueue.length = 0;
    state.currentSessionId = 'sid_test';
    state.openHeapdumpReports = [];
    state.heapdumpHistoryReports = [];
    state.activeReportContexts = [];
    state.currentHeapdumpReport = null;
    state.currentHeapdumpReportId = null;
    document.getElementById('heapdumpHistoryList').innerHTML = '';
    document.getElementById('heapdumpSidebarList').innerHTML = '';
    document.getElementById('activeReportContext').innerHTML = '';
  });

  it('attaching a history item (data-session="false") populates activeReportContexts', async () => {
    const reports = [
      makeHistoryItem('hd_a', 'a.hprof', '2024-01-02T00:00:00Z'),
      makeHistoryItem('hd_b', 'b.hprof', '2024-01-01T00:00:00Z'),
    ];
    fetchQueue.push({ reports });
    await refreshHeapdumpHistory();

    expect(state.activeReportContexts.length).toBe(0);
    expect(state.openHeapdumpReports.length).toBe(0);

    const attachBtn = document.querySelector(
      '#heapdumpSidebarList .sidebar-item[data-id="hd_a"] .si-attach-btn'
    );
    expect(attachBtn).not.toBeNull();
    attachBtn.click();

    await new Promise(r => setTimeout(r, 10));

    const attached = state.activeReportContexts.find(
      c => c.type === 'heapdump' && c.report_id === 'hd_a'
    );
    expect(attached).toBeDefined();
    expect(attached.filename).toBe('a.hprof');
  });
});