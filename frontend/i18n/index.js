/**
 * i18n — 前端国际化模块
 *
 * 用法:
 *   import { t, setLang, getLang } from "./i18n/index.js"
 *   t("sidebar.new_session")                     → "＋ 新建会话"
 *   t("sidebar.quota_title", { plan: "免费版" })  → "<svg>…</svg> 免费版"
 */
import { ico } from '../src/icons.js';
import zh from "./zh.json" with { type: 'json' };
import en from "./en.json" with { type: 'json' };

const STORAGE_KEY = "jvmind_lang";

const EMOJI_MAP = {
  '📋': 'ClipboardList',
  '📁': 'Folder',
  '🤖': 'Bot',
  '🕐': 'Clock',
  '🧠': 'Brain',
  '💬': 'MessageSquare',
  '🧪': 'FlaskConical',
  '🚀': 'Rocket',
  '💳': 'CreditCard',
  '👋': 'Hand',
  '⚙': 'Settings',
  '⚠': 'TriangleAlert',
  '🧑': 'User',
  '🤔': 'Lightbulb',
  '🛠': 'Wrench',
  '👀': 'Eye',
  '✅': 'Check',
  '🔍': 'Search',
  '📥': 'Download',
  '👍': 'ThumbsUp',
  '👎': 'ThumbsDown',
  '🔐': 'Lock',
  '📝': 'FileText',
  '📚': 'BookOpen',
  '🔌': 'Plug',
  '❌': 'X',
  '🔄': 'RefreshCw',
  '👥': 'Users',
  '👤': 'User',
  '🗑': 'Trash2',
  '👁': 'Eye',
  '💰': 'DollarSign',
  '📊': 'BarChart3',
  '⚡': 'Zap',
  '🔧': 'Wrench',
  '📄': 'FileText',
  '🛡': 'Shield',
  '🌐': 'Globe',
  '🚪': 'DoorOpen',
  '🧩': 'Puzzle',
  '🧾': 'Receipt',
  '💡': 'Lightbulb',
  '💾': 'Save',
  '🤝': 'Handshake',
  '✓': 'Check',
  '📎': 'Paperclip',
  '🔒': 'Lock',
};

const locales = { zh, en };

function detectLang() {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored && locales[stored]) return stored;
  const nav = (navigator.language || "").toLowerCase();
  if (nav.startsWith("zh")) return "zh";
  return "en";
}

let currentLang = detectLang();

export function getLang() {
  return currentLang;
}

export function setLang(lang) {
  if (!locales[lang]) return;
  currentLang = lang;
  localStorage.setItem(STORAGE_KEY, lang);
  window.dispatchEvent(new CustomEvent("langchange", { detail: lang }));
}

export function t(key, vars = {}) {
  const dict = locales[currentLang] || en;
  let text = dict[key];
  if (text === undefined) {
    text = en[key];
    if (text === undefined) return key;
  }
  for (const [k, v] of Object.entries(vars)) {
    text = text.split(`{${k}}`).join(String(v));
  }
  return text;
}

export function th(key, vars = {}) {
  let text = t(key, vars);
  for (const [emoji, iconName] of Object.entries(EMOJI_MAP)) {
    const svg = ico(iconName);
    if (text.includes(emoji)) {
      text = text.split(emoji).join(svg);
    }
  }
  return text;
}
