import { describe, it, expect, vi, beforeEach } from 'vitest';

// The module accesses DOM elements at top level when imported,
// so we need to create ALL required elements before importing it
document.body.innerHTML = `
  <div id="configMask"></div>
  <button id="configClose"></button>
  <button id="cancelBtn"></button>
  <button id="saveBtn"></button>
  <button id="testBtn"></button>
  <div id="testResult"></div>
  <div id="cfgCustomModelFields"></div>
  <input type="text" id="cfgBaseUrl">
  <input type="password" id="cfgApiKey">
  <input type="text" id="cfgModel">
  <input type="number" id="cfgTemp">
  <input type="number" id="cfgMaxIter">
  <textarea id="cfgPrompt"></textarea>
`;

// Now import the module after all required DOM elements exist
const configDialogModule = require('./config-dialog.js');
const { setOnSaved, openConfig, closeConfig } = configDialogModule.default || configDialogModule;

beforeEach(() => {
  // Reset form to known state before each test
  ['cfgBaseUrl', 'cfgApiKey', 'cfgModel', 'cfgTemp', 'cfgMaxIter', 'cfgPrompt'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
});

describe('config-dialog', () => {
  it('should export all public functions', () => {
    expect(setOnSaved).toBeDefined();
    expect(openConfig).toBeDefined();
    expect(closeConfig).toBeDefined();
  });

  describe('setOnSaved', () => {
    it('should set callback without error', () => {
      const mockFn = vi.fn();
      setOnSaved(mockFn);
      // Just verify it accepts the function
      expect(mockFn).not.toHaveBeenCalled();
    });
  });

  describe('openConfig and closeConfig', () => {
    it('should open config without error for non-free user', () => {
      // We just need to verify it doesn't crash when opening
      // The actual loading would require more mocking
      const mockApiFn = vi.fn().mockResolvedValue({
        use_built_in: true,
        openai_base_url: 'https://api.openai.com',
        openai_api_key_set: false,
        openai_model: 'gpt-4o',
        temperature: 0.3,
        max_iterations: 10,
        system_prompt_extra: ''
      });
      
      // Should not throw
      openConfig({ plan: 'pro' }, mockApiFn);
      
      // configMask should have 'open' class
      expect(document.getElementById('configMask').classList.contains('open')).toBe(true);
      expect(mockApiFn).toHaveBeenCalledWith('/api/config');
    });

    it('should do nothing for free user and not open', () => {
      const mockApiFn = vi.fn();
      // Get initial class list
      const wasOpen = document.getElementById('configMask').classList.contains('open');
      
      // This should call alert but we don't mock it - it should still exit early
      try {
        openConfig({ plan: 'free' }, mockApiFn);
      } catch (e) {
        // Ignore if alert is not implemented in jsdom - we just care it didn't open
      }
      
      expect(mockApiFn).not.toHaveBeenCalled();
      // Should still have the same open state as before
      expect(document.getElementById('configMask').classList.contains('open')).toBe(wasOpen);
    });

    it('should close config and clear test result', () => {
      // Open first
      document.getElementById('configMask').classList.add('open');
      document.getElementById('testResult').innerHTML = '<p>Some test result</p>';
      
      closeConfig();
      
      expect(document.getElementById('configMask').classList.contains('open')).toBe(false);
      expect(document.getElementById('testResult').innerHTML).toBe('');
    });
  });
});
