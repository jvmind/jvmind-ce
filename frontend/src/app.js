import { state } from "./state.js";
import { api } from "./api.js";
import { t } from "../i18n/index.js";

export const app = {};

app.checkAuth = async function checkAuth() {
  try {
    const user = await api("/api/auth/me");
    return user;
  } catch {
    return null;
  }
};

app.showAuthUI = async function showAuthUI(user) {
  state.currentUser = user;
  const mask = document.getElementById("loginMask");
  const content = document.getElementById("appContent");
  if (mask) mask.classList.remove("open");
  if (content) content.style.display = "flex";

  const nameLabel = document.getElementById("userNameLabel");
  if (nameLabel) nameLabel.textContent = user.username || user.email || "";
  const info = document.getElementById("userInfo");
  if (info) info.style.display = "flex";
  const adminBtn = document.getElementById("adminBtn");
  if (adminBtn) adminBtn.style.display = user.is_admin ? "" : "none";

  document.querySelectorAll("[data-auth-visibility]").forEach(el => {
    const v = el.dataset.authVisibility;
    if (v === "logged-in") el.style.display = user ? "" : "none";
    if (v === "not-logged-in") el.style.display = user ? "none" : "";
  });
  app.updateQuotaUI();
};

app.updateConfigPrompt = function updateConfigPrompt() {
  const el = document.getElementById("configPrompt");
  if (!el) return;
  el.style.display = state.llmConfigured ? "none" : "block";
};

app.updateQuotaUI = async function updateQuotaUI() {
  const labelText = document.getElementById("sbFileLabelText");
  if (labelText) labelText.textContent = t("sidebar.quota_file");
  try {
    const q = await api("/api/quota");
    const setText = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val != null ? String(val) : "-";
    };
    setText("sbFileUsed", 0);
    setText("sbFileLimit", q.llm_calls_unmetered ? "∞" : q.llm_calls_limit);
    setText("sbLlmUsed", q.llm_calls_used);
    setText("sbLlmLimit", q.llm_calls_unmetered ? "∞" : q.llm_calls_limit);
    setText("sbSessionsUsed", 0);
    setText("sbSessionsLimit", "∞");
  } catch {}
};

app.showLoginUI = async function showLoginUI() {
  const mask = document.getElementById("loginMask");
  const content = document.getElementById("appContent");
  if (content) content.style.display = "none";
  if (mask) {
    mask.classList.add("open");
    mask.style.display = "";
  }
};

// CE single-user: logout is a no-op that just reloads
document.addEventListener("DOMContentLoaded", () => {
  const logoutBtn = document.getElementById("logoutBtn");
  if (logoutBtn) {
    logoutBtn.onclick = async () => {
      try { await api("/api/auth/logout"); } catch {}
      location.reload();
    };
  }
});

// CE single-user: login form is decorative; clicking login re-checks auth
document.addEventListener("DOMContentLoaded", () => {
  const loginBtn = document.getElementById("authLoginBtn");
  if (loginBtn) {
    loginBtn.onclick = async () => {
      const user = await app.checkAuth();
      if (user) {
        await app.showAuthUI(user);
        const m = document.getElementById("loginMask");
        if (m) m.classList.remove("open");
      }
    };
  }
});

document.addEventListener("DOMContentLoaded", () => {
  const closeBtn = document.getElementById("loginCloseBtn");
  if (closeBtn) {
    closeBtn.onclick = () => {
      const mask = document.getElementById("loginMask");
      if (mask) mask.style.display = "none";
    };
  }
});
