/**
 * Regression test: sidebar batch bar layout (Bug 2).
 *
 * Pre-fix bug: the `.sidebar-batch-bar` rendered three buttons with `flex: 1`
 * (no `min-width: 0`) and the bulk-delete button used `innerHTML = "... (N)"`
 * to show the selected count. As N grew, the label expanded into a single
 * line, crowding the other two buttons (全选 / 取消) and clipping their text
 * on the narrow 160px sidebar.
 *
 * Post-fix:
 *  - Delete button has stable internal structure: `.si-icon` + `.si-label`
 *    + `.batch-count` (independent badge span).
 *  - Updates never replace innerHTML — only toggle the badge text/visibility,
 *    so the label never grows with N.
 *  - CSS gives buttons `flex: 1 1 auto; min-width: 0; white-space: nowrap;
 *    overflow: hidden; text-overflow: ellipsis;` so the label can shrink to
 *    fit and the layout no longer overflows on narrow sidebars.
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
  <div id="gcSidebarList"></div>
  <div id="gcReportHeader"></div>
  <div id="gcReportFilename"></div>
  <div id="gcReportAiBadge"></div>
  <div id="gcReportAttachBtn"></div>
  <div id="gcReportArea"></div>
  <div id="gcLoading" style="display:none"></div>
  <div id="gcError"></div>
  <div id="activeReportContext"></div>
  <button id="gcSidebarSelectBtn"></button>
  <div id="gcSidebarBatchBar">
    <button class="sidebar-cancel-btn"></button>
    <button class="sidebar-selectall-btn"></button>
    <button class="sidebar-delete-btn">
      <span class="si-icon"></span>
      <span class="si-label" data-i18n="reports.bulk_delete"></span>
      <span class="batch-count" hidden></span>
    </button>
  </div>
  <input type="file" id="gcSidebarFile" />
  <div id="gcSidebarUploadZone"></div>
  <button id="reportsTabCount"></button>
  <div class="mode-body" data-mode="gc"></div>
  <div class="mode-body" data-mode="reports"></div>
  <div class="mode-body" data-mode="heapdump"></div>
  <div class="mode-body" data-mode="jstack"></div>
  <div class="gc-tabs">
    <button class="tab" data-subtab="current"></button>
    <button class="tab" data-subtab="history"></button>
  </div>
  <button class="mode-tab" data-mode="gc"></button>
  <button class="mode-tab" data-mode="reports"></button>
  <button class="mode-tab" data-mode="heapdump"></button>
  <button class="mode-tab" data-mode="jstack"></button>
`;

vi.mock('../../i18n/index.js', () => ({ t: (key) => key }));

vi.mock('../api.js', () => ({
  api: vi.fn(async () => ({})),
}));

globalThis.marked = { parse: (t) => `<p>${t}</p>` };
globalThis.DOMPurify = { sanitize: (h) => h };
window.marked = globalThis.marked;
window.DOMPurify = globalThis.DOMPurify;

window.confirm = vi.fn(() => true);
window.alert = vi.fn();

await import('../gc-analysis/index.js');

function makeSidebarItem(id, filename) {
  const item = document.createElement('div');
  item.className = 'sidebar-item batch-active';
  item.dataset.id = id;
  item.dataset.session = 'true';
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

describe('Bug 2: sidebar batch bar layout', () => {
  beforeEach(() => {
    state.currentSessionId = 'sid_test';
    state.openGcReports = [];
    state.gcHistoryReports = [];
    state.activeReportContexts = [];
    state.currentReport = null;
    state.currentReportId = null;
    document.getElementById('gcSidebarList').innerHTML = '';
    document.getElementById('activeReportContext').innerHTML = '';
  });

  it('preserves si-icon / si-label / batch-count structure after many updates', () => {
    renderSidebar([
      makeSidebarItem('gc_a', 'app1.log'),
      makeSidebarItem('gc_b', 'app2.log'),
      makeSidebarItem('gc_c', 'app3.log'),
    ]);
    document.getElementById('gcSidebarSelectBtn').click();
    const deleteBtn = document.querySelector('#gcSidebarBatchBar .sidebar-delete-btn');
    const selectAllBtn = document.querySelector('#gcSidebarBatchBar .sidebar-selectall-btn');

    // Capture original structure
    const icon = deleteBtn.querySelector('.si-icon');
    const label = deleteBtn.querySelector('.si-label');
    expect(icon).not.toBeNull();
    expect(label).not.toBeNull();

    // Run a long sequence of select/deselect cycles
    for (let i = 0; i < 10; i++) {
      selectAllBtn.click();
      selectAllBtn.click();
    }
    selectAllBtn.click(); // 3 selected

    // Structure must survive: same DOM nodes (or at least same classes)
    expect(deleteBtn.querySelector('.si-icon')).not.toBeNull();
    expect(deleteBtn.querySelector('.si-label')).not.toBeNull();
    const count = deleteBtn.querySelector('.batch-count');
    expect(count).not.toBeNull();
    expect(count.textContent).toBe('3');
    expect(count.hidden).toBe(false);
  });

  it('count is rendered as a separate badge, not appended to the label', () => {
    renderSidebar([
      makeSidebarItem('gc_a', 'app1.log'),
      makeSidebarItem('gc_b', 'app2.log'),
    ]);
    document.getElementById('gcSidebarSelectBtn').click();
    const deleteBtn = document.querySelector('#gcSidebarBatchBar .sidebar-delete-btn');
    const selectAllBtn = document.querySelector('#gcSidebarBatchBar .sidebar-selectall-btn');
    selectAllBtn.click();

    // Label text should be the bare i18n label, never "(2)"
    const label = deleteBtn.querySelector('.si-label');
    expect(label.textContent.trim()).not.toContain('(');
    expect(label.textContent.trim()).not.toContain('2');

    // Count is its own element
    const count = deleteBtn.querySelector('.batch-count');
    expect(count.textContent).toBe('2');
  });

  it('label width does not grow when count becomes large (e.g. 99)', () => {
    renderSidebar(Array.from({ length: 99 }, (_, i) => makeSidebarItem(`gc_${i}`, `app${i}.log`)));
    document.getElementById('gcSidebarSelectBtn').click();
    const deleteBtn = document.querySelector('#gcSidebarBatchBar .sidebar-delete-btn');
    const selectAllBtn = document.querySelector('#gcSidebarBatchBar .sidebar-selectall-btn');
    selectAllBtn.click();

    // Label text content is unchanged — only the badge text grew
    const label = deleteBtn.querySelector('.si-label');
    const count = deleteBtn.querySelector('.batch-count');
    expect(count.textContent).toBe('99');
    // Label should NOT contain "99" or "(99)"
    expect(label.textContent).not.toContain('99');
    expect(label.textContent).not.toContain('(');
  });
});