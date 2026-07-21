import { escapeHtml, fmtDateOnly } from "./shared.js";

let currentPage = 1;
let currentTag = "";
let totalPages = 1;
const limit = 9;

async function api(path) {
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try { const j = JSON.parse(text); detail = j.detail || j.message || text; } catch {}
    throw new Error(detail);
  }
  return res.json();
}

function slugFromPath() {
  const parts = window.location.pathname.replace(/\/+$/, "").split("/");
  const last = parts[parts.length - 1];
  if (last && last !== "blog") return last;
  return null;
}

function formatDate(s) {
  return fmtDateOnly(s);
}

function renderMarkdown(text) {
  if (!text) return "";
  const markedLib = window.marked || (typeof marked !== "undefined" ? marked : null);
  if (!markedLib) return escapeHtml(text).replace(/\n/g, "<br>");
  let html = "";
  if (typeof markedLib.parse === "function") {
    html = markedLib.parse(text, { breaks: true, gfm: true });
  } else if (typeof markedLib === "function") {
    html = markedLib(text, { breaks: true, gfm: true });
  } else {
    return escapeHtml(text).replace(/\n/g, "<br>");
  }
  if (window.DOMPurify) {
    html = window.DOMPurify.sanitize(html, { ADD_TAGS: ["pre", "code", "table", "thead", "tbody", "tr", "th", "td", "blockquote", "img", "ul", "ol", "li", "a"] });
  }
  return html;
}

async function loadList(page, tag) {
  const grid = document.getElementById("blogGrid");
  const pager = document.getElementById("blogPager");
  const empty = document.getElementById("blogEmpty");
  const tagsEl = document.getElementById("blogTags");

  grid.innerHTML = '<div style="color:var(--text-dim);text-align:center;padding:40px;">Loading...</div>';
  pager.innerHTML = "";
  empty.style.display = "none";

  try {
    let url = `/api/blog?page=${page}&limit=${limit}`;
    if (tag) url += `&tag=${encodeURIComponent(tag)}`;
    const data = await api(url);
    totalPages = data.pages || 1;
    currentPage = data.page || 1;

    if (!data.posts || data.posts.length === 0) {
      grid.innerHTML = "";
      empty.style.display = "";
      renderTags(tagsEl, []);
      return;
    }

    grid.innerHTML = data.posts.map(p => {
      const tags = (p.tags || "").split(",").map(t => t.trim()).filter(Boolean);
      const tagsHtml = tags.length ? `<div class="card-tags">${tags.map(t => `<span class="card-tag">${escapeHtml(t)}</span>`).join("")}</div>` : "";
      return `<a class="bp-card" href="/blog/${encodeURIComponent(p.slug)}">
        <h3>${escapeHtml(p.title)}</h3>
        <div class="excerpt">${escapeHtml((p.excerpt || "").substring(0, 150) || p.content.substring(0, 150))}</div>
        <div class="meta">
          <span>${formatDate(p.published_at || p.created_at)}</span>
          <span>· ${p.view_count || 0} views</span>
        </div>
        ${tagsHtml}
      </a>`;
    }).join("");

    const allTags = collectTags(data.posts);
    renderTags(tagsEl, allTags);

    pager.innerHTML = `
      <button class="btn" ${currentPage <= 1 ? "disabled" : ""} id="bpPrev">Previous</button>
      <span class="info">Page ${currentPage} / ${totalPages}</span>
      <button class="btn" ${currentPage >= totalPages ? "disabled" : ""} id="bpNext">Next</button>
    `;
    document.getElementById("bpPrev").onclick = () => { currentPage--; loadList(currentPage, currentTag); window.scrollTo(0, 0); };
    document.getElementById("bpNext").onclick = () => { currentPage++; loadList(currentPage, currentTag); window.scrollTo(0, 0); };
  } catch (e) {
    grid.innerHTML = `<div style="color:var(--red);text-align:center;padding:40px;">${escapeHtml(e.message)}</div>`;
  }
}

function collectTags(posts) {
  const tagCount = {};
  posts.forEach(p => {
    (p.tags || "").split(",").map(t => t.trim()).filter(Boolean).forEach(t => {
      tagCount[t] = (tagCount[t] || 0) + 1;
    });
  });
  return Object.entries(tagCount).sort((a, b) => b[1] - a[1]);
}

function renderTags(container, tags) {
  if (!tags.length) { container.innerHTML = ""; return; }
  const items = tags.slice(0, 10);
  let html = `<span class="bp-tag ${!currentTag ? "active" : ""}" data-tag="">All</span>`;
  items.forEach(([tag, count]) => {
    html += `<span class="bp-tag ${currentTag === tag ? "active" : ""}" data-tag="${escapeHtml(tag)}">${escapeHtml(tag)} (${count})</span>`;
  });
  container.innerHTML = html;
  container.querySelectorAll(".bp-tag").forEach(el => {
    el.onclick = () => {
      currentTag = el.dataset.tag;
      currentPage = 1;
      loadList(currentPage, currentTag);
    };
  });
}

async function loadDetail(slug) {
  const listEl = document.getElementById("blogList");
  const detailEl = document.getElementById("blogDetail");
  const titleEl = document.getElementById("detailTitle");
  const metaEl = document.getElementById("detailMeta");
  const contentEl = document.getElementById("detailContent");
  const tocEl = document.getElementById("detailToc");

  listEl.style.display = "none";
  detailEl.style.display = "";

  // 若服务端已预渲染，仅更新 view_count 并生成 TOC
  const alreadyRendered = contentEl && contentEl.innerHTML.trim().length > 0 && !contentEl.innerHTML.includes("Loading...");

  try {
    const post = await api(`/api/blog/${encodeURIComponent(slug)}`);
    document.title = post.title + " — JVMind Blog";

    if (!alreadyRendered) {
      titleEl.textContent = post.title;
      metaEl.textContent = `${formatDate(post.published_at || post.created_at)} · ${post.view_count || 0} views`;
      contentEl.innerHTML = renderMarkdown(post.content);
    } else {
      // 仅更新 meta 中的 view_count，保持服务端渲染的标题/正文不变
      if (metaEl) {
        metaEl.textContent = `${formatDate(post.published_at || post.created_at)} · ${post.view_count || 0} views`;
      }
    }
    buildToc(contentEl, tocEl);
  } catch (e) {
    if (!alreadyRendered) {
      contentEl.innerHTML = `<div style="color:var(--red);text-align:center;padding:40px;">${escapeHtml(e.message)}</div>`;
    }
  }
}

// 生成 slug 形式的锚点 id
function slugifyHeading(text, used) {
  let base = String(text || "")
    .toLowerCase()
    .trim()
    .replace(/[^\w\u4e00-\u9fa5]+/g, "-")
    .replace(/^-+|-+$/g, "") || "section";
  let id = base;
  let i = 2;
  while (used.has(id)) { id = `${base}-${i++}`; }
  used.add(id);
  return id;
}

// 解析正文中的 h2/h3 生成文章内目录（TOC）
function buildToc(contentEl, tocEl) {
  if (!tocEl) return;
  const headings = contentEl.querySelectorAll("h2, h3");
  if (headings.length < 2) {
    // 标题太少不展示目录
    tocEl.style.display = "none";
    return;
  }
  tocEl.style.display = "";

  const used = new Set();
  const items = [];
  headings.forEach(h => {
    const id = slugifyHeading(h.textContent, used);
    h.id = id;
    h.style.scrollMarginTop = "80px";
    items.push({ id, text: h.textContent, level: h.tagName.toLowerCase() });
  });

  tocEl.innerHTML =
    `<div class="toc-title">On this page</div>` +
    items.map(it =>
      `<a class="toc-link toc-${it.level}" href="#${it.id}" data-id="${it.id}">${escapeHtml(it.text)}</a>`
    ).join("");

  const links = tocEl.querySelectorAll(".toc-link");

  function setActive(id) {
    links.forEach(l => l.classList.toggle("active", l.dataset.id === id));
  }

  links.forEach(link => {
    link.addEventListener("click", e => {
      e.preventDefault();
      const target = document.getElementById(link.dataset.id);
      if (target) {
        setActive(link.dataset.id); // 点击立即点亮
        target.scrollIntoView({ behavior: "smooth", block: "start" });
        history.replaceState(null, "", "#" + link.dataset.id);
      }
    });
  });

  // scroll-spy：高亮当前可视小节
  function updateActive() {
    // 页面已滚到底部时，强制点亮最后一项（尾部较短的小节顶部永远到不了阈值）
    const scrollEl = document.scrollingElement || document.documentElement;
    if (scrollEl.scrollTop + window.innerHeight >= scrollEl.scrollHeight - 4) {
      setActive(items[items.length - 1].id);
      return;
    }
    let currentId = items[0].id;
    for (const it of items) {
      const el = document.getElementById(it.id);
      if (el && el.getBoundingClientRect().top <= 90) currentId = it.id;
    }
    setActive(currentId);
  }
  window.addEventListener("scroll", updateActive, { passive: true });
  updateActive();

  // 进入时若带 hash，定位到对应小节
  if (location.hash) {
    const target = document.getElementById(location.hash.slice(1));
    if (target) setTimeout(() => { target.scrollIntoView({ block: "start" }); updateActive(); }, 0);
  }
}


function init() {
  const slug = slugFromPath();
  if (slug) {
    loadDetail(slug);
  } else {
    document.getElementById("blogList").style.display = "";
    document.getElementById("blogDetail").style.display = "none";
    loadList(currentPage, currentTag);
  }
}

init();
