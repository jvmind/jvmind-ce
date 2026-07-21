/**
 * Regression test: clicking the attach button on a GC history item in the
 * sidebar must attach the report to chat context, even when the item is
 * NOT in state.openGcReports.
 *
 * Pre-fix bug (regression from commit e8ae2b1): the sidebar attach handler
 * only attached if the report was in state.openGcReports. After GC's
 * dontTrack fix for the sidebar-history-click bug, history clicks no
 * longer push to that list, so the attach button became a no-op for
 * history items. Mirrors the heapdump regression fixed in 901fc9d.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ico } from '../icons.js';
import { state } from '../state.js';

// Provide all DOM nodes gc-analysis/bindings.js touches at import time.
document.body.innerHTML = `
  <div id="analysisFab"></div>
  <div id="gcPanel"></div>
  <button id="gcClose"></button>
  <button id="gcSidebarToggle"></button>
  <input id="gcFile" />
  <div id="uploadZone"></div>
  <div id="gcBodyCurrent"></div>
  <div id="gcBodyHistory"></div>
  <button id="gcSidebarSelectBtn"></button>
  <div id="gcSidebarBatchBar">
    <button class="sidebar-cancel-btn"></button>
    <button class="sidebar-selectall-btn"></button>
    <button class="sidebar-delete-btn"></button>
  </div>
  <div id="gcSidebarList"></div>
  <div id="gcReportHeader"></div>
  <div id="gcReportFilename"></div>
  <div id="gcReportAiBadge"></div>
  <div id="gcReportAttachBtn"></div>
  <div id="gcLoading"></div>
  <div id="gcError"></div>
  <div id="gcReportArea"></div>
  <input type="file" id="gcSidebarFile" />
  <div id="gcSidebarUploadZone"></div>
  <button id="reportsTabCount"></button>
  <div id="activeReportContext"></div>
  <button class="mode-tab" data-mode="gc"></button>
  <button class="mode-tab" data-mode="reports"></button>
  <button class="mode-tab" data-mode="heapdump"></button>
  <button class="mode-tab" data-mode="jstack"></button>
  <div class="mode-body" data-mode="gc"></div>
  <div class="mode-body" data-mode="reports"></div>
  <div class="mode-body" data-mode="heapdump"></div>
  <div class="mode-body" data-mode="jstack"></div>
  <div class="gc-tabs">
    <button class="tab" data-subtab="current"></button>
    <button class="tab" data-subtab="history"></button>
  </div>
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

const gcModule = await import('../gc-analysis/index.js');
// Touch the import so bindings.js is evaluated (it binds DOM at module
// import time). Just importing any gc-analysis module triggers the
// chain because index.js re-exports bindings.
const _gc = gcModule;

function makeHistoryItem(id, filename, created_at) {
  return {
    id, filename, created_at, status: 'DONE', has_ai: false,
    session_id: 'sid_test',
  };
}

describe('attach button on GC history items (sidebar)', () => {
  beforeEach(() => {
    apiCalls.length = 0;
    fetchQueue.length = 0;
    state.currentSessionId = 'sid_test';
    state.currentUser = null;
    state.openGcReports = [];
    state.gcHistoryReports = [];
    state.allReports = [];
    state.activeReportContexts = [];
    state.currentReport = null;
    state.currentReportId = null;
    state.sessionTab = 'personal';
    state.currentOrg = null;
    document.getElementById('gcSidebarList').innerHTML = '';
    document.getElementById('activeReportContext').innerHTML = '';
  });

  it('attaches a history item (data-session="false") to chat context', () => {
    const historyItem = makeHistoryItem('gc_a', 'app-gc.log', '2024-05-10T08:30:00Z');
    state.gcHistoryReports = [historyItem];

    // Manually inject a sidebar history item into the DOM (the render
    // helper is exported but its full input requirements are heavy; for
    // this regression we just need a row matching the sidebar selector
    // shape and a click that bubbles to the sidebar list).
    const list = document.getElementById('gcSidebarList');
    list.innerHTML = `
      <div class="sidebar-item" data-id="gc_a" data-session="false">
        <div class="si-row1"><span class="si-filename">${historyItem.filename}</span></div>
        <div class="si-row2">
          <button class="si-attach-btn" data-action="attach" title="attach">${ico('paperclip')}</button>
          <button class="si-close-btn" data-action="close">${ico('x')}</button>
        </div>
      </div>
    `;

    expect(state.activeReportContexts.length).toBe(0);
    expect(state.openGcReports.length).toBe(0);

    const attachBtn = list.querySelector('.si-attach-btn[data-action="attach"]');
    attachBtn.click();

    const attached = state.activeReportContexts.find(
      c => c.type === 'gc' && c.report_id === 'gc_a'
    );
    expect(attached).toBeDefined();
    expect(attached.filename).toBe('app-gc.log');
  });

  it('attaches a session item (data-session="true") too (existing path)', () => {
    const sessionItem = {
      id: 'gc_sess',
      filename: 'session-gc.log',
      report: { id: 'gc_sess', filename: 'session-gc.log', file_id: 'file_xyz' },
    };
    state.openGcReports = [sessionItem];

    const list = document.getElementById('gcSidebarList');
    list.innerHTML = `
      <div class="sidebar-item" data-id="gc_sess" data-session="true">
        <div class="si-row1"><span class="si-filename">${sessionItem.filename}</span></div>
        <div class="si-row2">
          <button class="si-attach-btn" data-action="attach">${ico('paperclip')}</button>
          <button class="si-close-btn" data-action="close">${ico('x')}</button>
        </div>
      </div>
    `;

    document.querySelector('.si-attach-btn[data-action="attach"]').click();

    const attached = state.activeReportContexts.find(
      c => c.type === 'gc' && c.report_id === 'gc_sess'
    );
    expect(attached).toBeDefined();
    expect(attached.file_id).toBe('file_xyz');
  });
});