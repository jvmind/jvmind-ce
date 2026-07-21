import { describe, it, expect, beforeEach } from 'vitest';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

// pricing.html ships its renderPlans() inline. Extract and evaluate it against
// mock plan data to lock the "Coming Soon" behavior for non-public plans across
// BOTH billing cycles (regression: monthly showed Coming Soon, yearly didn't).

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PRICING_HTML = path.resolve(__dirname, '../pricing.html');

function loadRenderPlans() {
  const src = fs.readFileSync(PRICING_HTML, 'utf8');
  const start = src.indexOf('function renderPlans(plans)');
  const end = src.indexOf('\n}', start) + 2;
  const body = src.slice(start, end);
  let captured = '';
  const doc = { getElementById: () => ({ set innerHTML(v) { captured = v; } }) };
  const fn = new Function(
    'plans', 'escapeHtml', 'defaultPlanDescription', 'currentCycle', 'document',
    body + '\n renderPlans(plans);'
  );
  return (plans, cycle) => {
    captured = '';
    fn(plans, (s) => String(s ?? ''), () => 'desc', cycle, doc);
    return captured;
  };
}

const render = loadRenderPlans();

function cardOf(html) {
  const price = html.match(/<div class="price">([\s\S]*?)<\/div>/);
  const cta = html.match(/<div class="cta([^"]*)"[^>]*>([^<]*)</);
  const tag = html.match(/<div class="tag">([^<]*)</);
  return {
    price: price ? price[1] : null,
    ctaClass: cta ? cta[1].trim() : null,
    ctaText: cta ? cta[2] : null,
    tag: tag ? tag[1] : null,
  };
}

const nonPublicPro = {
  slug: 'pro', display_name: 'Pro', description: 'd',
  price_monthly: 3900, price_yearly: 39000, max_seats: 1,
  file_size_limit_mb: 500, is_public: false, features: {},
};

describe('pricing renderPlans — non-public plan shows Coming Soon (both cycles)', () => {
  for (const cycle of ['monthly', 'yearly']) {
    it(`${cycle}: tag, price and CTA all say Coming Soon`, () => {
      const c = cardOf(render([nonPublicPro], cycle));
      expect(c.tag).toBe('Coming Soon');
      expect(c.ctaText).toBe('Coming Soon');
      expect(c.ctaClass).toBe('disabled');
      expect(c.price).toContain('Coming Soon');
      // must NOT leak a real price
      expect(c.price).not.toMatch(/\$\d/);
    });
  }
});

describe('pricing renderPlans — public plans unaffected', () => {
  const publicPro = { ...nonPublicPro, is_public: true };
  const free = {
    slug: 'free', display_name: 'Free', description: 'd',
    price_monthly: 0, price_yearly: 0, max_seats: 1,
    file_size_limit_mb: 50, is_public: true, features: {},
  };

  it('public paid plan shows price + checkout CTA (monthly)', () => {
    const c = cardOf(render([publicPro], 'monthly'));
    expect(c.price).toContain('$39');
    expect(c.ctaText).toContain('Choose');
    expect(c.ctaClass).toBe('');
  });

  it('public paid plan shows yearly price', () => {
    const c = cardOf(render([publicPro], 'yearly'));
    expect(c.price).toContain('$390');
    expect(c.ctaText).toContain('Choose');
  });

  it('free plan shows $0 + Get Started Free', () => {
    const c = cardOf(render([free], 'monthly'));
    expect(c.price).toContain('$0');
    expect(c.ctaText).toBe('Get Started Free');
  });
});
