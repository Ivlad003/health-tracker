import { telegramApp } from "./telegram";

const TOKEN_KEY = "ht_session";
const AUTH_PATH = "/api/v1/webapp/auth/telegram";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, details: Record<string, unknown> = {}) {
    super(code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export interface AuthResponse {
  session_token: string;
  csrf_token: string;
  expires_at: string;
  user: { id: number; language: string; timezone: string; is_admin: boolean };
}

// --- session storage (sessionStorage: cleared when the WebView closes) ---

function storage(): Pick<Storage, "getItem" | "setItem" | "removeItem"> | null {
  try {
    return typeof sessionStorage === "undefined" ? null : sessionStorage;
  } catch {
    return null; // storage disabled
  }
}

export function sessionToken(): string {
  return storage()?.getItem(TOKEN_KEY) ?? "";
}

export function saveSession(token: string): void {
  storage()?.setItem(TOKEN_KEY, token);
}

export function clearSession(): void {
  storage()?.removeItem(TOKEN_KEY);
}

// --- "session lost" notification (App shows the reopen screen) ---

const lostListeners = new Set<() => void>();

export function onSessionLost(listener: () => void): () => void {
  lostListeners.add(listener);
  return () => lostListeners.delete(listener);
}

export function idempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replaceAll("-", "");
  }
  return `web${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
}

// --- requests ---

async function readBody(response: Response): Promise<{ json: unknown; isJson: boolean }> {
  const text = await response.text();
  if (!text) return { json: null, isJson: true };
  const type = response.headers.get("content-type") ?? "";
  if (!type.includes("json")) return { json: null, isJson: false };
  try {
    return { json: JSON.parse(text) as unknown, isJson: true };
  } catch {
    return { json: null, isJson: false };
  }
}

function errorFrom(status: number, body: unknown): ApiError {
  const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : undefined;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const record = detail as Record<string, unknown>;
    if (typeof record.error === "string") return new ApiError(status, record.error, record);
  }
  if (status >= 500) return new ApiError(status, "server_error");
  return new ApiError(status, `http_${status}`);
}

async function send(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = sessionToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  try {
    return await fetch(path, { ...init, headers });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "network_error");
  }
}

let pendingLogin: Promise<boolean> | null = null;

/** Exchange initData for a session (single flight for concurrent 401s). */
export async function login(initData: string): Promise<AuthResponse> {
  const response = await send(AUTH_PATH, {
    method: "POST",
    body: JSON.stringify({ init_data: initData }),
  });
  const { json, isJson } = await readBody(response);
  if (!response.ok) throw errorFrom(response.status, json);
  if (!isJson) throw new ApiError(response.status, "bad_response");
  const auth = json as AuthResponse;
  saveSession(auth.session_token);
  return auth;
}

function relogin(): Promise<boolean> {
  const initData = telegramApp()?.initData;
  if (!initData) return Promise.resolve(false);
  pendingLogin ??= login(initData)
    .then(() => true, () => false)
    .finally(() => { pendingLogin = null; });
  return pendingLogin;
}

/**
 * JSON API call. On 401 the session is re-created once from Telegram's
 * initData (valid for a few minutes after the Mini App opens) and the
 * request is replayed; otherwise listeners are told the session is gone.
 */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response = await send(path, init);
  if (response.status === 401 && path !== AUTH_PATH) {
    clearSession();
    if (await relogin()) response = await send(path, init);
    if (response.status === 401) {
      clearSession();
      lostListeners.forEach((listener) => listener());
    }
  }
  const { json, isJson } = await readBody(response);
  if (!response.ok) throw errorFrom(response.status, json);
  if (!isJson) throw new ApiError(response.status, "bad_response");
  return json as T;
}
