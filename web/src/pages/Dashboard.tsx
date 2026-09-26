import { useEffect, useState } from "react";
import { api } from "../api";
import { formatNumber, t, type Lang } from "../i18n";
import type { DayView, Me, Page, TodayStats } from "../types";

export function DashboardPage({ me, onOpen }: { me: Me; onOpen: (page: Page) => void }) {
  const lang: Lang = me.language === "en" ? "en" : "uk";
  const [day, setDay] = useState<DayView | null>(null);
  const [stats, setStats] = useState<TodayStats | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    Promise.all([
      api<DayView>("/api/v1/webapp/food-entries"),
      api<TodayStats>("/api/v1/webapp/today"),
    ]).then(([food, today]) => {
      if (!live) return;
      setDay(food);
      setStats(today);
    }).catch((err: unknown) => {
      if (live) setError(err instanceof Error ? err.message : "error");
    });
    return () => { live = false; };
  }, []);

  if (error) return <p className="banner">{error}</p>;
  if (!day || !stats) return <p>{t(lang, "loading")}</p>;

  const eaten = Number(day.total_kcal);
  const burned = stats.today_calories_out || 0;
  const balance = eaten - burned;
  const goal = day.goal?.calories || me.daily_calorie_goal || 0;
  const progress = goal > 0 ? Math.min(100, (eaten / goal) * 100) : 0;
  const tone = balance < -50 ? "good" : balance > 50 ? "warn" : "good";

  return (
    <>
      <header className="top">
        <h1>{t(lang, "dashboard")}</h1>
        <button className="icon-btn" type="button" aria-label={t(lang, "profile")} onClick={() => onOpen("profile")}>⚙</button>
      </header>
      {day.partial && <p className="banner">{t(lang, "partial")}</p>}
      <section className="card hero">
        <h2>{t(lang, "balance")}</h2>
        <div className={`num ${tone}`}>{balance > 0 ? "+" : ""}{formatNumber(lang, balance)} kcal</div>
        <div className="bar"><span style={{ width: `${progress}%` }} /></div>
        <div className="split">
          <span>{t(lang, "eaten")}: {formatNumber(lang, eaten)}</span>
          <span>{t(lang, "burned")}: {formatNumber(lang, burned)}</span>
        </div>
        {goal > 0 && <p className="caption">{t(lang, "goal")}: {formatNumber(lang, goal)} kcal</p>}
      </section>
      <div className="row">
        <section className="card">
          <h2>🍎 {t(lang, "food")}</h2>
          <div className="metric">{formatNumber(lang, eaten)}</div>
          <p className="caption">
            {t(lang, "protein")} {formatNumber(lang, day.protein_g)} · {t(lang, "fat")} {formatNumber(lang, day.fat_g)} · {t(lang, "carbs")} {formatNumber(lang, day.carbs_g)}
          </p>
        </section>
        <section className="card">
          <h2>💪 {t(lang, "activity")}</h2>
          <div className="metric" style={{ color: "var(--accent)" }}>{formatNumber(lang, burned)}</div>
          <p className="caption">{t(lang, "strain")}: {stats.today_strain.toFixed(1)}</p>
        </section>
      </div>
      <section className="card">
        <h2>{t(lang, "quick")}</h2>
        <div className="actions">
          <button className="secondary" type="button" onClick={() => onOpen("food")}>{t(lang, "manual")}</button>
          <button className="secondary" type="button" onClick={() => onOpen("food")}>{t(lang, "photo")}</button>
        </div>
        <p className="note">{t(lang, "voiceHint")}</p>
      </section>
      <section className="card">
        <h2>{t(lang, "recent")}</h2>
        {day.entries.length === 0 ? <p className="note">{t(lang, "emptyDay")}</p> : (
          <ul className="list">
            {day.entries.map((entry, index) => (
              <li key={`${entry.id ?? "r"}-${index}`}>
                <span>{entry.name || "—"}<br /><span className="caption">{entry.meal_type}</span></span>
                <strong>{formatNumber(lang, entry.energy_kcal)}</strong>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
