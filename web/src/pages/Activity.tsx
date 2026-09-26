import { useEffect, useState } from "react";
import { api, telegramApp } from "../api";
import { formatNumber, t, type Lang } from "../i18n";
import type { Integrations, TodayStats } from "../types";

export function ActivityPage({ lang }: { lang: Lang }) {
  const [stats, setStats] = useState<TodayStats | null>(null);
  const [links, setLinks] = useState<Integrations | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api<TodayStats>("/api/v1/webapp/today"),
      api<Integrations>("/api/v1/webapp/integrations"),
    ]).then(([today, integrations]) => {
      setStats(today);
      setLinks(integrations);
    }).catch((err: unknown) => setError(err instanceof Error ? err.message : "error"));
  }, []);

  async function connect(provider: "whoop" | "fatsecret") {
    const result = await api<{ url: string }>(`/api/v1/webapp/integrations/${provider}/connect-link`, { method: "POST" });
    const tg = telegramApp();
    if (tg) tg.openLink(result.url);
    else window.location.href = result.url;
  }

  if (error) return <p className="banner">{error}</p>;
  if (!stats || !links) return <p>{t(lang, "loading")}</p>;

  return (
    <>
      <header className="top"><h1>{t(lang, "activity")}</h1></header>
      <section className="card">
        <h2>{t(lang, "whoop")}</h2>
        <p>{links.whoop.connected ? t(lang, "connected") : t(lang, "notConnected")}</p>
        {!links.whoop.connected && (
          <button className="primary" type="button" onClick={() => void connect("whoop")}>{t(lang, "connect")}</button>
        )}
      </section>
      <section className="card">
        <h2>{t(lang, "recent")}</h2>
        <div className="row">
          <div><div className="caption">{t(lang, "strain")}</div><div className="metric">{stats.today_strain.toFixed(1)}</div></div>
          <div><div className="caption">{t(lang, "workouts")}</div><div className="metric">{stats.today_workout_count}</div></div>
          <div><div className="caption">{t(lang, "burned")}</div><div className="metric">{formatNumber(lang, stats.today_calories_out)}</div></div>
        </div>
        {stats.whoop_recovery && <p>{t(lang, "recovery")}: {stats.whoop_recovery}</p>}
        {stats.whoop_sleep && <p>{t(lang, "sleep")}: {stats.whoop_sleep}</p>}
        {stats.whoop_activities && <p className="note">{stats.whoop_activities}</p>}
        <p className="caption">{t(lang, "source")}: {stats.calories_burned_source}</p>
      </section>
      <section className="card">
        <h2>{t(lang, "apple")}</h2>
        <p>{links.apple_health.connected ? t(lang, "connected") : t(lang, "notConnected")}</p>
        {links.apple_health.last_sync_at && <p className="caption">{t(lang, "lastSync")}: {links.apple_health.last_sync_at}</p>}
        <ul className="list">
          <li><span>{t(lang, "steps")}</span><strong>{formatNumber(lang, stats.apple_health_steps)}</strong></li>
          <li><span>{t(lang, "heart")}</span><strong>{formatNumber(lang, stats.apple_health_avg_heart_rate)}</strong></li>
          <li><span>{t(lang, "sleep")}</span><strong>{stats.apple_health_sleep_hours.toFixed(1)} h</strong></li>
          <li><span>{t(lang, "distance")}</span><strong>{stats.apple_health_distance_km.toFixed(1)} km</strong></li>
          <li><span>{t(lang, "exercise")}</span><strong>{formatNumber(lang, stats.apple_health_exercise_minutes)} min</strong></li>
          <li><span>{t(lang, "weight")}</span><strong>{stats.apple_health_body_mass_kg ? `${stats.apple_health_body_mass_kg} kg` : "—"}</strong></li>
          <li><span>{t(lang, "bmr")}</span><strong>{stats.bmr_kcal ? `${formatNumber(lang, stats.bmr_kcal)} kcal` : "—"}</strong></li>
        </ul>
        {stats.apple_health_workouts && <p className="note">{stats.apple_health_workouts}</p>}
        <p className="note">{t(lang, "appleHint")}</p>
      </section>
    </>
  );
}
