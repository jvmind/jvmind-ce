import { csrfHeaders, i18nText } from "./shared.js";
import { t } from "../i18n/index.js";

let _on401 = null;
export function setAuthFailureHandler(fn) { _on401 = fn; }

const API = "";
export async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    credentials: "same-origin",
    ...opts,
    headers: csrfHeaders({ "Content-Type": "application/json", ...(opts.headers || {}) }),
  });
  if (res.status === 401) { if (_on401) await _on401(); throw new Error(t("auth.relogin")); }
  if (!res.ok) {
    const text = await res.text();
    let detail = text;
    try {
      const json = JSON.parse(text);
      const candidate = json.detail || json.error || json.message;
      if (candidate != null) detail = candidate;
    } catch {}
    if (typeof detail !== "string") {
      try { detail = JSON.stringify(detail); } catch { detail = String(detail); }
    }
    throw new Error(i18nText(detail));
  }
  return res.json();
}
