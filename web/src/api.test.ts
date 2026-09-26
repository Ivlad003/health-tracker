import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function loadApi() {
  vi.resetModules();
  return import("./api");
}

describe("api()", () => {
  let fetchMock: ReturnType<typeof vi.fn<(path: string, init?: RequestInit) => Promise<Response>>>;

  beforeEach(() => {
    fetchMock = vi.fn<(path: string, init?: RequestInit) => Promise<Response>>();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("sessionStorage", memoryStorage());
    vi.stubGlobal("window", { Telegram: { WebApp: { initData: "signed-init-data" } } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends the bearer token and parses JSON", async () => {
    const { api, saveSession } = await loadApi();
    saveSession("tok1");
    fetchMock.mockResolvedValueOnce(json(200, { ok: true }));
    await expect(api("/api/v1/webapp/me")).resolves.toEqual({ ok: true });
    const headers = (fetchMock.mock.calls[0]?.[1] as RequestInit).headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer tok1");
  });

  it("re-authenticates once on 401 and replays the request", async () => {
    const { api, saveSession, sessionToken } = await loadApi();
    saveSession("stale");
    fetchMock
      .mockResolvedValueOnce(json(401, { detail: { error: "session_invalid" } }))
      .mockResolvedValueOnce(json(200, { session_token: "fresh", csrf_token: "c", expires_at: "x", user: {} }))
      .mockResolvedValueOnce(json(200, { value: 1 }));
    await expect(api("/api/v1/webapp/today")).resolves.toEqual({ value: 1 });
    expect(sessionToken()).toBe("fresh");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/v1/webapp/auth/telegram");
    const replay = (fetchMock.mock.calls[2]?.[1] as RequestInit).headers as Headers;
    expect(replay.get("Authorization")).toBe("Bearer fresh");
  });

  it("shares one login between concurrent 401s", async () => {
    const { api } = await loadApi();
    const seen = new Map<string, number>();
    const respond = (path: string): Response => {
      if (path.endsWith("/auth/telegram")) {
        return json(200, { session_token: "fresh", csrf_token: "c", expires_at: "x", user: {} });
      }
      const count = (seen.get(path) ?? 0) + 1;
      seen.set(path, count);
      return count === 1 ? json(401, { detail: { error: "session_invalid" } }) : json(200, { path });
    };
    fetchMock.mockImplementation((path: string) => Promise.resolve(respond(path)));
    await Promise.all([api("/a"), api("/b")]);
    expect(fetchMock.mock.calls.filter(([p]) => String(p).endsWith("/auth/telegram"))).toHaveLength(1);
  });

  it("reports a lost session when re-login is impossible", async () => {
    vi.stubGlobal("window", {});
    const { api, onSessionLost } = await loadApi();
    const lost = vi.fn();
    onSessionLost(lost);
    fetchMock.mockResolvedValueOnce(json(401, { detail: { error: "session_invalid" } }));
    await expect(api("/api/v1/webapp/me")).rejects.toMatchObject({ status: 401, code: "session_invalid" });
    expect(lost).toHaveBeenCalledOnce();
  });

  it("turns HTML error pages and network failures into ApiErrors", async () => {
    const { api } = await loadApi();
    fetchMock.mockResolvedValueOnce(new Response("<html>Bad gateway</html>", {
      status: 502, headers: { "content-type": "text/html" },
    }));
    await expect(api("/x")).rejects.toMatchObject({ status: 502, code: "server_error" });
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(api("/x")).rejects.toMatchObject({ status: 0, code: "network_error" });
    fetchMock.mockResolvedValueOnce(new Response("<html>ok</html>", { status: 200, headers: { "content-type": "text/html" } }));
    await expect(api("/x")).rejects.toMatchObject({ code: "bad_response" });
  });

  it("keeps error details such as current_version", async () => {
    const { api } = await loadApi();
    fetchMock.mockResolvedValueOnce(json(409, { detail: { error: "version_conflict", current_version: 4 } }));
    await expect(api("/x", { method: "PATCH", body: "{}" })).rejects.toMatchObject({
      code: "version_conflict", details: { current_version: 4 },
    });
    fetchMock.mockResolvedValueOnce(json(422, { detail: [{ loc: ["body"], msg: "x" }] }));
    await expect(api("/x")).rejects.toMatchObject({ code: "http_422" });
  });

  it("creates idempotency keys the API accepts", async () => {
    const { idempotencyKey } = await loadApi();
    const key = idempotencyKey();
    expect(key).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
    expect(idempotencyKey()).not.toBe(key);
  });
});
