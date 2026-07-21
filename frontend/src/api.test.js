import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { api, setAuthFailureHandler } from './api.js';

// sessions.js 顶层会对多个按钮绑定 onclick；import 前准备这些元素，
// 以便验证其导出的 apiWithAuth 是否复用 api。
document.body.innerHTML = `
  <button id="newSessionBtn"></button>
  <button id="refreshBtn"></button>
  <button id="deleteBtn"></button>
  <button id="clearBtn"></button>
  <button id="renameBtn"></button>
  <button id="sidebarToggle"></button>
`;
const { apiWithAuth } = await import('./sessions.js');

describe('api() 核心行为（sessions.js apiWithAuth 现已复用此实现）', () => {
  beforeEach(() => {
    document.cookie = 'csrf_token=test-csrf-token';
    setAuthFailureHandler(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('注入 X-CSRF-Token 头', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });
    await api('/api/test');
    const opts = fetchSpy.mock.calls[0][1];
    expect(opts.headers['X-CSRF-Token']).toBe('test-csrf-token');
  });

  it('401 触发已注册的认证失败处理器', async () => {
    const onFail = vi.fn();
    setAuthFailureHandler(onFail);
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 401,
      text: async () => '{"detail":"unauth"}',
    });
    await expect(api('/api/test')).rejects.toThrow();
    expect(onFail).toHaveBeenCalledTimes(1);
  });

  it('解析后端 detail 错误信息', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: false,
      status: 400,
      text: async () => '{"detail":"会话不存在 / Session not found"}',
    });
    await expect(api('/api/test')).rejects.toThrow(/Session not found|会话不存在/);
  });

  it('sessions.js 的 apiWithAuth 是 api 的同一引用（去重）', () => {
    expect(apiWithAuth).toBe(api);
  });
});
