import { lazy, Suspense, useCallback, useEffect, useState, type ReactNode } from "react";
import { api, login, onSessionLost, sessionToken } from "./api";
import { ErrorState, Loading } from "./components/ui";
import { NavIcon } from "./components/NavIcon";
import { useBackButton } from "./hooks/useBackButton";
import { asLang, t, type Lang } from "./i18n";
import { LangProvider } from "./LangContext";
import { ActivityPage } from "./pages/Activity";
import { DashboardPage } from "./pages/Dashboard";
import { FoodPage } from "./pages/Food";
import { HistoryPage } from "./pages/History";
import { ProfilePage } from "./pages/Profile";
import { applyTheme, onThemeChange, telegramApp } from "./telegram";
import { USER_PAGES, type Me, type Page } from "./types";

// Operator screen: most users never download it.
const AdminPage = lazy(() => import("./pages/Admin"));

type Phase = "boot" | "gate" | "expired" | "ready" | "error";

function currentPage(): Page {
  const name = location.hash.replace(/^#\/?/, "").split("/")[0] ?? "";
  if (name === "admin" || (USER_PAGES as readonly string[]).includes(name)) return name as Page;
  return "dashboard";
}

/** Language before /me is known: Telegram's client language. */
function initialLang(): Lang {
  return asLang(telegramApp()?.initDataUnsafe.user?.language_code?.slice(0, 2));
}

export function App() {
  const [page, setPage] = useState<Page>(currentPage);
  const [me, setMe] = useState<Me | null>(null);
  const [phase, setPhase] = useState<Phase>("boot");
  const [error, setError] = useState<unknown>(null);
  const lang: Lang = me ? asLang(me.language) : initialLang();

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  useEffect(() => {
    const onHash = () => setPage(currentPage());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const tg = telegramApp();
    tg?.ready();
    tg?.expand();
    applyTheme(tg);
    const off = onThemeChange(() => applyTheme(telegramApp()));
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const onMedia = () => applyTheme(telegramApp());
    media?.addEventListener?.("change", onMedia);
    return () => {
      off();
      media?.removeEventListener?.("change", onMedia);
    };
  }, []);

  useEffect(() => onSessionLost(() => setPhase("expired")), []);

  const boot = useCallback(async () => {
    setPhase("boot");
    setError(null);
    const initData = telegramApp()?.initData;
    if (!sessionToken() && !initData) {
      setPhase("gate");
      return;
    }
    try {
      if (!sessionToken() && initData) await login(initData);
      setMe(await api<Me>("/api/v1/webapp/me"));
      setPhase("ready");
    } catch (err) {
      setError(err);
      setPhase((current) => (current === "expired" ? current : "error"));
    }
  }, []);

  useEffect(() => {
    void boot();
  }, [boot]);

  const go = useCallback((next: Page) => {
    if (location.hash !== `#${next}`) location.hash = next;
    setPage(next);
    window.scrollTo?.(0, 0);
  }, []);
  const back = useCallback(() => go("dashboard"), [go]);
  useBackButton(phase === "ready" && page !== "dashboard" ? back : null);

  if (phase === "boot") return <Shell lang={lang}><Loading cards={3} /></Shell>;
  if (phase === "gate" || phase === "expired") {
    const expired = phase === "expired";
    return (
      <Shell lang={lang}>
        <main className="center">
          <div>
            <h1>{t(lang, expired ? "expiredTitle" : "gateTitle")}</h1>
            <p>{t(lang, expired ? "expiredBody" : "gateBody")}</p>
            {expired && telegramApp()?.close && (
              <button className="primary" type="button" onClick={() => telegramApp()?.close?.()}>{t(lang, "close")}</button>
            )}
          </div>
        </main>
      </Shell>
    );
  }
  if (phase === "error" || !me) {
    return (
      <Shell lang={lang}>
        <main className="center"><ErrorState error={error} onRetry={() => void boot()} /></main>
      </Shell>
    );
  }

  const tabs: readonly Page[] = me.is_admin ? [...USER_PAGES, "admin"] : USER_PAGES;
  return (
    <Shell lang={lang}>
      <main className="app">
        {page === "dashboard" && <DashboardPage me={me} onOpen={go} />}
        {page === "food" && <FoodPage me={me} />}
        {page === "activity" && <ActivityPage />}
        {page === "history" && <HistoryPage me={me} />}
        {page === "profile" && <ProfilePage me={me} onMe={setMe} />}
        {page === "admin" && me.is_admin && (
          <Suspense fallback={<Loading />}><AdminPage /></Suspense>
        )}
      </main>
      <div className="nav">
        <nav aria-label={t(lang, "navigation")}>
          {tabs.map((item) => (
            <button
              key={item}
              type="button"
              className={item === page ? "on" : ""}
              aria-current={item === page ? "page" : undefined}
              onClick={() => go(item)}
            >
              <NavIcon page={item} active={item === page} />
              <span>{t(lang, item)}</span>
            </button>
          ))}
        </nav>
      </div>
    </Shell>
  );
}

function Shell({ lang, children }: { lang: Lang; children: ReactNode }) {
  return <LangProvider lang={lang}>{children}</LangProvider>;
}
