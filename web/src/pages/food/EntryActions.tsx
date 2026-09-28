import { useState } from "react";
import { ApiError, api } from "../../api";
import { confirmAction } from "../../telegram";
import { Field, SelectField } from "../../components/ui";
import { useAction } from "../../hooks/useAction";
import { MEAL_TYPES } from "../../i18n";
import { useT } from "../../LangContext";
import type { FoodEntry } from "../../types";

export function EntryActions({ entry, onChanged }: { entry: FoodEntry; onChanged: () => void }) {
  const { t } = useT();
  const action = useAction();
  const [open, setOpen] = useState(false);
  const [grams, setGrams] = useState(entry.grams == null ? "" : String(entry.grams));
  const [meal, setMeal] = useState(entry.meal_type ?? "snack");
  const [day, setDay] = useState(entry.local_date ?? "");
  const [kcal, setKcal] = useState("");
  const [protein, setProtein] = useState("");
  const [fat, setFat] = useState("");
  const [carbs, setCarbs] = useState("");
  const marked = entry.custom_fs_state === "refused" || entry.custom_fs_state === "needs_macros";
  if (entry.id == null || entry.version == null) return null;

  const savePortion = () => action.run(async () => {
    await api(`/api/v1/webapp/food-entries/${entry.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        version: entry.version,
        grams: grams.replace(",", "."),
        meal_type: meal,
        local_date: day || undefined,
      }),
    });
    onChanged();
    return t("saved");
  });

  const saveNumbers = () => action.run(async () => {
    await api(`/api/v1/webapp/products/${entry.product_id}/custom-nutrition`, {
      method: "POST",
      body: JSON.stringify({
        energy_kcal: kcal.replace(",", "."),
        protein_g: protein.replace(",", ".") || "0",
        fat_g: fat.replace(",", ".") || "0",
        carbs_g: carbs.replace(",", ".") || "0",
      }),
    });
    onChanged();
    return t("saved");
  });

  const pin = () => action.run(async () => {
    if (entry.product_id == null || !entry.name) return;
    const body = {
      alias: entry.name,
      suggested_portion_g: entry.grams,
      replace: false,
    };
    try {
      await api(`/api/v1/webapp/products/${entry.product_id}/pin`, {
        method: "POST", body: JSON.stringify(body),
      });
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 409) throw err;
      if (!(await confirmAction(t("pinReplace")))) return;
      await api(`/api/v1/webapp/products/${entry.product_id}/pin`, {
        method: "POST", body: JSON.stringify({ ...body, replace: true }),
      });
    }
    return t("saved");
  });

  const retry = () => action.run(async () => {
    await api(`/api/v1/webapp/products/${entry.product_id}/custom-retry`, { method: "POST" });
    onChanged();
    return t("saved");
  });

  return (
    <div className="entry-edit">
      <button className="secondary" type="button" onClick={() => setOpen((value) => !value)}>
        {t("editEntry")}
      </button>
      {entry.product_id != null && (
        <button className="secondary" type="button" disabled={action.busy} onClick={() => void pin()}>
          {t("pin")}
        </button>
      )}
      {open && (
        <>
          <Field label={t("grams")} inputMode="decimal" value={grams} onChange={(event) => setGrams(event.target.value)} />
          <SelectField label={t("meal")} value={meal} onChange={(event) => setMeal(event.target.value)}>
            {MEAL_TYPES.map((item) => <option key={item} value={item}>{t(item)}</option>)}
          </SelectField>
          <Field label={t("date")} type="date" value={day} onChange={(event) => setDay(event.target.value)} />
          <button className="primary" type="button" disabled={action.busy} onClick={() => void savePortion()}>
            {t("saveEntry")}
          </button>
        </>
      )}
      {marked && entry.product_id != null && (
        <>
          <p className="note">{t("fsMark")}</p>
          <Field label={t("kcal100")} inputMode="decimal" value={kcal} onChange={(event) => setKcal(event.target.value)} />
          <Field label={t("protein")} inputMode="decimal" value={protein} onChange={(event) => setProtein(event.target.value)} />
          <Field label={t("fat")} inputMode="decimal" value={fat} onChange={(event) => setFat(event.target.value)} />
          <Field label={t("carbs")} inputMode="decimal" value={carbs} onChange={(event) => setCarbs(event.target.value)} />
          <button className="secondary" type="button" disabled={action.busy || kcal.trim() === ""}
            onClick={() => void saveNumbers()}>
            {t("saveNumbers")}
          </button>
          <button className="secondary" type="button" disabled={action.busy} onClick={() => void retry()}>
            {t("retryFs")}
          </button>
        </>
      )}
    </div>
  );
}
