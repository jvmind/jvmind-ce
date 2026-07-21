/**
 * Regression test: clicking the attach button on a JStack history item in
 * the sidebar must attach the report to chat context, even when the item
 * is NOT in state.openJstackReports.
 *
 * Pre-fix bug: the sidebar attach handler only attached if the report was
 * in state.openJstackReports. After the JStack sidebar-history-click was
 * changed to openReport(..., { dontTrack: true }), history items no
 * longer push to that list, so the attach button became a no-op for
 * history items. Mirrors the heapdump regression fixed in 901fc9d and
 * the GC regression fixed in 07c5a6c.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ico } from '../icons.js';
import { state } from '../state.js';

// Provide all DOM nodes jstack-analysis.js touches at import time. jstack
// is a single-file module (not split like gc-analysis), so its module
// import side-effects cover the full DOM-binding surface.
document.body.innerHTML = `
  <div id="jstackUploadZone">
    <input id="jstackFile" />
  </div>
  <div id="jstackLoading" style="display:none"></div>
  <div id="jstackError" style="display:none"></div>
  <div id="jstackReportArea"></div>
  <div id="jstackBodyCurrent"></div>
  <div id="jstackBodyHistory"></div>
  <div id="jstackSidebar" style="display:none"></div>
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
  <div id="jstackSendToAgentBtn"></div>
  <div id="threadTableBody"></div>
  <div id="threadPagination"></div>
  <input id="threadSearch" />
  <button id="sortDepthBtn"></button>
  <span id="sortDepthIcon"></span>
  <div id="jstackTabCount"></div>
  <div id="activeReportContext"></div>
  <div id="jstackReportHeader"></div>
  <div id="jstackReportFilename"></div>
  <div id="jstackReportAiBadge"></div>
  <div id="jstackReportAttachBtn"></div>
  <div id="jstackHistoryList"></div>
  <div id="jstackHistoryEmpty"></div>
  <div id="jstackHistoryCount"></div>
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

window.confirm = vi.fn(() => false);
window.alert = vi.fn();

// Provide stubs for the app.* methods jstack-analysis.js calls into.
// The real app object is assembled in main.js. The bindReportContext
// stub forwards to the real implementation so state.activeReportContexts
// actually grows as the test expects.
import { app } from '../app.js';
import { bindReportContext, addActiveReportContext, removeActiveReportContextByReport } from '../gc-analysis/context.js';
app.bindReportContext = (type, report) => bindReportContext(type, report);
app.removeActiveReportContextByReport = (rid) => removeActiveReportContextByReport(rid);
app.activateReportTab = vi.fn();
app.openReport = vi.fn();
app.appendSystemHint = vi.fn();
app.updateBadge = vi.fn();
app.refreshAllReportHistory = vi.fn(async () => {});
app.updateQuotaUI = vi.fn(async () => {});
app.renderJstackReport = vi.fn();

const jsModule = await import('../jstack-analysis.js');
const _js = jsModule;

function makeHistoryItem(id, filename, created_at) {
  return {
    id, filename, created_at, status: 'DONE', has_ai: false,
    session_id: 'sid_test', total_threads: 100, blocked_count: 0, deadlock_count: 0,
  };
}

describe('attach button on JStack history items (sidebar)', () => {
  beforeEach(() => {
    apiCalls.length = 0;
    fetchQueue.length = 0;
    state.currentSessionId = 'sid_test';
    state.currentUser = null;
    state.openJstackReports = [];
    state.jstackHistoryReports = [];
    state.allReports = [];
    state.activeReportContexts = [];
    state.currentJstackReport = null;
    state.currentJstackReportId = null;
    document.getElementById('jstackSidebarList').innerHTML = '';
    document.getElementById('activeReportContext').innerHTML = '';
  });

  it('attaches a history item (data-session="false") to chat context', () => {
    const historyItem = makeHistoryItem('js_a', 'app-threads.txt', '2024-05-10T08:30:00Z');
    state.jstackHistoryReports = [historyItem];

    const list = document.getElementById('jstackSidebarList');
    list.innerHTML = `
      <div class="sidebar-item" data-id="js_a" data-session="false">
        <div class="si-row1"><span class="si-filename">${historyItem.filename}</span></div>
        <div class="si-row2">
          <button class="si-attach-btn" data-action="attach">${ico('paperclip')}</button>
          <button class="si-close-btn" data-action="close">${ico('x')}</button>
        </div>
      </div>
    `;

    expect(state.activeReportContexts.length).toBe(0);
    expect(state.openJstackReports.length).toBe(0);

    const attachBtn = list.querySelector('.si-attach-btn[data-action="attach"]');
    attachBtn.click();

    const attached = state.activeReportContexts.find(
      c => c.type === 'jstack' && c.report_id === 'js_a'
    );
    expect(attached).toBeDefined();
    expect(attached.filename).toBe('app-threads.txt');
  });

  it('attaches a session item (data-session="true") too (existing path)', () => {
    const sessionItem = {
      id: 'js_sess',
      filename: 'session-threads.txt',
      report: { id: 'js_sess', filename: 'session-threads.txt', file_id: 'file_xyz' },
    };
    state.openJstackReports = [sessionItem];

    const list = document.getElementById('jstackSidebarList');
    list.innerHTML = `
      <div class="sidebar-item" data-id="js_sess" data-session="true">
        <div class="si-row1"><span class="si-filename">${sessionItem.filename}</span></div>
        <div class="si-row2">
          <button class="si-attach-btn" data-action="attach">${ico('paperclip')}</button>
          <button class="si-close-btn" data-action="close">${ico('x')}</button>
        </div>
      </div>
    `;

    document.querySelector('.si-attach-btn[data-action="attach"]').click();

    const attached = state.activeReportContexts.find(
      c => c.type === 'jstack' && c.report_id === 'js_sess'
    );
    expect(attached).toBeDefined();
    expect(attached.file_id).toBe('file_xyz');
  });
});