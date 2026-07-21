import { describe, it, expect, beforeEach, vi } from 'vitest';
import { state } from './state.js';
import { app } from './app.js';

// messages.js binds events at import time (factInput, chatArea, sendBtn, msg)
// and calls setupBilling() which binds billing buttons. Provide every element
// and the marked/DOMPurify globals used by renderMarkdown.
document.body.innerHTML = `
  <div id="chatArea"></div>
  <input id="factInput" />
  <div id="factsList"></div>
  <button id="sendBtn"></button>
  <textarea id="msg"></textarea>
  <button id="sbBillingBtn"></button>
  <button id="billingClose"></button>
  <button id="billingPricingBtn"></button>
  <button id="billingRenewBtn"></button>
  <button id="billingCancelBtn"></button>
`;

// marked stub: wrap text in <p>; DOMPurify passthrough (assert escaping at
// the appendMessage/user-bubble layer, which uses escapeHtml directly).
globalThis.marked = { parse: (t) => `<p>${t}</p>` };
globalThis.DOMPurify = { sanitize: (h) => h };
window.marked = globalThis.marked;
window.DOMPurify = globalThis.DOMPurify;

const messages = await import('./messages.js');
const { appendMessage, createMsgActions, renderStep, toggleObs, renderMessages, renderFacts } = messages;

beforeEach(() => {
  document.getElementById('chatArea').innerHTML = '';
});

describe('appendMessage', () => {
  it('user message escapes HTML and converts newlines to <br>', () => {
    const wrap = appendMessage('user', 'a<script>alert(1)</script>\nb');
    const bubble = wrap.querySelector('.bubble');
    expect(bubble.querySelector('script')).toBeNull();
    expect(bubble.innerHTML).toContain('&lt;script&gt;');
    expect(bubble.innerHTML).toContain('<br>');
  });

  it('assistant message renders through markdown', () => {
    const wrap = appendMessage('assistant', 'hello');
    const bubble = wrap.querySelector('.bubble');
    expect(bubble.innerHTML).toContain('hello');
  });

  it('stores raw content in dataset.content', () => {
    const wrap = appendMessage('user', 'raw text');
    expect(wrap.dataset.content).toBe('raw text');
  });

  it('removes the empty placeholder when appending', () => {
    const area = document.getElementById('chatArea');
    area.innerHTML = '<div class="empty">nothing</div>';
    appendMessage('user', 'hi');
    expect(area.querySelector('.empty')).toBeNull();
  });

  it('adds action buttons for a user message', () => {
    const wrap = appendMessage('user', 'hi');
    expect(wrap.querySelector('.msg-actions')).not.toBeNull();
  });
});

describe('createMsgActions', () => {
  it('user actions include copy + regenerate, no save', () => {
    const el = createMsgActions('user');
    expect(el.querySelector('.copy-btn')).not.toBeNull();
    expect(el.querySelector('.regenerate-btn')).not.toBeNull();
    expect(el.querySelector('.save-report-btn')).toBeNull();
  });

  it('assistant actions include copy + save, no regenerate', () => {
    const el = createMsgActions('assistant');
    expect(el.querySelector('.copy-btn')).not.toBeNull();
    expect(el.querySelector('.save-report-btn')).not.toBeNull();
    expect(el.querySelector('.regenerate-btn')).toBeNull();
  });

  it('assistant WITHOUT message id has no feedback controls', () => {
    const el = createMsgActions('assistant');
    expect(el.querySelector('.msg-feedback')).toBeNull();
    expect(el.querySelector('.fb-comment-row')).toBeNull();
  });

  it('assistant WITH message id renders verdict buttons + hidden comment row', () => {
    const el = createMsgActions('assistant', 42);
    const fb = el.querySelector('.msg-feedback');
    expect(fb).not.toBeNull();
    expect(fb.dataset.mid).toBe('42');
    expect(el.querySelectorAll('.fb-btn').length).toBe(3);
    const row = el.querySelector('.fb-comment-row');
    expect(row).not.toBeNull();
    expect(row.style.display).toBe('none');           // 默认隐藏
    expect(el.querySelector('.fb-comment-input')).not.toBeNull();
    expect(el.querySelector('.fb-comment-submit')).not.toBeNull();
  });
});

describe('feedback interaction (chatArea delegated handler)', () => {
  let fetchCalls;
  beforeEach(() => {
    fetchCalls = [];
    globalThis.fetch = vi.fn(async (url, opts) => {
      fetchCalls.push({ url, opts });
      return { ok: true, status: 200, json: async () => ({ ok: true }), text: async () => '{}' };
    });
  });

  function mountAssistantWithFeedback(mid = 7) {
    const area = document.getElementById('chatArea');
    const wrap = document.createElement('div');
    wrap.className = 'msg assistant';
    wrap.appendChild(createMsgActions('assistant', mid));
    area.appendChild(wrap);
    return wrap;
  }

  it('clicking a verdict posts feedback and reveals the comment row', async () => {
    const wrap = mountAssistantWithFeedback(7);
    wrap.querySelector('.fb-btn[data-verdict="useless"]').click();
    await vi.waitFor(() => expect(fetchCalls.length).toBeGreaterThan(0));

    const call = fetchCalls.find((c) => c.url === '/api/feedback');
    expect(call).toBeTruthy();
    const body = JSON.parse(call.opts.body);
    expect(body.target_type).toBe('chat');
    expect(body.target_id).toBe('7');
    expect(body.verdict).toBe('useless');

    // 评价记录到 dataset，评论行展开（均在 await 之后写入）
    const fb = wrap.querySelector('.msg-feedback');
    await vi.waitFor(() => expect(fb.dataset.verdict).toBe('useless'));
    expect(wrap.querySelector('.fb-comment-row').style.display).toBe('flex');
    // 固定可见：脱离 :hover 依赖（中文输入法候选窗不会让评论框淡出）
    expect(wrap.querySelector('.msg-actions').classList.contains('fb-pinned')).toBe(true);
  });

  it('submitting a comment posts verdict + comment text', async () => {
    const wrap = mountAssistantWithFeedback(9);
    wrap.querySelector('.fb-btn[data-verdict="wrong"]').click();
    // 等评价异步落库完成（dataset.verdict 在 await 之后才写入）
    await vi.waitFor(() => expect(wrap.querySelector('.msg-feedback').dataset.verdict).toBe('wrong'));
    fetchCalls.length = 0;   // 清掉第一次（评价）调用

    wrap.querySelector('.fb-comment-input').value = 'GC advice was off';
    wrap.querySelector('.fb-comment-submit').click();
    await vi.waitFor(() => expect(fetchCalls.length).toBeGreaterThan(0));

    const body = JSON.parse(fetchCalls[0].opts.body);
    expect(body.verdict).toBe('wrong');
    expect(body.comment).toBe('GC advice was off');
    expect(body.target_id).toBe('9');
  });
});

describe('renderStep', () => {
  let container;
  beforeEach(() => {
    container = document.createElement('div');
  });

  it('escapes thought / action / action_input (XSS defense)', () => {
    renderStep(container, {
      thought: '<img src=x onerror=alert(1)>',
      action: 'analyze_gc_log',
      action_input: '<b>fid</b>',
      observation: '',
      final_answer: null,
    });
    expect(container.querySelector('img')).toBeNull();
    expect(container.innerHTML).toContain('&lt;img src=x onerror=alert(1)&gt;');
  });

  it('renders observation block with expand hint', () => {
    renderStep(container, {
      thought: 't', action: 'a', action_input: 'i',
      observation: 'big output', final_answer: null,
    });
    expect(container.querySelector('.obs')).not.toBeNull();
    expect(container.querySelector('.obs-hint')).not.toBeNull();
  });

  it('renders final answer row when present', () => {
    renderStep(container, {
      thought: '', action: '', action_input: '',
      observation: '', final_answer: 'done',
    });
    expect(container.querySelector('.final')).not.toBeNull();
    expect(container.innerHTML).toContain('done');
  });
});

describe('toggleObs', () => {
  it('toggles expanded class on the observation element', () => {
    document.body.insertAdjacentHTML('beforeend',
      '<div id="obs_test" class="obs">x</div><div class="obs-hint">expand</div>');
    toggleObs('obs_test');
    expect(document.getElementById('obs_test').classList.contains('expanded')).toBe(true);
    toggleObs('obs_test');
    expect(document.getElementById('obs_test').classList.contains('expanded')).toBe(false);
  });

  it('no-op for missing element', () => {
    expect(() => toggleObs('nope')).not.toThrow();
  });
});

describe('renderMessages', () => {
  it('renders empty placeholder when no messages', () => {
    renderMessages([]);
    expect(document.getElementById('chatArea').querySelector('.empty')).not.toBeNull();
  });

  it('renders one bubble per message', () => {
    renderMessages([
      { role: 'user', content: 'q' },
      { role: 'assistant', content: 'a' },
    ]);
    const bubbles = document.getElementById('chatArea').querySelectorAll('.msg');
    expect(bubbles.length).toBe(2);
  });
});

describe('renderFacts', () => {
  beforeEach(() => {
    document.getElementById('factsList').innerHTML = '';
  });

  it('shows none-hint when empty', () => {
    renderFacts([]);
    expect(document.getElementById('factsList').textContent.length).toBeGreaterThan(0);
  });

  it('escapes fact content (XSS defense)', () => {
    renderFacts(['<img src=x onerror=alert(1)>']);
    const list = document.getElementById('factsList');
    expect(list.querySelector('img')).toBeNull();
    expect(list.innerHTML).toContain('&lt;img');
  });

  it('renders one item per fact with a delete affordance', () => {
    renderFacts(['fact one', 'fact two']);
    const items = document.getElementById('factsList').querySelectorAll('.fact-item');
    expect(items.length).toBe(2);
    expect(items[0].querySelector('.x')).not.toBeNull();
  });
});

describe('sendMessage stop button', () => {
  let fetchCalls;
  let readController;

  beforeEach(() => {
    fetchCalls = [];
    readController = null;
    state.isStreaming = false;
    state.llmConfigured = true;
    state.currentSessionId = 'sess-123';
    document.getElementById('msg').value = 'hi';
    const btn = document.getElementById('sendBtn');
    btn.classList.remove('stop-mode');
    btn.textContent = '';
    btn.disabled = false;
    app.loadSessions = vi.fn();
    app.updateQuotaUI = vi.fn();

    globalThis.fetch = vi.fn(async (url, opts) => {
      fetchCalls.push({ url, opts });
      if (url === '/api/chat/stream') {
        let resolveRead;
        let rejectRead;
        const readPromise = new Promise((r, rej) => { resolveRead = r; rejectRead = rej; });
        readController = { resolve: resolveRead, reject: rejectRead };
        if (opts.signal) {
          opts.signal.onabort = () => {
            rejectRead(new DOMException('Aborted', 'AbortError'));
          };
        }
        return {
          ok: true,
          status: 200,
          body: {
            getReader: () => ({
              read: () => readPromise,
              cancel: vi.fn(),
            }),
          },
        };
      }
      return { ok: true, status: 200, json: async () => ({ ok: true }), text: async () => '{}' };
    });
  });

  it('toggles send button to stop mode while streaming and returns after stream ends', async () => {
    const btn = document.getElementById('sendBtn');
    btn.click();
    await vi.waitFor(() => expect(btn.classList.contains('stop-mode')).toBe(true));
    expect(fetchCalls.some((c) => c.url === '/api/chat/stream')).toBe(true);

    readController.resolve({ done: true });
    await vi.waitFor(() => expect(btn.classList.contains('stop-mode')).toBe(false));
  });

  it('clicking stop button aborts fetch and calls /api/chat/stop', async () => {
    const btn = document.getElementById('sendBtn');
    btn.click();
    await vi.waitFor(() => expect(btn.classList.contains('stop-mode')).toBe(true));

    btn.click();
    await vi.waitFor(() => expect(fetchCalls.some((c) => c.url === '/api/chat/stop')).toBe(true));
    const stopCall = fetchCalls.find((c) => c.url === '/api/chat/stop');
    expect(stopCall.opts.method).toBe('POST');
    expect(JSON.parse(stopCall.opts.body).session_id).toBe('sess-123');

    await vi.waitFor(() => expect(btn.classList.contains('stop-mode')).toBe(false));
  });

  it('removes the blinking cursor after stop is clicked', async () => {
    const btn = document.getElementById('sendBtn');
    btn.click();
    await vi.waitFor(() => expect(btn.classList.contains('stop-mode')).toBe(true));
    expect(document.querySelector('.typing-cursor')).not.toBeNull();

    btn.click();
    await vi.waitFor(() => expect(btn.classList.contains('stop-mode')).toBe(false));
    expect(document.querySelector('.typing-cursor')).toBeNull();
  });
});
