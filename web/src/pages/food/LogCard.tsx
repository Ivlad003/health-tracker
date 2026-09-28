import { useEffect, useState } from "react";
import { api } from "../../api";
import { ActionFeedback, Field, Section } from "../../components/ui";
import { isAbort } from "../../errors";
import { useAction } from "../../hooks/useAction";
import { MEAL_TYPES, formatNumber, type Meal } from "../../i18n";
import { useT } from "../../LangContext";
import type { SearchItem } from "../../types";
import { ensureProduct, logCustom, logProduct } from "./actions";

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
  const [noMatch, setNoMatch] = useState(false);
  const [kcal, setKcal] = useState("");
  const [searchError, setSearchError] = useState<unknown>(null);
  const action = useAction();
  const gramsReady = grams.trim() !== "" && Number(grams.replace(",", ".")) > 0;

  useEffect(() => {
    const text = query.trim();
    if (!gramsReady || text.length < SEARCH_MIN_CHARS) {
      setHits([]);
      setNoMatch(false);
      setSearchError(null);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      api<{ decision: string; candidates: SearchItem[] }>("/api/v1/webapp/food/match", {
        method: "POST",
        body: JSON.stringify({ text, grams: grams.replace(",", ".") }),
        signal: controller.signal,
      })
        .then((found) => {
          setHits(found.candidates);
          setNoMatch(found.decision === "none");
          setSearchError(null);
        })
        .catch((err: unknown) => { if (!isAbort(err)) setSearchError(err); });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, grams, gramsReady]);

  const pick = (item: SearchItem) => action.run(async () => {
    const productId = await ensureProduct(item);
    await logProduct(productId, grams.replace(",", "."), meal);
    setQuery("");
    setHits([]);
    setNoMatch(false);
    onLogged();
    return t("saved");
  });

  const create = () => action.run(async () => {
    await logCustom(query.trim(), kcal.replace(",", "."), grams.replace(",", "."), meal);
    setQuery("");
    setKcal("");
    setHits([]);
    setNoMatch(false);
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
        hint={!gramsReady ? t("gramsRequired")
          : query.trim().length > 0 && query.trim().length < SEARCH_MIN_CHARS ? t("searchHint") : undefined}
        onChange={(event) => setQuery(event.target.value)} />
      <ActionFeedback error={action.error ?? searchError} notice={action.notice} />
      {hits.length > 0 && (
        <ul className="list">
          {hits.map((item) => (
            <li key={`${item.provider}-${item.external_id ?? item.product_id ?? item.label}`}>
              <button className="item" type="button" disabled={action.busy} onClick={() => void pick(item)}>
                <span>{item.label}</span>
                <span className="caption">
                  {item.portion_kcal != null
                    ? `${formatNumber(lang, item.portion_kcal)} ${t("kcal")}`
                    : `? ${t("kcal")}`}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {noMatch && (
        <>
          <p className="note">{t("noMatch")}</p>
          <Field label={t("kcal100")} inputMode="decimal" value={kcal} onChange={(event) => setKcal(event.target.value)} />
          <button className="primary" type="button" disabled={action.busy || kcal.trim() === ""}
            onClick={() => void create()}>
            {t("createAndLog")}
          </button>
        </>
      )}
    </Section>
  );
}
