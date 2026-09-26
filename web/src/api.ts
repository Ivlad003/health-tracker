const TOKEN_KEY = "ht_session";

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string) {
    super(code);
    this.status = status;
    this.code = code;
  }
}

export function sessionToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) || "";
}

export function saveSession(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
}

export function clearSession(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function idempotencyKey(): string {
  const rand = Math.random().toString(36).slice(2, 10);
  return `web${Date.now().toString(36)}${rand}`;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = sessionToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401) clearSession();
  const text = await response.text();
  const payload = text ? JSON.parse(text) as unknown : null;
  if (!response.ok) {
    const detail = (payload as { detail?: { error?: string } } | null)?.detail;
    throw new ApiError(response.status, detail?.error || `http_${response.status}`);
  }
  return payload as T;
}

export function telegramApp(): TelegramWebApp | null {
  return window.Telegram?.WebApp ?? null;
}
