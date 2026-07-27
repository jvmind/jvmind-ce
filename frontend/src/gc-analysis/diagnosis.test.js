// Tests for the GC root-cause diagnosis rendering structure.
// 0.1.13: aligned with react_agent_project (SaaS) — 5 sections:
//   1. 根因 box (.diag-root-cause) with category color
//   2. 证据 section (.diag-section-block.diag-evidence-block)
//   3. 次生表现 section (with empty placeholder when no symptoms)
//   4. 建议措施 section with 4-tier grouping (immediate/short_term/tuning/profiling)
//   5. Rules reference panel (collapsed by default)
import { describe, it, expect, beforeAll } from 'vitest';

const { renderRulesReference, renderReport } = await import('./render.js');
const { state } = await import('../state.js');
const { escapeHtml } = await import('../shared.js');
const { t, getLang, setLang } = await import('../../i18n/index.js');

// Pin language to 'zh' for deterministic string assertions.
beforeAll(() => setLang('zh'));

function newDiagnosis(overrides = {}) {
  return {
    leak_risk: "none",
    oom_risk: "none",
    collector: "CMS",
    root_cause: {
      category: "performance",
      label_zh: "性能问题",
      label_en: "Performance issue",
      summary_zh: "GC 暂停时间/频率过高, 应用可用时间被吞噬",
      summary_en: "GC pause time / frequency too high",
    },
    evidence: [
      { rule: "throughput_low", severity: "high",
        title_zh: "应用吞吐率仅 33.3%", title_en: "Throughput 33.3%",
        detail_zh: "低于 90% 阈值", detail_en: "below 90% threshold" },
      { rule: "stw_time_ratio_high", severity: "high",
        title_zh: "GC 暂停时间占比 66.7%", title_en: "Pause ratio 66.7%",
        detail_zh: "超过 10% 阈值", detail_en: "above 10% threshold" },
    ],
    symptoms: [
      { rule: "latency_p99_high", severity: "medium",
        title_zh: "p99 延迟升高", title_en: "p99 latency up",
        detail_zh: "受 GC 暂停拖累", detail_en: "dragged by GC pauses" },
    ],
    recommendations: [
      { tier: "tuning",
        action_zh: "调整 -XX:CMSMarkStackSize", action_en: "Tune CMSMarkStackSize",
        triggered_by: ["cms_remark_too_long"] },
      { tier: "profiling",
        action_zh: "分析分配热点", action_en: "Profile allocation hotspots",
        triggered_by: ["throughput_low"] },
    ],
    rule_definitions: {
      throughput_low: { category: "performance", thresholds: { medium: 0.9, high: 0.8, unit: "ratio" } },
      stw_time_ratio_high: { category: "performance", thresholds: { medium: 0.05, high: 0.1, unit: "ratio" } },
    },
    findings: [],
    recommendations_zh: [],
    recommendations_en: [],
    ...overrides,
  };
}

function wrap(html) {
  const div = document.createElement('div');
  div.innerHTML = html;
  return div;
}

describe('renderRulesReference (SaaS panel)', () => {
  it('groups rules by CATEGORY_ORDER (performance / leak / oom)', () => {
    const html = renderRulesReference({
      throughput_low: { category: "performance", thresholds: { medium: 0.9, high: 0.8, unit: "ratio" } },
      reclaim_low: { category: "leak", thresholds: { avg_reclaim_ratio: 0.05 } },
      g1_full_gc: { category: "oom", thresholds: { n_count_high: 1 } },
    }, getLang());
    const w = wrap(html);
    const cats = w.querySelectorAll('.diag-rules-category');
    expect(cats.length).toBe(3);
    const titles = [...cats].map(c => c.querySelector('.diag-rules-category-title')?.textContent);
    expect(titles[0]).toContain('通用 - 性能');
    expect(titles[1]).toContain('通用 - 泄漏');
    expect(titles[2]).toContain('通用 - OOM');
  });

  it('is collapsed by default (body display:none)', () => {
    const html = renderRulesReference({ throughput_low: { category: "performance" } }, getLang());
    const w = wrap(html);
    const body = w.querySelector('.diag-rules-ref-body');
    expect(body).not.toBeNull();
    expect(body.style.display).toBe('none');
    const header = w.querySelector('.diag-rules-ref-header');
    expect(header.getAttribute('data-act')).toBe('toggle-rules-ref');
    expect(w.querySelector('.diag-rules-ref-chevron').textContent).toBe('▶');
  });

  it('renders rule id pill, name, applies label, description, and thresholds', () => {
    const html = renderRulesReference({
      throughput_low: {
        category: "performance",
        thresholds: { medium: 0.9, high: 0.8, unit: "ratio" },
        applies_to: "all",
      },
    }, getLang());
    const w = wrap(html);
    const item = w.querySelector('.diag-rules-item');
    expect(item).not.toBeNull();
    expect(item.querySelector('.diag-rule-id')?.textContent).toBe('throughput_low');
    expect(item.querySelector('.diag-rules-item-name')?.textContent.length).toBeGreaterThan(0);
    expect(item.querySelector('.diag-rules-item-applies')?.textContent).toContain('适用所有收集器');
    expect(item.querySelector('.diag-rules-item-desc')?.textContent.length).toBeGreaterThan(0);
    expect(item.querySelector('.diag-rules-item-thresholds')?.textContent).toContain('%');
  });
});

describe('renderReport — memory diagnosis (0.1.13 SaaS-aligned)', () => {
  // Minimal DOM scaffold needed by renderReport
  function setupDom() {
    document.body.innerHTML = `
      <div id="gcReportArea"></div>
      <table><tbody id="slowestTbody"></tbody></table>
      <div id="aiSection"></div>
    `;
    state.currentSessionId = "s1";
  }

  it('renders 根因 box with category color class', () => {
    setupDom();
    renderReport({
      id: "r1", filename: "x.log", size: 100, created_at: "2026-07-27",
      ai_conclusion: "",
      stats: {
        collector: "CMS", events_total: 5, total_pause_ms: 100, duration_sec: 10,
        throughput: 0.5, heap_max_mb: 512, avg_alloc_rate_mb_s: 1,
        events_per_minute: 5, avg_heap_usage_pct: 80, max_heap_usage_pct: 90,
        by_category: {}, slowest: [], series: [], diagnosis: newDiagnosis(),
      },
    });
    const area = document.getElementById("gcReportArea");
    const rc = area.querySelector('.diag-root-cause');
    expect(rc).not.toBeNull();
    expect(rc.classList.contains('rc-category-performance')).toBe(true);
    expect(rc.querySelector('.rc-label')?.textContent).toBe('根因分析');
    expect(rc.querySelector('.rc-title')?.textContent.length).toBeGreaterThan(0);
  });

  it('renders 证据 / 次生表现 / 建议措施 / 规则说明 as 4 separate section blocks', () => {
    setupDom();
    renderReport({
      id: "r1", filename: "x.log", size: 100, created_at: "2026-07-27",
      ai_conclusion: "",
      stats: {
        collector: "CMS", events_total: 5, total_pause_ms: 100, duration_sec: 10,
        throughput: 0.5, heap_max_mb: 512, avg_alloc_rate_mb_s: 1,
        events_per_minute: 5, avg_heap_usage_pct: 80, max_heap_usage_pct: 90,
        by_category: {}, slowest: [], series: [], diagnosis: newDiagnosis(),
      },
    });
    const area = document.getElementById("gcReportArea");
    const blocks = area.querySelectorAll('.diag-section-block');
    // 3 section-blocks: evidence / symptoms / recs
    expect(blocks.length).toBe(3);
    const blockClasses = [...blocks].map(b => b.className).join(' ');
    expect(blockClasses).toContain('diag-evidence-block');
    expect(blockClasses).toContain('diag-symptoms-block');
    expect(blockClasses).toContain('diag-recs-block');
    // 4th element is the rules reference panel (not a section-block)
    expect(area.querySelector('.diag-rules-reference')).not.toBeNull();
  });

  it('finding cards show severity tag + rule_id pill (when rule present)', () => {
    setupDom();
    renderReport({
      id: "r1", filename: "x.log", size: 100, created_at: "2026-07-27",
      ai_conclusion: "",
      stats: {
        collector: "CMS", events_total: 5, total_pause_ms: 100, duration_sec: 10,
        throughput: 0.5, heap_max_mb: 512, avg_alloc_rate_mb_s: 1,
        events_per_minute: 5, avg_heap_usage_pct: 80, max_heap_usage_pct: 90,
        by_category: {}, slowest: [], series: [], diagnosis: newDiagnosis(),
      },
    });
    const area = document.getElementById("gcReportArea");
    const findings = area.querySelectorAll('.diag-section-block.diag-evidence-block .diag-finding');
    expect(findings.length).toBe(2);
    expect(findings[0].querySelector('.diag-severity-tag')?.textContent.length).toBeGreaterThan(0);
    expect(findings[0].querySelector('.diag-rule-id')?.textContent).toBe('throughput_low');
  });

  it('groups recommendations by tier (immediate / short_term / tuning / profiling)', () => {
    setupDom();
    renderReport({
      id: "r1", filename: "x.log", size: 100, created_at: "2026-07-27",
      ai_conclusion: "",
      stats: {
        collector: "CMS", events_total: 5, total_pause_ms: 100, duration_sec: 10,
        throughput: 0.5, heap_max_mb: 512, avg_alloc_rate_mb_s: 1,
        events_per_minute: 5, avg_heap_usage_pct: 80, max_heap_usage_pct: 90,
        by_category: {}, slowest: [], series: [], diagnosis: newDiagnosis(),
      },
    });
    const area = document.getElementById("gcReportArea");
    const tiers = area.querySelectorAll('.diag-rec-tier');
    expect(tiers.length).toBe(2);
    const tierClasses = [...tiers].map(t => t.className).join(' ');
    expect(tierClasses).toContain('tier-tuning');
    expect(tierClasses).toContain('tier-profiling');
    // Each tier has an icon
    expect(tiers[0].querySelector('.diag-rec-tier-icon')).not.toBeNull();
    // triggered_by label present (rendered as "触发于:" with trailing colon)
    expect(area.querySelector('.diag-rec-triggered-label')?.textContent).toContain('触发于');
  });

  it('renders symptoms placeholder when symptoms list is empty', () => {
    setupDom();
    renderReport({
      id: "r1", filename: "x.log", size: 100, created_at: "2026-07-27",
      ai_conclusion: "",
      stats: {
        collector: "CMS", events_total: 5, total_pause_ms: 100, duration_sec: 10,
        throughput: 0.5, heap_max_mb: 512, avg_alloc_rate_mb_s: 1,
        events_per_minute: 5, avg_heap_usage_pct: 80, max_heap_usage_pct: 90,
        by_category: {}, slowest: [], series: [],
        diagnosis: newDiagnosis({ symptoms: [] }),
      },
    });
    const area = document.getElementById("gcReportArea");
    const symptomsBlock = area.querySelector('.diag-section-block.diag-symptoms-block');
    expect(symptomsBlock).not.toBeNull();
    expect(symptomsBlock.querySelector('.diag-empty')?.textContent.length).toBeGreaterThan(0);
  });

  it('renders recs placeholder when recommendations list is empty', () => {
    setupDom();
    renderReport({
      id: "r1", filename: "x.log", size: 100, created_at: "2026-07-27",
      ai_conclusion: "",
      stats: {
        collector: "CMS", events_total: 5, total_pause_ms: 100, duration_sec: 10,
        throughput: 0.5, heap_max_mb: 512, avg_alloc_rate_mb_s: 1,
        events_per_minute: 5, avg_heap_usage_pct: 80, max_heap_usage_pct: 90,
        by_category: {}, slowest: [], series: [],
        diagnosis: newDiagnosis({ recommendations: [] }),
      },
    });
    const area = document.getElementById("gcReportArea");
    const recsBlock = area.querySelector('.diag-section-block.diag-recs-block');
    expect(recsBlock).not.toBeNull();
    expect(recsBlock.querySelector('.diag-empty')?.textContent.length).toBeGreaterThan(0);
  });

  it('returns empty diagnosis-section when diagnosis is missing', () => {
    setupDom();
    renderReport({
      id: "r1", filename: "x.log", size: 100, created_at: "2026-07-27",
      ai_conclusion: "",
      stats: {
        collector: "CMS", events_total: 5, total_pause_ms: 100, duration_sec: 10,
        throughput: 0.5, heap_max_mb: 512, avg_alloc_rate_mb_s: 1,
        events_per_minute: 5, avg_heap_usage_pct: 80, max_heap_usage_pct: 90,
        by_category: {}, slowest: [], series: [], diagnosis: null,
      },
    });
    const area = document.getElementById("gcReportArea");
    expect(area.querySelector('.diagnosis-section')).toBeNull();
  });
});
