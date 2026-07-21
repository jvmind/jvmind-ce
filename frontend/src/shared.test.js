import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  getCookie,
  csrfHeaders,
  escapeHtml,
  formatN,
  formatTime,
  i18nText,
  formatEpoch,
  fmtDateOnly,
  parseServerTime,
  getPendingInvite,
  consumePendingInvite,
} from './shared.js';
import { setLang } from '../i18n/index.js';

describe('getCookie', () => {
  it('should return empty string when cookie not found', () => {
    document.cookie = '';
    expect(getCookie('csrf_token')).toBe('');
  });
});

describe('csrfHeaders', () => {
  it('should return original headers when no csrf cookie', () => {
    document.cookie = '';
    const headers = { 'Content-Type': 'application/json' };
    expect(csrfHeaders(headers)).toEqual(headers);
  });
});

describe('escapeHtml', () => {
  it('should escape special HTML characters', () => {
    expect(escapeHtml('<div class="test">')).toBe('&lt;div class=&quot;test&quot;&gt;');
    expect(escapeHtml('&')).toBe('&amp;');
    expect(escapeHtml('\'')).toBe('&#39;');
    expect(escapeHtml('')).toBe('');
    expect(escapeHtml(null)).toBe('');
    expect(escapeHtml(123)).toBe('123');
  });

  it('should handle mixed content', () => {
    const input = 'Hello <World> & "test"';
    const expected = 'Hello &lt;World&gt; &amp; &quot;test&quot;';
    expect(escapeHtml(input)).toBe(expected);
  });
});

describe('formatN', () => {
  it('should format numbers correctly', () => {
    expect(formatN(1500)).toBe('1.5k');
    expect(formatN(150)).toBe('150');       // >= 100 → 0 decimals
    expect(formatN(100.0)).toBe('100');     // >= 100 → 0 decimals
    expect(formatN(99.9)).toBe('99.9');     // 99.9 < 100 && >= 10 → 1 decimals
    expect(formatN(50)).toBe('50.0');       // 50 >= 10 && < 100 → 1 decimals
    expect(formatN(15.5)).toBe('15.5');     // >= 10 && < 100 → 1 decimals
    expect(formatN(1.234)).toBe('1.23');    // < 10 → 2 decimals
    expect(formatN(999)).toBe('999');       // < 1000 && >= 100 → 0 decimals
    expect(formatN(9.99)).toBe('9.99');     // < 10 → 2 decimals
    expect(formatN(9.999)).toBe('10.00');   // < 10 → 2 decimals → 10.00
  });
});

describe('formatTime', () => {
  it('should format time correctly', () => {
    expect(formatTime(3600)).toBe('1.00h');
    expect(formatTime(7200)).toBe('2.00h');
    expect(formatTime(120)).toBe('2.00m');
    expect(formatTime(90.5)).toBe('1.51m');
    expect(formatTime(59)).toBe('59.0s');
    expect(formatTime(9.567)).toBe('9.57s');
    expect(formatTime(1.234)).toBe('1.23s');
    expect(formatTime(0)).toBe('0.00s');
  });
});

describe('isValidEmail', () => {
  it('should return true for valid emails', () => {
    const { isValidEmail } = require('./shared.js');
    expect(isValidEmail('test@example.com')).toBe(true);
    expect(isValidEmail('user.name+tag@domain.co.uk')).toBe(true);
    expect(isValidEmail('  alice@bob.org  ')).toBe(true);
  });

  it('should return false for invalid emails', () => {
    const { isValidEmail } = require('./shared.js');
    expect(isValidEmail('')).toBe(false);
    expect(isValidEmail('not-an-email')).toBe(false);
    expect(isValidEmail('missing@domain')).toBe(false);
    expect(isValidEmail('@missing-local.com')).toBe(false);
  });
});

describe('money', () => {
  it('should format cents to correct currency string', () => {
    const { money } = require('./shared.js');
    expect(money(1000)).toBe('USD 10.00');
    expect(money(500, 'CNY')).toBe('CNY 5.00');
    expect(money(1234)).toBe('USD 12.34');
    expect(money(0)).toBe('USD 0.00');
    expect(money(null)).toBe('USD 0.00');
  });
});

describe('fmtDate', () => {
  it('should return "-" for empty input', () => {
    const { fmtDate } = require('./shared.js');
    expect(fmtDate('')).toBe('-');
    expect(fmtDate(null)).toBe('-');
  });

  it('should format ISO date correctly', () => {
    const { fmtDate } = require('./shared.js');
    // Backend stores UTC; fmtDate renders in local tz (TZ=Asia/Shanghai, UTC+8).
    expect(fmtDate('2024-06-17T12:34:56.123Z')).toBe('2024-06-17 20:34');
    // Marker-less strings are interpreted as UTC (backend storage convention).
    expect(fmtDate('2024-06-17 12:34:56')).toBe('2024-06-17 20:34');
    expect(fmtDate('2024-06-17T12:34:56')).toBe('2024-06-17 20:34');
  });
});

describe('fmtDateOnly', () => {
  it('returns empty string for falsy input', () => {
    expect(fmtDateOnly('')).toBe('');
    expect(fmtDateOnly(null)).toBe('');
  });

  it('renders UTC timestamp as local date (UTC+8 may roll to next day)', () => {
    // 2024-06-17 20:00 UTC → 2024-06-18 04:00 local (Shanghai).
    expect(fmtDateOnly('2024-06-17 20:00:00')).toBe('2024-06-18');
    expect(fmtDateOnly('2024-06-17 00:00:00')).toBe('2024-06-17');
  });

  it('handles date-only input as midnight UTC', () => {
    // 2024-06-17 00:00 UTC → 2024-06-17 08:00 local → same date.
    expect(fmtDateOnly('2024-06-17')).toBe('2024-06-17');
  });
});

describe('parseServerTime', () => {
  it('interprets marker-less strings as UTC', () => {
    const d = parseServerTime('2024-06-17 12:34:56');
    expect(d.getTime()).toBe(Date.UTC(2024, 5, 17, 12, 34, 56));
  });

  it('honors explicit Z / offset markers', () => {
    expect(parseServerTime('2024-06-17T12:34:56Z').getTime())
      .toBe(Date.UTC(2024, 5, 17, 12, 34, 56));
  });

  it('returns null for empty or invalid input', () => {
    expect(parseServerTime('')).toBe(null);
    expect(parseServerTime(null)).toBe(null);
    expect(parseServerTime('not-a-date')).toBe(null);
  });
});

describe('parseSSE', () => {
  const { parseSSE } = require('./shared.js');

  it('should return null when no data', () => {
    expect(parseSSE('event: message\n')).toBe(null);
    expect(parseSSE('')).toBe(null);
  });

  it('should parse simple data message', () => {
    const result = parseSSE('data: {"type":"token","content":"hello"}');
    expect(result).not.toBe(null);
    expect(result.event).toBe('message');
    expect(result.data.type).toBe('token');
    expect(result.data.content).toBe('hello');
  });

  it('should parse custom event', () => {
    const result = parseSSE('event: done\ndata: {"finished":true}');
    expect(result.event).toBe('done');
    expect(result.data.finished).toBe(true);
  });

  it('should return null for invalid JSON', () => {
    const result = parseSSE('data: { not valid json }');
    expect(result).toBe(null);
  });

  it('should handle multi-line data', () => {
    const result = parseSSE('data: {"type":\ndata: "token",\ndata: "content": "multi"}');
    expect(result.data.type).toBe('token');
    expect(result.data.content).toBe('multi');
  });
});

describe('calculateGCHealth', () => {
  const { calculateGCHealth } = require('./shared.js');

  it('should return "good" when no issues', () => {
    const stats = {
      by_category: {},
      throughput: 1.0,
      max_heap_usage_pct: 90,
    };
    expect(calculateGCHealth(stats)).toBe('good');
  });

  it('should return "bad" when more than 3 Full GC', () => {
    const stats = {
      by_category: { Full: { count: 4 } },
      throughput: 1.0,
      max_heap_usage_pct: 90,
    };
    expect(calculateGCHealth(stats)).toBe('bad');
  });

  it('should return "warn" when has Full GC but <= 3', () => {
    const stats = {
      by_category: { Full: { count: 2 } },
      throughput: 1.0,
      max_heap_usage_pct: 90,
    };
    expect(calculateGCHealth(stats)).toBe('warn');
  });

  it('should return "warn" when heap usage > 95%', () => {
    const stats = {
      by_category: {},
      throughput: 1.0,
      max_heap_usage_pct: 96,
    };
    expect(calculateGCHealth(stats)).toBe('warn');
  });

  it('should return "warn" when throughput < 0.9', () => {
    const stats = {
      by_category: {},
      throughput: 0.8,
      max_heap_usage_pct: 90,
    };
    expect(calculateGCHealth(stats)).toBe('warn');
  });

  it('should return "caution" when max pause > 200ms', () => {
    const stats = {
      by_category: { Young: { max_pause_ms: 250 } },
      throughput: 1.0,
      max_heap_usage_pct: 90,
    };
    expect(calculateGCHealth(stats)).toBe('caution');
  });

  it('should handle empty stats', () => {
    expect(calculateGCHealth({})).toBe('good');
  });

  it('ParallelGC: many Full GC events with fast pause is healthy (not bad)', () => {
    const stats = {
      collector: 'Parallel',
      by_category: { Full: { count: 10, max_pause_ms: 800 } },
      throughput: 0.97,
      max_heap_usage_pct: 70,
    };
    expect(calculateGCHealth(stats)).toBe('good');
  });

  it('ParallelGC: slow Full GC (>1s) escalates to warn', () => {
    const stats = {
      collector: 'Parallel',
      by_category: { Full: { count: 3, max_pause_ms: 1500 } },
      throughput: 0.97,
      max_heap_usage_pct: 70,
    };
    expect(calculateGCHealth(stats)).toBe('warn');
  });

  it('ParallelGC: very slow Full GC (>2s) escalates to bad', () => {
    const stats = {
      collector: 'Parallel',
      by_category: { Full: { count: 2, max_pause_ms: 3000 } },
      throughput: 0.97,
      max_heap_usage_pct: 70,
    };
    expect(calculateGCHealth(stats)).toBe('bad');
  });

  it('ParallelGC: diagnosis leak_risk=high still escalates to bad', () => {
    const stats = {
      collector: 'Parallel',
      by_category: { Full: { count: 3, max_pause_ms: 500 } },
      throughput: 0.97,
      max_heap_usage_pct: 70,
      diagnosis: { leak_risk: 'high' },
    };
    expect(calculateGCHealth(stats)).toBe('bad');
  });

  it('G1: any Full GC still escalates (not collector-aware relaxation)', () => {
    const stats = {
      collector: 'G1',
      by_category: { Full: { count: 2, max_pause_ms: 200 } },
      throughput: 0.97,
      max_heap_usage_pct: 70,
    };
    expect(calculateGCHealth(stats)).toBe('warn');
  });
});

describe('detectReportMode', () => {
  const { detectReportMode } = require('./shared.js');

  it('should parse GC report from path', () => {
    delete window.location;
    window.location = new URL('http://example.com/report/session123/report456');
    const result = detectReportMode();
    expect(result).toEqual({ sid: 'session123', rid: 'report456', type: 'gc' });
  });

  it('should parse jstack report from path', () => {
    delete window.location;
    window.location = new URL('http://example.com/jstack-report/session123/report456');
    const result = detectReportMode();
    expect(result).toEqual({ sid: 'session123', rid: 'report456', type: 'jstack' });
  });

  it('should parse GC report from query param', () => {
    delete window.location;
    window.location = new URL('http://example.com/?report=session123/report456');
    const result = detectReportMode();
    expect(result).toEqual({ sid: 'session123', rid: 'report456', type: 'gc' });
  });

  it('should return null when no report detected', () => {
    delete window.location;
    window.location = new URL('http://example.com/orgs');
    const result = detectReportMode();
    expect(result).toBeNull();
  });
});

describe('getSelectedReports', () => {
  const { getSelectedReports } = require('./shared.js');

  it('should return empty array when no selected reports', () => {
    const container = document.createElement('div');
    expect(getSelectedReports(container)).toEqual([]);
  });

  it('should return array of selected reports', () => {
    const container = document.createElement('div');
    const cb1 = document.createElement('input');
    cb1.type = 'checkbox';
    cb1.className = 'report-select';
    cb1.dataset.id = '1';
    cb1.dataset.type = 'gc';
    cb1.dataset.session = 's1';
    cb1.checked = true;

    const cb2 = document.createElement('input');
    cb2.type = 'checkbox';
    cb2.className = 'report-select';
    cb2.dataset.id = '2';
    cb2.dataset.type = 'jstack';
    cb2.dataset.session = 's2';
    cb2.checked = false;

    const cb3 = document.createElement('input');
    cb3.type = 'checkbox';
    cb3.className = 'report-select';
    cb3.dataset.id = '3';
    cb3.dataset.type = 'gc';
    cb3.dataset.session = 's3';
    cb3.checked = true;

    container.appendChild(cb1);
    container.appendChild(cb2);
    container.appendChild(cb3);

    const result = getSelectedReports(container);
    expect(result).toEqual([
      { id: '1', type: 'gc', sessionId: 's1' },
      { id: '3', type: 'gc', sessionId: 's3' },
    ]);
  });
});

describe('updateReportBulkBar', () => {
  const { updateReportBulkBar } = require('./shared.js');

  it('should update bulk bar with 0 selected', () => {
    const container = document.createElement('div');
    const count = document.createElement('span');
    count.className = 'report-selected-count';
    const bulkDelete = document.createElement('button');
    bulkDelete.className = 'bulk-delete';
    const selectAll = document.createElement('input');
    selectAll.className = 'report-select-all';
    selectAll.type = 'checkbox';

    container.appendChild(count);
    container.appendChild(bulkDelete);
    container.appendChild(selectAll);

    updateReportBulkBar(container);

    expect(count.textContent).toBe('0 selected');
    expect(bulkDelete.disabled).toBe(true);
    expect(selectAll.checked).toBe(false);
    expect(selectAll.indeterminate).toBe(false);
  });

  it('should update bulk bar when some selected', () => {
    const container = document.createElement('div');
    const count = document.createElement('span');
    count.className = 'report-selected-count';
    const bulkDelete = document.createElement('button');
    bulkDelete.className = 'bulk-delete';
    const selectAll = document.createElement('input');
    selectAll.className = 'report-select-all';
    selectAll.type = 'checkbox';

    const cb1 = document.createElement('input');
    cb1.type = 'checkbox';
    cb1.className = 'report-select';
    cb1.dataset.id = '1';
    cb1.checked = true;

    const cb2 = document.createElement('input');
    cb2.type = 'checkbox';
    cb2.className = 'report-select';
    cb2.dataset.id = '2';
    cb2.checked = false;

    container.appendChild(cb1);
    container.appendChild(cb2);
    container.appendChild(count);
    container.appendChild(bulkDelete);
    container.appendChild(selectAll);

    updateReportBulkBar(container);

    expect(count.textContent).toBe('1 selected');
    expect(bulkDelete.disabled).toBe(false);
    expect(selectAll.checked).toBe(false);
    expect(selectAll.indeterminate).toBe(true);
  });

  it('should update bulk bar when all selected', () => {
    const container = document.createElement('div');
    const count = document.createElement('span');
    count.className = 'report-selected-count';
    const bulkDelete = document.createElement('button');
    bulkDelete.className = 'bulk-delete';
    const selectAll = document.createElement('input');
    selectAll.className = 'report-select-all';
    selectAll.type = 'checkbox';

    const cb1 = document.createElement('input');
    cb1.type = 'checkbox';
    cb1.className = 'report-select';
    cb1.dataset.id = '1';
    cb1.checked = true;

    const cb2 = document.createElement('input');
    cb2.type = 'checkbox';
    cb2.className = 'report-select';
    cb2.dataset.id = '2';
    cb2.checked = true;

    container.appendChild(cb1);
    container.appendChild(cb2);
    container.appendChild(count);
    container.appendChild(bulkDelete);
    container.appendChild(selectAll);

    updateReportBulkBar(container);

    expect(count.textContent).toBe('2 selected');
    expect(bulkDelete.disabled).toBe(false);
    expect(selectAll.checked).toBe(true);
    expect(selectAll.indeterminate).toBe(false);
  });
});

describe('canManageTeam', () => {
  const { canManageTeam } = require('./shared.js');

  it('should return false for owner regardless of isOwner', () => {
    expect(canManageTeam({ role: 'owner' }, true)).toBe(false);
    expect(canManageTeam({ role: 'owner' }, false)).toBe(false);
  });

  it('should return true for non-owner when current user is owner', () => {
    expect(canManageTeam({ role: 'member' }, true)).toBe(true);
  });

  it('should return false for non-owner when current user is not owner', () => {
    expect(canManageTeam({ role: 'member' }, false)).toBe(false);
  });
});

describe('isReportActionTarget', () => {
  const { isReportActionTarget } = require('./shared.js');

  it('should return true for report action elements', () => {
    const del = document.createElement('div');
    del.className = 'del';
    expect(isReportActionTarget(del)).toBe(true);

    const select = document.createElement('input');
    select.className = 'report-select';
    expect(isReportActionTarget(select)).toBe(true);
  });

  it('should return false for non-action elements', () => {
    const div = document.createElement('div');
    expect(isReportActionTarget(div)).toBe(false);
  });
});

describe('validateGCFile', () => {
  const { validateGCFile } = require('./shared.js');

  it('should return valid for correct .log file', () => {
    const file = new File(['content'], 'test.log', { type: 'text/plain' });
    const result = validateGCFile(file);
    expect(result.valid).toBe(true);
  });

  it('should return invalid for wrong extension', () => {
    const file = new File(['content'], 'test.pdf', { type: 'application/pdf' });
    const result = validateGCFile(file);
    expect(result.valid).toBe(false);
    expect(result.error).toBe('invalid_type');
  });

  it('should return invalid when file exceeds max size', () => {
    const maxSize = 100;
    const blob = new Blob(['x'.repeat(200)], { type: 'text/plain' });
    const file = new File([blob], 'test.log');
    const result = validateGCFile(file, ['.log'], maxSize);
    expect(result.valid).toBe(false);
    expect(result.error).toBe('too_large');
  });
});

describe('calculateGCStatsClasses', () => {
  const { calculateGCStatsClasses } = require('./shared.js');

  it('should calculate correct classes when no issues', () => {
    const stats = {
      by_category: {},
      throughput: 1.0,
    };
    const result = calculateGCStatsClasses(stats);
    expect(result.fullCount).toBe(0);
    expect(result.maxPause).toBe(0);
    expect(result.pauseClass).toBe('good');
    expect(result.tpClass).toBe('good');
    expect(result.fullClass).toBe('good');
  });

  it('should mark bad when maxPause > 500', () => {
    const stats = {
      by_category: { Full: { max_pause_ms: 600 } },
      throughput: 1.0,
    };
    const result = calculateGCStatsClasses(stats);
    expect(result.pauseClass).toBe('bad');
  });

  it('should mark warn when maxPause > 200', () => {
    const stats = {
      by_category: { Full: { max_pause_ms: 300 } },
      throughput: 1.0,
    };
    const result = calculateGCStatsClasses(stats);
    expect(result.pauseClass).toBe('warn');
  });

  it('should mark warn when throughput < 0.95', () => {
    const stats = {
      by_category: {},
      throughput: 0.9,
    };
    const result = calculateGCStatsClasses(stats);
    expect(result.tpClass).toBe('warn');
  });

  it('should mark bad when fullCount > 0', () => {
    const stats = {
      by_category: { Full: { count: 1 } },
      throughput: 1.0,
    };
    const result = calculateGCStatsClasses(stats);
    expect(result.fullClass).toBe('bad');
    expect(result.fullCount).toBe(1);
  });
});

describe('detectProvider', () => {
  const { detectProvider } = require('./shared.js');

  it('should detect OpenAI', () => {
    expect(detectProvider('https://api.openai.com/v1')).toBe('OpenAI');
  });

  it('should detect DeepSeek', () => {
    expect(detectProvider('https://api.deepseek.com/v1')).toBe('DeepSeek');
  });

  it('should detect 通义千问', () => {
    expect(detectProvider('https://dashscope.aliyuncs.com')).toBe('通义千问');
  });

  it('should detect Kimi', () => {
    expect(detectProvider('https://api.moonshot.cn/v1')).toBe('Kimi');
  });

  it('should return empty for unknown provider', () => {
    expect(detectProvider('https://custom.example.com')).toBe('');
  });

  it('should return empty for empty url', () => {
    expect(detectProvider('')).toBe('');
  });
});

describe('consumeLoginRedirect', () => {
  const { consumeLoginRedirect } = require('./shared.js');

  beforeEach(() => {
    sessionStorage.clear();
  });

  it('should return empty when no redirect', () => {
    expect(consumeLoginRedirect()).toBe('');
  });

  it('should consume and return valid report redirect', () => {
    delete window.location;
    window.location = new URL('http://example.com/');
    sessionStorage.setItem('loginRedirect', 'http://example.com/report/sid/rid');
    expect(consumeLoginRedirect()).toBe('/report/sid/rid');
    expect(sessionStorage.getItem('loginRedirect')).toBeNull();
  });

  it('should return empty for cross-origin redirect', () => {
    delete window.location;
    window.location = new URL('http://example.com/');
    sessionStorage.setItem('loginRedirect', 'http://other.com/report/sid/rid');
    expect(consumeLoginRedirect()).toBe('');
  });

  it('should return empty for non-report path', () => {
    delete window.location;
    window.location = new URL('http://example.com/');
    sessionStorage.setItem('loginRedirect', 'http://example.com/dashboard');
    expect(consumeLoginRedirect()).toBe('');
  });
});

describe('renderReportBulkToolbar', () => {
  const { renderReportBulkToolbar } = require('./shared.js');

  const mockT = (key) => {
    const map = {
      'reports.select_all': 'Select All',
      'reports.selected_count': '0 selected',
      'reports.bulk_delete': 'Bulk Delete',
    };
    return map[key] || key;
  };

  it('should render correct HTML with scope', () => {
    const html = renderReportBulkToolbar('gc', mockT);
    expect(html).toContain('report-bulk-bar');
    expect(html).toContain('data-scope="gc"');
    expect(html).toContain('report-select-all');
    expect(html).toContain('report-selected-count');
    expect(html).toContain('bulk-delete');
    expect(html).toContain('Select All');
    expect(html).toContain('Bulk Delete');
  });
});

describe('formatHealthBanner', () => {
  const { formatHealthBanner, calculateGCHealth } = require('./shared.js');

  const mockT = (key, params) => {
    const map = {
      'gc.health_bad': 'Bad',
      'gc.health_warn': 'Warn',
      'gc.health_caution': 'Caution',
      'gc.health_good': 'Good',
      'gc.health_detail_full': 'Full GC: {n}, max pause {ms}ms',
      'gc.health_detail_tp': 'Throughput: {p}%',
      'gc.health_detail_heap': 'Heap: {p}%',
      'gc.health_detail_pause': 'Max pause {ms}ms',
      'gc.health_detail_ok': 'Throughput: {tp}',
      'gc.diagnosis_leak_risk': 'Leak Risk',
      'gc.diagnosis_oom_risk': 'OOM Risk',
      'gc.diagnosis_risk_high': 'High',
      'gc.diagnosis_risk_medium': 'Medium',
      'gc.diagnosis_risk_low': 'Low',
      'gc.diagnosis_risk_none': 'None',
    };
    let text = map[key] || key;
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        text = text.split(`{${k}}`).join(String(v));
      }
    }
    return text;
  };

  it('should return bad level when more than 3 Full GC', () => {
    const stats = {
      by_category: { Full: { count: 4 } },
      throughput: 1.0,
      max_heap_usage_pct: 90,
    };
    const html = formatHealthBanner(stats, mockT);
    expect(html).toContain('level-bad');
    expect(html).toContain('m15 9-6 6');
    expect(html).toContain('Bad');
  });

  it('should return good level when no issues', () => {
    const stats = {
      by_category: {},
      throughput: 0.98,
      max_heap_usage_pct: 80,
    };
    const html = formatHealthBanner(stats, mockT);
    expect(html).toContain('level-good');
    expect(html).toContain('m9 12 2 2 4-4');
    expect(html).toContain('Good');
  });

  it('should include correct level class based on calculateGCHealth result', () => {
    const stats = {
      by_category: { Full: { count: 2 } },
      throughput: 1.0,
      max_heap_usage_pct: 90,
    };
    const html = formatHealthBanner(stats, mockT);
    const level = calculateGCHealth(stats);
    expect(html).toContain(`level-${level}`);
  });

  it('ParallelGC banner shows green + Healthy for many fast Full GCs (not Bad)', () => {
    const stats = {
      collector: 'Parallel',
      by_category: { Full: { count: 21, max_pause_ms: 33 } },
      throughput: 0.98,
      max_heap_usage_pct: 70,
    };
    const html = formatHealthBanner(stats, mockT);
    expect(html).toContain('level-good');
    expect(html).toContain('m9 12 2 2 4-4');
    expect(html).toContain('Good');
    expect(html).not.toContain('m15 9-6 6');
    expect(html).not.toContain('Bad');
    // Always shows the same metric slots in fixed order
    expect(html).toContain('Parallel');
    expect(html).toContain('Throughput: 98.0%');
    expect(html).toContain('Heap: 70%');
    expect(html).toContain('Full GC: 21, max pause 33ms');
  });

  it('ParallelGC banner shows Bad for slow Full GC (>2s)', () => {
    const stats = {
      collector: 'Parallel',
      by_category: { Full: { count: 3, max_pause_ms: 3000 } },
      throughput: 0.98,
      max_heap_usage_pct: 70,
    };
    const html = formatHealthBanner(stats, mockT);
    expect(html).toContain('level-bad');
    expect(html).toContain('m15 9-6 6');
    expect(html).toContain('Bad');
    expect(html).toContain('Full GC: 3, max pause 3000ms');
  });

  it('Banner layout: metrics appear in fixed slot order (collector, throughput, heap, full GC)', () => {
    const stats = {
      collector: 'G1',
      by_category: { Full: { count: 5, max_pause_ms: 200 } },
      throughput: 0.95,
      max_heap_usage_pct: 60,
    };
    const html = formatHealthBanner(stats, mockT);
    const collectorPos = html.indexOf('G1');
    const tpPos = html.indexOf('Throughput: 95.0%');
    const heapPos = html.indexOf('Heap: 60%');
    const fullPos = html.indexOf('Full GC: 5');
    expect(collectorPos).toBeGreaterThan(-1);
    expect(tpPos).toBeGreaterThan(collectorPos);
    expect(heapPos).toBeGreaterThan(tpPos);
    expect(fullPos).toBeGreaterThan(heapPos);
  });

  it('Banner omits missing metrics (no placeholders)', () => {
    const stats = {
      collector: 'G1',
      by_category: {},
      throughput: null,
      max_heap_usage_pct: 0,
    };
    const html = formatHealthBanner(stats, mockT);
    expect(html).toContain('G1');
    expect(html).not.toContain('Throughput');
    expect(html).not.toContain('Heap');
    expect(html).not.toContain('Full GC');
  });
});

describe('i18nText', () => {
  it('returns input unchanged when no " / " separator', () => {
    expect(i18nText('plain message')).toBe('plain message');
  });

  it('returns empty string for falsy / non-string input', () => {
    expect(i18nText('')).toBe('');
    expect(i18nText(null)).toBe('');
    expect(i18nText(undefined)).toBe('');
  });

  it('splits bilingual "中文 / English" by current language', () => {
    setLang('zh');
    expect(i18nText('用户不存在 / User not found')).toBe('用户不存在');
    setLang('en');
    expect(i18nText('用户不存在 / User not found')).toBe('User not found');
  });
});

describe('formatEpoch', () => {
  it('formats same-day timestamp as HH:MM:SS', () => {
    const base = new Date(2026, 0, 1, 10, 0, 0).getTime();
    const ts = new Date(2026, 0, 1, 13, 5, 9).getTime();
    expect(formatEpoch(ts, base)).toBe('13:05:09');
  });

  it('includes MM-DD prefix when crossing days', () => {
    const base = new Date(2026, 0, 1, 10, 0, 0).getTime();
    const ts = new Date(2026, 0, 2, 8, 30, 0).getTime();
    expect(formatEpoch(ts, base)).toBe('01-02 08:30:00');
  });
});

describe('getPendingInvite / consumePendingInvite', () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  it('returns null when nothing stored', () => {
    expect(getPendingInvite()).toBeNull();
  });

  it('returns null on malformed JSON', () => {
    sessionStorage.setItem('pendingInvite', '{not-json');
    expect(getPendingInvite()).toBeNull();
  });

  it('parses a stored invite object', () => {
    sessionStorage.setItem('pendingInvite', JSON.stringify({ orgId: 'o1', token: 't1' }));
    expect(getPendingInvite()).toEqual({ orgId: 'o1', token: 't1' });
  });

  it('consume removes the stored invite after reading', () => {
    sessionStorage.setItem('pendingInvite', JSON.stringify({ orgId: 'o1' }));
    expect(consumePendingInvite()).toEqual({ orgId: 'o1' });
    expect(sessionStorage.getItem('pendingInvite')).toBeNull();
    expect(consumePendingInvite()).toBeNull();
  });
});
