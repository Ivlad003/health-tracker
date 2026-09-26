import { useEffect } from "react";
import { telegramApp } from "../telegram";

/** Show Telegram's native Back button while `onBack` is set. */
export function useBackButton(onBack: (() => void) | null): void {
  useEffect(() => {
    const button = telegramApp()?.BackButton;
    if (!button || !onBack) {
      button?.hide();
      return;
    }
    button.onClick(onBack);
    button.show();
    return () => {
      button.offClick(onBack);
      button.hide();
    };
  }, [onBack]);
}
