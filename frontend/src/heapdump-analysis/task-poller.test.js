import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  pollTask,
  cancelTask,
  TaskEvictedError,
  TaskFailedError,
  TaskTimeoutError,
  AbortError,
} from './task-poller.js';

const REPORT_ID = 'hd_abc';
const TASK_ID = 't-1';

/** 让 fetch 依次返回一组 JSON body（模拟轮询）。 */
function mockFetchSequence(sequence) {
  let i = 0;
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
    const body = sequence[Math.min(i, sequence.length - 1)];
    i += 1;
    return {
      ok: true,
      status: 200,
      json: async () => body,
    };
  });
}

describe('pollTask', () => {
  beforeEach(() => {
    document.cookie = 'csrf_token=t';
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('RUNNING → RUNNING → DONE 返回 result 并推进度', async () => {
    mockFetchSequence([
      { status: 'RUNNING', progress: 0.2, phase: 'Pass1' },
      { status: 'RUNNING', progress: 0.6, phase: 'DominatorTree' },
      { status: 'DONE', result: { headline: 'ok' } },
    ]);
    const onProgress = vi.fn();
    const result = await pollTask(REPORT_ID, TASK_ID, {
      intervalMs: 1,
      onProgress,
    });
    expect(result).toEqual({ headline: 'ok' });
    expect(onProgress).toHaveBeenCalledTimes(2);
    expect(onProgress.mock.calls[0][0]).toMatchObject({
      status: 'RUNNING',
      progress: 0.2,
      phase: 'Pass1',
    });
  });

  it('DONE + resultEvicted 抛 TaskEvictedError', async () => {
    mockFetchSequence([{ status: 'DONE', resultEvicted: true }]);
    await expect(pollTask(REPORT_ID, TASK_ID, { intervalMs: 1 }))
      .rejects.toBeInstanceOf(TaskEvictedError);
  });

  it('FAILED 抛 TaskFailedError 携带 error 与 status', async () => {
    mockFetchSequence([{ status: 'FAILED', error: 'timeout after 300s' }]);
    try {
      await pollTask(REPORT_ID, TASK_ID, { intervalMs: 1 });
      throw new Error('should have thrown');
    } catch (e) {
      expect(e).toBeInstanceOf(TaskFailedError);
      expect(e.status).toBe('FAILED');
      expect(e.message).toContain('timeout after 300s');
    }
  });

  it('CANCELLED / NOT_FOUND 归为 TaskFailedError', async () => {
    mockFetchSequence([{ status: 'CANCELLED' }]);
    await expect(pollTask(REPORT_ID, TASK_ID, { intervalMs: 1 }))
      .rejects.toBeInstanceOf(TaskFailedError);

    mockFetchSequence([{ status: 'NOT_FOUND' }]);
    await expect(pollTask(REPORT_ID, TASK_ID, { intervalMs: 1 }))
      .rejects.toBeInstanceOf(TaskFailedError);
  });

  it('maxTries 用尽仍 RUNNING 抛 TaskTimeoutError', async () => {
    mockFetchSequence([{ status: 'RUNNING', progress: 0.1 }]);
    await expect(pollTask(REPORT_ID, TASK_ID, { intervalMs: 1, maxTries: 3 }))
      .rejects.toBeInstanceOf(TaskTimeoutError);
  });

  it('AbortSignal 触发 AbortError 且不再继续请求', async () => {
    const controller = new AbortController();
    const fetchSpy = mockFetchSequence([{ status: 'RUNNING' }]);
    // 第一次请求后立即取消
    setTimeout(() => controller.abort(), 5);
    await expect(pollTask(REPORT_ID, TASK_ID, {
      intervalMs: 20,
      signal: controller.signal,
    })).rejects.toBeInstanceOf(AbortError);
    // 请求次数应远小于 maxTries=600
    expect(fetchSpy.mock.calls.length).toBeLessThan(3);
  });

  it('onProgress 抛错不影响轮询继续', async () => {
    mockFetchSequence([
      { status: 'RUNNING', progress: 0.1 },
      { status: 'DONE', result: { ok: true } },
    ]);
    const bad = vi.fn(() => { throw new Error('bad'); });
    const result = await pollTask(REPORT_ID, TASK_ID, {
      intervalMs: 1,
      onProgress: bad,
    });
    expect(result).toEqual({ ok: true });
    expect(bad).toHaveBeenCalled();
  });

  it('URL 编码：reportId / taskId 特殊字符', async () => {
    const fetchSpy = mockFetchSequence([{ status: 'DONE', result: 1 }]);
    await pollTask('hd/1', 't 2', { intervalMs: 1 });
    const url = fetchSpy.mock.calls[0][0];
    expect(url).toContain(encodeURIComponent('hd/1'));
    expect(url).toContain(encodeURIComponent('t 2'));
  });
});

describe('cancelTask', () => {
  afterEach(() => vi.restoreAllMocks());

  it('DELETE 成功返回 true', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true, status: 200, json: async () => ({ cancelled: true }),
    });
    expect(await cancelTask(REPORT_ID, TASK_ID)).toBe(true);
  });

  it('异常时静默返回 false（用户离开视图，不阻断 UX）', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('offline'));
    expect(await cancelTask(REPORT_ID, TASK_ID)).toBe(false);
  });
});
