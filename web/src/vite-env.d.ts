/// <reference types="vite/client" />

interface TelegramWebAppUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  language_code?: string;
}

interface TelegramBackButton {
  show: () => void;
  hide: () => void;
  onClick: (cb: () => void) => void;
  offClick: (cb: () => void) => void;
}

interface TelegramHapticFeedback {
  impactOccurred: (style: "light" | "medium" | "heavy" | "rigid" | "soft") => void;
  notificationOccurred: (type: "error" | "success" | "warning") => void;
  selectionChanged: () => void;
}

type TelegramEvent = "themeChanged" | "viewportChanged" | "activated" | "deactivated";

interface TelegramWebApp {
  initData: string;
  initDataUnsafe: { user?: TelegramWebAppUser };
  version?: string;
  colorScheme?: "light" | "dark";
  themeParams?: Record<string, string | undefined>;
  ready: () => void;
  expand: () => void;
  close?: () => void;
  openLink: (url: string) => void;
  setHeaderColor?: (color: string) => void;
  setBackgroundColor?: (color: string) => void;
  showConfirm?: (message: string, callback: (confirmed: boolean) => void) => void;
  isVersionAtLeast?: (version: string) => boolean;
  onEvent?: (event: TelegramEvent, cb: () => void) => void;
  offEvent?: (event: TelegramEvent, cb: () => void) => void;
  BackButton?: TelegramBackButton;
  HapticFeedback?: TelegramHapticFeedback;
}

interface TelegramNamespace {
  WebApp: TelegramWebApp;
}

interface Window {
  Telegram?: TelegramNamespace;
}
