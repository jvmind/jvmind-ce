import { describe, it, expect, beforeEach, vi } from "vitest";

// feedback-widget renders the report-type (gc/jstack) feedback controls and
// wires submit + echo through /api/feedback. These tests lock in缺口1 (report
// entry exists) and缺口2 (echo hydrates existing verdict) so they can't silently
// regress.

import { feedbackWidgetHtml, bindFeedbackWidget } from "./feedback-widget.js";

describe("feedbackWidgetHtml", () => {
  it("renders 3 verdict buttons + hidden comment row with escaped ids", () => {
    const html = feedbackWidgetHtml("gc", "rid_123");
    const div = document.createElement("div");
    div.innerHTML = html;
    const root = div.firstElementChild;
    expect(root.dataset.mid).toBe("rid_123");
    expect(root.dataset.targetType).toBe("gc");
    expect(root.querySelectorAll(".fb-btn").length).toBe(3);
    const row = root.querySelector(".fb-comment-row");
    expect(row).not.toBeNull();
    expect(row.style.display).toBe("none");
  });
});

describe("bindFeedbackWidget", () => {
  let fetchCalls;
  beforeEach(() => {
    document.body.innerHTML = "";
    fetchCalls = [];
    globalThis.fetch = vi.fn(async (url, opts) => {
      fetchCalls.push({ url, opts });
      // echo GET returns no prior feedback by default
      const isGet = !opts || (opts.method || "GET") === "GET";
      return {
        ok: true,
        status: 200,
        json: async () => (isGet ? { feedback: null } : { ok: true }),
        text: async () => "{}",
      };
    });
  });

  function mount(targetType = "jstack", id = "rid_9") {
    const host = document.createElement("div");
    host.innerHTML = feedbackWidgetHtml(targetType, id);
    document.body.appendChild(host);
    const root = host.querySelector(".report-feedback");
    bindFeedbackWidget(root);
    return root;
  }

  it("clicking a verdict POSTs report feedback and reveals comment row", async () => {
    const root = mount("jstack", "rid_9");
    root.querySelector('.fb-btn[data-verdict="wrong"]').click();
    await vi.waitFor(() =>
      expect(fetchCalls.some((c) => c.url === "/api/feedback" && c.opts.method === "POST")).toBe(true)
    );
    const post = fetchCalls.find((c) => c.opts && c.opts.method === "POST");
    const body = JSON.parse(post.opts.body);
    expect(body.target_type).toBe("jstack");
    expect(body.target_id).toBe("rid_9");
    expect(body.verdict).toBe("wrong");
    await vi.waitFor(() =>
      expect(root.querySelector(".fb-comment-row").style.display).toBe("flex")
    );
  });

  it("submitting a comment POSTs verdict + comment", async () => {
    const root = mount("gc", "rid_5");
    root.querySelector('.fb-btn[data-verdict="useless"]').click();
    await vi.waitFor(() => expect(root.querySelector(".msg-feedback").dataset.verdict).toBe("useless"));
    fetchCalls.length = 0;
    root.querySelector(".fb-comment-input").value = "too generic";
    root.querySelector(".fb-comment-submit").click();
    await vi.waitFor(() => expect(fetchCalls.length).toBeGreaterThan(0));
    const body = JSON.parse(fetchCalls[0].opts.body);
    expect(body.verdict).toBe("useless");
    expect(body.comment).toBe("too generic");
  });

  it("echoes existing verdict on bind (hydrate)", async () => {
    globalThis.fetch = vi.fn(async (url, opts) => {
      const isGet = !opts || (opts.method || "GET") === "GET";
      return {
        ok: true,
        status: 200,
        json: async () => (isGet ? { feedback: { verdict: "useful", comment: "" } } : { ok: true }),
        text: async () => "{}",
      };
    });
    const host = document.createElement("div");
    host.innerHTML = feedbackWidgetHtml("gc", "rid_echo");
    document.body.appendChild(host);
    const root = host.querySelector(".report-feedback");
    bindFeedbackWidget(root);
    await vi.waitFor(() =>
      expect(root.querySelector('.fb-btn[data-verdict="useful"]').classList.contains("fb-active")).toBe(true)
    );
  });

  it("is idempotent: re-binding does not double-fire", () => {
    const root = mount("gc", "rid_idem");
    const before = root.dataset.fbBound;
    bindFeedbackWidget(root);
    expect(root.dataset.fbBound).toBe(before);
    expect(before).toBe("1");
  });
});
