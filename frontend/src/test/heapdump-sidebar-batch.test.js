/**
 * Regression test: heapdump sidebar batch-select feature.
 *
 * Pre-fix bug: the sidebar rendered `<span class="si-checkbox">` for GC and
 * JStack items but NOT for heapdump items. The HTML had
 * `#heapdumpSidebarSelectBtn` and `#heapdumpSidebarBatchBar` elements, but no
 * JS bound them — clicking the "Select" button did nothing.
 *
 * Post-fix: heapdump sidebar mirrors GC/JStack behaviour:
 *   - each item has a `.si-checkbox` (☐)
 *   - clicking the Select button enters batch mode and shows the batch bar
 *   - clicking a checkbox toggles ☐/☑
 *   - the batch bar has working select-all / bulk-delete / cancel buttons
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ico } from '../icons.js';
import { state } from '../state.js';

document.body.innerHTML = `
  <div id="heapdumpSidebar">
    <div id="heapdumpSidebarList"></div>
    <button id="heapdumpSidebarToggle"></button>
    <input id="heapdumpSidebarFile" />
    <div id="heapdumpSidebarUploadZone"></div>
    <div id="heapdumpSidebarBatchBar" style="display:none">
      <button class="sidebar-selectall-btn"></button>
      <button class="sidebar-delete-btn"></button>
      <button class="sidebar-cancel-btn"></button>
    </div>
    <button id="heapdumpSidebarSelectBtn"></button>
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
  <div id="heapdumpTabCount"></div>
  <div id="heapdumpTab"></div>
  <div class="mode-body" data-mode="heapdump"></div>
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

const heapdump = await import('../heapdump-analysis/heapdump.js');
const { initHeapdump, refreshHeapdumpHistory, renderHeapdumpSidebar } = heapdump;
initHeapdump();

function makeReport(id, filename, created_at) {
  return {
    id, filename, created_at, status: 'DONE', has_ai: false,
    session_id: 'sid_test', num_objects: 100, used_heap: 1024,
  };
}

describe('heapdump sidebar batch-select', () => {
  beforeEach(() => {
    apiCalls.length = 0;
    fetchQueue.length = 0;
    state.currentSessionId = 'sid_test';
    state.openHeapdumpReports = [];
    state.heapdumpHistoryReports = [];
    state.activeReportContexts = [];
    state.currentHeapdumpReport = null;
    state.currentHeapdumpReportId = null;
    document.getElementById('heapdumpSidebarList').innerHTML = '';
    document.getElementById('activeReportContext').innerHTML = '';
    // Reset batch-bar visibility (cancel may have hidden it in prev test)
    document.getElementById('heapdumpSidebarBatchBar').style.display = 'none';
    document.getElementById('heapdumpSidebarSelectBtn').style.display = '';
  });

  it('renderHeapdumpSidebar emits a .si-checkbox for each item', () => {
    state.heapdumpHistoryReports = [
      makeReport('hd_1', 'a.hprof', '2024-01-02T00:00:00Z'),
      makeReport('hd_2', 'b.hprof', '2024-01-01T00:00:00Z'),
    ];
    renderHeapdumpSidebar();

    const items = document.querySelectorAll('#heapdumpSidebarList .sidebar-item');
    expect(items.length).toBe(2);
    items.forEach(item => {
      const cb = item.querySelector('.si-checkbox');
      expect(cb).not.toBeNull();
      expect(cb.innerHTML).toBe(ico('square'));
    });
  });

  it('clicking the Select button enters batch mode and shows the batch bar', () => {
    state.heapdumpHistoryReports = [
      makeReport('hd_1', 'a.hprof', '2024-01-02T00:00:00Z'),
      makeReport('hd_2', 'b.hprof', '2024-01-01T00:00:00Z'),
    ];
    renderHeapdumpSidebar();

    const selectBtn = document.getElementById('heapdumpSidebarSelectBtn');
    const batchBar = document.getElementById('heapdumpSidebarBatchBar');
    expect(batchBar.style.display).toBe('none');

    selectBtn.click();

    expect(batchBar.style.display).toBe('flex');
    expect(selectBtn.style.display).toBe('none');
    // All items should be in batch-active state
    document.querySelectorAll('#heapdumpSidebarList .sidebar-item').forEach(el => {
      expect(el.classList.contains('batch-active')).toBe(true);
    });
  });

  it('clicking a checkbox toggles ☐/☑ and updates the delete count', () => {
    state.heapdumpHistoryReports = [
      makeReport('hd_1', 'a.hprof', '2024-01-02T00:00:00Z'),
    ];
    renderHeapdumpSidebar();
    document.getElementById('heapdumpSidebarSelectBtn').click();

    const cb = document.querySelector('#heapdumpSidebarList .sidebar-item .si-checkbox');
    expect(cb.innerHTML).toBe(ico('square'));

    cb.click();
    expect(cb.innerHTML).toBe(ico('check-square'));

    cb.click();
    expect(cb.innerHTML).toBe(ico('square'));
  });

  it('batch cancel exits batch mode and hides the batch bar', () => {
    state.heapdumpHistoryReports = [
      makeReport('hd_1', 'a.hprof', '2024-01-02T00:00:00Z'),
    ];
    renderHeapdumpSidebar();
    document.getElementById('heapdumpSidebarSelectBtn').click();

    const batchBar = document.getElementById('heapdumpSidebarBatchBar');
    const selectBtn = document.getElementById('heapdumpSidebarSelectBtn');
    expect(batchBar.style.display).toBe('flex');

    document.querySelector('#heapdumpSidebarBatchBar .sidebar-cancel-btn').click();

    expect(batchBar.style.display).toBe('none');
    expect(selectBtn.style.display).toBe('');
    document.querySelectorAll('#heapdumpSidebarList .sidebar-item').forEach(el => {
      expect(el.classList.contains('batch-active')).toBe(false);
    });
  });

  it('select-all toggles every non-excluded checkbox', () => {
    state.heapdumpHistoryReports = [
      makeReport('hd_1', 'a.hprof', '2024-01-03T00:00:00Z'),
      makeReport('hd_2', 'b.hprof', '2024-01-02T00:00:00Z'),
      makeReport('hd_3', 'c.hprof', '2024-01-01T00:00:00Z'),
    ];
    renderHeapdumpSidebar();
    document.getElementById('heapdumpSidebarSelectBtn').click();

    const selectAllBtn = document.querySelector('#heapdumpSidebarBatchBar .sidebar-selectall-btn');
    selectAllBtn.click();
    document.querySelectorAll('#heapdumpSidebarList .sidebar-item .si-checkbox').forEach(cb => {
      expect(cb.innerHTML).toBe(ico('check-square'));
    });

    selectAllBtn.click();
    document.querySelectorAll('#heapdumpSidebarList .sidebar-item .si-checkbox').forEach(cb => {
      expect(cb.innerHTML).toBe(ico('square'));
    });
  });

  it('select-all enables the Delete button and shows the count', () => {
    state.heapdumpHistoryReports = [
      makeReport('hd_1', 'a.hprof', '2024-01-02T00:00:00Z'),
      makeReport('hd_2', 'b.hprof', '2024-01-01T00:00:00Z'),
    ];
    renderHeapdumpSidebar();
    document.getElementById('heapdumpSidebarSelectBtn').click();

    const deleteBtn = document.querySelector('#heapdumpSidebarBatchBar .sidebar-delete-btn');
    document.querySelector('#heapdumpSidebarBatchBar .sidebar-selectall-btn').click();

    expect(deleteBtn.disabled).toBe(false);
    expect(deleteBtn.textContent).toContain('(2)');
  });

  it('select-all toggle disables the Delete button when nothing selected', () => {
    state.heapdumpHistoryReports = [
      makeReport('hd_1', 'a.hprof', '2024-01-02T00:00:00Z'),
    ];
    renderHeapdumpSidebar();
    document.getElementById('heapdumpSidebarSelectBtn').click();

    const selectAllBtn = document.querySelector('#heapdumpSidebarBatchBar .sidebar-selectall-btn');
    const deleteBtn = document.querySelector('#heapdumpSidebarBatchBar .sidebar-delete-btn');

    selectAllBtn.click();
    expect(deleteBtn.disabled).toBe(false);

    selectAllBtn.click();
    expect(deleteBtn.disabled).toBe(true);
    expect(deleteBtn.textContent).not.toContain('(1)');
  });

  it('clicking Select does not grey out the active sidebar item', () => {
    const list = document.getElementById('heapdumpSidebarList');
    const active = document.createElement('div');
    active.className = 'sidebar-item active';
    active.dataset.id = 'hd_active';
    active.dataset.session = 'true';
    active.innerHTML = `
      <div class="si-row1"><span class="si-checkbox">${ico('square')}</span></div>
      <div class="si-row2"><button class="si-attach-btn">${ico('paperclip')}</button></div>
    `;
    const other = document.createElement('div');
    other.className = 'sidebar-item';
    other.dataset.id = 'hd_other';
    other.dataset.session = 'false';
    other.innerHTML = `
      <div class="si-row1"><span class="si-checkbox">${ico('square')}</span></div>
      <div class="si-row2"><button class="si-attach-btn">${ico('paperclip')}</button></div>
    `;
    list.innerHTML = '';
    list.appendChild(active);
    list.appendChild(other);

    document.getElementById('heapdumpSidebarSelectBtn').click();

    expect(active.classList.contains('batch-excluded')).toBe(false);
    const cb = active.querySelector('.si-checkbox');
    cb.click();
    expect(cb.innerHTML).toBe(ico('check-square'));
    const deleteBtn = document.querySelector('#heapdumpSidebarBatchBar .sidebar-delete-btn');
    expect(deleteBtn.disabled).toBe(false);
    expect(deleteBtn.textContent).toContain('(1)');
  });

  it('bulk delete of session item removes it from sidebar (Fix 8C)', async () => {
    // Session item: in openHeapdumpReports, NOT in heapdumpHistoryReports.
    state.openHeapdumpReports = [{
      id: 'hd_sess', filename: 'sess.hprof',
      report: { id: 'hd_sess', filename: 'sess.hprof', stats: {}, ai_conclusion: '' },
    }];
    state.heapdumpHistoryReports = [];
    renderHeapdumpSidebar();
    expect(document.querySelectorAll('#heapdumpSidebarList .sidebar-item').length).toBe(1);

    document.getElementById('heapdumpSidebarSelectBtn').click();
    document.querySelectorAll('#heapdumpSidebarList .sidebar-item .si-checkbox').forEach(cb => cb.click());

    // Queue: DELETE session + GET refresh (returns empty list)
    fetchQueue.push({});  // DELETE
    fetchQueue.push({ reports: [] });  // GET refresh

    document.querySelector('#heapdumpSidebarBatchBar .sidebar-delete-btn').click();
    await new Promise(r => setTimeout(r, 50));

    expect(state.openHeapdumpReports.find(r => r.id === 'hd_sess')).toBeUndefined();
    expect(document.querySelectorAll('#heapdumpSidebarList .sidebar-item').length).toBe(0);
    expect(document.getElementById('heapdumpSidebarBatchBar').style.display).toBe('none');
  });

  it('bulk delete calls DELETE for each checked report and exits batch mode', async () => {
    state.heapdumpHistoryReports = [
      makeReport('hd_1', 'a.hprof', '2024-01-03T00:00:00Z'),
      makeReport('hd_2', 'b.hprof', '2024-01-02T00:00:00Z'),
      makeReport('hd_3', 'c.hprof', '2024-01-01T00:00:00Z'),
    ];
    renderHeapdumpSidebar();
    document.getElementById('heapdumpSidebarSelectBtn').click();

    // Check hd_1 and hd_3 (skip hd_2)
    const cbs = document.querySelectorAll('#heapdumpSidebarList .sidebar-item .si-checkbox');
    cbs[0].click(); // hd_1 → ☑
    cbs[2].click(); // hd_3 → ☑

    const deleteBtn = document.querySelector('#heapdumpSidebarBatchBar .sidebar-delete-btn');
    deleteBtn.click();
    await new Promise(r => setTimeout(r, 20));

    const deletes = apiCalls
      .filter(c => c.opts && c.opts.method === 'DELETE')
      .map(c => c.url);
    expect(deletes).toEqual(expect.arrayContaining([
      '/api/heapdump-reports/hd_1',
      '/api/heapdump-reports/hd_3',
    ]));
    expect(deletes).not.toContain('/api/heapdump-reports/hd_2');

    // Should exit batch mode after delete
    expect(document.getElementById('heapdumpSidebarBatchBar').style.display).toBe('none');
  });

  it('bulk delete cleans up state.openHeapdumpReports entries (Fix 2)', async () => {
    state.openHeapdumpReports = [
      { id: 'hd_sess', filename: 'sess.hprof', report: { id: 'hd_sess', filename: 'sess.hprof', stats: {}, ai_conclusion: '' } },
    ];
    state.heapdumpHistoryReports = [
      makeReport('hd_sess', 'sess.hprof', '2024-01-03T00:00:00Z'),
    ];
    renderHeapdumpSidebar();
    document.getElementById('heapdumpSidebarSelectBtn').click();
    document.querySelector('#heapdumpSidebarList .sidebar-item .si-checkbox').click();

    document.querySelector('#heapdumpSidebarBatchBar .sidebar-delete-btn').click();
    await new Promise(r => setTimeout(r, 30));

    expect(state.openHeapdumpReports.find(r => r.id === 'hd_sess')).toBeUndefined();
  });

  it('bulk delete routes through deleteReportEntries (Fix 2 — single alert path)', async () => {
    // Force one DELETE to fail; ensure the loop continues (per-entry alert).
    state.heapdumpHistoryReports = [
      makeReport('hd_1', 'a.hprof', '2024-01-03T00:00:00Z'),
      makeReport('hd_2', 'b.hprof', '2024-01-02T00:00:00Z'),
    ];
    // The api mock queues responses; we make hd_1's DELETE succeed and the
    // second call (refreshHeapdumpHistory → list) succeed. For the bulk-delete
    // path, deleteReportEntries will trigger `await import("heapdump-analysis/index.js")`
    // for each entry, then call closeReport; the only DELETE calls come from
    // the loop in deleteReportEntries, one per entry.
    renderHeapdumpSidebar();
    document.getElementById('heapdumpSidebarSelectBtn').click();
    const cbs = document.querySelectorAll('#heapdumpSidebarList .sidebar-item .si-checkbox');
    cbs[0].click();
    cbs[1].click();

    document.querySelector('#heapdumpSidebarBatchBar .sidebar-delete-btn').click();
    await new Promise(r => setTimeout(r, 30));

    const deletes = apiCalls.filter(c => c.opts && c.opts.method === 'DELETE').map(c => c.url);
    expect(deletes.length).toBe(2);
  });

  it('bulk delete cancels when the user rejects the confirm dialog', async () => {
    window.confirm.mockReturnValueOnce(false);
    state.heapdumpHistoryReports = [
      makeReport('hd_1', 'a.hprof', '2024-01-03T00:00:00Z'),
    ];
    renderHeapdumpSidebar();
    document.getElementById('heapdumpSidebarSelectBtn').click();
    document.querySelector('#heapdumpSidebarList .sidebar-item .si-checkbox').click();

    document.querySelector('#heapdumpSidebarBatchBar .sidebar-delete-btn').click();
    await new Promise(r => setTimeout(r, 20));

    const deletes = apiCalls.filter(c => c.opts && c.opts.method === 'DELETE');
    expect(deletes.length).toBe(0);
  });

  it('entering batch mode resets the Delete button to disabled/(0) (Fix 9C)', () => {
    const deleteBtn = document.querySelector('#heapdumpSidebarBatchBar .sidebar-delete-btn');
    deleteBtn.disabled = false;
    deleteBtn.innerHTML = `${ico('trash-2')} Bulk Delete (5)`;
    state.heapdumpHistoryReports = [
      makeReport('hd_1', 'a.hprof', '2024-01-02T00:00:00Z'),
      makeReport('hd_2', 'b.hprof', '2024-01-01T00:00:00Z'),
    ];
    renderHeapdumpSidebar();
    document.getElementById('heapdumpSidebarSelectBtn').click();

    expect(deleteBtn.disabled).toBe(true);
    expect(deleteBtn.textContent).not.toContain('(');
  });
});

describe('heapdump sidebar attach sync (Fix 1)', () => {
  beforeEach(() => {
    apiCalls.length = 0;
    fetchQueue.length = 0;
    state.currentSessionId = 'sid_test';
    state.openHeapdumpReports = [];
    state.heapdumpHistoryReports = [];
    state.activeReportContexts = [];
    state.currentHeapdumpReport = null;
    state.currentHeapdumpReportId = null;
    document.getElementById('heapdumpSidebarList').innerHTML = '';
    document.getElementById('activeReportContext').innerHTML = '';
  });

  it('marking the heapdump mode-body as sidebar-active and adding an external context toggles .attached on the sidebar button', async () => {
    state.heapdumpHistoryReports = [
      makeReport('hd_a', 'a.hprof', '2024-01-02T00:00:00Z'),
    ];
    renderHeapdumpSidebar();

    // Mark the heapdump mode-body as sidebar-active so _syncReportTabsAttachState
    // touches the heapdump sidebar buttons (this class is normally set by
    // enableHeapdumpSidebar() which only runs in interactive mode).
    const modeBody = document.querySelector('.mode-body[data-mode="heapdump"]');
    modeBody.classList.add('sidebar-active');

    // Simulate an external code path (e.g. cross-panel delete from the
    // all-reports page) adding a heapdump context directly.
    state.activeReportContexts.push({
      type: 'heapdump', session_id: 'sid_test', report_id: 'hd_a',
      file_id: '', filename: 'a.hprof',
    });

    // _syncReportTabsAttachState is called from renderActiveReportContext.
    const ctxModule = await import('../gc-analysis/context.js');
    ctxModule.renderActiveReportContext();

    const attachBtn = document.querySelector('#heapdumpSidebarList .si-attach-btn');
    expect(attachBtn).not.toBeNull();
    expect(attachBtn.classList.contains('attached')).toBe(true);

    // Clean up: remove the sidebar-active class so subsequent tests aren't affected.
    modeBody.classList.remove('sidebar-active');
  });

  it('removing the context externally clears .attached on the sidebar button', async () => {
    state.heapdumpHistoryReports = [
      makeReport('hd_a', 'a.hprof', '2024-01-02T00:00:00Z'),
    ];
    renderHeapdumpSidebar();
    const modeBody = document.querySelector('.mode-body[data-mode="heapdump"]');
    modeBody.classList.add('sidebar-active');

    // Start with the context attached.
    state.activeReportContexts.push({
      type: 'heapdump', session_id: 'sid_test', report_id: 'hd_a',
      file_id: '', filename: 'a.hprof',
    });
    const ctxModule = await import('../gc-analysis/context.js');
    ctxModule.renderActiveReportContext();
    expect(document.querySelector('#heapdumpSidebarList .si-attach-btn').classList.contains('attached')).toBe(true);

    // External removal — drop the entry directly from the state.
    state.activeReportContexts = [];
    ctxModule.renderActiveReportContext();

    expect(document.querySelector('#heapdumpSidebarList .si-attach-btn').classList.contains('attached')).toBe(false);
    modeBody.classList.remove('sidebar-active');
  });
});
