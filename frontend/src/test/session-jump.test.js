/**
 * Regression test: clicking a session item must NOT destroy and rebuild the
 * session-item DOM node. The pre-fix code called renderSessionList()
 * (innerHTML=""; full createElement loop) inside selectSession, which
 * wiped all session-item nodes mid-click. The user's hover state
 * (.session-item:hover { transform: translateX(4px); }) was lost during
 * the wipe, then re-applied a frame later, producing a 4px "jump" left
 * then right back.
 *
 * Post-fix: selectSession only toggles the .active class on existing
 * nodes via _setActiveSessionClass. The DOM nodes are preserved.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { state } from '../state.js';
import { app } from '../app.js';

// Required DOM nodes for sessions.js and the modules selectSession touches.
document.body.innerHTML = `
  <div id="sessionList"></div>
  <button id="newSessionBtn"></button>
  <button id="refreshBtn"></button>
  <button id="deleteBtn"></button>
  <button id="clearBtn"></button>
  <button id="renameBtn"></button>
  <button id="sidebarToggle"></button>
  <div id="sessionUpdateBanner"></div>
  <div id="currentTitle"></div>
  <div id="chatArea"></div>
  <div id="factsList"></div>
  <div id="gcReportArea"></div>
  <div id="gcLoading"></div>
  <div id="gcError"></div>
  <div id="jstackReportArea"></div>
  <div id="jstackError"></div>
  <div id="heapdumpReportArea"></div>
  <div id="heapdumpError"></div>
  <div id="activeReportContext"></div>
  <div id="gcSidebar"></div>
  <div id="gcBodyHistory"></div>
  <div id="uploadZone"></div>
  <div id="gcReportTabs"></div>
  <div id="gcReportHeader"></div>
  <div id="gcReportFilename"></div>
  <div id="gcReportAiBadge"></div>
  <div id="gcReportAttachBtn"></div>
  <div id="gcSidebarToggle"></div>
  <div id="historyList"></div>
  <div id="historyEmpty"></div>
  <div id="historyCount"></div>
  <div id="jstackHistoryList"></div>
  <div id="jstackHistoryEmpty"></div>
  <div id="jstackHistoryCount"></div>
  <div id="heapdumpHistoryList"></div>
  <div id="heapdumpHistoryEmpty"></div>
  <div id="heapdumpHistoryCount"></div>
  <div id="allReportsList"></div>
  <div id="allReportsEmpty"></div>
  <div id="gcTabCount"></div>
  <div id="jstackTabCount"></div>
  <div id="heapdumpTabCount"></div>
  <div id="reportsTabCount"></div>
  <div id="gcBadge"></div>
`;

vi.mock('../../i18n/index.js', () => ({ t: (key) => key }));

const fetchQueue = [];
vi.mock('../api.js', () => ({
  api: vi.fn(async (url, opts) => {
    if (fetchQueue.length) return fetchQueue.shift();
    return {};
  }),
}));

window.confirm = vi.fn(() => false);
window.alert = vi.fn();

// Provide stubs for the app.* methods selectSession calls into so the test
// exercises the success path. The real app object is assembled in main.js.
app.renderMessages = vi.fn();
app.renderFacts = vi.fn();
app.setAuthorNames = vi.fn();
app.clearActiveReportContext = vi.fn();
app.renderReportTabs = vi.fn();
app.refreshHistory = vi.fn(async () => {});
app.refreshJstackHistory = vi.fn(async () => {});
app.refreshHeapdumpHistory = vi.fn(async () => {});
app.refreshAllReportHistory = vi.fn(async () => {});
app.updateBadge = vi.fn();
app.updateQuotaUI = vi.fn(async () => {});

const sessions = await import('../sessions.js');
const { renderSessionList } = sessions;

function listChildren() {
  return [...document.getElementById('sessionList').children];
}

describe('selectSession preserves DOM nodes (no jump)', () => {
  beforeEach(() => {
    fetchQueue.length = 0;
    state.currentSessionId = null;
    state.currentUser = null;
    state.sessionTab = 'personal';
    state.currentOrg = null;
    state.sessions = [];
    state.isStreaming = false;
    state.openGcReports = [];
    state.openJstackReports = [];
    state.openHeapdumpReports = [];
    state.currentReport = null;
    state.currentReportId = null;
    state.currentJstackReport = null;
    state.currentJstackReportId = null;
    state.currentHeapdumpReport = null;
    state.currentHeapdumpReportId = null;
    state.activeReportContexts = [];
    state.allReports = [];
    state.gcHistoryReports = [];
    document.getElementById('sessionList').innerHTML = '';
  });

  it('clicking a session item preserves the DOM node (no innerHTML wipe)', async () => {
    state.sessions = [
      { id: 's_1', title: 'First',  msg_count: 5,  updated_at: '2024-01-02T00:00:00Z', user_id: 'u1', org_id: null },
      { id: 's_2', title: 'Second', msg_count: 3,  updated_at: '2024-01-01T00:00:00Z', user_id: 'u1', org_id: null },
    ];
    state.currentSessionId = 's_1';
    renderSessionList();

    const before = listChildren();
    expect(before).toHaveLength(2);
    const secondItem = before[1];
    expect(secondItem.dataset.id).toBe('s_2');
    expect(secondItem.classList.contains('active')).toBe(false);

    fetchQueue.push({
      id: 's_2', title: 'Second', org_id: null, user_id: 'u1',
      messages: [], facts: [], updated_at: '2024-01-01T00:00:00Z',
    });
    secondItem.click();
    await new Promise(r => setTimeout(r, 30));

    // Pre-fix: renderSessionList() inside selectSession wiped and rebuilt the
    // list, so `secondItem.isConnected === false` after the click. Post-fix:
    // the same DOM node survives the click.
    expect(secondItem.isConnected).toBe(true);
    expect(secondItem.classList.contains('active')).toBe(true);

    // The other node is also preserved and its active class was toggled off.
    expect(before[0].isConnected).toBe(true);
    expect(before[0].classList.contains('active')).toBe(false);
    expect(state.currentSessionId).toBe('s_2');
  });

  it('data-id is set on session items for the active-class toggle', () => {
    state.sessions = [
      { id: 's_x', title: 'X', msg_count: 1, updated_at: '2024-01-01T00:00:00Z', user_id: 'u1', org_id: null },
    ];
    renderSessionList();
    expect(listChildren()[0].dataset.id).toBe('s_x');
  });
});