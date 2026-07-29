/**
 * Regression tests for session rename (Bug 3).
 *
 * Pre-fix bugs:
 *   A. `prompt(t("chat.rename_prompt"))` was called without the current
 *      session title as its second argument, so the input box opened empty.
 *   B. After the PATCH succeeded, only `loadSessions()` ran — the topbar's
 *      `#currentTitle` was never updated, so the displayed title was stale
 *      until the user clicked away and back into the session.
 *
 * Post-fix:
 *   - Prompt pre-fills with the current session title.
 *   - Whitespace-only / unchanged titles short-circuit before PATCH.
 *   - On successful rename, topbar `#currentTitle` is updated from
 *     `state.sessions` (re-rendered via the shared `renderCurrentTitle`).
 *   - `selectSession()` also uses the same helper for consistency.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { state } from '../state.js';
import { app } from '../app.js';

// Required DOM nodes for sessions.js
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

// Capture prompt() arguments directly via a module-level spy. jsdom's stub
// returns undefined/null, but our patch wraps `window.prompt` so we can
// assert what arguments renameSession() passes to it.
const promptCalls = [];
const originalPrompt = window.prompt;
window.prompt = vi.fn((msg, def) => {
  promptCalls.push({ msg, def });
  return def; // Return default so the rename "succeeds" by default
});

const apiCalls = [];
vi.mock('../api.js', () => ({
  api: vi.fn(async (url, opts) => {
    apiCalls.push({ url, opts });
    return { sessions: [
      { id: 's_1', title: '新标题', msg_count: 1, updated_at: '2024-01-02T00:00:00Z', user_id: 'u1', org_id: null },
    ] };
  }),
}));

window.confirm = vi.fn(() => false);
window.alert = vi.fn();

// Stubs for methods selectSession/loadSessions call into.
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
const { renameSession } = sessions;

function reset() {
  apiCalls.length = 0;
  promptCalls.length = 0;
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
  state.jstackHistoryReports = [];
  state.heapdumpHistoryReports = [];
  state.agentReady = false;
  state.analysisFeatures = ['gc', 'jstack'];
  document.getElementById('sessionList').innerHTML = '';
  document.getElementById('currentTitle').textContent = '';
}

describe('renameSession (Bug 3)', () => {
  beforeEach(reset);

  it('Bug 3A: prompt() receives the current session title as its default', async () => {
    state.sessions = [
      { id: 's_1', title: '旧标题', msg_count: 1, updated_at: '2024-01-01T00:00:00Z', user_id: 'u1', org_id: null },
    ];
    state.currentSessionId = 's_1';

    await renameSession();

    expect(promptCalls).toHaveLength(1);
    const { def } = promptCalls[0];
    expect(def).toBe('旧标题');
  });

  it('Bug 3A: when there is no current session, prompt() is not called', async () => {
    state.currentSessionId = null;
    state.sessions = [];

    await renameSession();

    expect(promptCalls).toHaveLength(0);
    expect(apiCalls).toHaveLength(0);
  });

  it('Bug 3B: topbar #currentTitle reflects the new title after rename', async () => {
    state.agentReady = true;
    state.sessions = [
      { id: 's_1', title: '旧标题', msg_count: 1, updated_at: '2024-01-01T00:00:00Z', user_id: 'u1', org_id: null },
    ];
    state.currentSessionId = 's_1';
    document.getElementById('currentTitle').textContent = '旧标题';

    // Rename to a new value
    window.prompt.mockReturnValueOnce('新标题');

    await renameSession();

    expect(document.getElementById('currentTitle').textContent).toBe('新标题');

    const patches = apiCalls.filter(c => c.opts && c.opts.method === 'PATCH');
    expect(patches).toHaveLength(1);
    expect(JSON.parse(patches[0].opts.body)).toEqual({ title: '新标题' });
  });

  it('does not PATCH when the new title equals the current one (no-op rename)', async () => {
    state.sessions = [
      { id: 's_1', title: '原标题', msg_count: 1, updated_at: '2024-01-01T00:00:00Z', user_id: 'u1', org_id: null },
    ];
    state.currentSessionId = 's_1';

    // User "accepts" the default unchanged title (our mock returns default)
    await renameSession();

    const patches = apiCalls.filter(c => c.opts && c.opts.method === 'PATCH');
    expect(patches).toHaveLength(0);
  });

  it('does not PATCH when the new title is whitespace-only', async () => {
    state.sessions = [
      { id: 's_1', title: '原标题', msg_count: 1, updated_at: '2024-01-01T00:00:00Z', user_id: 'u1', org_id: null },
    ];
    state.currentSessionId = 's_1';

    window.prompt.mockReturnValueOnce('   ');
    await renameSession();

    const patches = apiCalls.filter(c => c.opts && c.opts.method === 'PATCH');
    expect(patches).toHaveLength(0);
  });

  it('does not PATCH when user cancels the prompt (null/empty)', async () => {
    state.sessions = [
      { id: 's_1', title: '原标题', msg_count: 1, updated_at: '2024-01-01T00:00:00Z', user_id: 'u1', org_id: null },
    ];
    state.currentSessionId = 's_1';

    window.prompt.mockReturnValueOnce(null);
    await renameSession();

    const patches = apiCalls.filter(c => c.opts && c.opts.method === 'PATCH');
    expect(patches).toHaveLength(0);
  });
});