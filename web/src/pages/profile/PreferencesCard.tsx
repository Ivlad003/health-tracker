import { useCallback, useState } from "react";
import { api } from "../../api";
import { ActionFeedback, ErrorState, Field, Loading, Section, SelectField, Switch } from "../../components/ui";
import { useAction } from "../../hooks/useAction";
import { useApi } from "../../hooks/useApi";
import { useT } from "../../LangContext";
import type { Preferences } from "../../types";

interface PrefsResponse {
  preferences: Preferences;
  version: number;
}

export function PreferencesCard() {
  const { t } = useT();
  const load = useCallback((signal: AbortSignal) => api<PrefsResponse>("/api/v1/webapp/preferences", { signal }), []);
  const { data, error, reload, setData } = useApi(load);
  const [changes, setChanges] = useState<Partial<Preferences>>({});
  const action = useAction();

  if (error != null && !data) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <Loading cards={1} />;
  const prefs: Preferences = { ...data.preferences, ...changes };
  const set = <K extends keyof Preferences>(key: K, value: Preferences[K]) =>
    setChanges((current) => ({ ...current, [key]: value }));

  const save = () => action.run(async () => {
    const saved = await api<PrefsResponse>("/api/v1/webapp/preferences", {
      method: "PUT",
      body: JSON.stringify({ version: data.version, changes }),
    });
    setData(saved);
    setChanges({});
    return t("saved");
  });

  return (
    <Section title={t("recording")}>
      <SelectField label={t("recording")} value={prefs.recording_policy}
        onChange={(event) => set("recording_policy", event.target.value as Preferences["recording_policy"])}>
        <option value="auto_confirmed">{t("auto")}</option>
        <option value="review_all">{t("review")}</option>
      </SelectField>
      <Switch label={t("catalogAdd")} checked={prefs.catalog_auto_add} onChange={(value) => set("catalog_auto_add", value)} />
      <Switch label={t("exportFs")} checked={prefs.fatsecret_export} onChange={(value) => set("fatsecret_export", value)} />
      <Switch label={t("morning")} checked={prefs.briefing_morning_enabled}
        onChange={(value) => set("briefing_morning_enabled", value)} />
      <Field label={t("morning")} type="time" value={prefs.briefing_morning_time}
        onChange={(event) => set("briefing_morning_time", event.target.value)} />
      <Switch label={t("evening")} checked={prefs.briefing_evening_enabled}
        onChange={(value) => set("briefing_evening_enabled", value)} />
      <Field label={t("evening")} type="time" value={prefs.briefing_evening_time}
        onChange={(event) => set("briefing_evening_time", event.target.value)} />
      <ActionFeedback error={action.error} notice={action.notice} />
      <button className="primary" type="button" disabled={action.busy || Object.keys(changes).length === 0}
        onClick={() => void save()}>
        {t("save")}
      </button>
    </Section>
  );
}
