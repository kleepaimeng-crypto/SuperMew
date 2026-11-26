import { afterEach, describe, expect, it, vi } from "vitest";
import { randomUuid } from "./uuid";

const UUID_V4_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("randomUuid", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses crypto.randomUUID when available", () => {
    const fixed = "11111111-2222-4333-8444-555555555555";
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => fixed),
      getRandomValues: globalThis.crypto.getRandomValues.bind(
        globalThis.crypto,
      ),
    });
    expect(randomUuid()).toBe(fixed);
  });

  it("falls back to crypto.getRandomValues in non-secure contexts", () => {
    vi.stubGlobal("crypto", {
      getRandomValues: globalThis.crypto.getRandomValues.bind(
        globalThis.crypto,
      ),
    });
    const id = randomUuid();
    expect(id).toMatch(UUID_V4_RE);
  });
});
