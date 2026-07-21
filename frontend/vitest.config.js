import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['**/*.test.{js,ts}'],
    // Pin timezone so UTC→local date formatting assertions are deterministic.
    // Asia/Shanghai is UTC+8 year-round (no DST).
    env: {
      TZ: 'Asia/Shanghai',
    },
  },
  json: {
    namedExports: true,
  },
})
