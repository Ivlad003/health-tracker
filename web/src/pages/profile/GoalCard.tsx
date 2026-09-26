import { useCallback, useState } from "react";
import { api } from "../../api";
import { ActionFeedback, ErrorState, Field, Loading, Section } from "../../components/ui";
import { useAction } from "../../hooks/useAction";
import { useApi } from "../../hooks/useApi";
import { useT } from "../../LangContext";
import type { GoalsResponse } from "../../types";

export const GOAL_MIN = 500;
export const GOAL_MAX = 10000;

export function parseGoal(value: string): number | null {
  const n = Number(value.trim());
  return Number.isInteger(n) && n >= GOAL_MIN && n <= GOAL_MAX ? n : null;
}

export function GoalCard() {
  const { t } = useT();
  const load = useCallback((signal: AbortSignal) => api<GoalsResponse>("/api/v1/webapp/goals", { signal }), []);
  const { data, error, reload, setData } = useApi(load);
  const [draft, setDraft] = useState<string | null>(null);
  const action = useAction();

  if (error != null && !data) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <Loading cards={1} />;
  const current = data.current?.calories ?? null;
  const value = draft ?? (current == null ? "" : String(current));
  const parsed = parseGoal(value);
  const changed = parsed !== null && parsed !== current;

  const save = () => action.run(async () => {
    if (parsed === null) return;
    const saved = await api<{ calories: number; effective_date: string }>("/api/v1/webapp/goals", {
      method: "PUT",
      body: JSON.stringify({ calories: parsed }),
    });
    setData({ ...data, current: saved });
    setDraft(null);
    return t("saved");
  });

  return (
    <Section title={t("goal")}>
      <Field
        label={t("calories")}
        inputMode="numeric"
        value={value}
        hint={t("goalRange")}
        aria-invalid={value.trim() !== "" && parsed === null}
        onChange={(event) => setDraft(event.target.value)}
      />
      <ActionFeedback error={action.error} notice={action.notice} />
      <button className="primary" type="button" disabled={action.busy || !changed} onClick={() => void save()}>
        {t("save")}
      </button>
    </Section>
  );
}
