/// <reference types="vite/client" />

interface TelegramWebAppUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  language_code?: string;
}

interface TelegramWebApp {
  initData: string;
  initDataUnsafe: { user?: TelegramWebAppUser };
  colorScheme?: "light" | "dark";
  ready: () => void;
  expand: () => void;
  openLink: (url: string) => void;
  setHeaderColor?: (color: string) => void;
  setBackgroundColor?: (color: string) => void;
}

interface TelegramNamespace {
  WebApp: TelegramWebApp;
}

declare const Telegram: TelegramNamespace | undefined;

interface Window {
  Telegram?: TelegramNamespace;
}
