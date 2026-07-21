// Heapdump 异步任务轮询助手（对齐 IMPLEMENTATION_GUIDE §6.4 / EXECUTION_PLAN P2.5）
//
// query-service 的重操作（leak-suspects / diagnose-oom / retained-set / merge-paths）
// 提交后返回 taskId，通过 Python 反代的 /api/heapdump-reports/{id}/tasks/{taskId} 轮询。
//
// TaskManager 真实状态：RUNNING / DONE / FAILED / CANCELLED / NOT_FOUND
// 结果是软引用缓存，DONE 可能带 resultEvicted:true（需要重新提交）。

import { api } from "../api.js";

/**
 * @typedef {Object} PollOptions
 * @property {number} [intervalMs=1000]  轮询间隔（毫秒）
 * @property {number} [maxTries=600]     最大轮询次数（默认 600 = 10 分钟）
 * @property {(state: {status: string, progress?: number, phase?: string}) => void} [onProgress]
 * @property {AbortSignal} [signal]      支持外部取消
 */

/**
 * 轮询异步任务直到终态；返回 result 或抛错。
 *
 * @param {string} reportId  heapdump 报告 id
 * @param {string} taskId    query-service 返回的任务 id
 * @param {PollOptions} [opts]
 * @returns {Promise<any>}   task.result
 */
export async function pollTask(reportId, taskId, opts = {}) {
  const {
    intervalMs = 1000,
    maxTries = 600,
    onProgress,
    signal,
  } = opts;

  for (let i = 0; i < maxTries; i++) {
    if (signal?.aborted) throw new AbortError("cancelled by user");

    const t = await api(`/api/heapdump-reports/${encodeURIComponent(reportId)}/tasks/${encodeURIComponent(taskId)}`);

    if (t.status === "DONE") {
      if (t.resultEvicted) {
        throw new TaskEvictedError("结果已过期（软引用被回收），请重新分析 / Result evicted, please retry");
      }
      return t.result;
    }
    if (t.status === "FAILED" || t.status === "CANCELLED" || t.status === "NOT_FOUND") {
      throw new TaskFailedError(t.error || `task ${t.status}`, t.status);
    }
    // RUNNING: 推进度给调用方
    if (onProgress) {
      try {
        onProgress({
          status: t.status,
          progress: t.progress,
          phase: t.phase || "",
        });
      } catch {
        // onProgress 抛错不应中断轮询
      }
    }
    await sleep(intervalMs, signal);
  }
  throw new TaskTimeoutError(`task poll timeout after ${maxTries * intervalMs}ms`);
}

/** 主动取消一个正在跑的异步任务（用户切走视图时调）。 */
export async function cancelTask(reportId, taskId) {
  try {
    await api(`/api/heapdump-reports/${encodeURIComponent(reportId)}/tasks/${encodeURIComponent(taskId)}`, {
      method: "DELETE",
    });
    return true;
  } catch {
    return false;
  }
}

// ---- 错误类型：调用方可用 instanceof 分支处理 ----

export class TaskEvictedError extends Error {
  constructor(msg) { super(msg); this.name = "TaskEvictedError"; this.code = "EVICTED"; }
}
export class TaskFailedError extends Error {
  constructor(msg, status) { super(msg); this.name = "TaskFailedError"; this.code = "FAILED"; this.status = status; }
}
export class TaskTimeoutError extends Error {
  constructor(msg) { super(msg); this.name = "TaskTimeoutError"; this.code = "TIMEOUT"; }
}
export class AbortError extends Error {
  constructor(msg) { super(msg); this.name = "AbortError"; this.code = "ABORTED"; }
}

/** 支持 AbortSignal 的 sleep。 */
function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new AbortError("aborted"));
    const timer = setTimeout(() => {
      cleanup();
      resolve();
    }, ms);
    const onAbort = () => {
      cleanup();
      reject(new AbortError("aborted"));
    };
    const cleanup = () => {
      clearTimeout(timer);
      signal?.removeEventListener?.("abort", onAbort);
    };
    signal?.addEventListener?.("abort", onAbort);
  });
}
