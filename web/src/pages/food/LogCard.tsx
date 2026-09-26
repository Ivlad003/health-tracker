import { useEffect, useState } from "react";
import { api } from "../../api";
import { ActionFeedback, Field, Section } from "../../components/ui";
import { isAbort } from "../../errors";
import { useAction } from "../../hooks/useAction";
import { MEAL_TYPES, formatNumber, type Meal } from "../../i18n";
import { useT } from "../../LangContext";
import { portionFor } from "../../lib/meal";
import type { SearchItem } from "../../types";
import { ensureProduct, logProduct } from "./actions";

export const SEARCH_MIN_CHARS = 2;
export const SEARCH_DEBOUNCE_MS = 300;

export function LogCard({ meal, onMeal, grams, onGrams, onLogged }: {
  meal: Meal;
  onMeal: (meal: Meal) => void;
  grams: string;
  onGrams: (value: string) => void;
  onLogged: () => void;
}) {
  const { t, lang } = useT();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchItem[]>([]);
  const [searchError, setSearchError] = useState<unknown>(null);
  const action = useAction();

  useEffect(() => {
    const text = query.trim();
    if (text.length < SEARCH_MIN_CHARS) {
      setHits([]);
      setSearchError(null);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api<{ items: SearchItem[] }>(`/api/v1/webapp/products/search?q=${encodeURIComponent(text)}`, { signal: controller.signal })
        .then((found) => { setHits(found.items); setSearchError(null); })
        .catch((err: unknown) => { if (!isAbort(err)) setSearchError(err); });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query]);

  const pick = (item: SearchItem) => action.run(async () => {
    const productId = await ensureProduct(item);
    await logProduct(productId, portionFor(grams, item.suggested_portion_g), meal);
    setQuery("");
    onLogged();
    return t("saved");
  });

  return (
    <Section title={t("meal")}>
      <div className="pills" role="radiogroup" aria-label={t("meal")}>
        {MEAL_TYPES.map((item) => (
          <button key={item} type="button" role="radio" aria-checked={meal === item}
            className={meal === item ? "on" : ""} onClick={() => onMeal(item)}>
            {t(item)}
          </button>
        ))}
      </div>
      <Field label={t("grams")} hint={t("gramsAuto")} inputMode="decimal" value={grams}
        onChange={(event) => onGrams(event.target.value)} />
      <Field label={t("search")} type="search" value={query} autoComplete="off"
        hint={query.trim().length > 0 && query.trim().length < SEARCH_MIN_CHARS ? t("searchHint") : undefined}
        onChange={(event) => setQuery(event.target.value)} />
      <ActionFeedback error={action.error ?? searchError} notice={action.notice} />
      {hits.length > 0 && (
        <ul className="list">
          {hits.map((item) => (
            <li key={`${item.provider}-${item.external_id ?? item.product_id ?? item.label}`}>
              <button className="item" type="button" disabled={action.busy} onClick={() => void pick(item)}>
                <span>{item.label}</span>
                <span className="caption">
                  {item.kcal_per_100g ? `${formatNumber(lang, item.kcal_per_100g)} ${t("kcal")}${t("per100g")}` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
