import { useId, useState } from "react";
import { api } from "../../api";
import { ActionFeedback, Field, Section, SelectField, Switch } from "../../components/ui";
import { useAction } from "../../hooks/useAction";
import type { Lang } from "../../i18n";
import { useT } from "../../LangContext";
import type { Me } from "../../types";

function timeZones(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return [];
  }
}

function toNumber(value: string): number | null {
  const n = Number(value.replace(",", "."));
  return value.trim() && Number.isFinite(n) ? n : null;
}

export function PersonalCard({ me, onMe }: { me: Me; onMe: (me: Me) => void }) {
  const { t } = useT();
  const [form, setForm] = useState(me);
  const action = useAction();
  const zonesId = useId();
  const set = <K extends keyof Me>(key: K, value: Me[K]) => setForm((current) => ({ ...current, [key]: value }));

  const save = () => action.run(async () => {
    const updated = await api<Me>("/api/v1/webapp/me", {
      method: "PATCH",
      body: JSON.stringify({
        profile_version: form.profile_version,
        language: form.language,
        timezone: form.timezone,
        birth_year: form.birth_year,
        sex: form.sex,
        height_cm: form.height_cm,
        journal_enabled: form.journal_enabled,
        journal_time_1: form.journal_time_1,
        journal_time_2: form.journal_time_2,
      }),
    });
    setForm(updated);
    onMe(updated);
    return t("saved");
  });

  return (
    <Section title={t("personal")}>
      <SelectField label={t("language")} value={form.language} onChange={(event) => set("language", event.target.value as Lang)}>
        <option value="uk">Українська</option>
        <option value="en">English</option>
      </SelectField>
      <Field label={t("timezone")} list={zonesId} value={form.timezone} autoComplete="off"
        onChange={(event) => set("timezone", event.target.value)} />
      <datalist id={zonesId}>
        {timeZones().map((zone) => <option key={zone} value={zone} />)}
      </datalist>
      <Field label={t("height")} inputMode="decimal" value={form.height_cm ?? ""}
        onChange={(event) => set("height_cm", toNumber(event.target.value))} />
      <Field label={t("birthYear")} inputMode="numeric" value={form.birth_year ?? ""}
        onChange={(event) => set("birth_year", toNumber(event.target.value))} />
      <SelectField label={t("sex")} value={form.sex ?? ""}
        onChange={(event) => set("sex", (event.target.value || null) as Me["sex"])}>
        <option value="">{t("unspecified")}</option>
        <option value="female">{t("female")}</option>
        <option value="male">{t("male")}</option>
      </SelectField>
      <Switch label={t("journal")} checked={form.journal_enabled} onChange={(value) => set("journal_enabled", value)} />
      <Field label={t("journalTime1")} type="time" value={form.journal_time_1 ?? ""}
        onChange={(event) => set("journal_time_1", event.target.value || null)} />
      <Field label={t("journalTime2")} type="time" value={form.journal_time_2 ?? ""}
        onChange={(event) => set("journal_time_2", event.target.value || null)} />
      <ActionFeedback error={action.error} notice={action.notice} />
      <button className="primary" type="button" disabled={action.busy} onClick={() => void save()}>{t("save")}</button>
    </Section>
  );
}
