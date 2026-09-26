import { useState } from "react";
import { api } from "../../api";
import { ActionFeedback, Field, Section } from "../../components/ui";
import { useAction } from "../../hooks/useAction";
import { useT } from "../../LangContext";

export function NewProductCard({ onCreated }: { onCreated: () => void }) {
  const { t } = useT();
  const [name, setName] = useState("");
  const [kcal, setKcal] = useState("");
  const [alias, setAlias] = useState("");
  const action = useAction();

  const create = () => action.run(async () => {
    const energy = kcal.trim().replace(",", ".");
    await api("/api/v1/webapp/products", {
      method: "POST",
      body: JSON.stringify({
        name: name.trim(),
        default_alias: alias.trim() || null,
        nutrition: energy ? { basis_quantity: 100, basis_unit: "g", grams_per_basis: 100, energy_kcal: energy } : null,
      }),
    });
    setName("");
    setKcal("");
    setAlias("");
    onCreated();
    return t("saved");
  });

  return (
    <Section title={t("newProduct")}>
      <Field label={t("name")} value={name} maxLength={255} onChange={(event) => setName(event.target.value)} />
      <Field label={t("kcal100")} inputMode="decimal" value={kcal} onChange={(event) => setKcal(event.target.value)} />
      <Field label={t("alias")} value={alias} maxLength={255} onChange={(event) => setAlias(event.target.value)} />
      <ActionFeedback error={action.error} notice={action.notice} />
      <button className="primary" type="button" disabled={action.busy || name.trim().length === 0} onClick={() => void create()}>
        {t("create")}
      </button>
    </Section>
  );
}
