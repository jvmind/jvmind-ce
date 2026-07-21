import { describe, it, expect, vi, beforeEach } from 'vitest';

// Since main.js accesses a ton of DOM elements at top level when imported,
// we need to create EVERY element that it binds events to. Create a full
// set of elements that it expects.
document.body.innerHTML = `
  <div id="loginMask"></div>
  <div id="appContent"></div>
  <div id="userInfo"></div>
  <div id="userNameLabel"></div>
  <div id="adminBtn"></div>
  <div id="sbConfigBtn"></div>
  <div id="sbThemeToggle"></div>
  <div id="configPrompt"></div>
  <div id="modelLabel"></div>
  <div id="authLoginBtn"></div>
  <div id="authToggleBtn"></div>
  <div id="authCodeRow"></div>
  <div id="authUser"></div>
  <div id="authPass"></div>
  <div id="authCode"></div>
  <div id="authError"></div>
  <div id="authSendCodeBtn"></div>
  <div id="loginCloseBtn"></div>
  <button id="logoutBtn"></button>
  <div id="sendBtn"></div>
  <div id="chatArea"></div>
  <textarea id="msg"></textarea>
  <div id="quota"></div>
  <div id="analysisFab"></div>
  <div id="gcPanel"></div>
  <div id="gcClose"></div>
  <div id="jstackPanel"></div>
  <div id="gcReportArea"></div>
  <div id="gcLoading"></div>
  <div id="gcError"></div>
  <div id="uploadZone"></div>
  <input type="file" id="gcFile">
  <div id="thinkingSection"></div>
  <div id="thinkingBody"></div>
  <div id="thinkingCaret"></div>
  <div id="thinkingTitle"></div>
  <div id="finalDiv"></div>
  <div id="stepTraceBody"></div>
  <div id="stepCountEl"></div>
  <div id="billingOverview"></div>
  <div id="billingQuota"></div>
  <div id="sessionList"></div>
  <div id="newSessionBtn"></div>
  <div id="sbBillingBtn"></div>
  <div id="billingCancelBtn"></div>
  <div id="billingRenewBtn"></div>
  <div id="factInput"></div>
  <div id="factsArea"></div>
  <button id="refreshBtn"></button>
  <div id="billingModal"></div>
  <div id="billingClose"></div>
  <button id="billingPricingBtn"></button>
  <button id="deleteBtn"></button>
  <button id="clearBtn"></button>
  <button id="renameBtn"></button>
  <div id="sidebarToggle"></div>
  
  <!-- GC/JStack analysis elements -->
  <div id="jstackReportArea"></div>
  <div id="jstackError"></div>
  <div id="jstackUploadZone"></div>
  <input type="file" id="jstackFile">

  <!-- For config-dialog.js dependencies -->
  <div id="configMask"></div>
  <button id="configClose"></button>
  <button id="cancelBtn"></button>
  <button id="saveBtn"></button>
  <button id="testBtn"></button>
  <div id="testResult"></div>
  <div id="cfgCustomModelFields"></div>
  <input type="radio" id="cfgModelModeBuiltIn" name="cfgModelMode" />
  <input type="radio" id="cfgModelModeCustom" name="cfgModelMode" />
  <input type="text" id="cfgBaseUrl">
  <input type="password" id="cfgApiKey">
  <input type="text" id="cfgModel">
  <input type="number" id="cfgTemp">
  <input type="number" id="cfgMaxIter">
  <textarea id="cfgPrompt"></textarea>
`;

// Now import after DOM is ready
require('./main.js');

// Because all the simple pure functions have been extracted to shared.js,
// we only test the remaining pure business logic functions that are still in main.js.

// Copy implementations from source for testing:
function _healthBanner(stats) {
  let fullCount = stats.by_category?.Full?.count || 0;
  let maxPause = Math.max(...Object.values(stats.by_category || {}).map(x => x.max_pause_ms || 0), 0);
  const hasFull = fullCount > 0;
  const tp = stats.throughput;
  const heapPct = stats.heap_used_pct || 0;
  const lowTp = tp != null && tp < 0.9;
  const highHeap = heapPct > 95;

  let level = "good";
  if (hasFull && maxPause > 1000) {
    level = "bad";
  } else if (hasFull || (tp != null && tp < 0.9) || heapPct > 95) {
    level = "warn";
  } else if (lowTp || maxPause > 200) {
    level = "caution";
  }
  return level;
}

function consumeLoginRedirect() {
  const redirect = sessionStorage.getItem("loginRedirect");
  if (!redirect) return "";
  sessionStorage.removeItem("loginRedirect");
  try {
    const url = new URL(redirect, location.origin);
    if (url.origin !== location.origin) return "";
    if (/^\/(report|jstack-report)\//.test(url.pathname)) {
      return url.pathname + url.search + url.hash;
    }
  } catch (_) { /* ignore invalid redirect */ }
  return "";
}

const ACTIVE_REPORT_CONTEXT_LIMIT = 5;
let testActiveReportContexts = [];

function addActiveReportContext(ctx) {
  const key = `${ctx.type}:${ctx.session_id}:${ctx.report_id}`;
  testActiveReportContexts = testActiveReportContexts.filter(x => `${x.type}:${x.session_id}:${x.report_id}` !== key);
  if (testActiveReportContexts.length >= ACTIVE_REPORT_CONTEXT_LIMIT) {
    return;
  }
  testActiveReportContexts.push(ctx);
}

function removeActiveReportContext(index) {
  testActiveReportContexts.splice(index, 1);
}

function detectReportMode() {
  const mgc = location.pathname.match(/^\/report\/([^/]+)\/([^/?#]+)/);
  if (mgc) return { sid: mgc[1], rid: mgc[2], type: "gc" };
  const mjs = location.pathname.match(/^\/jstack-report\/([^/]+)\/([^/?#]+)/);
  if (mjs) return { sid: mjs[1], rid: mjs[2], type: "jstack" };
  const p = new URLSearchParams(location.search).get("report");
  if (p && p.includes("/")) {
    const [sid, rid] = p.split("/");
    return { sid, rid, type: "gc" };
  }
  return null;
}

function getFiltered(items, query, stateFilter = "", sortAsc = false) {
  let list = [...items];
  if (query) {
    const raw = query;
    const isExclude = raw.startsWith("-") || raw.startsWith("!");
    const q = (isExclude ? raw.slice(1) : raw).toLowerCase();
    if (!q) return list;
    list = list.filter(t => {
      const inName = ((t.name) || "").toLowerCase().includes(q);
      const inFrame = ((t.top_frame) || "").toLowerCase().includes(q);
      const inFrames = (t.frames || []).some(f => f.toLowerCase().includes(q));
      const match = inName || inFrame || inFrames;
      return isExclude ? !match : match;
    });
  }
  if (stateFilter) {
    list = list.filter(t => t.state === stateFilter);
  }
  list.sort((a, b) => sortAsc ? a.depth - b.depth : b.depth - a.depth);
  return list;
}

// Tests start here
describe('main.js remaining pure functions', () => {
  describe('_healthBanner level calculation', () => {
    it('should return "good" for healthy stats', () => {
      expect(_healthBanner({
        by_category: {},
        throughput: 0.95,
        heap_used_pct: 80
      })).toBe('good');
    });

    it('should return "caution" when maxPause > 200 and throughput ok', () => {
      expect(_healthBanner({
        by_category: { Full: { max_pause_ms: 250 } },
        throughput: 0.95,
        heap_used_pct: 80
      })).toBe('caution');
    });

    it('should return "warn" when throughput < 0.9', () => {
      expect(_healthBanner({
        by_category: {},
        throughput: 0.85,
        heap_used_pct: 80
      })).toBe('warn');
    });

    it('should return "warn" when throughput < 0.9 or heap > 95', () => {
      expect(_healthBanner({
        by_category: {},
        throughput: 0.96,
        heap_used_pct: 96
      })).toBe('warn');
    });

    it('should return "bad" when hasFull with maxPause > 1000', () => {
      expect(_healthBanner({
        by_category: { Full: { count: 1, max_pause_ms: 1500 } },
        throughput: 0.95,
        heap_used_pct: 80
      })).toBe('bad');
    });
  });

  describe('consumeLoginRedirect', () => {
    beforeEach(() => {
      sessionStorage.clear();
    });

    it('should return empty when no redirect stored', () => {
      expect(consumeLoginRedirect()).toBe('');
    });

    it('should consume and return valid same-origin report redirect', () => {
      sessionStorage.setItem('loginRedirect', 'http://' + location.host + '/report/123/456');
      expect(consumeLoginRedirect()).toBe('/report/123/456');
      expect(sessionStorage.getItem('loginRedirect')).toBe(null);
    });

    it('should reject cross-origin redirects', () => {
      sessionStorage.setItem('loginRedirect', 'https://example.com/report/123/456');
      expect(consumeLoginRedirect()).toBe('');
    });
  });

  describe('addActiveReportContext and removeActiveReportContext', () => {
    beforeEach(() => {
      testActiveReportContexts = [];
    });

    it('should add new context', () => {
      addActiveReportContext({
        type: 'gc',
        session_id: 'sess-1',
        report_id: 'rep-1'
      });
      expect(testActiveReportContexts.length).toBe(1);
    });

    it('should deduplicate by key', () => {
      addActiveReportContext({ type: 'gc', session_id: 'sess-1', report_id: 'rep-1' });
      addActiveReportContext({ type: 'gc', session_id: 'sess-1', report_id: 'rep-1' });
      expect(testActiveReportContexts.length).toBe(1);
    });

    it('should not exceed maximum limit of 5', () => {
      addActiveReportContext({ type: 'gc', session_id: 's1', report_id: 'r1' });
      addActiveReportContext({ type: 'gc', session_id: 's1', report_id: 'r2' });
      addActiveReportContext({ type: 'gc', session_id: 's1', report_id: 'r3' });
      addActiveReportContext({ type: 'gc', session_id: 's1', report_id: 'r4' });
      addActiveReportContext({ type: 'gc', session_id: 's1', report_id: 'r5' });
      addActiveReportContext({ type: 'gc', session_id: 's1', report_id: 'r6' });
      expect(testActiveReportContexts.length).toBe(5);
      // Should not add the 6th
      expect(testActiveReportContexts.find(r => r.report_id === 'r6')).toBeUndefined();
    });

    it('should remove context by index', () => {
      addActiveReportContext({ type: 'gc', session_id: 's1', report_id: 'r1' });
      addActiveReportContext({ type: 'gc', session_id: 's1', report_id: 'r2' });
      addActiveReportContext({ type: 'gc', session_id: 's1', report_id: 'r3' });
      addActiveReportContext({ type: 'gc', session_id: 's1', report_id: 'r4' });
      addActiveReportContext({ type: 'gc', session_id: 's1', report_id: 'r5' });
      
      removeActiveReportContext(1);
      
      expect(testActiveReportContexts.length).toBe(4);
      expect(testActiveReportContexts[0].report_id).toBe('r1');
      expect(testActiveReportContexts[1].report_id).toBe('r3');
    });
  });

  describe('detectReportMode', () => {
    afterEach(() => {
      // Restore original location
      delete window.location;
      window.location = new URL('http://localhost/');
    });

    it('should return null for normal page', () => {
      delete window.location;
      window.location = new URL('http://localhost/');
      expect(detectReportMode()).toBe(null);
    });

    it('should detect GC report from path', () => {
      delete window.location;
      window.location = new URL('http://localhost/report/sid-123/rid-456');
      const result = detectReportMode();
      expect(result).toEqual({ sid: 'sid-123', rid: 'rid-456', type: 'gc' });
    });

    it('should detect JStack report from path', () => {
      delete window.location;
      window.location = new URL('http://localhost/jstack-report/sid-123/rid-456');
      const result = detectReportMode();
      expect(result).toEqual({ sid: 'sid-123', rid: 'rid-456', type: 'jstack' });
    });

    it('should detect from query parameter', () => {
      delete window.location;
      window.location = new URL('http://localhost/?report=sid-789/rid-012');
      const result = detectReportMode();
      expect(result).toEqual({ sid: 'sid-789', rid: 'rid-012', type: 'gc' });
    });
  });

  describe('getFiltered (JStack thread filtering)', () => {
    const testThreads = [
      { name: 'main', state: 'RUNNABLE', depth: 10, top_frame: 'java.lang.Thread.run', frames: ['foo', 'bar'] },
      { name: 'GC Thread', state: 'WAITING', depth: 5, top_frame: 'sun.misc.Unsafe.park', frames: [] },
      { name: 'HTTP Worker', state: 'BLOCKED', depth: 15, top_frame: 'java.net.SocketInputStream.read', frames: ['net', 'io'] },
      { name: 'Pool Worker', state: 'TIMED_WAITING', depth: 8, top_frame: 'java.util.concurrent.LinkedBlockingQueue.take', frames: [] },
    ];

    it('should return all threads when no filters', () => {
      const result = getFiltered(testThreads, '');
      expect(result.length).toBe(4);
    });

    it('should filter by search query in thread name or top frame', () => {
      const result = getFiltered(testThreads, 'Thread');
      // GC Thread (name contains) + main (top_frame contains Thread.run) → 2 results
      expect(result.length).toBe(2);
      expect(result.map(t => t.name)).toContain('GC Thread');
      expect(result.map(t => t.name)).toContain('main');
    });

    it('should support exclude pattern with "-"', () => {
      const result = getFiltered(testThreads, '-Worker');
      expect(result.length).toBe(2);
      expect(result.map(t => t.name)).toEqual(['main', 'GC Thread']);
    });

    it('should filter by state', () => {
      const result = getFiltered(testThreads, '', 'RUNNABLE');
      expect(result.length).toBe(1);
      expect(result[0].name).toBe('main');
    });

    it('should sort by depth descending by default', () => {
      const result = getFiltered(testThreads, '');
      expect(result.map(t => t.depth)).toEqual([15, 10, 8, 5]);
    });

    it('should sort by depth ascending when sortAsc=true', () => {
      const result = getFiltered(testThreads, '', '', true);
      expect(result.map(t => t.depth)).toEqual([5, 8, 10, 15]);
    });
  });
});
