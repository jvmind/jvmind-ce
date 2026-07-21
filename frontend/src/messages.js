import { state } from "./state.js";
import { app } from "./app.js";
import { csrfHeaders, escapeHtml, parseSSE, i18nText } from "./shared.js";
import { api } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { t, th, getLang } from "../i18n/index.js";
import { ico } from './icons.js';

const _s = s => s.replace(/[\u{1F300}-\u{1F9FF}\u{2600}-\u{27BF}]/gu, '').trim();

export function renderMessages(msgs) {
  const area = document.getElementById("chatArea");
  area.innerHTML = "";
  if (!msgs.length) {
    area.innerHTML = '<div class="empty"><h2>' + th("chat.start_title") + '</h2><div>' + t("chat.start_desc") + '</div></div>';
    return;
  }
  for (const m of msgs) {
    appendMessage(m.role, m.content, m.author_id, m.id);
  }
  scrollToBottom();
  // 回显历史 assistant 消息已有的反馈评价（避免刷新后状态丢失）
  Promise.resolve(hydrateFeedbackStates()).catch(console.warn);
}

// 团队会话发言人名映射（user_id -> username），由会话加载时设置
let _authorNames = {};
export function setAuthorNames(map) {
  _authorNames = map || {};
}

export function appendMessage(role, content, authorId, messageId) {
  const area = document.getElementById("chatArea");
  // 清空 empty
  const empty = area.querySelector(".empty");
  if (empty) empty.remove();

  const wrap = document.createElement("div");
  wrap.className = "msg " + role;
  wrap.dataset.content = content;
  const avatar = role === "user" ? ico('user') : ico('bot');
  // 团队会话中，user 消息显示发言人名（独占一行，不破坏 avatar+bubble 的 flex 布局）
  const authorName = (role === "user" && authorId && _authorNames[authorId]) ? _authorNames[authorId] : "";
  const authorTag = authorName ? `<div class="msg-author" style="flex-basis:100%;font-size:11px;color:var(--text-dim);margin-bottom:-4px;${role === "user" ? "text-align:right;" : ""}">${escapeHtml(authorName)}</div>` : "";
  wrap.innerHTML = `
    ${authorTag}
    <div class="avatar">${avatar}</div>
    <div class="bubble"></div>
  `;
  if (content) {
    if (role === "user") {
      wrap.querySelector(".bubble").innerHTML = escapeHtml(content).replace(/\n/g, "<br>");
    } else {
      wrap.querySelector(".bubble").innerHTML = renderMarkdown(content);
    }
  }
  area.appendChild(wrap);

  // 非流式消息直接追加操作按钮（历史 assistant 消息带上 message id 以支持反馈）
  if (role === "user" || content) {
    wrap.appendChild(createMsgActions(role, role === "assistant" ? messageId : undefined));
  }
  return wrap;
}

// 回显当前已渲染的 assistant 消息的反馈评价（页面加载/会话切换后调用）
async function hydrateFeedbackStates() {
  const spans = document.querySelectorAll("#chatArea .msg-feedback[data-mid]");
  for (const fbSpan of spans) {
    const mid = fbSpan.dataset.mid;
    if (!mid) continue;
    try {
      const r = await api(`/api/feedback/chat/${encodeURIComponent(mid)}`);
      const fb = r && r.feedback;
      if (!fb || !fb.verdict) continue;
      fbSpan.dataset.verdict = fb.verdict;
      fbSpan.querySelectorAll(".fb-btn").forEach((b) => {
        b.classList.toggle("fb-active", b.dataset.verdict === fb.verdict);
      });
      const actions = fbSpan.closest(".msg-actions");
      if (actions) actions.classList.add("fb-pinned");
    } catch (e) {
      console.warn("feedback hydrate failed", e);
    }
  }
}

export function scrollToBottom() {
  const area = document.getElementById("chatArea");
  area.scrollTop = area.scrollHeight;
}

// ---------- 记忆 ----------
export function renderFacts(facts) {
  const el = document.getElementById("factsList");
  el.innerHTML = "";
  if (!facts.length) {
    el.innerHTML = '<div style="color:var(--text-dim);font-size:12px;">' + t("fact.none") + '</div>';
    return;
  }
  facts.forEach((f, i) => {
    const item = document.createElement("div");
    item.className = "fact-item";
    item.innerHTML = `<span>${escapeHtml(f)}</span><span class="x" title="${t("sidebar.fact_delete")}">×</span>`;
    item.querySelector(".x").onclick = async () => {
      const r = await api(`/api/sessions/${state.currentSessionId}/facts/${i}`, { method: "DELETE" });
      renderFacts(r.facts);
    };
    el.appendChild(item);
  });
}

document.getElementById("factInput").addEventListener("keydown", async (e) => {
  if (e.key === "Enter" && e.target.value.trim() && state.currentSessionId) {
    const r = await api(`/api/sessions/${state.currentSessionId}/facts`, {
      method: "POST",
      body: JSON.stringify({ fact: e.target.value.trim() }),
    });
    renderFacts(r.facts);
    e.target.value = "";
  }
});

// ---------- 消息操作按钮 ----------
export function createMsgActions(role, messageId) {
  const div = document.createElement("div");
  div.className = "msg-actions";
  const copy = `<button class="msg-action-btn copy-btn" title="${_s(t("chat.copy_btn"))}">${ico('copy')}</button>`;
  const regen = role === "user" ? `<button class="msg-action-btn regenerate-btn" title="${_s(t("chat.regenerate_btn"))}">${ico('refresh-cw')}</button>` : "";
  const save = role === "assistant" ? `<button class="msg-action-btn save-report-btn" title="${_s(t("chat.save_to_report"))}">${ico('download')}</button>` : "";
  // 诊断反馈（飞轮采集）：仅对有稳定 message_id 的 assistant 消息展示
  let feedback = "";
  if (role === "assistant" && messageId != null) {
    const mid = escapeHtml(String(messageId));
    feedback =
      `<span class="msg-feedback" data-mid="${mid}" data-target-type="chat">` +
      `<button class="msg-action-btn fb-btn" data-verdict="useful" title="${_s(t("chat.feedback_useful"))}">${ico('thumbs-up')}</button>` +
      `<button class="msg-action-btn fb-btn" data-verdict="useless" title="${_s(t("chat.feedback_useless"))}">${ico('thumbs-down')}</button>` +
      `<button class="msg-action-btn fb-btn" data-verdict="wrong" title="${_s(t("chat.feedback_wrong"))}">${ico('triangle-alert')}</button>` +
      `</span>` +
      `<span class="fb-comment-row" style="display:none;flex-basis:100%;gap:6px;margin-top:4px;">` +
      `<input type="text" class="fb-comment-input" maxlength="2000" placeholder="${t("chat.feedback_comment_placeholder")}" ` +
      `style="flex:1;min-width:160px;background:var(--bg-3);border:1px solid var(--border);border-radius:4px;padding:4px 8px;color:var(--text);font-size:12px;outline:none;">` +
      `<button class="msg-action-btn fb-comment-submit">${t("chat.feedback_submit")}</button>` +
      `</span>`;
  }
  div.innerHTML = copy + regen + save + feedback;
  return div;
}

async function postFeedback(fbSpan, verdict, comment) {
  const targetId = fbSpan.dataset.mid;
  const targetType = fbSpan.dataset.targetType || "chat";
  if (!targetId || !verdict) return false;
  try {
    await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({
        target_type: targetType,
        target_id: targetId,
        verdict,
        comment: comment || "",
        session_id: state.currentSessionId || null,
      }),
    });
    return true;
  } catch (e) {
    console.warn("feedback submit failed", e);
    return false;
  }
}

async function submitFeedback(fbSpan, verdict) {
  const ok = await postFeedback(fbSpan, verdict, "");
  if (!ok) return;
  // 记录当前评价，并高亮已选项
  fbSpan.dataset.verdict = verdict;
  fbSpan.querySelectorAll(".fb-btn").forEach((b) => {
    b.classList.toggle("fb-active", b.dataset.verdict === verdict);
  });
  // 展示评论输入框：负面评价默认展开（最有价值的迭代信号），正面也允许补充
  const actions = fbSpan.closest(".msg-actions");
  const row = actions ? actions.querySelector(".fb-comment-row") : null;
  if (row) row.style.display = "flex";
  // 固定显示，脱离 :hover 依赖（防止中文输入法候选窗导致整块淡出）
  if (actions) actions.classList.add("fb-pinned");
}

document.getElementById("chatArea").addEventListener("click", async (e) => {
  const commentBtn = e.target.closest(".fb-comment-submit");
  if (commentBtn) {
    const actions = commentBtn.closest(".msg-actions");
    const fbSpan = actions ? actions.querySelector(".msg-feedback") : null;
    const input = actions ? actions.querySelector(".fb-comment-input") : null;
    if (fbSpan && input && fbSpan.dataset.verdict) {
      const ok = await postFeedback(fbSpan, fbSpan.dataset.verdict, input.value.trim());
      if (ok) {
        commentBtn.textContent = t("chat.feedback_thanks");
        commentBtn.disabled = true;
      }
    }
    return;
  }
  const fbBtn = e.target.closest(".fb-btn");
  if (fbBtn) {
    const fbSpan = fbBtn.closest(".msg-feedback");
    if (fbSpan) await submitFeedback(fbSpan, fbBtn.dataset.verdict);
    return;
  }
  const copyBtn = e.target.closest(".copy-btn");
  if (copyBtn) {
    const text = copyBtn.closest(".msg").dataset.content;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      copyBtn.innerHTML = ico('check');
      setTimeout(() => { copyBtn.innerHTML = ico('copy'); }, 2000);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta);
      ta.select(); document.execCommand("copy");
      document.body.removeChild(ta);
      copyBtn.innerHTML = ico('check');
      setTimeout(() => { copyBtn.innerHTML = ico('copy'); }, 2000);
    }
    return;
  }
  const regenBtn = e.target.closest(".regenerate-btn");
  if (regenBtn) {
    const text = regenBtn.closest(".msg").dataset.content;
    if (text) sendMessage(text);
  }
  const saveBtn = e.target.closest(".save-report-btn");
  if (saveBtn) {
    const text = saveBtn.closest(".msg").dataset.content;
    if (!text) {
      saveBtn.innerHTML = ico('triangle-alert');
      setTimeout(() => { saveBtn.textContent = t("chat.save_to_report"); }, 2000);
      return;
    }
    app.saveToReport('gc', text, saveBtn);
  }
});

// ---------- 流式对话 ----------
export async function sendMessage(explicitText) {
  if (state.isStreaming) return;
  if (!state.llmConfigured) {
    document.getElementById("configPrompt").style.display = "block";
    return;
  }
  const ta = document.getElementById("msg");
  const text = typeof explicitText === "string" ? explicitText : ta.value.trim();
  if (!text) return;
  if (!state.currentSessionId) {
    await app.newSession();
  }

  if (explicitText === undefined) {
    ta.value = "";
    ta.style.height = "auto";
  }

  // 1) 立即追加用户消息
  appendMessage("user", text);
  scrollToBottom();

  // 2) 创建一个 assistant 占位气泡
  const wrap = appendMessage("assistant", "");
  scrollToBottom();
  const bubble = wrap.querySelector(".bubble");

  // 思考区 & 轨迹区
  const toolCardsEl = document.createElement("div");
  toolCardsEl.className = "tool-cards";
  bubble.appendChild(toolCardsEl);

  const finalDiv = document.createElement("div");
  finalDiv.className = "final-content";
  bubble.appendChild(finalDiv);
  finalDiv.innerHTML = '<span class="typing-cursor"></span>';

  const traceEl = document.createElement("div");
  traceEl.className = "trace";
  traceEl.innerHTML = `
    <div class="trace-header">
      <span>${th("chat.trace_debug_title")}</span>
      <span class="step-count"></span>
      <span class="caret">▶</span>
    </div>
    <div class="trace-body"></div>
  `;
  traceEl.querySelector(".trace-header").onclick = () => traceEl.classList.toggle("open");
  bubble.appendChild(traceEl);
  const traceBody = traceEl.querySelector(".trace-body");
  const stepCountEl = traceEl.querySelector(".step-count");
  let stepCounter = 0;

  const controller = new AbortController();

  function setStopMode() {
    const btn = document.getElementById("sendBtn");
    btn.textContent = t("chat.stop");
    btn.classList.add("stop-mode");
    btn.disabled = false;
    btn.onclick = () => {
      btn.disabled = true;
      api("/api/chat/stop", {
        method: "POST",
        body: JSON.stringify({ session_id: state.currentSessionId }),
      }).catch(() => {});
      finalizeCardsOnDone(toolCardsEl);
      controller.abort();
    };
  }

  function resetSendMode() {
    const btn = document.getElementById("sendBtn");
    btn.textContent = t("chat.send");
    btn.classList.remove("stop-mode");
    btn.disabled = false;
    btn.onclick = () => sendMessage();
  }

  state.isStreaming = true;
  setStopMode();

  let finalBuf = "";
  let hadStreamError = false;
  let streamErrorText = "";
  let finalMessageId = null;

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      credentials: "same-origin",
      signal: controller.signal,
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ session_id: state.currentSessionId, message: text, lang: getLang(), report_contexts: state.activeReportContexts }),
    });
    if (!res.ok) {
      const _txt = await res.text();
      let _detail = _txt;
      try { const _json = JSON.parse(_txt); _detail = _json.detail || _txt; } catch {}
      throw new Error(i18nText(_detail));
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      // 解析 SSE: 兼容 \n\n 和 \r\n\r\n 分块
      let m;
      while ((m = buf.match(/\r?\n\r?\n/))) {
        const idx = m.index;
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + m[0].length);
        const ev = parseSSE(raw);
        if (ev) handleEvent(ev);
      }
    }
    buf += decoder.decode();
    if (buf.trim()) {
      const ev = parseSSE(buf);
      if (ev) handleEvent(ev);
    }

    function handleEvent({ event, data }) {
      const type = data.type;
      if (type === "token") {
        if (data.phase === "reason") {
          appendThinkingToken(toolCardsEl, data.content);
        } else if (data.phase === "final") {
          finalBuf += data.content;
          finalDiv.innerHTML = renderMarkdown(finalBuf) + '<span class="typing-cursor"></span>';
        }
        scrollToBottom();
      } else if (type === "step") {
        stepCounter++;
        stepCountEl.textContent = `(${stepCounter})`;
        renderStep(traceBody, data.step);
        scrollToBottom();
      } else if (type === "tool_start") {
        upsertToolCard(toolCardsEl, {
          event: "start",
          tool_call_id: data.tool_call_id,
          name: data.name,
          args: data.args,
        });
        scrollToBottom();
      } else if (type === "tool_end") {
        upsertToolCard(toolCardsEl, {
          event: "end",
          tool_call_id: data.tool_call_id,
          name: data.name,
          args: data.args,
          observation: data.observation,
          status: data.status,
        });
        scrollToBottom();
      } else if (type === "step.progress") {
        updateToolCardProgress(toolCardsEl, {
          tool_call_id: data.tool_call_id,
          tool: data.tool,
          msg: data.msg,
        });
      } else if (type === "final") {
        // 若已通过流式 token 累积出正文，则仅在内容不一致时才重渲染，
        // 否则只是去掉打字光标，避免气泡内容“整段一次性刷新”的跳变。
        if (data.content !== finalBuf) {
          finalBuf = data.content;
        }
        if (data.message_id != null) finalMessageId = data.message_id;
        finalDiv.innerHTML = renderMarkdown(finalBuf);
        // (handled by helpers; no-op)
        finalizeCardsOnDone(toolCardsEl);
        scrollToBottom();
      } else if (type === "fact_added") {
        // 刷新 facts
        api(`/api/sessions/${state.currentSessionId}/facts`).then(r => renderFacts(r.facts));
      } else if (type === "error") {
        hadStreamError = true;
        streamErrorText = data.content || "";
        finalDiv.innerHTML = `<span style="color:var(--red)">${ico('x')} ${escapeHtml(streamErrorText)}</span>`;
        finalizeCardsOnDone(toolCardsEl);
      } else if (type === "done") {
        if (data.message_id != null) finalMessageId = data.message_id;
        finalizeCardsOnDone(toolCardsEl);
        if (hadStreamError) {
          finalDiv.innerHTML = `<span style="color:var(--red)">${ico('x')} ${escapeHtml(streamErrorText)}</span>`;
        } else {
          finalDiv.innerHTML = renderMarkdown(finalBuf || t("chat.no_reply"));
        }
        // (no-op: thinking/title references removed)
        scrollToBottom();
        app.updateQuotaUI();
        wrap.dataset.content = hadStreamError ? streamErrorText : finalBuf;
        if (!wrap.querySelector('.msg-actions')) {
          wrap.appendChild(createMsgActions("assistant", hadStreamError ? null : finalMessageId));
        }
      }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      // user clicked stop; content already rendered, just stop
    } else {
      finalDiv.innerHTML = `<span style="color:var(--red)">${ico('x')} ${escapeHtml(e.message)}</span>`;
      const msg = (e.message || "").toLowerCase();
      if (msg.includes("not found") || msg.includes("不存在")) {
        state.currentSessionId = null;
      }
    }
    // Disconnect / network failure / abort without terminal event — flip any
    // still-spinning cards to their terminal state so the UI doesn't freeze.
    finalizeCardsOnDone(toolCardsEl);
  } finally {
    state.isStreaming = false;
    resetSendMode();
    // 如果流被中止而未收到 done/final，去掉闪烁光标并补全操作按钮
    if (!hadStreamError && finalDiv.querySelector(".typing-cursor")) {
      finalDiv.innerHTML = renderMarkdown(finalBuf || "");
      wrap.dataset.content = finalBuf;
      if (!wrap.querySelector(".msg-actions")) {
        wrap.appendChild(createMsgActions("assistant", finalMessageId));
      }
    }
    // 刷新会话列表（更新 msg_count / 标题）
    app.loadSessions();
    // 自己发消息会 bump updated_at，刷新更新提示基线避免误触发
    if (typeof app.refreshSessionUpdateBaseline === "function") {
      Promise.resolve(app.refreshSessionUpdateBaseline()).catch(console.warn);
    }
  }
}

export function renderStep(container, step) {
  const div = document.createElement("div");
  div.className = "step";
  let html = "";
  if (step.thought) {
    html += `<div class="row thought-action"><span class="label">${ico('lightbulb')}</span> <span class="thought">${escapeHtml(step.thought)}</span>`;
    if (step.action) {
      html += ` → <span class="label">${ico('wrench')}</span> <span class="action">${escapeHtml(step.action)}</span> <span class="input">${escapeHtml(step.action_input)}</span>`;
    }
    html += `</div>`;
  } else if (step.action) {
    html += `<div class="row"><span class="label">${ico('wrench')}</span> <span class="action">${escapeHtml(step.action)}</span> <span class="input">${escapeHtml(step.action_input)}</span></div>`;
  }
  if (step.observation) {
    const obsId = "obs_" + (++renderStep._counter || (renderStep._counter = 0));
    html += `<div class="obs" id="${obsId}" onclick="toggleObs('${obsId}')"><span class="label">${th("chat.observation")}</span> ${escapeHtml(step.observation)}</div>`;
    html += `<div class="obs-hint" onclick="toggleObs('${obsId}')">${t("chat.expand")}</div>`;
  }
  if (step.final_answer) {
    html += `<div class="row"><span class="label">${ico('check')}</span> <span class="final">${escapeHtml(step.final_answer)}</span></div>`;
  }
  div.innerHTML = html;
  container.appendChild(div);
}

export function toggleObs(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle("expanded");
  const hint = el.nextElementSibling;
  if (hint && hint.classList.contains("obs-hint")) {
    hint.textContent = el.classList.contains("expanded") ? t("chat.collapse") : t("chat.expand");
  }
}
window.toggleObs = toggleObs;

// ---------- 事件绑定 ----------
document.getElementById("sendBtn").onclick = () => sendMessage();
document.getElementById("msg").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});
document.getElementById("msg").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = Math.min(e.target.scrollHeight, 200) + "px";
});

// ---------- Inline thinking/tool cards (Tool Call Inline Cards) ----------

function _getCardState(container) {
  if (!container._cardState) {
    container._cardState = {
      thinkingCard: null,
      thinkingIndex: 0,
      toolCards: new Map(),
      cardByName: new Map(),
      cardIds: new WeakMap(),
      recentToolKey: null,
      recentToolCount: 0,
    };
  }
  return container._cardState;
}

function _escapeText(s) {
  return String(s == null ? "" : s);
}

function _createThinkingCard(container, n) {
  const card = document.createElement("div");
  card.className = "thinking-card";
  card.dataset.status = "streaming";
  card.dataset.tcid = "think-" + n;
  card.innerHTML = `
    <div class="thinking-card-header">
      <span class="tool-icon"></span>
      <span class="card-title"></span>
      <span class="card-status"><span class="status-text"></span></span>
      <span class="caret">▶</span>
    </div>
    <div class="thinking-card-body" hidden>
      <div class="thinking-content"></div>
    </div>
  `;
  card.querySelector(".tool-icon").innerHTML = ico("lightbulb");
  card.querySelector(".card-title").textContent = t("chat.thinking_card_title", { n });
  card.querySelector(".status-text").textContent = t("chat.thinking_streaming");
  card.querySelector(".thinking-card-header").onclick = () => toggleCard(card);
  container.appendChild(card);
  return card;
}

function _createToolCard(container, tcid, name) {
  const card = document.createElement("div");
  card.className = "tool-card";
  card.dataset.status = "running";
  card.dataset.tcid = tcid;
  card.innerHTML = `
    <div class="tool-card-header">
      <span class="tool-icon tool-icon-running"></span>
      <span class="tool-icon tool-icon-done"></span>
      <span class="card-title"></span>
      <span class="card-times" hidden>× 1</span>
      <span class="card-status"><span class="spinner"></span><span class="status-text"></span></span>
      <span class="caret">▶</span>
    </div>
    <div class="tool-card-body" hidden>
      <div class="tool-args-label"></div>
      <pre class="tool-args"></pre>
      <div class="tool-obs-label"></div>
      <div class="tool-obs"></div>
    </div>
  `;
  card.querySelector(".tool-icon-running").innerHTML = ico("wrench");
  card.querySelector(".tool-icon-done").innerHTML = ico("check");
  card.querySelector(".card-title").textContent = _escapeText(name);
  card.querySelector(".tool-args-label").textContent = t("chat.tool_args_label");
  card.querySelector(".tool-obs-label").textContent = t("chat.observation");
  card.querySelector(".status-text").textContent = t("chat.tool_running");
  card.querySelector(".tool-card-header").onclick = () => toggleCard(card);
  container.appendChild(card);
  return card;
}

function _formatArgs(args) {
  if (!args || typeof args !== "string") return "{}";
  const trimmed = args.trim();
  if (!trimmed) return "{}";
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch (_) {
    return args;
  }
}

export function appendThinkingToken(container, text) {
  if (!container || text == null) return;
  const state = _getCardState(container);
  if (!state.thinkingCard) {
    state.thinkingIndex += 1;
    state.thinkingCard = _createThinkingCard(container, state.thinkingIndex);
  }
  const el = state.thinkingCard.querySelector(".thinking-content");
  el.textContent += text;
  el.scrollTop = el.scrollHeight;
}

export function upsertToolCard(container, payload) {
  if (!container || !payload || !payload.event) return;
  const state = _getCardState(container);
  const event = payload.event;

  if (event === "start") {
    // Freeze the streaming thinking-card (if any).
    if (state.thinkingCard) {
      state.thinkingCard.dataset.status = "done";
      const st = state.thinkingCard.querySelector(".status-text");
      if (st) st.textContent = t("chat.thinking_done");
      state.thinkingCard = null;
    }
    const tcid = _escapeText(payload.tool_call_id || "call_" + Math.random().toString(36).slice(2, 8));
    const name = _escapeText(payload.name || "");
    const argsBuf = _escapeText(payload.args || "");

    let card;
    if (name && state.cardByName.has(name)) {
      // Dedup: merge this call into the existing card with the same name.
      card = state.cardByName.get(name);
      state.toolCards.set(tcid, card);
      let ids = state.cardIds.get(card);
      if (!ids) {
        ids = new Set();
        state.cardIds.set(card, ids);
      }
      ids.add(tcid);
      const badge = card.querySelector(".card-times");
      if (badge) {
        badge.textContent = "× " + ids.size;
        badge.hidden = false;
      }
      return card;
    }

    card = _createToolCard(container, tcid, name);
    card.querySelector(".tool-args").textContent = _formatArgs(argsBuf);
    state.toolCards.set(tcid, card);
    state.cardIds.set(card, new Set([tcid]));
    if (name) state.cardByName.set(name, card);
    state.recentToolKey = name;
    state.recentToolCount = 1;
    return card;
  }

  if (event === "end") {
    const tcid = _escapeText(payload.tool_call_id || "");
    let card = state.toolCards.get(tcid);
    if (!card) {
      // Defensive: orphan end event. Create a one-shot done card immediately.
      card = _createToolCard(container, tcid, payload.name || "");
      card.querySelector(".tool-args").textContent = _formatArgs(payload.args || "");
    } else {
      // Refresh authoritative name/args from tool_end so the card reflects
      // the final values, even if tool_start was emitted with partial data.
      if (payload.name) {
        card.querySelector(".card-title").textContent = _escapeText(payload.name);
      }
      if (payload.args) {
        card.querySelector(".tool-args").textContent = _formatArgs(payload.args);
      }
    }
    const ids = state.cardIds.get(card);
    state.toolCards.delete(tcid);
    if (ids) {
      ids.delete(tcid);
    }
    if (!ids || ids.size === 0) {
      // Last tracked id for this card ended → finalize.
      const status = payload.status === "error" ? "error" : "done";
      card.dataset.status = status;
      const statusText = card.querySelector(".status-text");
      if (statusText) {
        statusText.textContent = status === "error"
          ? t("chat.tool_error_label")
          : t("chat.tool_done");
      }
      const obsEl = card.querySelector(".tool-obs");
      let obs = _escapeText(payload.observation || "");
      if (obs.length > 2000) {
        obs = obs.slice(0, 2000) + "…";
      }
      if (obsEl) obsEl.textContent = obs;

      // Drop dedup entry so the next call with the same name gets a fresh card.
      const cardName = card.querySelector(".card-title")?.textContent || "";
      if (cardName && state.cardByName.get(cardName) === card) {
        state.cardByName.delete(cardName);
      }
      state.cardIds.delete(card);
      state.recentToolKey = null;
      state.recentToolCount = 0;
    }
    return card;
  }
}

export function updateToolCardProgress(container, payload) {
  if (!container || !payload) return;
  const state = _getCardState(container);
  const tcid = _escapeText(payload.tool_call_id || "");
  let card = state.toolCards.get(tcid);
  if (!card && payload.tool) {
    // Fallback: match by tool name when tool_call_id is missing (legacy step.progress).
    // Only apply when exactly one running card has the matching tool name —
    // multiple matches means we cannot disambiguate safely.
    const candidates = Array.from(
      container.querySelectorAll('.tool-card[data-status="running"]'),
    ).filter((c) => c.querySelector(".card-title")?.textContent === payload.tool);
    if (candidates.length === 1) {
      card = candidates[0];
    }
  }
  if (!card) return;
  const statusText = card.querySelector(".status-text");
  if (statusText && payload.msg) statusText.textContent = _escapeText(payload.msg);
}

export function finalizeCardsOnDone(container) {
  if (!container) return;
  const state = _getCardState(container);
  if (state.thinkingCard) {
    state.thinkingCard.dataset.status = "done";
    const st = state.thinkingCard.querySelector(".status-text");
    if (st) st.textContent = t("chat.thinking_done");
    state.thinkingCard = null;
  }
  for (const card of state.toolCards.values()) {
    if (card.dataset.status === "running") {
      card.dataset.status = "cancelled";
      const st = card.querySelector(".status-text");
      if (st) st.textContent = t("chat.tool_cancelled");
    }
  }
  state.toolCards.clear();
  state.cardByName.clear();
  state.cardIds = new WeakMap();
  state.recentToolKey = null;
  state.recentToolCount = 0;
}

export function toggleCard(card) {
  if (!card) return;
  card.classList.toggle("open");
  const body = card.querySelector(".tool-card-body, .thinking-card-body");
  if (body) body.hidden = !card.classList.contains("open");
}
window.toggleCard = toggleCard;

Object.assign(app, {
  renderMessages, appendMessage, scrollToBottom, renderFacts,
  createMsgActions, sendMessage, renderStep, toggleObs, setAuthorNames,
  // Exported for tests + future use:
  appendThinkingToken, upsertToolCard, updateToolCardProgress,
  finalizeCardsOnDone, toggleCard,
});
