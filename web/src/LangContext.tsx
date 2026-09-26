import { createContext, useContext, useMemo, type ReactNode } from "react";
import { errorMessage } from "./errors";
import { t, type Key, type Lang } from "./i18n";

const LangContext = createContext<Lang>("uk");

export function LangProvider({ lang, children }: { lang: Lang; children: ReactNode }) {
  return <LangContext.Provider value={lang}>{children}</LangContext.Provider>;
}

export interface Translator {
  lang: Lang;
  t: (key: Key) => string;
  err: (error: unknown) => string;
}

export function useT(): Translator {
  const lang = useContext(LangContext);
  return useMemo(() => ({
    lang,
    t: (key: Key) => t(lang, key),
    err: (error: unknown) => errorMessage(lang, error),
  }), [lang]);
}
