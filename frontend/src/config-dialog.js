// config-dialog.js — 模型配置对话框
import { t, th } from "../i18n/index.js";
import { csrfHeaders, escapeHtml } from "./shared.js";

// ---------- 回调注册 ----------
let _onSaved = null;
let _lastConfig = null;

export function setOnSaved(fn) {
  _onSaved = fn;
}

// ---------- 打开/关闭 ----------
const configMask = document.getElementById("configMask");

export function openConfig(currentUser, apiFn) {
  if (currentUser && currentUser.plan === "free") {
    alert(t("config.free_llm_note"));
    return;
  }
  configMask.classList.add("open");
  loadConfigForm(apiFn);
}

export function closeConfig() {
  configMask.classList.remove("open");
  document.getElementById("testResult").innerHTML = "";
}

document.getElementById("configClose").onclick = closeConfig;
document.getElementById("cancelBtn").onclick = closeConfig;
configMask.addEventListener("click", (e) => { if (e.target === configMask) closeConfig(); });

// ---------- 加载配置 ----------
async function loadConfigForm(apiFn) {
  try {
    const cfg = await apiFn("/api/config");
    _lastConfig = cfg;
    document.getElementById("cfgBaseUrl").value = cfg.openai_base_url || "";
    document.getElementById("cfgApiKey").value = "";
    document.getElementById("cfgApiKey").placeholder = cfg.openai_api_key_set
      ? t("config.api_key_saved", { key: cfg.openai_api_key || "****" })
      : t("config.api_key_placeholder_empty");
    document.getElementById("cfgModel").value = cfg.openai_model || "";
    document.getElementById("cfgTemp").value = cfg.temperature ?? 0.3;
    document.getElementById("cfgMaxIter").value = cfg.max_iterations ?? 10;
    document.getElementById("cfgPrompt").value = cfg.system_prompt_extra || "";
  } catch (e) {
    alert(t("config.load_error", { msg: e.message }));
  }
}

// ---------- 预设 ----------
document.querySelectorAll("#presetRow .preset-chip").forEach(chip => {
  chip.onclick = () => {
    const p = JSON.parse(chip.dataset.preset);
    if (p.openai_base_url) document.getElementById("cfgBaseUrl").value = p.openai_base_url;
    if (p.openai_model) document.getElementById("cfgModel").value = p.openai_model;
    if (p.openai_api_key) document.getElementById("cfgApiKey").value = p.openai_api_key;
  };
});

// ---------- 收集表单 ----------
function collectForm() {
  return {
    use_built_in: false,
    openai_base_url: document.getElementById("cfgBaseUrl").value.trim(),
    openai_api_key: document.getElementById("cfgApiKey").value,
    openai_model: document.getElementById("cfgModel").value.trim(),
    temperature: parseFloat(document.getElementById("cfgTemp").value),
    max_iterations: parseInt(document.getElementById("cfgMaxIter").value),
    system_prompt_extra: document.getElementById("cfgPrompt").value,
  };
}

// ---------- 测试连接 ----------
document.getElementById("testBtn").onclick = async () => {
  const result = document.getElementById("testResult");
  result.innerHTML = '<div class="test-result" style="background:var(--bg-3);color:var(--text-dim);">' + th("config.test_connecting") + '</div>';
  const f = collectForm();
  try {
    const res = await fetch("/api/config/test", {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        openai_base_url: f.openai_base_url,
        openai_api_key: f.openai_api_key,
        openai_model: f.openai_model,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      result.innerHTML = `<div class="test-result ok">${th("config.test_success", { ms: String(data.latency_ms), model: escapeHtml(data.model) })}<br/>${t("config.test_reply_sample")}<i>${escapeHtml(data.reply || t("config.test_empty"))}</i></div>`;
    } else {
      result.innerHTML = `<div class="test-result err">${th("config.test_fail", { ms: String(data.latency_ms) })}<br/>${escapeHtml(data.error || t("config.test_unknown_error"))}</div>`;
    }
  } catch (e) {
    result.innerHTML = `<div class="test-result err">${th("config.test_exception", { msg: escapeHtml(e.message) })}</div>`;
  }
};

// ---------- 保存 ----------
document.getElementById("saveBtn").onclick = async () => {
  const f = collectForm();
  if (!f.openai_base_url || !f.openai_model) {
    alert(t("config.required_empty"));
    return;
  }
  try {
    const res = await fetch("/api/config", {
      method: "PUT",
      credentials: "same-origin",
      headers: csrfHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(f),
    });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    const data = await res.json();
    closeConfig();
    if (_onSaved) _onSaved(data);
  } catch (e) {
    alert(t("config.save_error", { msg: e.message }));
  }
};
