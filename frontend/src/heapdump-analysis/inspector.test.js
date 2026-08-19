import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderInspectorSection } from './inspector.js';

const RID = 'hd_test';

function mockApi(routes) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, opts = {}) => {
    const path = String(url);
    let status = 200;
    let body = null;
    for (const [needle, resp] of routes) {
      if (path.includes(needle)) {
        if (typeof resp === 'function') {
          status = 200;
          body = resp(path, opts);
        } else if (Array.isArray(resp)) {
          [status, body] = resp;
        } else {
          body = resp;
        }
        break;
      }
    }
    if (body === null) {
      status = 500;
      body = { detail: `no route for ${path}` };
    }
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    };
  });
}

function domFrom(html) {
  const c = document.createElement('div');
  c.innerHTML = html;
  document.body.appendChild(c);
  return c;
}

describe('renderInspectorSection', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    document.cookie = 'csrf_token=t';
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders dominator tree and inspector hint on init', async () => {
    mockApi([
      ['/dominator', { parent: 'ROOT', totalRows: 2, rows: [
        { objectId: 10, label: 'java.lang.String', className: 'java.lang.String',
          shallowBytes: 24, retainedBytes: 100, expandable: true },
        { objectId: 11, label: 'byte[]', className: 'byte[]',
          shallowBytes: 64, retainedBytes: 64, expandable: false },
      ] }],
    ]);
    const c = domFrom('<div></div>');
    await renderInspectorSection(c, RID);
    expect(c.querySelector('.insp-pane-title')).not.toBeNull();
    const rows = c.querySelectorAll('.insp-dom-row');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('java.lang.String');
    // 默认提示
    expect(c.querySelector('[data-obj="panel"]').textContent).toContain('object');
  });

  it('clicking a dominator row loads the object inspector with fields + references', async () => {
    mockApi([
      ['/dominator', { parent: 'ROOT', totalRows: 1, rows: [
        { objectId: 10, label: 'foo', className: 'java.util.HashMap',
          shallowBytes: 24, retainedBytes: 200, expandable: true },
      ] }],
      ['/object', {
        objectId: 10, label: 'foo', type: 'java.util.HashMap', address: '0x1',
        shallowBytes: 24, retainedBytes: 200, gcRoot: false, kind: 'instance',
        fields: [
          { name: 'table', type: 'ref', value: '"map"', isRef: true, targetClass: 'java.util.HashMap$Node[]', refId: 20 },
          { name: 'size', type: 'int', value: '1', isRef: false },
        ],
        staticFields: [],
      }],
      ['/references', { kind: 'object-list', direction: 'out', source: 10,
        totalRows: 1, rows: [
          { objectId: 20, label: 'map', className: 'java.util.HashMap$Node[]', fieldName: 'table', shallowBytes: 64, retainedBytes: 64 },
        ] }],
    ]);
    const c = domFrom('<div></div>');
    await renderInspectorSection(c, RID);
    const row = c.querySelector('.insp-dom-row');
    row.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    const panel = c.querySelector('[data-obj="panel"]');
    expect(panel.textContent).toContain('java.util.HashMap');
    expect(panel.textContent).toContain('table');
    expect(panel.textContent).toMatch(/Incoming refs|入引用/);
  });

  it('renders array elements + collection entries with load-more', async () => {
    mockApi([
      ['/dominator', { parent: 'ROOT', totalRows: 1, rows: [
        { objectId: 30, label: 'buf', className: 'java.lang.Object[]',
          shallowBytes: 128, retainedBytes: 128, expandable: false },
      ] }],
      ['/object', {
        objectId: 30, label: 'buf', type: 'java.lang.Object[]', address: '0x2',
        shallowBytes: 128, retainedBytes: 128, gcRoot: false, kind: 'objectArray',
        length: 30, hasArrayElements: true,
        fields: [], staticFields: [],
      }],
      ['/array-elements', { kind: 'array-elements', objectId: 30, totalRows: 30, returned: 25,
        rows: [{ objectId: 1, label: 'a', className: 'java.lang.String' }], cursor: 'offset:25' }],
      ['/collection-entries', { kind: 'collection-entries', objectId: 40, totalRows: 2, returned: 2,
        rows: [{ objectId: 5, label: 'v', className: 'x' }] }],
      ['/references', { kind: 'object-list', direction: 'out', source: 30, totalRows: 0, rows: [] }],
    ]);
    const c = domFrom('<div></div>');
    await renderInspectorSection(c, RID);
    const row = c.querySelector('.insp-dom-row');
    row.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    const panel = c.querySelector('[data-obj="panel"]');
    expect(panel.textContent).toContain('java.lang.Object[]');
    // load-more button present (array has cursor)
    expect(panel.querySelector('[data-more]')).not.toBeNull();
  });
});
