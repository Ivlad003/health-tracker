/** Thin, optional wrappers around window.Telegram.WebApp (absent in a browser). */

export function telegramApp(): TelegramWebApp | null {
  if (typeof window === "undefined") return null;
  return window.Telegram?.WebApp ?? null;
}

function supports(tg: TelegramWebApp, version: string): boolean {
  return tg.isVersionAtLeast?.(version) ?? false;
}

/** Follow the Telegram theme: the SDK exposes --tg-theme-* CSS variables;
 * we only mirror the scheme on <html data-theme> and colour the chrome. */
export function applyTheme(tg: TelegramWebApp | null): void {
  const scheme = tg?.colorScheme
    ?? (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = scheme;
  if (tg && supports(tg, "6.1")) {
    tg.setHeaderColor?.("secondary_bg_color");
    tg.setBackgroundColor?.("secondary_bg_color");
  }
}

export function onThemeChange(cb: () => void): () => void {
  const tg = telegramApp();
  tg?.onEvent?.("themeChanged", cb);
  return () => tg?.offEvent?.("themeChanged", cb);
}

export function haptic(kind: "success" | "error" | "warning" | "select"): void {
  const feedback = telegramApp()?.HapticFeedback;
  if (!feedback) return;
  if (kind === "select") feedback.selectionChanged();
  else feedback.notificationOccurred(kind);
}

/** Native confirm dialog inside Telegram, window.confirm elsewhere. */
export function confirmAction(message: string): Promise<boolean> {
  const tg = telegramApp();
  if (tg?.showConfirm && supports(tg, "6.2")) {
    return new Promise((resolve) => tg.showConfirm?.(message, resolve));
  }
  return Promise.resolve(window.confirm(message));
}

export function openExternal(url: string): void {
  const tg = telegramApp();
  if (tg) tg.openLink(url);
  else window.location.href = url;
}
