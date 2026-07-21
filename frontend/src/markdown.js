import { escapeHtml } from "./shared.js";

export function renderMarkdown(text) {
  return renderMarkdownExtended(text);
}
// ============ GC 慢事件 AI 分析入口 =============================

// 扩展 Markdown（基于 marked）
export function renderMarkdownExtended(text) {
  if (!text) return "";
  if (typeof text !== "string") text = String(text);
  text = text.replace(/\r\n/g, "\n").trim();
  if (!text.includes("\n") && text.includes("\\n")) text = text.replace(/\\n/g, "\n");
  text = text.replace(/^```\s*(markdown|md|text)\s*\n([\s\S]*?)\n```$/i, "$2").trim();
  text = text.replace(/\n{3,}/g, "\n\n");

  const markedLib = window.marked || (typeof marked !== "undefined" ? marked : null);
  if (!markedLib) return escapeHtml(text).replace(/\n/g, "<br>");

  const Renderer = markedLib.Renderer;
  const renderer = Renderer ? new Renderer() : undefined;
  if (renderer) {
    const origCode = renderer.code.bind(renderer);
    renderer.code = function(...args) {
      const first = args[0];
      const lang = typeof first === "object" ? first.lang : args[1];
      const code = typeof first === "object" ? first.text : first;
      if (/^(markdown|md|text)$/i.test(String(lang || "").trim())) {
        return renderMarkdownExtended(code || "");
      }
      return origCode(...args).replace('<pre>', '<pre style="background:var(--code-bg);padding:8px;border-radius:6px;overflow-x:auto;margin:6px 0;">');
    };

    const origBlockquote = renderer.blockquote.bind(renderer);
    renderer.blockquote = function(...args) {
      return origBlockquote(...args).replace('<blockquote>', '<blockquote style="border-left:3px solid var(--primary);padding:4px 10px;margin:6px 0;color:var(--text-dim);">');
    };

    const origTable = renderer.table.bind(renderer);
    renderer.table = function(...args) {
      return origTable(...args).replace('<table>', '<table class="md-table">');
    };
  }

  const opts = { renderer, breaks: true, gfm: true };
  let html = "";
  if (typeof markedLib.parse === "function") html = markedLib.parse(text, opts);
  else if (typeof markedLib === "function") html = markedLib(text, opts);
  else return escapeHtml(text).replace(/\n/g, "<br>");
  const doc = new DOMParser().parseFromString(html, "text/html");
  const onlyCode = doc.body.children.length === 1 ? doc.body.querySelector("pre > code.language-markdown, pre > code.language-md, pre > code.language-text") : null;
  if (onlyCode) return renderMarkdownExtended(onlyCode.textContent || "");
  try {
    if (window.DOMPurify) {
      html = window.DOMPurify.sanitize(html, { ADD_TAGS: ["pre", "code", "table", "thead", "tbody", "tr", "th", "td", "blockquote"] });
    }
  } catch (_) {}
  return html;
}
