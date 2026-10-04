import { afterEach, describe, expect, it, vi } from "vitest";

import { apiGet, ApiError } from "./api";

describe("apiGet abort handling", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("translates AbortError into a readable timeout error", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
      const sig = init.signal as AbortSignal;
      return new Promise((_res, rej) => {
        sig.addEventListener("abort", () =>
          rej(new DOMException("signal is aborted without reason", "AbortError")));
      });
    }));
    await expect(apiGet("/api/v1/symbol/NVDA", 25)).rejects.toMatchObject({
      name: "ApiError",
      status: 0,
    });
    await expect(apiGet("/api/v1/symbol/NVDA", 25)).rejects
      .toThrow(/timed out.*client abort/i);
  });

  it("analytical endpoints get the slow-timeout tier", async () => {
    vi.useFakeTimers();
    try {
      const abortAt: number[] = [];
      vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
        const sig = init.signal as AbortSignal;
        return new Promise((_res, rej) => {
          sig.addEventListener("abort", () => {
            abortAt.push(Date.now());
            rej(new DOMException("aborted", "AbortError"));
          });
        });
      }));
      const slow = apiGet("/api/v1/symbol/NVDA");
      const fast = apiGet("/api/v1/auth/me");
      slow.catch(() => {}); fast.catch(() => {});
      // at 10s: only the fast path has fired
      await vi.advanceTimersByTimeAsync(10_000);
      expect(abortAt).toHaveLength(1);
      // the slow path survives until ~45s
      await vi.advanceTimersByTimeAsync(35_000);
      expect(abortAt).toHaveLength(2);
      await expect(slow).rejects.toThrow(ApiError);
      await expect(fast).rejects.toThrow(ApiError);
    } finally {
      vi.useRealTimers();
    }
  });
});
