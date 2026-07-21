import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderCatTable, drawJstackCharts, drawFlamegraph } from './charts.js';

describe('renderCatTable', () => {
  it('should return empty message when no categories', () => {
    const html = renderCatTable({});
    expect(html).toContain('color:var(--text-dim)');
    expect(html).toContain('</div>');
  });

  it('should render table with multiple categories', () => {
    const byCat = {
      Full: {
        count: 5,
        total_pause_ms: 1234.5,
        avg_pause_ms: 246.9,
        max_pause_ms: 500.0,
        p95_pause_ms: 450.0,
        p99_pause_ms: 490.0,
        avg_freed_mb: 123.4,
      },
      Young: {
        count: 100,
        total_pause_ms: 500.0,
        avg_pause_ms: 5.0,
        max_pause_ms: 10.0,
        p95_pause_ms: 8.0,
        p99_pause_ms: 9.0,
        avg_freed_mb: 10.5,
      },
    };

    const html = renderCatTable(byCat);
    expect(html).toContain('<table class="cat-table">');
    expect(html).toContain('Full');
    expect(html).toContain('Young');
    expect(html).toContain('1234.5ms');
    expect(html).toContain('123.4 MB');
    expect(html).toContain('</tbody></table>');
  });

  it('should escape malicious category keys (XSS defense)', () => {
    const byCat = {
      '<img src=x onerror=alert(1)>': {
        count: 1,
        total_pause_ms: 1.0,
        avg_pause_ms: 1.0,
        max_pause_ms: 1.0,
        p95_pause_ms: 1.0,
        p99_pause_ms: 1.0,
        avg_freed_mb: 1.0,
      },
    };
    const html = renderCatTable(byCat);
    // 原始危险标签不得出现，必须被转义
    expect(html).not.toContain('<img src=x');
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
  });
});

describe('drawJstackCharts ResizeObserver cleanup', () => {
  let observeCalls, disconnectCalls;
  let observedElements;

  beforeEach(() => {
    // 模拟 echarts 全局
    globalThis.echarts = {
      init: () => ({ setOption: () => {}, resize: () => {} }),
    };
    globalThis.ResizeObserver = class MockRO {
      constructor(cb) { this.cb = cb; this.target = null; this.disconnected = false; }
      observe(el) { this.target = el; this.disconnected = false; observedElements.push(this); observeCalls++; }
      disconnect() { this.target = null; this.disconnected = true; disconnectCalls++; }
    };
    observedElements = [];
    observeCalls = 0;
    disconnectCalls = 0;
    document.body.innerHTML = `
      <div id="jstackStateChart"></div>
      <div id="jstackRunnableChart"></div>
      <div id="jstackPoolChart"></div>
    `;
  });

  afterEach(() => {
    delete globalThis.echarts;
    delete globalThis.ResizeObserver;
  });

  it('should disconnect old ResizeObservers on re-render (no leak)', () => {
    const stats = {
      by_state: { RUNNABLE: 5, BLOCKED: 2 },
      runnable_hot_methods: [['com.foo.Bar.run', 3], ['com.foo.Baz.go', 2]],
      thread_pools: [
        { pool: 'http-nio', total: 4, RUNNABLE: 2, BLOCKED: 1, WAITING: 1, TIMED_WAITING: 0 },
      ],
    };

    // 多次渲染同一组图表
    drawJstackCharts(stats);
    drawJstackCharts(stats);
    drawJstackCharts(stats);

    // 每次重渲染前应 disconnect 旧的 observer（清理机制存在）
    // 3 个图表 × 2 次重渲染 = 6 次 disconnect（每次重渲染清理 3 个旧的）
    expect(disconnectCalls).toBe(6);
    // 净存活的 observer 数（observe - disconnect）应保持稳定，不应随渲染次数线性膨胀
    expect(observeCalls - disconnectCalls).toBe(3);
    // 所有旧 observer 都已 disconnect
    const oldObservers = observedElements.slice(0, -3);
    for (const ro of oldObservers) {
      expect(ro.disconnected).toBe(true);
    }
  });
});

describe('drawFlamegraph — bottom-up + ancestor-preserving drill-down', () => {
  // root(6) -> entry(6) -> {mid(4) -> leaf(4)} + {other(2)}
  const root = {
    name: 'root', value: 6, children: [
      { name: 'entry', value: 6, children: [
        { name: 'mid', value: 4, children: [
          { name: 'leaf', value: 4, children: [] },
        ] },
        { name: 'other', value: 2, children: [] },
      ] },
    ],
  };

  beforeEach(() => {
    document.body.innerHTML = `
      <span id="flameTitle"></span>
      <button id="flameResetBtn" style="display:none;"></button>
      <div id="flamegraphContainer" style="width:600px;"></div>
    `;
    // jsdom clientWidth 默认 0，强制返回稳定宽度
    Object.defineProperty(
      document.getElementById('flamegraphContainer'),
      'clientWidth',
      { configurable: true, value: 600 },
    );
  });

  function frames() {
    return [...document.querySelectorAll('#flamegraphContainer .flame-frame')]
      .map(el => ({ top: parseInt(el.style.top, 10), text: el.textContent }));
  }

  it('renders entry at the bottom and stack top above (bottom-up)', () => {
    drawFlamegraph(root);
    const fs = frames();
    expect(fs.length).toBeGreaterThan(0);
    const entry = fs.find(f => f.text === 'entry');
    const leaf = fs.find(f => f.text === 'leaf');
    expect(entry).toBeTruthy();
    expect(leaf).toBeTruthy();
    // bottom-up：入口（栈底）top 值更大（更靠下），栈顶 leaf 更小（更靠上）
    expect(entry.top).toBeGreaterThan(leaf.top);
  });
});
