import { useEffect, useState } from "react";
import { api } from "../api";
import { formatNumber, t, type Lang } from "../i18n";
import type { DayView, Me } from "../types";

function todayIn(timezone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(new Date());
}

function shift(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function HistoryPage({ me }: { me: Me }) {
  const lang: Lang = me.language === "en" ? "en" : "uk";
  const [days, setDays] = useState<DayView[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const start = todayIn(me.timezone);
    const dates = Array.from({ length: 7 }, (_, index) => shift(start, -index));
    Promise.all(dates.map((date) => api<DayView>(`/api/v1/webapp/food-entries?date=${date}`)))
      .then(setDays)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "error"));
  }, [me.timezone]);

  if (error) return <p className="banner">{error}</p>;
  if (days.length === 0) return <p>{t(lang, "loading")}</p>;

  return (
    <>
      <header className="top"><h1>{t(lang, "history")}</h1></header>
      <section className="card">
        <h2>{t(lang, "week")}</h2>
        <ul className="list">
          {days.map((day) => (
            <li key={day.local_date}>
              <button className="item" type="button" onClick={() => setOpen(open === day.local_date ? null : day.local_date)}>
                <span>{day.local_date}</span>
                <strong>{formatNumber(lang, day.total_kcal)} kcal</strong>
              </button>
            </li>
          ))}
        </ul>
      </section>
      {days.filter((day) => day.local_date === open).map((day) => (
        <section className="card" key={day.local_date}>
          <h2>{day.local_date}</h2>
          {day.partial && <p className="banner">{t(lang, "partial")}</p>}
          {day.entries.length === 0 ? <p className="note">{t(lang, "emptyDay")}</p> : (
            <ul className="list">
              {day.entries.map((entry, index) => (
                <li key={`${entry.id ?? "r"}-${index}`}>
                  <span>{entry.name}<br /><span className="caption">{entry.meal_type}</span></span>
                  <strong>{formatNumber(lang, entry.energy_kcal)}</strong>
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </>
  );
}
