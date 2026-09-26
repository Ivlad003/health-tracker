import { useCallback } from "react";
import { api } from "../api";
import { EntryList, ErrorState, Loading, PageHeader, Section, Banner } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { formatNumber } from "../i18n";
import { useT } from "../LangContext";
import { balanceTone, effectiveGoal, goalProgress, type BalanceTone } from "../lib/balance";
import type { DayView, Me, Page, TodayStats } from "../types";

const TONE_LABEL = {
  "deficit": "deficit",
  "on-target": "onTarget",
  "surplus": "surplus",
  "large-surplus": "surplus",
} as const satisfies Record<BalanceTone, string>;

export function DashboardPage({ me, onOpen }: { me: Me; onOpen: (page: Page) => void }) {
  const { t, lang } = useT();
  const load = useCallback((signal: AbortSignal) => Promise.all([
    api<DayView>("/api/v1/webapp/food-entries", { signal }),
    api<TodayStats>("/api/v1/webapp/today", { signal }),
  ]), []);
  const { data, error, loading, reload } = useApi(load);

  const header = (
    <PageHeader
      title={t("dashboard")}
      action={(
        <button className="icon-btn" type="button" aria-label={t("profile")} onClick={() => onOpen("profile")}>
          <GearIcon />
        </button>
      )}
    />
  );
  if (error != null && !data) return <>{header}<ErrorState error={error} onRetry={reload} /></>;
  if (!data) return <>{header}<Loading cards={3} /></>;

  const [day, stats] = data;
  const eaten = Number(day.total_kcal);
  const burned = stats.today_calories_out || 0;
  const balance = eaten - burned;
  const goal = effectiveGoal(day.goal?.calories, me.daily_calorie_goal);
  const progress = goalProgress(eaten, goal);
  const tone = balanceTone(balance);

  return (
    <>
      {header}
      {day.partial && <Banner>{t("partial")}</Banner>}
      <Section title={t("balance")} className="hero">
        <div className={`num tone-${tone}`} aria-busy={loading}>
          {balance > 0 ? "+" : ""}{formatNumber(lang, balance)} {t("kcal")}
        </div>
        <p className={`caption tone-${tone}`}>{t(TONE_LABEL[tone])}</p>
        {goal != null && (
          <div
            className="bar"
            role="progressbar"
            aria-label={t("goal")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(progress)}
          >
            <span style={{ width: `${progress}%` }} />
          </div>
        )}
        <div className="split">
          <span>{t("eaten")}: {formatNumber(lang, eaten)}</span>
          <span>{t("burned")}: {formatNumber(lang, burned)}</span>
        </div>
        {goal != null && <p className="caption">{t("goal")}: {formatNumber(lang, goal)} {t("kcal")}</p>}
      </Section>
      <div className="row">
        <button className="card tile" type="button" onClick={() => onOpen("history")}>
          <span className="tile-title">{t("food")}</span>
          <span className="metric">{formatNumber(lang, eaten)}</span>
          <span className="caption">
            {t("protein")} {formatNumber(lang, day.protein_g)} · {t("fat")} {formatNumber(lang, day.fat_g)} · {t("carbs")} {formatNumber(lang, day.carbs_g)}
          </span>
        </button>
        <button className="card tile" type="button" onClick={() => onOpen("activity")}>
          <span className="tile-title">{t("activity")}</span>
          <span className="metric accent">{formatNumber(lang, burned)}</span>
          <span className="caption">{t("strain")}: {formatNumber(lang, stats.today_strain, 1)}</span>
        </button>
      </div>
      <Section title={t("quick")}>
        <div className="actions">
          <button className="secondary" type="button" onClick={() => onOpen("food")}>{t("manual")}</button>
          <button className="secondary" type="button" onClick={() => onOpen("food")}>{t("photo")}</button>
        </div>
        <p className="note">{t("voiceHint")}</p>
      </Section>
      <Section title={t("recent")}>
        <EntryList entries={day.entries} />
        {day.entries.length === 0 && (
          <button className="primary" type="button" onClick={() => onOpen("food")}>{t("logFood")}</button>
        )}
      </Section>
    </>
  );
}

function GearIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </svg>
  );
}
