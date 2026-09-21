import { afterEach, describe, expect, it, vi } from "vitest";
import { Api } from "./api";
import { authErrorMessage } from "./auth";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  if (typeof window !== "undefined")
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
});

describe("auth error copy", () => {
  it("surfaces backend rejection details without the code prefix", () => {
    expect(authErrorMessage("backend_rejected:Владелец уже создан")).toBe(
      "Владелец уже создан",
    );
    expect(authErrorMessage("backend_unreachable")).toContain("недоступен");
    expect(authErrorMessage(new Error("plain"))).toBe("plain");
  });
});

describe("Api 401 recovery", () => {
  it("refreshes once and retries, sharing one refresh across parallel requests", async () => {
    const refresh = vi.fn(async () => "fresh-token");
    const api = new Api("http://127.0.0.1:8000", "expired", refresh);
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (_input, init) => {
        const headers = new Headers(init?.headers);
        if (headers.get("Authorization") === "Bearer fresh-token") {
          return jsonResponse(200, { ok: true });
        }
        return jsonResponse(401, { detail: "Session expired" });
      });
    const [a, b] = await Promise.all([api.json("/chats"), api.json("/chats")]);
    expect(a).toEqual({ ok: true });
    expect(b).toEqual({ ok: true });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls.some(([, init]) => !!init?.body)).toBe(false);
    expect(api.authToken()).toBe("fresh-token");
  });

  it("clears nothing and throws once when refresh fails", async () => {
    const refresh = vi.fn(async () => null);
    const api = new Api("http://127.0.0.1:8000", "expired", refresh);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(401, { detail: "Session expired" }),
    );
    await expect(api.json("/chats")).rejects.toMatchObject({ status: 401 });
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("never refreshes auth endpoints and does not loop", async () => {
    const refresh = vi.fn(async () => "fresh-token");
    const api = new Api("http://127.0.0.1:8000", null, refresh);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(401, { detail: "Incorrect email or password" }),
    );
    await expect(
      api.json("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: "a@b.c", password: "x".repeat(10) }),
      }),
    ).rejects.toMatchObject({ status: 401 });
    expect(refresh).not.toHaveBeenCalled();
  });

  it("does not refresh when there is no refresh hook (browser dev mode)", async () => {
    const api = new Api("http://127.0.0.1:8000", "expired");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(401, { detail: "Session expired" }),
    );
    await expect(api.json("/chats")).rejects.toMatchObject({ status: 401 });
  });
});
