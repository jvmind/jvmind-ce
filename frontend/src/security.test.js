import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// jstack-analysis.js 在 import 时访问 #jstackUploadZone / #jstackFile，并对其绑定事件，
// 因此必须在 import 前准备这些元素。
document.body.innerHTML = `
  <div id="jstackUploadZone"></div>
  <input id="jstackFile" type="file" />
  <table><tbody id="threadTableBody"></tbody></table>
  <div id="threadPagination"></div>
  <input id="threadSearch" />
  <button id="sortDepthBtn"></button>
  <span id="sortDepthIcon"></span>
`;

// 提供最小 echarts/marked/DOMPurify 全局，避免渲染路径抛错
globalThis.echarts = { init: () => ({ setOption: () => {}, resize: () => {} }) };

const jstackModule = await import('./jstack-analysis.js');
const { initThreadTable } = jstackModule;

describe('jstack initThreadTable — XSS 转义', () => {
  beforeEach(() => {
    document.getElementById('threadTableBody').innerHTML = '';
    document.body.classList.remove('report-only');
  });

  it('应转义恶意线程名，不产生未转义的 script/img 标签', () => {
    const malicious = '"><img src=x onerror=alert(1)>';
    const threads = [{
      name: malicious,
      state: 'RUNNABLE',
      daemon: false,
      depth: 3,
      top_frame: 'com.foo.Bar.run',
      lock_waiting: null,
      frames: ['frame1', 'frame2'],
    }];
    initThreadTable(threads, { id: 'rid_1', filename: 'dump.txt' });

    const tbody = document.getElementById('threadTableBody');
    // 关键：DOM 中不得存在真正的 img 元素（注入未生效）
    expect(tbody.querySelector('img')).toBeNull();
    // 名称单元格的文本内容应为原始字符串（已正确解码显示，但非 HTML 节点）
    const nameCell = tbody.querySelector('td.name');
    expect(nameCell.textContent).toBe(malicious);
    expect(nameCell.querySelector('img')).toBeNull();
  });

  it('应转义恶意 top_frame 与 frames', () => {
    const threads = [{
      name: 'worker-1',
      state: 'RUNNABLE',
      daemon: true,
      depth: 1,
      top_frame: '<svg onload=alert(2)>',
      lock_waiting: '<b>lock</b>',
      frames: ['<script>evil()</script>'],
    }];
    initThreadTable(threads, { id: 'rid_2', filename: 'dump.txt' });
    const tbody = document.getElementById('threadTableBody');
    expect(tbody.querySelector('svg')).toBeNull();
    expect(tbody.querySelector('script')).toBeNull();
    // top_frame 单元格文本应为原始字符串且无注入节点
    const frameCell = tbody.querySelector('td.top-frame');
    expect(frameCell.textContent).toBe('<svg onload=alert(2)>');
    // 展开行 pre 中的 frame 文本应被转义为纯文本
    const pre = tbody.querySelector('pre');
    expect(pre.querySelector('script')).toBeNull();
    expect(pre.textContent).toContain('<script>evil()</script>');
  });
});

describe('jstack initThreadTable — 事件委托（无内联 onclick）', () => {
  beforeEach(() => {
    document.getElementById('threadTableBody').innerHTML = '';
    document.body.classList.remove('report-only');
  });

  it('渲染的行与按钮不应包含内联 onclick 属性', () => {
    const threads = [{
      name: "thread';alert(1);'",
      state: 'BLOCKED',
      daemon: false,
      depth: 2,
      top_frame: 'x',
      lock_waiting: null,
      frames: [],
    }];
    initThreadTable(threads, { id: 'rid_3', filename: "f';alert(1);'.txt" });
    const tbody = document.getElementById('threadTableBody');
    // 关键：不再有内联 onclick（防 XSS + 为 CSP 铺路）
    expect(tbody.innerHTML).not.toContain('onclick');
    // 改用 data-act 委托标记
    expect(tbody.innerHTML).toContain('data-act="toggle-stack"');
    expect(tbody.innerHTML).toContain('data-act="send-thread"');
  });

  it('点击发送按钮应调用 window.sendToAgent 并正确传参（含特殊字符）', () => {
    const spy = vi.fn();
    window.sendToAgent = spy;
    const trickyName = "thread';alert(1);'";
    const trickyFile = "f\"><x>.txt";
    const threads = [{
      name: trickyName,
      state: 'RUNNABLE',
      daemon: false,
      depth: 1,
      top_frame: 'x',
      lock_waiting: null,
      frames: [],
    }];
    initThreadTable(threads, { id: 'rid_4', filename: trickyFile });

    const btn = document.querySelector('[data-act="send-thread"]');
    expect(btn).not.toBeNull();
    btn.click();

    expect(spy).toHaveBeenCalledTimes(1);
    const args = spy.mock.calls[0];
    expect(args[0]).toBe('jstack_thread');
    expect(args[1]).toBe('rid_4');
    expect(args[2]).toBe(trickyFile);   // 原始值（未被破坏）经 dataset 还原
    expect(args[3]).toBe(trickyName);
  });

  it('点击行应切换对应栈帧行的显示', () => {
    const threads = [{
      name: 'worker',
      state: 'RUNNABLE',
      daemon: false,
      depth: 1,
      top_frame: 'x',
      lock_waiting: null,
      frames: ['a', 'b'],
    }];
    initThreadTable(threads, { id: 'rid_5', filename: 'dump.txt' });
    const row = document.querySelector('[data-act="toggle-stack"]');
    const rowId = row.dataset.rowId;
    const expandRow = document.getElementById(rowId);
    expect(expandRow.style.display).toBe('none');
    row.click();
    expect(expandRow.style.display).toBe('table-row');
  });
});

afterEach(() => {
  delete window.sendToAgent;
});
