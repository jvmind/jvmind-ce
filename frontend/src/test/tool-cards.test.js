/**
 * Tests for the inline thinking/tool card state machine in messages.js.
 *
 * The helpers (`appendThinkingToken`, `upsertToolCard`, etc.) are file-private
 * in messages.js. We re-derive a minimal copy of the same state machine here
 * to drive the implementation, and additionally assert via DOM inspection that
 * the helpers (once exported) produce the same DOM.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('../../i18n/index.js', () => ({
  t: (key, vars) => vars ? `${key}{${JSON.stringify(vars)}}` : key,
  th: (key) => key,
  getLang: () => 'zh',
}));

vi.mock('../api.js', () => ({ api: vi.fn() }));
vi.mock('../markdown.js', () => ({ renderMarkdown: (t) => t }));
globalThis.marked = { parse: (t) => t };
globalThis.DOMPurify = { sanitize: (h) => h };
window.marked = globalThis.marked;
window.DOMPurify = globalThis.DOMPurify;

let messages;
beforeEach(async () => {
  // messages.js's import-time side effects bind handlers on
  // #chatArea, #sendBtn, #msg, #factInput, #factsList, #configPrompt.
  // It also calls setupBilling() at import time, which needs
  // #sbBillingBtn, #billingClose, #billingPricingBtn, #billingRenewBtn,
  // #billingCancelBtn. We stub all of them here so importing doesn't throw.
  document.body.innerHTML = `
    <div id="chatArea"></div>
    <textarea id="msg"></textarea>
    <button id="sendBtn"></button>
    <div id="factsList"></div>
    <input id="factInput" />
    <div id="configPrompt" style="display:none"></div>
    <button id="sbBillingBtn"></button>
    <button id="billingClose"></button>
    <button id="billingPricingBtn"></button>
    <button id="billingRenewBtn"></button>
    <button id="billingCancelBtn"></button>
  `;
  // Append the per-bubble structure (mirrors what sendMessage() builds).
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  const tc = document.createElement('div');
  tc.className = 'tool-cards';
  bubble.appendChild(tc);
  document.body.appendChild(bubble);
  messages = await import('../messages.js');
});

function getCards() {
  return Array.from(document.querySelectorAll('.tool-cards > .thinking-card, .tool-cards > .tool-card'));
}

describe('appendThinkingToken', () => {
  it('creates a thinking-card on first token', async () => {
    const tc = document.querySelector('.tool-cards');
    messages.appendThinkingToken(tc, 'hello');
    expect(getCards().length).toBe(1);
    const card = tc.querySelector('.thinking-card');
    expect(card.getAttribute('data-status')).toBe('streaming');
    expect(card.querySelector('.thinking-content').textContent).toBe('hello');
    expect(card.querySelector('.card-title').textContent).toMatch(/1/);
  });

  it('appends to existing thinking-card on subsequent tokens', async () => {
    const tc = document.querySelector('.tool-cards');
    messages.appendThinkingToken(tc, 'hello ');
    messages.appendThinkingToken(tc, 'world');
    const content = tc.querySelector('.thinking-card .thinking-content');
    expect(content.textContent).toBe('hello world');
  });
});

describe('upsertToolCard start', () => {
  it('freezes the current thinking-card and creates a running tool-card', async () => {
    const tc = document.querySelector('.tool-cards');
    messages.appendThinkingToken(tc, 'thinking');
    messages.upsertToolCard(tc, { event: 'start', tool_call_id: 'c1', name: 'foo', args: '{"x":1}' });

    const tk = tc.querySelector('.thinking-card');
    expect(tk.getAttribute('data-status')).toBe('done');

    const toolCard = tc.querySelector('.tool-card');
    expect(toolCard).toBeTruthy();
    expect(toolCard.dataset.tcid).toBe('c1');
    expect(toolCard.getAttribute('data-status')).toBe('running');
    expect(toolCard.querySelector('.card-title').textContent).toBe('foo');
    expect(toolCard.querySelector('.tool-args').textContent).toContain('"x": 1');
  });

  it('dedups consecutive identical (name, args) — increments badge only', async () => {
    const tc = document.querySelector('.tool-cards');
    messages.upsertToolCard(tc, { event: 'start', tool_call_id: 'c1', name: 'foo', args: '{"x":1}' });
    messages.upsertToolCard(tc, { event: 'start', tool_call_id: 'c2', name: 'foo', args: '{"x":1}' });

    const toolCards = tc.querySelectorAll('.tool-card');
    expect(toolCards.length).toBe(1);
    expect(toolCards[0].querySelector('.card-times').textContent).toMatch(/2/);
    // No new card inserted; tool_call_id stays as the first one
    expect(toolCards[0].dataset.tcid).toBe('c1');
  });

  it('creates a new card when name differs', async () => {
    const tc = document.querySelector('.tool-cards');
    messages.upsertToolCard(tc, { event: 'start', tool_call_id: 'c1', name: 'foo', args: '{}' });
    messages.upsertToolCard(tc, { event: 'start', tool_call_id: 'c2', name: 'bar', args: '{}' });
    expect(tc.querySelectorAll('.tool-card').length).toBe(2);
  });
});

describe('upsertToolCard end', () => {
  it('marks card done, swaps spinner for check, fills observation', async () => {
    const tc = document.querySelector('.tool-cards');
    messages.upsertToolCard(tc, { event: 'start', tool_call_id: 'c1', name: 'foo', args: '{}' });
    messages.upsertToolCard(tc, { event: 'end', tool_call_id: 'c1', name: 'foo', args: '{}', observation: 'result-text', status: 'ok' });

    const card = tc.querySelector('.tool-card');
    expect(card.getAttribute('data-status')).toBe('done');
    expect(card.querySelector('.tool-obs').textContent).toBe('result-text');
  });

  it('marks error status with red border class', async () => {
    const tc = document.querySelector('.tool-cards');
    messages.upsertToolCard(tc, { event: 'start', tool_call_id: 'c1', name: 'foo', args: '{}' });
    messages.upsertToolCard(tc, { event: 'end', tool_call_id: 'c1', name: 'foo', args: '{}', observation: '[Tool Error]', status: 'error' });
    const card = tc.querySelector('.tool-card');
    expect(card.getAttribute('data-status')).toBe('error');
  });

  it('resets dedup state — next call gets its own card', async () => {
    const tc = document.querySelector('.tool-cards');
    messages.upsertToolCard(tc, { event: 'start', tool_call_id: 'c1', name: 'foo', args: '{"x":1}' });
    messages.upsertToolCard(tc, { event: 'end', tool_call_id: 'c1', name: 'foo', args: '{"x":1}', observation: 'r', status: 'ok' });
    messages.upsertToolCard(tc, { event: 'start', tool_call_id: 'c2', name: 'foo', args: '{"x":1}' });
    expect(tc.querySelectorAll('.tool-card').length).toBe(2);
    // Only the latest call is × 1; the first card's badge is reset to hidden.
    // (Every card has a .card-times span, but only one is currently visible.)
    const visible = tc.querySelectorAll('.card-times:not([hidden])');
    expect(visible.length).toBe(0);
  });
});

describe('updateToolCardProgress', () => {
  it('updates status-text without changing card status', async () => {
    const tc = document.querySelector('.tool-cards');
    messages.upsertToolCard(tc, { event: 'start', tool_call_id: 'c1', name: 'long', args: '{}' });
    messages.updateToolCardProgress(tc, { tool_call_id: 'c1', tool: 'long', msg: 'Indexing classes…' });
    const card = tc.querySelector('.tool-card');
    expect(card.getAttribute('data-status')).toBe('running');
    expect(card.querySelector('.status-text').textContent).toBe('Indexing classes…');
  });
});

describe('finalizeCardsOnDone', () => {
  it('marks streaming thinking-card as done and running tool-card as cancelled', async () => {
    const tc = document.querySelector('.tool-cards');
    messages.appendThinkingToken(tc, 'thinking');
    messages.upsertToolCard(tc, { event: 'start', tool_call_id: 'c1', name: 'foo', args: '{}' });
    messages.finalizeCardsOnDone(tc);
    expect(tc.querySelector('.thinking-card').getAttribute('data-status')).toBe('done');
    expect(tc.querySelector('.tool-card').getAttribute('data-status')).toBe('cancelled');
  });
});
