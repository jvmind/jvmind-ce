// Tests for the GC root-cause diagnosis rendering structure.
// 0.1.12: refactored into 4 sub-sections — 根因 / 证据 / 次生表现 / 建议措施.
import { describe, it, expect } from 'vitest';

const { renderDiagnosisSection } = await import('./render.js');

const T = (k, vars) => (vars ? `${k}::${JSON.stringify(vars)}` : k);

function newDiagnosis() {
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
    ],
    rule_definitions: {},
    oom_candidates: [],
    findings: [],
    recommendations_zh: [],
    recommendations_en: [],
  };
}

describe('renderDiagnosisSection (0.1.12 — 4-section layout)', () => {
  it('renders 根因 / 证据 / 次生表现 / 建议措施 as 4 separate subsections', () => {
    const html = renderDiagnosisSection(newDiagnosis(), T);
    const wrap = document.createElement('div');
    wrap.innerHTML = html;

    // 1. 4 sub-sections in order
    const subs = wrap.querySelectorAll('.diagnosis-section .diag-subsection');
    expect(subs.length).toBe(4);

    const titles = [...subs].map(s => s.querySelector('.diag-subsection-title')?.textContent);
    expect(titles).toEqual([
      'gc.root_cause.label',       // 根因
      'gc.diagnosis_evidence',     // 证据
      'gc.diagnosis_symptoms',     // 次生表现
      'gc.diagnosis_recommendations', // 建议措施
    ]);

    // 2. 根因 uses root-cause-specific classes
    const root = subs[0];
    expect(root.classList.contains('diag-subsection-root')).toBe(true);
    expect(root.classList.contains('rc-category-performance')).toBe(true);
    // Title may be either zh or en depending on active i18n; assert non-empty.
    const rcTitle = root.querySelector('.rc-title')?.textContent || '';
    const rcSummary = root.querySelector('.rc-summary')?.textContent || '';
    expect(rcTitle.length).toBeGreaterThan(0);
    expect(rcSummary.length).toBeGreaterThan(0);

    // 3. 证据 lists only evidence items (2 cards)
    const evidenceCards = subs[1].querySelectorAll('.diag-finding');
    expect(evidenceCards.length).toBe(2);
    expect([...evidenceCards].every(c => c.classList.contains('diag-severity-high'))).toBe(true);

    // 4. 次生表现 lists only symptoms (1 card)
    const symptomCards = subs[2].querySelectorAll('.diag-finding');
    expect(symptomCards.length).toBe(1);
    expect(symptomCards[0].classList.contains('diag-severity-medium')).toBe(true);

    // 5. 建议措施 uses diag-rec-item (tiered)
    const recs = subs[3].querySelectorAll('.diag-rec-item');
    expect(recs.length).toBe(1);
    expect(recs[0].classList.contains('rec-tier-tuning')).toBe(true);
  });

  it('omits empty subsections instead of rendering empty titles', () => {
    const d = newDiagnosis();
    d.symptoms = [];
    d.root_cause = null;
    const html = renderDiagnosisSection(d, T);
    const wrap = document.createElement('div');
    wrap.innerHTML = html;
    const subs = wrap.querySelectorAll('.diag-subsection');
    expect(subs.length).toBe(2); // evidence + recommendations only
    const titles = [...subs].map(s => s.querySelector('.diag-subsection-title')?.textContent);
    expect(titles).toEqual(['gc.diagnosis_evidence', 'gc.diagnosis_recommendations']);
  });

  it('falls back to legacy findings + recommendations_zh when evidence/symptoms missing', () => {
    const d = {
      findings: [
        { rule: 'legacy', severity: 'high',
          title_zh: 'legacy 旧规则', title_en: 'legacy rule',
          detail_zh: '旧细节', detail_en: 'legacy detail' },
      ],
      recommendations_zh: ['旧建议'],
    };
    const html = renderDiagnosisSection(d, T);
    const wrap = document.createElement('div');
    wrap.innerHTML = html;
    const subs = wrap.querySelectorAll('.diag-subsection');
    // 证据 (fallback to findings) + 建议措施 (fallback to recommendations_zh)
    expect(subs.length).toBe(2);
    expect(subs[0].querySelector('.diag-subsection-title')?.textContent).toBe('gc.diagnosis_evidence');
    expect(subs[0].querySelectorAll('.diag-finding').length).toBe(1);
    expect(subs[1].querySelector('.diag-subsection-title')?.textContent).toBe('gc.diagnosis_recommendations');
    expect(subs[1].querySelector('.diag-rec-item')?.textContent).toBe('旧建议');
  });

  it('returns empty string when diagnosis is missing', () => {
    expect(renderDiagnosisSection(null, T)).toBe('');
    expect(renderDiagnosisSection(undefined, T)).toBe('');
  });

  it('returns empty string when diagnosis has no usable data', () => {
    expect(renderDiagnosisSection({ findings: [] }, T)).toBe('');
  });
});
