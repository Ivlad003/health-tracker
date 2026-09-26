import { useCallback } from "react";
import { api } from "../api";
import { ActionFeedback, ErrorState, Loading, PageHeader, Section } from "../components/ui";
import { useAction } from "../hooks/useAction";
import { useApi } from "../hooks/useApi";
import { formatDateTime, formatNumber } from "../i18n";
import { useT } from "../LangContext";
import { openExternal } from "../telegram";
import type { Integrations, TodayStats } from "../types";

export async function openConnectLink(provider: "whoop" | "fatsecret"): Promise<void> {
  const result = await api<{ url: string }>(`/api/v1/webapp/integrations/${provider}/connect-link`, { method: "POST" });
  openExternal(result.url);
}

export function ActivityPage() {
  const { t, lang } = useT();
  const load = useCallback((signal: AbortSignal) => Promise.all([
    api<TodayStats>("/api/v1/webapp/today", { signal }),
    api<Integrations>("/api/v1/webapp/integrations", { signal }),
  ]), []);
  const { data, error, reload } = useApi(load);
  const action = useAction();

  const header = <PageHeader title={t("activity")} />;
  if (error != null && !data) return <>{header}<ErrorState error={error} onRetry={reload} /></>;
  if (!data) return <>{header}<Loading cards={3} /></>;
  const [stats, links] = data;
  const unit = (value: string, key: "unitH" | "unitKm" | "unitMin" | "unitKg" | "kcal") => `${value} ${t(key)}`;

  return (
    <>
      {header}
      <ActionFeedback error={action.error} />
      <Section title={t("whoop")}>
        <p>{links.whoop.connected ? t("connected") : t("notConnected")}</p>
        {!links.whoop.connected && (
          <button className="primary" type="button" disabled={action.busy}
            onClick={() => void action.run(() => openConnectLink("whoop"))}>
            {t("connect")}
          </button>
        )}
      </Section>
      <Section title={t("recent")}>
        <div className="row metrics">
          <div><div className="caption">{t("strain")}</div><div className="metric">{formatNumber(lang, stats.today_strain, 1)}</div></div>
          <div><div className="caption">{t("workouts")}</div><div className="metric">{formatNumber(lang, stats.today_workout_count)}</div></div>
          <div><div className="caption">{t("burned")}</div><div className="metric">{formatNumber(lang, stats.today_calories_out)}</div></div>
        </div>
        {stats.whoop_recovery && <p>{t("recovery")}: {stats.whoop_recovery}</p>}
        {stats.whoop_sleep && <p>{t("sleep")}: {stats.whoop_sleep}</p>}
        {stats.whoop_activities && <p className="note">{stats.whoop_activities}</p>}
        <p className="caption">{t("source")}: {stats.calories_burned_source}</p>
      </Section>
      <Section title={t("apple")}>
        <p>{links.apple_health.connected ? t("connected") : t("notConnected")}</p>
        {links.apple_health.last_sync_at && (
          <p className="caption">{t("lastSync")}: {formatDateTime(lang, links.apple_health.last_sync_at)}</p>
        )}
        <ul className="list">
          <li><span>{t("steps")}</span><strong>{formatNumber(lang, stats.apple_health_steps)}</strong></li>
          <li><span>{t("heart")}</span><strong>{formatNumber(lang, stats.apple_health_avg_heart_rate)}</strong></li>
          <li><span>{t("sleep")}</span><strong>{unit(formatNumber(lang, stats.apple_health_sleep_hours, 1), "unitH")}</strong></li>
          <li><span>{t("distance")}</span><strong>{unit(formatNumber(lang, stats.apple_health_distance_km, 1), "unitKm")}</strong></li>
          <li><span>{t("exercise")}</span><strong>{unit(formatNumber(lang, stats.apple_health_exercise_minutes), "unitMin")}</strong></li>
          <li><span>{t("weight")}</span><strong>{stats.apple_health_body_mass_kg ? unit(formatNumber(lang, stats.apple_health_body_mass_kg, 1), "unitKg") : "—"}</strong></li>
          <li><span>{t("bmr")}</span><strong>{stats.bmr_kcal ? unit(formatNumber(lang, stats.bmr_kcal), "kcal") : "—"}</strong></li>
        </ul>
        {stats.apple_health_workouts && <p className="note">{stats.apple_health_workouts}</p>}
        <p className="note">{t("appleHint")}</p>
      </Section>
    </>
  );
}
