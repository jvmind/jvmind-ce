import { describe, it, expect, beforeEach, vi } from 'vitest';

// gc-analysis.js binds events at import (analysisFab, gcClose, uploadZone, gcFile,
// mode tabs) and appends a toggle button to uploadZone. Provide the elements.
document.body.innerHTML = `
  <button id="analysisFab"></button>
  <button id="gcClose"></button>
  <div id="gcPanel"></div>
  <div id="uploadZone"></div>
  <input id="gcFile" type="file" />
  <div id="activeReportContext"></div>
  <div id="gcReportTabs"></div>
  <div id="jstackReportTabs"></div>
  <div id="gcReportArea"></div>
  <div id="jstackReportArea"></div>
`;

globalThis.marked = { parse: (t) => `<p>${t}</p>` };
globalThis.DOMPurify = { sanitize: (h) => h };
window.marked = globalThis.marked;
window.DOMPurify = globalThis.DOMPurify;

const gc = await import('./gc-analysis.js');
const { state } = await import('./state.js');
const {
  reportScopeUrl,
  addActiveReportContext,
  removeActiveReportContext,
  clearActiveReportContext,
  removeActiveReportContextByReport,
  bindReportContext,
} = gc;

function resetState() {
  state.activeReportContexts = [];
  state.sessionTab = 'personal';
  state.currentOrg = null;
  state.currentSessionId = null;
}

beforeEach(resetState);

describe('reportScopeUrl', () => {
  it('personal scope by default', () => {
    state.sessionTab = 'personal';
    expect(reportScopeUrl()).toBe('/api/me/reports?personal=true');
  });

  it('org scope includes encoded org id', () => {
    state.sessionTab = 'org';
    state.currentOrg = { id: 'org 1&x' };
    expect(reportScopeUrl()).toBe('/api/me/reports?org_id=org%201%26x');
  });

  it('returns empty string for org scope with no current org', () => {
    state.sessionTab = 'org';
    state.currentOrg = null;
    expect(reportScopeUrl()).toBe('');
  });
});

describe('addActiveReportContext', () => {
  const ctx = (rid) => ({ type: 'gc', session_id: 's1', report_id: rid, filename: rid + '.log' });

  it('adds a context', () => {
    addActiveReportContext(ctx('r1'));
    expect(state.activeReportContexts.length).toBe(1);
  });

  it('dedupes the same type:session:report key (re-adds, no growth)', () => {
    addActiveReportContext(ctx('r1'));
    addActiveReportContext(ctx('r1'));
    expect(state.activeReportContexts.length).toBe(1);
  });

  it('enforces the limit of 5', () => {
    addActiveReportContext(ctx('r1'));
    addActiveReportContext(ctx('r2'));
    addActiveReportContext(ctx('r3'));
    addActiveReportContext(ctx('r4'));
    addActiveReportContext(ctx('r5'));
    addActiveReportContext(ctx('r6'));   // rejected (>= limit)
    expect(state.activeReportContexts.length).toBe(5);
    expect(state.activeReportContexts.map(c => c.report_id)).toEqual(['r1', 'r2', 'r3', 'r4', 'r5']);
  });
});

describe('removeActiveReportContext / clear / byReport', () => {
  const ctx = (rid) => ({ type: 'gc', session_id: 's1', report_id: rid, filename: rid });

  it('removes by index', () => {
    addActiveReportContext(ctx('r1'));
    addActiveReportContext(ctx('r2'));
    removeActiveReportContext(0);
    expect(state.activeReportContexts.map(c => c.report_id)).toEqual(['r2']);
  });

  it('clears all', () => {
    addActiveReportContext(ctx('r1'));
    clearActiveReportContext();
    expect(state.activeReportContexts.length).toBe(0);
  });

  it('removes by report_id', () => {
    addActiveReportContext(ctx('r1'));
    addActiveReportContext(ctx('r2'));
    removeActiveReportContextByReport('r1');
    expect(state.activeReportContexts.map(c => c.report_id)).toEqual(['r2']);
  });
});

describe('bindReportContext', () => {
  it('no-op without a current session', () => {
    state.currentSessionId = null;
    bindReportContext('gc', { id: 'r1' });
    expect(state.activeReportContexts.length).toBe(0);
  });

  it('binds with session id, type and filename', () => {
    state.currentSessionId = 's9';
    bindReportContext('gc', { id: 'r1', filename: 'a.log', file_id: 'fid1' });
    expect(state.activeReportContexts[0]).toMatchObject({
      type: 'gc', session_id: 's9', report_id: 'r1', filename: 'a.log', file_id: 'fid1',
    });
  });
});

describe('renderActiveReportContext (DOM)', () => {
  it('hides the chip when no contexts', () => {
    clearActiveReportContext();
    expect(document.getElementById('activeReportContext').style.display).toBe('none');
  });

  it('escapes filename in the rendered chip (XSS defense)', () => {
    state.currentSessionId = 's1';
    bindReportContext('gc', { id: 'r1', filename: '<img src=x onerror=alert(1)>' });
    const el = document.getElementById('activeReportContext');
    expect(el.querySelector('img')).toBeNull();
    expect(el.innerHTML).toContain('&lt;img');
  });
});
