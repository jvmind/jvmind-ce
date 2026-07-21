import { describe, it, expect, beforeEach } from 'vitest';

// Mirrors the report-tab logic in gc-analysis.js (openReport / closeReportTab /
// limit eviction / browse-vs-attach decoupling). The repo convention is to
// copy pure logic for unit tests since gc-analysis.js binds many DOM elements
// at module load time.

const OPEN_REPORT_LIMIT = 8;

function makeState() {
  return {
    openGcReports: [],
    openJstackReports: [],
    currentReport: null,
    currentReportId: null,
    currentJstackReport: null,
    currentJstackReportId: null,
    activeReportContexts: [],
  };
}

function listFor(state, type) {
  return type === "jstack" ? state.openJstackReports : state.openGcReports;
}
function activeIdFor(state, type) {
  return type === "jstack" ? state.currentJstackReportId : state.currentReportId;
}
function bindReportContext(state, type, report) {
  const key = `${type}:${report.id}`;
  if (state.activeReportContexts.some(c => `${c.type}:${c.report_id}` === key)) return;
  if (state.activeReportContexts.length >= 5) return;
  state.activeReportContexts.push({ type, report_id: report.id });
}

function openReport(state, type, report, { attach = false, dontTrack = false } = {}) {
  if (!report || !report.id) return;
  const list = listFor(state, type);
  const activeId = activeIdFor(state, type);
  const idx = list.findIndex(r => r.id === report.id);
  if (!dontTrack) {
    if (idx >= 0) {
      list[idx] = { id: report.id, filename: report.filename || "", report };
    } else {
      if (list.length >= OPEN_REPORT_LIMIT) {
        const victim = list.findIndex(r => r.id !== activeId);
        if (victim >= 0) list.splice(victim, 1);
      }
      list.push({ id: report.id, filename: report.filename || "", report });
    }
  }
  if (type === "jstack") { state.currentJstackReport = report; state.currentJstackReportId = report.id; }
  else { state.currentReport = report; state.currentReportId = report.id; }
  if (attach) bindReportContext(state, type, report);
}

function closeReportTab(state, type, id) {
  const list = listFor(state, type);
  const idx = list.findIndex(r => r.id === id);
  if (idx < 0) return;
  const wasActive = activeIdFor(state, type) === id;
  list.splice(idx, 1);
  if (!wasActive) return;
  const next = list[idx] || list[idx - 1] || null;
  if (next) {
    if (type === "jstack") { state.currentJstackReport = next.report; state.currentJstackReportId = next.id; }
    else { state.currentReport = next.report; state.currentReportId = next.id; }
  } else {
    if (type === "jstack") { state.currentJstackReport = null; state.currentJstackReportId = null; }
    else { state.currentReport = null; state.currentReportId = null; }
  }
}

const rep = (id) => ({ id, filename: id + '.log', stats: {} });

describe('report tabs logic', () => {
  let state;
  beforeEach(() => { state = makeState(); });

  it('opens a report and sets it active', () => {
    openReport(state, 'gc', rep('r1'));
    expect(state.openGcReports.length).toBe(1);
    expect(state.currentReportId).toBe('r1');
  });

  it('dedupes by id without growing the list', () => {
    openReport(state, 'gc', rep('r1'));
    openReport(state, 'gc', rep('r1'));
    expect(state.openGcReports.length).toBe(1);
  });

  it('evicts the earliest non-active tab beyond the limit of 8', () => {
    for (let i = 1; i <= 8; i++) openReport(state, 'gc', rep('r' + i));
    expect(state.openGcReports.length).toBe(8);
    expect(state.currentReportId).toBe('r8');
    openReport(state, 'gc', rep('r9'));
    expect(state.openGcReports.length).toBe(8);
    // r1 (earliest non-active) evicted, active r8 kept, r9 added
    expect(state.openGcReports.find(r => r.id === 'r1')).toBeUndefined();
    expect(state.openGcReports.find(r => r.id === 'r8')).toBeDefined();
    expect(state.openGcReports.find(r => r.id === 'r9')).toBeDefined();
  });

  it('gc and jstack maintain separate tab lists', () => {
    openReport(state, 'gc', rep('g1'));
    openReport(state, 'jstack', rep('j1'));
    expect(state.openGcReports.length).toBe(1);
    expect(state.openJstackReports.length).toBe(1);
  });

  it('dontTrack: true sets current without adding to session list', () => {
    openReport(state, 'gc', rep('r1'), { dontTrack: true });
    expect(state.openGcReports.length).toBe(0);
    expect(state.currentReportId).toBe('r1');
  });

  it('dontTrack: true lets history clicks stay in history (no list move)', () => {
    openReport(state, 'gc', rep('r1'), { dontTrack: true });
    openReport(state, 'gc', rep('r2'), { dontTrack: true });
    openReport(state, 'gc', rep('r3'), { dontTrack: true });
    expect(state.openGcReports.length).toBe(0);
    expect(state.currentReportId).toBe('r3');
  });

  it('browsing (attach=false) does NOT change context', () => {
    openReport(state, 'gc', rep('r1'));
    openReport(state, 'gc', rep('r2'));
    expect(state.activeReportContexts.length).toBe(0);
  });

  it('attach=true adds to context; upload semantics', () => {
    openReport(state, 'gc', rep('r1'), { attach: true });
    expect(state.activeReportContexts.length).toBe(1);
    expect(state.activeReportContexts[0].report_id).toBe('r1');
  });

  it('attach respects the 5-slot context limit', () => {
    openReport(state, 'gc', rep('r1'), { attach: true });
    openReport(state, 'gc', rep('r2'), { attach: true });
    openReport(state, 'gc', rep('r3'), { attach: true });
    openReport(state, 'gc', rep('r4'), { attach: true });
    openReport(state, 'gc', rep('r5'), { attach: true });
    openReport(state, 'gc', rep('r6'), { attach: true });
    expect(state.activeReportContexts.length).toBe(5);
  });

  it('closing the active tab activates a neighbor', () => {
    openReport(state, 'gc', rep('r1'));
    openReport(state, 'gc', rep('r2'));
    openReport(state, 'gc', rep('r3'));
    // active is r3; close r3 -> should fall back to r2
    closeReportTab(state, 'gc', 'r3');
    expect(state.currentReportId).toBe('r2');
    expect(state.openGcReports.length).toBe(2);
  });

  it('closing the last tab clears the active report', () => {
    openReport(state, 'gc', rep('r1'));
    closeReportTab(state, 'gc', 'r1');
    expect(state.openGcReports.length).toBe(0);
    expect(state.currentReport).toBe(null);
    expect(state.currentReportId).toBe(null);
  });

  it('closing a non-active tab keeps the active one', () => {
    openReport(state, 'gc', rep('r1'));
    openReport(state, 'gc', rep('r2'));
    closeReportTab(state, 'gc', 'r1');
    expect(state.currentReportId).toBe('r2');
    expect(state.openGcReports.length).toBe(1);
  });
});
