/**
 * Regression test: refreshJstackHistory must render the JStack history-list
 * DOM (#jstackHistoryList) and update the subtab badge (#jstackHistoryCount),
 * matching GC and Heapdump behaviour.
 *
 * Pre-fix bug: refreshJstackHistory only updated the sidebar and the
 * mode-tab badge; the dedicated history-list container
 * (#jstackHistoryList) was always empty when the user switched to the
 * "history" subtab in the JStack panel.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { state } from '../state.js';
import { app } from '../app.js';
import { bindReportContext, removeActiveReportContextByReport } from '../gc-analysis/context.js';

document.body.innerHTML = `
  <div class="mode-body" data-mode="jstack" style="display:none">
    <div id="jstackSidebar" style="display:none">
      <div id="jstackSidebarList"></div>
      <button id="jstackSidebarToggle"></button>
      <input id="jstackSidebarFile" />
      <div id="jstackSidebarUploadZone"></div>
      <button id="jstackSidebarSelectBtn"></button>
      <div id="jstackSidebarBatchBar">
        <button class="sidebar-cancel-btn"></button>
        <button class="sidebar-selectall-btn"></button>
        <button class="sidebar-delete-btn"></button>
      </div>
    </div>
    <div class="gc-tabs">
      <div class="tab active" data-subtab="current"></div>
      <div class="tab" data-subtab="history"><span id="jstackHistoryCount"></span></div>
    </div>
    <div id="jstackBodyCurrent">
      <div id="jstackUploadZone">
        <input id="jstackFile" />
      </div>
      <div id="jstackReportArea"></div>
      <div id="jstackLoading" style="display:none"></div>
      <div id="jstackError" style="display:none"></div>
      <div id="jstackSendToAgentBtn"></div>
      <div id="jstackReportHeader"></div>
      <div id="jstackReportFilename"></div>
      <div id="jstackReportAiBadge"></div>
      <div id="jstackReportAttachBtn"></div>
      <div id="threadTableBody"></div>
      <div id="threadPagination"></div>
      <input id="threadSearch" />
      <button id="sortDepthBtn"></button>
      <span id="sortDepthIcon"></span>
    </div>
    <div id="jstackBodyHistory" style="display:none">
      <div id="jstackHistoryList"></div>
      <div id="jstackHistoryEmpty" style="display:none"></div>
    </div>
    <div id="jstackTabCount"></div>
  </div>
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

window.confirm = vi.fn(() => true);
window.alert = vi.fn();

app.bindReportContext = (type, report) => bindReportContext(type, report);
app.removeActiveReportContextByReport = (rid) => removeActiveReportContextByReport(rid);
app.activateReportTab = vi.fn();
app.openReport = vi.fn();
app.closeReportTab = vi.fn();
app.appendSystemHint = vi.fn();
app.updateBadge = vi.fn();
app.refreshAllReportHistory = vi.fn(async () => {});
app.updateQuotaUI = vi.fn(async () => {});
app.renderJstackReport = vi.fn();

const jsModule = await import('../jstack-analysis.js');
const { refreshJstackHistory } = jsModule;

function makeReport(id, filename, created_at, stats = {}) {
  return {
    id, filename, created_at, status: 'DONE', has_ai: false,
    session_id: 'sid_test', ...stats,
  };
}

describe('refreshJstackHistory renders the history list (Fix 4)', () => {
  beforeEach(() => {
    apiCalls.length = 0;
    fetchQueue.length = 0;
    state.currentSessionId = 'sid_test';
    state.jstackHistoryReports = [];
    state.openJstackReports = [];
    state.activeReportContexts = [];
    state.currentJstackReport = null;
    state.currentJstackReportId = null;
    document.getElementById('jstackHistoryList').innerHTML = '';
    document.getElementById('jstackHistoryEmpty').style.display = 'none';
    document.getElementById('jstackHistoryCount').textContent = '';
    document.getElementById('jstackTabCount').textContent = '';
    app.openReport.mockClear();
  });

  it('populates #jstackHistoryList with one item per report', async () => {
    fetchQueue.push({ reports: [
      makeReport('js_a', 'a.txt', '2024-01-02T00:00:00Z'),
      makeReport('js_b', 'b.txt', '2024-01-01T00:00:00Z'),
    ] });
    await refreshJstackHistory();

    const items = document.querySelectorAll('#jstackHistoryList .history-item');
    expect(items.length).toBe(2);
    expect(items[0].dataset.id).toBe('js_a');
    expect(items[1].dataset.id).toBe('js_b');
  });

  it('updates #jstackHistoryCount subtab badge with the report count', async () => {
    fetchQueue.push({ reports: [
      makeReport('js_a', 'a.txt', '2024-01-02T00:00:00Z'),
      makeReport('js_b', 'b.txt', '2024-01-01T00:00:00Z'),
      makeReport('js_c', 'c.txt', '2023-12-31T00:00:00Z'),
    ] });
    await refreshJstackHistory();
    expect(document.getElementById('jstackHistoryCount').textContent).toBe('(3)');
  });

  it('clears #jstackHistoryCount and shows the empty placeholder when no reports', async () => {
    fetchQueue.push({ reports: [] });
    await refreshJstackHistory();
    expect(document.getElementById('jstackHistoryCount').textContent).toBe('');
    expect(document.getElementById('jstackHistoryEmpty').style.display).toBe('');
    expect(document.querySelectorAll('#jstackHistoryList .history-item').length).toBe(0);
  });

  it('clicking a history row opens the report via app.openReport (dontTrack: true)', async () => {
    const full = { id: 'js_a', filename: 'a.txt', stats: {} };
    fetchQueue.push({ reports: [makeReport('js_a', 'a.txt', '2024-01-02T00:00:00Z')] });
    fetchQueue.push(full);
    await refreshJstackHistory();

    const item = document.querySelector('#jstackHistoryList .history-item[data-id="js_a"]');
    expect(item).not.toBeNull();
    expect(typeof item.onclick).toBe('function');
    item.click();
    await new Promise(r => setTimeout(r, 30));

    expect(app.openReport).toHaveBeenCalledWith('jstack', full, { dontTrack: true });
  });

  it('bulk toolbar is rendered with select-all + bulk-delete', async () => {
    fetchQueue.push({ reports: [makeReport('js_a', 'a.txt', '2024-01-02T00:00:00Z')] });
    await refreshJstackHistory();
    expect(document.querySelector('#jstackHistoryList .report-bulk-bar')).not.toBeNull();
    expect(document.querySelector('#jstackHistoryList .report-select-all')).not.toBeNull();
    expect(document.querySelector('#jstackHistoryList .bulk-delete')).not.toBeNull();
  });
});
