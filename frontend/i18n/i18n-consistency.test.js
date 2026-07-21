import { describe, it, expect } from 'vitest';
import { t, setLang } from './index.js';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ZH_PATH = path.resolve(__dirname, 'zh.json');
const EN_PATH = path.resolve(__dirname, 'en.json');

const zhRaw = fs.readFileSync(ZH_PATH);
const enRaw = fs.readFileSync(EN_PATH);
const zh = JSON.parse(zhRaw.toString('utf8'));
const en = JSON.parse(enRaw.toString('utf8'));

const placeholders = (s) =>
  typeof s === 'string' ? new Set([...s.matchAll(/{(\w+)}/g)].map((m) => m[1])) : new Set();

describe('i18n locale file integrity', () => {
  it('zh.json and en.json have no UTF-8 BOM', () => {
    expect(zhRaw[0]).not.toBe(0xef);
    expect(enRaw[0]).not.toBe(0xef);
  });

  it('zh and en have identical key sets', () => {
    const zk = Object.keys(zh).sort();
    const ek = Object.keys(en).sort();
    const zhOnly = zk.filter((k) => !(k in en));
    const enOnly = ek.filter((k) => !(k in zh));
    expect(zhOnly, `keys only in zh: ${zhOnly.join(', ')}`).toEqual([]);
    expect(enOnly, `keys only in en: ${enOnly.join(', ')}`).toEqual([]);
  });

  it('placeholders ({var}) match between zh and en for every shared key', () => {
    const mismatches = [];
    for (const k of Object.keys(zh)) {
      if (!(k in en)) continue;
      const pz = placeholders(zh[k]);
      const pe = placeholders(en[k]);
      const same = pz.size === pe.size && [...pz].every((p) => pe.has(p));
      if (!same) mismatches.push(`${k}: zh{${[...pz]}} en{${[...pe]}}`);
    }
    expect(mismatches, mismatches.join(' | ')).toEqual([]);
  });

  it('no value contains an unsplittable bilingual separator "。/" (missing space)', () => {
    // Frontend i18nText splits on " / "; "。/" without space would not split.
    const bad = Object.entries(en)
      .filter(([, v]) => typeof v === 'string' && /[^\s]\/[^\s]/.test(v) && / \/ /.test(v) === false && v.includes('/') && /[\u4e00-\u9fff]/.test(v))
      .map(([k]) => k);
    expect(bad).toEqual([]);
  });
});

describe('t() variable substitution', () => {
  it('replaces ALL occurrences of a repeated placeholder', () => {
    setLang('en');
    // error.quota_llm uses {n} twice
    const out = t('error.quota_llm', { n: 5 });
    expect(out).not.toContain('{n}');
    // both slots replaced -> "(5/5)"
    expect(out).toContain('5/5');
  });

  it('replaces multiple distinct placeholders', () => {
    setLang('en');
    const out = t('demo.notice_1', { uploads: 50, calls: 30, sessions: 10 });
    expect(out).toContain('50');
    expect(out).toContain('30');
    expect(out).not.toContain('{uploads}');
    expect(out).not.toContain('{calls}');
  });
});
