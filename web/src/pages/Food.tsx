import { useCallback, useState } from "react";
import { api } from "../api";
import { ActionFeedback, EntryList, ErrorState, Loading, PageHeader, Section } from "../components/ui";
import { useAction } from "../hooks/useAction";
import { useApi } from "../hooks/useApi";
import type { Meal } from "../i18n";
import { useT } from "../LangContext";
import { mealNow } from "../lib/meal";
import type { DayView, Draft, FoodEntry, Me, Product } from "../types";
import { deleteEntry } from "./food/actions";
import { DraftsCard } from "./food/DraftsCard";
import { LogCard } from "./food/LogCard";
import { MyProductsCard } from "./food/MyProductsCard";
import { NewProductCard } from "./food/NewProductCard";
import { PhotoCard } from "./food/PhotoCard";
import { TryPhraseCard } from "./food/TryPhraseCard";

export const MY_PRODUCTS_LIMIT = 30;

export function FoodPage({ me }: { me: Me }) {
  const { t } = useT();
  const [meal, setMeal] = useState<Meal>(() => mealNow(me.timezone));
  // Empty = "auto": each product's usual/suggested portion.
  const [grams, setGrams] = useState("");
  const load = useCallback((signal: AbortSignal) => Promise.all([
    api<DayView>("/api/v1/webapp/food-entries", { signal }),
    api<{ items: Product[] }>(`/api/v1/webapp/products?limit=${MY_PRODUCTS_LIMIT}`, { signal }),
    api<{ items: Draft[] }>("/api/v1/webapp/food-drafts", { signal }),
  ]), []);
  const { data, error, reload } = useApi(load);
  const removal = useAction();

  const header = <PageHeader title={t("food")} />;
  if (error != null && !data) return <>{header}<ErrorState error={error} onRetry={reload} /></>;
  if (!data) return <>{header}<Loading cards={3} /></>;
  const [day, products, drafts] = data;

  const remove = (entry: FoodEntry) => {
    if (entry.id == null || entry.version == null) return;
    const { id, version } = entry;
    void removal.run(async () => { await deleteEntry(id, version); reload(); });
  };

  return (
    <>
      {header}
      <LogCard meal={meal} onMeal={setMeal} grams={grams} onGrams={setGrams} onLogged={reload} />
      <MyProductsCard products={products.items} meal={meal} grams={grams} onLogged={reload} />
      <Section title={t("recent")}>
        <ActionFeedback error={removal.error} />
        <EntryList entries={day.entries} onDelete={remove} busy={removal.busy} />
      </Section>
      <PhotoCard onUploaded={reload} />
      <DraftsCard drafts={drafts.items} meal={meal} onChanged={reload} />
      <NewProductCard onCreated={reload} />
      <TryPhraseCard />
    </>
  );
}
