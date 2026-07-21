/**
 * Regression test: GC sidebar batch-select Delete-button state.
 *
 * Pre-fix bug: clicking "Select All" toggled the checkbox glyphs (☐ ↔ ☑)
 * but did NOT update the Delete button's `disabled` state or the
 * "(N)" count in its label. Users who used Select All would see the
 * Delete button stay greyed out even though every checkbox was checked.
 *
 * Post-fix: select-all calls the shared `_updateGcBatchDeleteBtn()` helper
 * that recomputes `selected = count of ☑` and toggles
 * `deleteBtn.disabled` + `deleteBtn.textContent` accordingly.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ico } from '../icons.js';
import { state } from '../state.js';

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

window.confirm = vi.fn(() => true);
window.alert = vi.fn();

const gcModule = await import('../gc-analysis/index.js');
const _gc = gcModule;

function makeHistoryItem(id, filename, created_at) {
  return {
    id, filename, created_at, status: 'DONE', has_ai: false,
    session_id: 'sid_test',
  };
}

function makeSidebarItem(id, filename, isSession = true) {
  const item = document.createElement('div');
  item.className = 'sidebar-item batch-active' + (isSession ? '' : '');
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
  const list = document.getElementById('gcSidebarList');
  list.innerHTML = '';
  items.forEach(it => list.appendChild(it));
}

describe('GC sidebar batch-select Delete-button state', () => {
  beforeEach(() => {
    apiCalls.length = 0;
    fetchQueue.length = 0;
    state.currentSessionId = 'sid_test';
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

  it('select-all enables the Delete button and shows the count', () => {
    renderSidebar([
      makeSidebarItem('gc_a', 'app1-gc.log'),
      makeSidebarItem('gc_b', 'app2-gc.log'),
    ]);
    document.getElementById('gcSidebarSelectBtn').click();

    const deleteBtn = document.querySelector('#gcSidebarBatchBar .sidebar-delete-btn');
    document.querySelector('#gcSidebarBatchBar .sidebar-selectall-btn').click();

    expect(deleteBtn.disabled).toBe(false);
    expect(deleteBtn.textContent).toContain('(2)');
  });

  it('select-all toggle disables the Delete button when nothing selected', () => {
    renderSidebar([
      makeSidebarItem('gc_a', 'app1-gc.log'),
    ]);
    document.getElementById('gcSidebarSelectBtn').click();

    const selectAllBtn = document.querySelector('#gcSidebarBatchBar .sidebar-selectall-btn');
    const deleteBtn = document.querySelector('#gcSidebarBatchBar .sidebar-delete-btn');

    selectAllBtn.click();
    expect(deleteBtn.disabled).toBe(false);
    expect(deleteBtn.textContent).toContain('(1)');

    selectAllBtn.click();
    expect(deleteBtn.disabled).toBe(true);
    expect(deleteBtn.textContent).not.toContain('(1)');
  });

  it('clicking Select does not grey out the active sidebar item', () => {
    const active = makeSidebarItem('gc_active', 'active.hprof', true);
    active.classList.add('active');
    renderSidebar([
      active,
      makeSidebarItem('gc_b', 'b.hprof', false),
    ]);
    document.getElementById('gcSidebarSelectBtn').click();

    expect(active.classList.contains('batch-excluded')).toBe(false);
    // The active item's checkbox must be clickable
    const cb = active.querySelector('.si-checkbox');
    cb.click();
    expect(cb.innerHTML).toBe(ico('check-square'));
    // The Delete button count must reflect the active item selection
    const deleteBtn = document.querySelector('#gcSidebarBatchBar .sidebar-delete-btn');
    expect(deleteBtn.disabled).toBe(false);
    expect(deleteBtn.textContent).toContain('(1)');
  });

  it('bulk delete removes sidebar items after refresh (Fix 8A)', async () => {
    // Pre-fill with two history items via state so renderGcSidebar produces
    // the sidebar items, then enter batch mode and delete.
    state.gcHistoryReports = [
      { id: 'gc_a', filename: 'a-gc.log', created_at: '2024-01-02T00:00:00Z', status: 'DONE', has_ai: false, session_id: 'sid_test' },
      { id: 'gc_b', filename: 'b-gc.log', created_at: '2024-01-01T00:00:00Z', status: 'DONE', has_ai: false, session_id: 'sid_test' },
    ];
    // Render the real GC sidebar
    const { renderGcSidebar } = await import('../gc-analysis/sidebar.js');
    renderGcSidebar();

    expect(document.querySelectorAll('#gcSidebarList .sidebar-item').length).toBe(2);

    // Enter batch mode and check all
    document.getElementById('gcSidebarSelectBtn').click();
    document.querySelectorAll('#gcSidebarList .sidebar-item .si-checkbox').forEach(cb => cb.click());

    // Queue responses for the api calls in order:
    //   DELETE gc_a, DELETE gc_b, GET history
    fetchQueue.push({});  // DELETE gc_a
    fetchQueue.push({});  // DELETE gc_b
    fetchQueue.push({ reports: [] });  // GET refreshHistory

    document.querySelector('#gcSidebarBatchBar .sidebar-delete-btn').click();
    await new Promise(r => setTimeout(r, 100));

    // Sidebar should be empty
    expect(document.querySelectorAll('#gcSidebarList .sidebar-item').length).toBe(0);
    // Batch bar should be hidden (cancel called)
    expect(document.getElementById('gcSidebarBatchBar').style.display).toBe('none');
  });

  it('entering batch mode resets the Delete button to disabled/(0) (Fix 9A)', () => {
    // Pre-tamper the Delete button to simulate stale state from a previous
    // batch session that was cancelled without resetting the button.
    const deleteBtn = document.querySelector('#gcSidebarBatchBar .sidebar-delete-btn');
    deleteBtn.disabled = false;
    deleteBtn.innerHTML = `${ico('trash-2')} Bulk Delete (5)`;
    renderSidebar([
      makeSidebarItem('gc_a', 'a-gc.log'),
      makeSidebarItem('gc_b', 'b-gc.log'),
    ]);
    document.getElementById('gcSidebarSelectBtn').click();

    expect(deleteBtn.disabled).toBe(true);
    expect(deleteBtn.textContent).not.toContain('(');
  });
});
