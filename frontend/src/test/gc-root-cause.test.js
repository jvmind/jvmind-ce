/**
 * Tests for the root-cause oriented GC diagnosis rendering:
 *   - Root cause banner
 *   - Evidence / Symptoms section blocks
 *   - Tiered recommendations with icons + triggered_by
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

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
  <div id="chatArea"></div>
`;

globalThis.marked = { parse: (t) => `<p>${t}</p>` };
globalThis.DOMPurify = { sanitize: (h) => h };
window.marked = globalThis.marked;
window.DOMPurify = globalThis.DOMPurify;

const { renderReport } = await import('../gc-analysis/render.js');
const { calculateGCHealth } = await import('../shared.js');

const RULE_DEFS = {
  throughput_low: { category: 'perf', applies_to: 'all', thresholds: { medium: 0.95, high: 0.90, unit: 'ratio' } },
  reclaim_low: { category: 'oom', applies_to: 'all', thresholds: { avg_reclaim_ratio: 0.05, min_events: 3 } },
  gc_frequency_high: { category: 'perf', applies_to: 'all', thresholds: {} },
  g1_full_gc: { category: 'oom', applies_to: 'G1', thresholds: { n_count_high: 3 } },
};

function makeReport(overrides = {}) {
  return {
    id: 'r1',
    filename: 'test.log',
    size: 1024,
    created_at: '2026-07-25T00:00:00Z',
    ai_conclusion: '',
    stats: {
      collector: 'G1',
      heap_max_mb: 256,
      duration_sec: 60,
      events_total: 100,
      total_pause_ms: 4800,
      throughput: 0.92,
      avg_alloc_rate_mb_s: 1.0,
      avg_heap_usage_pct: 50,
      max_heap_usage_pct: 70,
      events_per_minute: 60,
      frequency_series: [],
      by_category: {
        Young: { count: 95, total_pause_ms: 4800, avg_pause_ms: 50, max_pause_ms: 600,
                 p95_pause_ms: 400, p99_pause_ms: 580, avg_freed_mb: 5, total_freed_mb: 475 },
        Full: { count: 5, total_pause_ms: 0, avg_pause_ms: 0, max_pause_ms: 0,
                p95_pause_ms: 0, p99_pause_ms: 0, avg_freed_mb: 0, total_freed_mb: 0 },
      },
      by_cause: {},
      by_cause_full: {},
      series: [],
      series_total_stw: 100,
      series_sampled_count: 100,
      slowest: [],
      parsed_lines: 100,
      total_lines: 100,
      jdk_version: '11',
      start_epoch_ms: null,
      jvm_args: null,
      diagnosis: {
        leak_risk: 'none',
        oom_risk: 'high',
        collector: 'G1',
        root_cause: {
          category: 'oom',
          label_zh: 'OOM 即将发生',
          label_en: 'OOM imminent',
          summary_zh: '堆已无法回收有效空间',
          summary_en: 'Heap can no longer reclaim space',
        },
        evidence: [
          { rule: 'g1_full_gc', severity: 'high',
            title_zh: 'G1 发生 Full GC', title_en: 'G1 experienced Full GC',
            detail_zh: '107 次 Full GC', detail_en: '107 Full GC events' },
          { rule: 'reclaim_low', severity: 'high',
            title_zh: 'Full GC 回收率极低', title_en: 'Low Full GC reclaim ratio',
            detail_zh: '回收率 1.0%', detail_en: 'Reclaim ratio 1.0%' },
        ],
        symptoms: [
          { rule: 'throughput_low', severity: 'high',
            title_zh: '吞吐率过低', title_en: 'Application throughput only 15.7%',
            detail_zh: '吞吐率 15.7%', detail_en: 'Throughput 15.7%' },
          { rule: 'gc_frequency_high', severity: 'high',
            title_zh: 'Young GC 频率过高', title_en: 'Young GC frequency too high',
            detail_zh: '319 次/分钟', detail_en: '319/min' },
        ],
        recommendations: [
          { tier: 'immediate', action_zh: '立即 jmap-dump',
            action_en: 'Run jmap-dump immediately',
            triggered_by: ['reclaim_low', 'g1_full_gc'] },
          { tier: 'short_term', action_zh: '检查 static 集合',
            action_en: 'Check static collections',
            triggered_by: ['reclaim_low'] },
          { tier: 'tuning', action_zh: '增大 -Xmx',
            action_en: 'Increase -Xmx',
            triggered_by: ['g1_full_gc', 'reclaim_low'] },
        ],
        rule_definitions: RULE_DEFS,
      },
      ...overrides,
    },
  };
}

beforeEach(() => {
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
    <div id="chatArea"></div>
  `;
});

describe('GC diagnosis — root cause banner', () => {
  it('renders the root cause label and title', () => {
    renderReport(makeReport());
    const banner = document.querySelector('.diag-root-cause');
    expect(banner).toBeTruthy();
    expect(banner.classList.contains('rc-category-oom')).toBe(true);
    expect(banner.textContent).toContain('OOM imminent');
    expect(banner.textContent).toContain('Heap can no longer reclaim space');
  });

  it('uses healthy category styling for no-findings reports', () => {
    const report = makeReport();
    report.stats.diagnosis.root_cause.category = 'healthy';
    report.stats.diagnosis.root_cause.label_en = 'No significant issues';
    report.stats.diagnosis.root_cause.summary_en = 'No issues';
    report.stats.diagnosis.evidence = [];
    report.stats.diagnosis.symptoms = [];
    renderReport(report);
    const banner = document.querySelector('.diag-root-cause');
    expect(banner.classList.contains('rc-category-healthy')).toBe(true);
  });
});

describe('GC diagnosis — evidence / symptoms', () => {
  it('renders evidence and symptoms in separate sections', () => {
    renderReport(makeReport());
    const evidenceBlock = document.querySelector('.diag-evidence-block');
    const symptomsBlock = document.querySelector('.diag-symptoms-block');
    expect(evidenceBlock).toBeTruthy();
    expect(symptomsBlock).toBeTruthy();
    expect(evidenceBlock.querySelectorAll('.diag-finding').length).toBe(2);
    expect(symptomsBlock.querySelectorAll('.diag-finding').length).toBe(2);
  });

  it('renders empty-state placeholder when no symptoms present', () => {
    const report = makeReport();
    report.stats.diagnosis.symptoms = [];
    renderReport(report);
    const symptomsBlock = document.querySelector('.diag-symptoms-block');
    // Default test lang is en, but accept zh fallback if i18n missing
    expect(symptomsBlock.textContent).toMatch(/未观察到|symptoms observed/);
  });
});

describe('GC diagnosis — tiered recommendations', () => {
  it('groups recs by tier with distinct styling', () => {
    renderReport(makeReport());
    const tiers = document.querySelectorAll('.diag-rec-tier');
    expect(tiers.length).toBe(3);
    expect(tiers[0].classList.contains('tier-immediate')).toBe(true);
    expect(tiers[1].classList.contains('tier-short-term')).toBe(true);
    expect(tiers[2].classList.contains('tier-tuning')).toBe(true);
  });

  it('renders tier icon SVG for each group', () => {
    renderReport(makeReport());
    const icons = document.querySelectorAll('.diag-rec-tier-icon svg');
    expect(icons.length).toBe(3);
  });

  it('renders triggered_by list under each rec', () => {
    renderReport(makeReport());
    const triggeredEls = document.querySelectorAll('.diag-rec-triggered');
    expect(triggeredEls.length).toBeGreaterThan(0);
    const dumpRec = Array.from(triggeredEls).find(e => e.textContent.includes('reclaim_low'));
    expect(dumpRec).toBeTruthy();
    expect(dumpRec.textContent).toContain('g1_full_gc');
  });

  it('omits the section entirely when no recommendations', () => {
    const report = makeReport();
    report.stats.diagnosis.recommendations = [];
    renderReport(report);
    expect(document.querySelector('.diag-rec-tier')).toBeNull();
  });
});

describe('calculateGCHealth — veryHighHeap semantic consistency', () => {
  function makeStats(overrides) {
    return {
      collector: 'G1',
      throughput: 0.95,
      max_heap_usage_pct: 0,
      by_category: {},
      diagnosis: null,
      ...overrides,
    };
  }

  it('rc=performance with veryHighHeap (98%) returns "bad" (raw stat overrides category cap)', () => {
    // Performance issues + extreme heap usage = imminent crash signal regardless
    // of category. Without this, a 98% heap + ~60% throughput log would show
    // "warn" instead of "bad", misleading the user.
    const stats = makeStats({
      throughput: 0.6,
      max_heap_usage_pct: 98,
      diagnosis: {
        root_cause: { category: 'performance', label_en: 'Performance issue' },
        evidence: [],
        symptoms: [],
      },
    });
    expect(calculateGCHealth(stats)).toBe('bad');
  });

  it('rc=performance with heap 95% returns "warn" (below veryHighHeap threshold)', () => {
    const stats = makeStats({
      throughput: 0.6,
      max_heap_usage_pct: 95,
      diagnosis: {
        root_cause: { category: 'performance', label_en: 'Performance issue' },
        evidence: [],
        symptoms: [],
      },
    });
    expect(calculateGCHealth(stats)).toBe('warn');
  });

  it('rc=performance with heap 50% returns "warn" (no extreme stats)', () => {
    const stats = makeStats({
      throughput: 0.85,
      max_heap_usage_pct: 50,
      diagnosis: {
        root_cause: { category: 'performance', label_en: 'Performance issue' },
        evidence: [],
        symptoms: [],
      },
    });
    expect(calculateGCHealth(stats)).toBe('warn');
  });
});