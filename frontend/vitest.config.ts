import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Unit tests for pure logic only — the error mapping, the retry predicate, the
// contract parsing. No jsdom: nothing under test renders, and adding a DOM would
// make these tests slower without making them stricter. Component behaviour is
// covered by the Playwright suite against a real backend, which is the only place
// it can be tested honestly.
export default defineConfig({
  resolve: {
    alias: [
      { find: /^@\//, replacement: fileURLToPath(new URL("./", import.meta.url)) },
    ],
  },
  test: {
    environment: "node",
    // Scoped so vitest never tries to collect the Playwright specs in e2e/, which
    // import @playwright/test and would fail on a bare `test()` call.
    include: ["lib/**/__tests__/**/*.test.ts", "features/**/__tests__/**/*.test.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text"],
      include: ["lib/api/**"],
    },
  },
});
