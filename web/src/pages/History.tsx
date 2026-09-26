import { useCallback, useState } from "react";
import { api } from "../api";
import { Banner, EntryList, ErrorState, Loading, PageHeader, Section } from "../components/ui";
import { useApi } from "../hooks/useApi";
import { formatDay, formatNumber, shiftDay, todayIn } from "../i18n";
import { useT } from "../LangContext";
import type { DayRange, Me } from "../types";

export const HISTORY_DAYS = 7;

export function HistoryPage({ me }: { me: Me }) {
  const { t, lang } = useT();
  const [open, setOpen] = useState<string | null>(null);
  const today = todayIn(me.timezone);
  const load = useCallback((signal: AbortSignal) => {
    const from = shiftDay(today, -(HISTORY_DAYS - 1));
    return api<DayRange>(`/api/v1/webapp/food-entries/range?from=${from}&to=${today}`, { signal });
  }, [today]);
  const { data, error, reload } = useApi(load);

  const header = <PageHeader title={t("history")} />;
  if (error != null && !data) return <>{header}<ErrorState error={error} onRetry={reload} /></>;
  if (!data) return <>{header}<Loading /></>;

  return (
    <>
      {header}
      <Section title={t("week")}>
        <ul className="list">
          {data.days.map((day) => {
            const expanded = open === day.local_date;
            return (
              <li key={day.local_date} className="day">
                <button
                  className="item"
                  type="button"
                  aria-expanded={expanded}
                  aria-controls={`day-${day.local_date}`}
                  onClick={() => setOpen(expanded ? null : day.local_date)}
                >
                  <span>{formatDay(lang, day.local_date, today)}</span>
                  <strong>{formatNumber(lang, day.total_kcal)} {t("kcal")}</strong>
                </button>
                {expanded && (
                  <div id={`day-${day.local_date}`} className="day-detail">
                    {day.partial && <Banner>{t("partial")}</Banner>}
                    <EntryList entries={day.entries} />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </Section>
    </>
  );
}
