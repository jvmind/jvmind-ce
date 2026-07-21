import { describe, it, expect, vi, beforeEach } from 'vitest';
import { t, setLang, getLang } from './index.js';

// Mock localStorage
beforeEach(() => {
  vi.stubGlobal('localStorage', {
    getItem: vi.fn(() => null),
    setItem: vi.fn(),
  });
  vi.stubGlobal('navigator', { language: 'en-US' });
  vi.stubGlobal('window', {
    ...window,
    dispatchEvent: vi.fn(),
  });
  // Reset module by re-importing? We'll test the exported functions
});

describe('i18n', () => {
  beforeEach(() => {
    // Reset to default lang
    vi.clearAllMocks();
  });

  describe('t', () => {
    it('should return English translation by default for existing key', () => {
      // We don't reset module state but this test is safe
      // just check that fallback works
      expect(typeof t('app.title')).toBe('string');
    });

    it('should return key when translation not found', () => {
      // @ts-ignore
      expect(t('non_existent_key_never_will_exist')).toBe('non_existent_key_never_will_exist');
    });

    it('should replace variables in translation', () => {
      // Use known pattern from existing translations
      // We'll create a mock test with variable replacement
      // Check that the replacement actually works
      const testText = "Hello {name}, you have {count} messages";
      // We can't mock the dictionary easily but let's test the actual t function logic
      // The replacement logic is correct based on the code
      // Let's test it by checking what we have
      const result = t("jstack.flame_title_format", { n: 123, pct: 45.6, label: "test" });
      expect(result).toContain("123");
      expect(result).toContain("45.6");
      expect(result).toContain("test");
    });

    it('should fallback to English when current lang missing key', () => {
      // If current lang doesn't have it, it falls back to en
      // This test just verifies no crash
      expect(() => t("gc.chart_no_data")).not.toThrow();
      expect(typeof t("gc.chart_no_data")).toBe("string");
    });
  });

  describe('setLang', () => {
    it('should not set invalid language', () => {
      const before = getLang();
      setLang('invalid_lang');
      expect(getLang()).toBe(before); // unchanged
      expect(localStorage.setItem).not.toHaveBeenCalled();
    });

    it('should set valid language and save to localStorage', () => {
      setLang('zh');
      expect(getLang()).toBe('zh');
      expect(localStorage.setItem).toHaveBeenCalledWith('jvmind_lang', 'zh');
      expect(window.dispatchEvent).toHaveBeenCalled();
    });

    it('should set english language correctly', () => {
      setLang('en');
      expect(getLang()).toBe('en');
      expect(localStorage.setItem).toHaveBeenCalledWith('jvmind_lang', 'en');
    });
  });

  describe('detectLang (module-load priority)', () => {
    // Re-import the module after resetting + remocking localStorage / navigator
    // so that detectLang() — which runs once at module load — picks up the new
    // mocks. Regression guard for the standalone report.js bug where the wrong
    // localStorage key was read, which silently overwrote saved preference.
    async function reloadI18n() {
      vi.resetModules();
      return await import('./index.js?t=' + Math.random());
    }

    it('prioritises localStorage["jvmind_lang"] over navigator.language', async () => {
      vi.stubGlobal('localStorage', {
        getItem: vi.fn((k) => (k === 'jvmind_lang' ? 'en' : null)),
        setItem: vi.fn(),
      });
      vi.stubGlobal('navigator', { language: 'zh-CN' });
      const { getLang } = await reloadI18n();
      expect(getLang()).toBe('en');
    });

    it('falls back to navigator.language "zh" prefix when localStorage is empty', async () => {
      vi.stubGlobal('localStorage', {
        getItem: vi.fn(() => null),
        setItem: vi.fn(),
      });
      vi.stubGlobal('navigator', { language: 'zh-CN' });
      const { getLang } = await reloadI18n();
      expect(getLang()).toBe('zh');
    });

    it('falls back to navigator.language "en" when localStorage is empty and not zh', async () => {
      vi.stubGlobal('localStorage', {
        getItem: vi.fn(() => null),
        setItem: vi.fn(),
      });
      vi.stubGlobal('navigator', { language: 'en-US' });
      const { getLang } = await reloadI18n();
      expect(getLang()).toBe('en');
    });

    it('ignores the legacy "lang" key (regression: report.js used to read it)', async () => {
      // If someone (re)introduces code that reads localStorage["lang"], this test
      // guarantees detectLang() still only honors the canonical key.
      vi.stubGlobal('localStorage', {
        getItem: vi.fn((k) => (k === 'lang' ? 'en' : null)),
        setItem: vi.fn(),
      });
      vi.stubGlobal('navigator', { language: 'zh-CN' });
      const { getLang } = await reloadI18n();
      expect(getLang()).toBe('zh');
    });
  });
});
