import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderMarkdown, renderMarkdownExtended } from './markdown.js';

// markdown.js depends on two CDN globals: window.marked and window.DOMPurify.
// We mock them to exercise both the fallback path (no marked) and the
// marked + DOMPurify sanitize path.

describe('renderMarkdown — fallback path (no marked lib)', () => {
  beforeEach(() => {
    delete window.marked;
    delete globalThis.marked;
    delete window.DOMPurify;
  });

  it('returns empty string for falsy / empty input', () => {
    expect(renderMarkdown("")).toBe("");
    expect(renderMarkdown(null)).toBe("");
    expect(renderMarkdown(undefined)).toBe("");
  });

  it('coerces non-string input to string', () => {
    expect(renderMarkdown(123)).toBe("123");
  });

  it('escapes HTML and converts newlines to <br> when marked is absent', () => {
    const out = renderMarkdown("a\nb");
    expect(out).toBe("a<br>b");
  });

  it('escapes dangerous HTML in fallback (no raw tags survive)', () => {
    const out = renderMarkdown('<script>alert(1)</script>');
    expect(out).not.toContain('<script>');
    expect(out).toContain('&lt;script&gt;');
  });

  it('converts literal \\n to real newline when no real newline present', () => {
    const out = renderMarkdown('line1\\nline2');
    expect(out).toBe('line1<br>line2');
  });
});

describe('renderMarkdownExtended — marked + DOMPurify path', () => {
  let sanitizeSpy;

  beforeEach(() => {
    // Minimal marked stub: Renderer with overridable code/blockquote/table,
    // plus a parse() that passes text through wrapped in <p>.
    function Renderer() {
      this.code = (...a) => `<pre><code>${typeof a[0] === 'object' ? a[0].text : a[0]}</code></pre>`;
      this.blockquote = (...a) => `<blockquote>${a[0]}</blockquote>`;
      this.table = (...a) => `<table>${a[0]}</table>`;
    }
    window.marked = {
      Renderer,
      parse: (text) => `<p>${text}</p>`,
    };

    // DOMPurify stub that strips <script> tags (simulates real sanitizer),
    // and we spy to confirm it is invoked.
    sanitizeSpy = vi.fn((html) => html.replace(/<script[\s\S]*?<\/script>/gi, ''));
    window.DOMPurify = { sanitize: sanitizeSpy };
  });

  afterEach(() => {
    delete window.marked;
    delete window.DOMPurify;
  });

  it('invokes DOMPurify.sanitize on the parsed HTML', () => {
    renderMarkdownExtended("hello");
    expect(sanitizeSpy).toHaveBeenCalledTimes(1);
    const arg = sanitizeSpy.mock.calls[0][0];
    expect(arg).toContain("hello");
  });

  it('strips <script> via DOMPurify (XSS defense)', () => {
    const out = renderMarkdownExtended("x<script>alert(1)</script>");
    expect(out).not.toContain("<script>");
  });

  it('passes ADD_TAGS allowlist to sanitize', () => {
    renderMarkdownExtended("table content");
    const opts = sanitizeSpy.mock.calls[0][1];
    expect(opts).toBeTruthy();
    expect(opts.ADD_TAGS).toEqual(
      expect.arrayContaining(["pre", "code", "table", "blockquote"])
    );
  });

  it('strips a wrapping ```markdown fence before parsing', () => {
    renderMarkdownExtended("```markdown\n# Title\n```");
    const arg = sanitizeSpy.mock.calls[0][0];
    // The fence should be removed; inner markdown parsed
    expect(arg).toContain("# Title");
    expect(arg).not.toContain("```");
  });

  it('collapses 3+ blank lines before parsing', () => {
    renderMarkdownExtended("a\n\n\n\nb");
    const arg = sanitizeSpy.mock.calls[0][0];
    expect(arg).not.toMatch(/\n{3,}/);
  });

  it('returns escaped fallback when DOMPurify is absent (no crash)', () => {
    delete window.DOMPurify;
    const out = renderMarkdownExtended("plain text");
    expect(typeof out).toBe("string");
    expect(out).toContain("plain text");
  });
});
