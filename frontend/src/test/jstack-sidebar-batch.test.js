/**
 * Regression test: JStack sidebar batch-select Delete-button state.
 *
 * Pre-fix bug: clicking "Select All" toggled the checkbox glyphs (☐ ↔ ☑)
 * but did NOT update the Delete button's `disabled` state or the
 * "(N)" count in its label.
 *
 * Post-fix: select-all calls the shared `_updateJstackBatchDeleteBtn()`
 * helper that recomputes `selected = count of ☑` and toggles
 * `deleteBtn.disabled` + `deleteBtn.textContent` accordingly.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ico } from '../icons.js';
import { state } from '../state.js';
import { app } from '../app.js';
import { bindReportContext, addActiveReportContext, removeActiveReportContextByReport, deleteReportEntries } from '../gc-analysis/context.js';

document.body.innerHTML = `
  <div id="jstackUploadZone">
    <input id="jstackFile" />
  </div>
  <div id="jstackLoading" style="display:none"></div>
  <div id="jstackError" style="display:none"></div>
  <div id="jstackReportArea"></div>
  <div id="jstackBodyCurrent"></div>
  <div id="jstackBodyHistory"></div>
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
  <div id="jstackSendToAgentBtn"></div>
  <div id="threadTableBody"></div>
  <div id="threadPagination"></div>
  <input id="threadSearch" />
  <button id="sortDepthBtn"></button>
  <span id="sortDepthIcon"></span>
  <div id="jstackTabCount"></div>
  <div id="jstackHistoryCount"></div>
  <div id="jstackHistoryList"></div>
  <div id="jstackHistoryEmpty" style="display:none"></div>
  <div class="mode-body" data-mode="jstack">
    <div class="gc-tabs">
      <div class="tab active" data-subtab="current"></div>
      <div class="tab" data-subtab="history"></div>
    </div>
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
app.appendSystemHint = vi.fn();
app.updateBadge = vi.fn();
app.refreshAllReportHistory = vi.fn(async () => {});
app.updateQuotaUI = vi.fn(async () => {});
app.renderJstackReport = vi.fn();
app.refreshJstackHistory = vi.fn(async () => {});
app.deleteReportEntries = deleteReportEntries;

const jsModule = await import('../jstack-analysis.js');
const { refreshJstackHistory, renderJstackSidebar } = jsModule;
// Replace the app.refreshJstackHistory with the real impl now that the
// module is loaded (it was overridden with a vi.fn() stub earlier).
app.refreshJstackHistory = refreshJstackHistory;

function makeSidebarItem(id, filename, isSession = true) {
  const item = document.createElement('div');
  item.className = 'sidebar-item batch-active';
  item.dataset.id = id;
  item.dataset.session = String(isSession);
  item.innerHTML = `
    <div class="si-row1">
      <span class="si-checkbox">${ico('square')}</span>
      <span class="si-filename">${filename}</span>
    </div>
    <div class="si-row2">
      <button class="si-attach-btn" data-action="attach">${ico('paperclip')}</button>
      <button class="si-close-btn" data-action="close">${ico('x')}</button>
    </div>
  `;
  return item;
}

function renderSidebar(items) {
  const list = document.getElementById('jstackSidebarList');
  list.innerHTML = '';
  items.forEach(it => list.appendChild(it));
}

describe('JStack sidebar batch-select Delete-button state', () => {
  beforeEach(() => {
    apiCalls.length = 0;
    fetchQueue.length = 0;
    state.currentSessionId = 'sid_test';
    state.openJstackReports = [];
    state.jstackHistoryReports = [];
    state.activeReportContexts = [];
    state.currentJstackReport = null;
    state.currentJstackReportId = null;
    document.getElementById('jstackSidebarList').innerHTML = '';
    document.getElementById('activeReportContext').innerHTML = '';
  });

  it('select-all enables the Delete button and shows the count', () => {
    renderSidebar([
      makeSidebarItem('js_a', 'thread1.txt'),
      makeSidebarItem('js_b', 'thread2.txt'),
    ]);
    document.getElementById('jstackSidebarSelectBtn').click();

    const deleteBtn = document.querySelector('#jstackSidebarBatchBar .sidebar-delete-btn');
    document.querySelector('#jstackSidebarBatchBar .sidebar-selectall-btn').click();

    expect(deleteBtn.disabled).toBe(false);
    expect(deleteBtn.textContent).toContain('(2)');
  });

  it('select-all toggle disables the Delete button when nothing selected', () => {
    renderSidebar([
      makeSidebarItem('js_a', 'thread1.txt'),
    ]);
    document.getElementById('jstackSidebarSelectBtn').click();

    const selectAllBtn = document.querySelector('#jstackSidebarBatchBar .sidebar-selectall-btn');
    const deleteBtn = document.querySelector('#jstackSidebarBatchBar .sidebar-delete-btn');

    selectAllBtn.click();
    expect(deleteBtn.disabled).toBe(false);
    expect(deleteBtn.textContent).toContain('(1)');

    selectAllBtn.click();
    expect(deleteBtn.disabled).toBe(true);
    expect(deleteBtn.textContent).not.toContain('(1)');
  });

  it('clicking Select does not grey out the active sidebar item', () => {
    const active = makeSidebarItem('js_active', 'active.txt', true);
    active.classList.add('active');
    renderSidebar([
      active,
      makeSidebarItem('js_b', 'b.txt', false),
    ]);
    document.getElementById('jstackSidebarSelectBtn').click();

    expect(active.classList.contains('batch-excluded')).toBe(false);
    // The active item's checkbox must be clickable
    const cb = active.querySelector('.si-checkbox');
    cb.click();
    expect(cb.innerHTML).toBe(ico('check-square'));
    const deleteBtn = document.querySelector('#jstackSidebarBatchBar .sidebar-delete-btn');
    expect(deleteBtn.disabled).toBe(false);
    expect(deleteBtn.textContent).toContain('(1)');
  });

  it('bulk delete removes sidebar items after refresh (Fix 8B)', async () => {
    state.jstackHistoryReports = [
      { id: 'js_a', filename: 'a.txt', created_at: '2024-01-02T00:00:00Z', status: 'DONE', has_ai: false, session_id: 'sid_test' },
      { id: 'js_b', filename: 'b.txt', created_at: '2024-01-01T00:00:00Z', status: 'DONE', has_ai: false, session_id: 'sid_test' },
    ];
    // Render via the real sidebar renderer
    renderJstackSidebar();
    expect(document.querySelectorAll('#jstackSidebarList .sidebar-item').length).toBe(2);

    document.getElementById('jstackSidebarSelectBtn').click();
    document.querySelectorAll('#jstackSidebarList .sidebar-item .si-checkbox').forEach(cb => cb.click());

    // Queue responses: 2 DELETEs, 1 GET (returns empty history)
    fetchQueue.push({});
    fetchQueue.push({});
    fetchQueue.push({ reports: [] });

    document.querySelector('#jstackSidebarBatchBar .sidebar-delete-btn').click();
    await new Promise(r => setTimeout(r, 100));

    // Sidebar should be empty
    expect(document.querySelectorAll('#jstackSidebarList .sidebar-item').length).toBe(0);
    // Batch bar should be hidden (cancel called)
    expect(document.getElementById('jstackSidebarBatchBar').style.display).toBe('none');
  });

  it('entering batch mode resets the Delete button to disabled/(0) (Fix 9B)', () => {
    const deleteBtn = document.querySelector('#jstackSidebarBatchBar .sidebar-delete-btn');
    deleteBtn.disabled = false;
    deleteBtn.innerHTML = `${ico('trash-2')} Bulk Delete (5)`;
    renderSidebar([
      makeSidebarItem('js_a', 'a.txt'),
      makeSidebarItem('js_b', 'b.txt'),
    ]);
    document.getElementById('jstackSidebarSelectBtn').click();

    expect(deleteBtn.disabled).toBe(true);
    expect(deleteBtn.textContent).not.toContain('(');
  });
});
