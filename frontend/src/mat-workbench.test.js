import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

function mockApi(routes) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, opts = {}) => {
    const path = String(url);
    let status = 200;
    let body = null;
    for (const [needle, resp] of routes) {
      if (path.includes(needle)) {
        if (typeof resp === 'function') { body = resp(path, opts); }
        else if (Array.isArray(resp)) { [status, body] = resp; }
        else { body = resp; }
        break;
      }
    }
    if (body === null) { status = 500; body = { detail: `no route for ${path}` }; }
    return { ok: status >= 200 && status < 300, status, json: async () => body };
  });
}

async function loadMatWorkbench() {
  await import('./mat-workbench.js');
  // main() is async and fires immediately; give it time to settle
  for (let i = 0; i < 5; i++) await new Promise(r => setTimeout(r, 0));
}

describe('mat workbench (/mat/{rid})', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="matApp"></div>';
    document.cookie = 'csrf_token=t';
    window.history.pushState({}, '', '/mat/hd_test');
  });
  afterEach(() => {
    vi.restoreAllMocks();
    vi.resetModules();
    document.body.innerHTML = '';
  });

  it('renders toolbar, core tabs and overview by default', async () => {
    mockApi([
      ['/overview', { usedHeapSize: 123456, numObjects: 100, numClasses: 5, numGcRoots: 2, jvmInfo: { javaVersion: '17' } }],
      ['/histogram', { rows: [] }],
    ]);
    await loadMatWorkbench();
    const app = document.getElementById('matApp');
    expect(app.querySelector('.mat-toolbar')).not.toBeNull();
    expect(app.querySelector('.mat-rid').textContent).toBe('hd_test');
    // core tabs
    const tabs = [...app.querySelectorAll('.mat-tab')].map(b => b.dataset.tab);
    expect(tabs).toContain('overview');
    expect(tabs).toContain('histogram');
    expect(tabs).toContain('dominator');
    expect(tabs).toContain('threads');
    expect(tabs).toContain('leak');
    expect(tabs).toContain('oql');
    expect(tabs).toContain('threadlocals');
    // overview is active by default and shows heap
    expect(app.querySelector('.mat-tab.overview')).toBeNull(); // class is 'active'
    expect(app.querySelector('.mat-tab.active').dataset.tab).toBe('overview');
    expect(app.querySelector('.mat-view').textContent).toContain('120.6 KB');
  });

  it('left inspector renders object detail when opened by id', async () => {
    mockApi([
      ['/overview', { rows: [] }],
      ['/object', {
        objectId: 7, label: 'obj7', type: 'java.lang.String', address: '0x1',
        shallowBytes: 24, retainedBytes: 80, gcRoot: false, kind: 'instance',
        fields: [{ name: 'value', type: 'char[]', value: '"x"', isRef: true, refId: 8 }],
        staticFields: [],
      }],
      ['/references', { rows: [] }],
    ]);
    await loadMatWorkbench();
    const app = document.getElementById('matApp');
    const input = app.querySelector('#inspId');
    input.value = '7';
    app.querySelector('#inspGo').click();
    for (let i = 0; i < 5; i++) await new Promise(r => setTimeout(r, 0));
    expect(app.querySelector('#matInspector').textContent).toContain('java.lang.String');
  });
});
