// 诊断反馈 widget（报告型 gc/jstack 复用，避免在两处重复实现）。
// 渲染 thumbs-up/thumbs-down/triangle-alert + 评论框，提交到 /api/feedback，并回显已有评价。
import { api } from "./api.js";
import { escapeHtml } from "./shared.js";
import { t } from "../i18n/index.js";
import { ico } from './icons.js';

const _s = s => s.replace(/[\u{1F300}-\u{1F9FF}\u{2600}-\u{27BF}]/gu, '').trim();

// 生成 widget HTML（插入到报告 AI 结论区下方）
export function feedbackWidgetHtml(targetType, targetId) {
  const tid = escapeHtml(String(targetId));
  const tt = escapeHtml(String(targetType));
  // 注意：不复用聊天区的 .msg-actions 类（其 flex-basis:100% / flex-wrap 会污染报告面板布局），
  // 报告型用独立的 .report-feedback 类，仅复用通用的 .msg-action-btn / .fb-* 视觉样式。
  return (
    `<div class="report-feedback" data-mid="${tid}" data-target-type="${tt}">` +
    `<span class="msg-feedback" data-mid="${tid}" data-target-type="${tt}">` +
    `<button class="msg-action-btn fb-btn" data-verdict="useful" title="${_s(t("chat.feedback_useful"))}">${ico('thumbs-up')}</button>` +
    `<button class="msg-action-btn fb-btn" data-verdict="useless" title="${_s(t("chat.feedback_useless"))}">${ico('thumbs-down')}</button>` +
    `<button class="msg-action-btn fb-btn" data-verdict="wrong" title="${_s(t("chat.feedback_wrong"))}">${ico('triangle-alert')}</button>` +
    `</span>` +
    `<span class="fb-comment-row" style="display:none;">` +
    `<input type="text" class="fb-comment-input" maxlength="2000" placeholder="${t("chat.feedback_comment_placeholder")}">` +
    `<button class="msg-action-btn fb-comment-submit">${t("chat.feedback_submit")}</button>` +
    `</span>` +
    `</div>`
  );
}

async function postReportFeedback(root, verdict, comment) {
  const targetId = root.dataset.mid;
  const targetType = root.dataset.targetType;
  if (!targetId || !verdict) return false;
  try {
    await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({
        target_type: targetType,
        target_id: targetId,
        verdict,
        comment: comment || "",
      }),
    });
    return true;
  } catch (e) {
    console.warn("report feedback submit failed", e);
    return false;
  }
}

// 给某个 widget 根元素绑定交互 + 回显已有评价。幂等：重复调用先解绑。
export function bindFeedbackWidget(root) {
  if (!root || root.dataset.fbBound === "1") return;
  root.dataset.fbBound = "1";
  const fbSpan = root.querySelector(".msg-feedback");
  const row = root.querySelector(".fb-comment-row");

  root.addEventListener("click", async (e) => {
    const commentBtn = e.target.closest(".fb-comment-submit");
    if (commentBtn) {
      const input = root.querySelector(".fb-comment-input");
      if (fbSpan.dataset.verdict && input) {
        const ok = await postReportFeedback(root, fbSpan.dataset.verdict, input.value.trim());
        if (ok) { commentBtn.textContent = t("chat.feedback_thanks"); commentBtn.disabled = true; }
      }
      return;
    }
    const fbBtn = e.target.closest(".fb-btn");
    if (fbBtn) {
      const ok = await postReportFeedback(root, fbBtn.dataset.verdict, "");
      if (!ok) return;
      fbSpan.dataset.verdict = fbBtn.dataset.verdict;
      fbSpan.querySelectorAll(".fb-btn").forEach((b) =>
        b.classList.toggle("fb-active", b.dataset.verdict === fbBtn.dataset.verdict));
      if (row) row.style.display = "flex";
    }
  });

  // 回显已有评价
  const mid = root.dataset.mid;
  const tt = root.dataset.targetType;
  if (mid && tt) {
    Promise.resolve(
      api(`/api/feedback/${encodeURIComponent(tt)}/${encodeURIComponent(mid)}`)
        .then((r) => {
          const fb = r && r.feedback;
          if (!fb || !fb.verdict) return;
          fbSpan.dataset.verdict = fb.verdict;
          fbSpan.querySelectorAll(".fb-btn").forEach((b) =>
            b.classList.toggle("fb-active", b.dataset.verdict === fb.verdict));
        })
    ).catch(console.warn);
  }
}
