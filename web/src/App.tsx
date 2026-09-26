import { useEffect, useState } from "react";
import { api, clearSession, saveSession, sessionToken, telegramApp } from "./api";
import { t, type Lang } from "./i18n";
import { ActivityPage } from "./pages/Activity";
import { AdminPage } from "./pages/Admin";
import { DashboardPage } from "./pages/Dashboard";
import { FoodPage } from "./pages/Food";
import { HistoryPage } from "./pages/History";
import { ProfilePage } from "./pages/Profile";
import type { Me, Page } from "./types";

const PAGES: Page[] = ["dashboard", "food", "activity", "history", "profile"];

function currentPage(): Page {
  const name = location.hash.replace("#", "");
  if (name === "admin" || PAGES.includes(name as Page)) return name as Page;
  return "dashboard";
}

export function App() {
  const [page, setPage] = useState<Page>(currentPage);
  const [me, setMe] = useState<Me | null>(null);
  const [phase, setPhase] = useState<"boot" | "gate" | "ready" | "error">("boot");
  const [error, setError] = useState("");

  useEffect(() => {
    const onHash = () => setPage(currentPage());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const tg = telegramApp();
    tg?.ready();
    tg?.expand();
    tg?.setHeaderColor?.("#4CAF50");
    tg?.setBackgroundColor?.("#F5F5F5");
    void boot();
  }, []);

  async function boot() {
    setPhase("boot");
    setError("");
    const tg = telegramApp();
    try {
      if (sessionToken()) {
        setMe(await api<Me>("/api/v1/webapp/me"));
        setPhase("ready");
        return;
      }
      if (!tg?.initData) {
        setPhase("gate");
        return;
      }
      const auth = await api<{ session_token: string }>("/api/v1/webapp/auth/telegram", {
        method: "POST",
        body: JSON.stringify({ init_data: tg.initData }),
      });
      saveSession(auth.session_token);
      setMe(await api<Me>("/api/v1/webapp/me"));
      setPhase("ready");
    } catch (err) {
      clearSession();
      if (!tg?.initData) {
        setPhase("gate");
        return;
      }
      setError(err instanceof Error ? err.message : "error");
      setPhase("error");
    }
  }

  function go(next: Page) {
    location.hash = next;
    setPage(next);
  }

  const lang: Lang = me?.language === "en" ? "en" : "uk";
  if (phase === "boot") return <main className="center"><p>{t("uk", "loading")}</p></main>;
  if (phase === "gate") {
    return (
      <main className="center">
        <div>
          <h1>{t("uk", "gateTitle")}</h1>
          <p>{t("uk", "gateBody")}</p>
          <p>{t("en", "gateBody")}</p>
        </div>
      </main>
    );
  }
  if (phase === "error" || !me) {
    return (
      <main className="center">
        <div>
          <p>{error}</p>
          <button className="primary" type="button" onClick={() => void boot()}>{t(lang, "retry")}</button>
        </div>
      </main>
    );
  }

  const tabs: Page[] = me.is_admin ? [...PAGES, "admin"] : PAGES;
  return (
    <div className="app">
      {page === "dashboard" && <DashboardPage me={me} onOpen={go} />}
      {page === "food" && <FoodPage me={me} />}
      {page === "activity" && <ActivityPage lang={lang} />}
      {page === "history" && <HistoryPage me={me} />}
      {page === "profile" && <ProfilePage me={me} onMe={setMe} />}
      {page === "admin" && me.is_admin && <AdminPage lang={lang} />}
      <div className="nav">
        <nav>
          {tabs.map((item) => (
            <button key={item} type="button" className={item === page ? "on" : ""} onClick={() => go(item)}>
              {t(lang, item)}
            </button>
          ))}
        </nav>
      </div>
    </div>
  );
}
