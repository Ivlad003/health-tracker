import { useEffect, useMemo, useState } from "react";
import { api, idempotencyKey } from "../api";
import { formatNumber, t, type Lang } from "../i18n";
import type { DayView, Draft, FoodEntry, Me, Product, SearchItem } from "../types";

type Meal = "breakfast" | "lunch" | "dinner" | "snack";

function mealNow(timezone: string): Meal {
  const hour = Number(new Intl.DateTimeFormat("en-GB", {
    timeZone: timezone, hour: "2-digit", hourCycle: "h23",
  }).format(new Date()));
  if (hour < 11) return "breakfast";
  if (hour < 16) return "lunch";
  if (hour < 21) return "dinner";
  return "snack";
}

export function FoodPage({ me }: { me: Me }) {
  const lang: Lang = me.language === "en" ? "en" : "uk";
  const [day, setDay] = useState<DayView | null>(null);
  const [products, setProducts] = useState<Product[]>([]);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchItem[]>([]);
  const [grams, setGrams] = useState("100");
  const [meal, setMeal] = useState<Meal>(mealNow(me.timezone));
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [caption, setCaption] = useState("");
  const [name, setName] = useState("");
  const [kcal, setKcal] = useState("");
  const [alias, setAlias] = useState("");
  const [phrase, setPhrase] = useState("");
  const [preview, setPreview] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const meals = useMemo(() => ["breakfast", "lunch", "dinner", "snack"] as const, []);

  async function reload() {
    const [food, mine, open] = await Promise.all([
      api<DayView>("/api/v1/webapp/food-entries"),
      api<{ items: Product[] }>("/api/v1/webapp/products?limit=30"),
      api<{ items: Draft[] }>("/api/v1/webapp/food-drafts"),
    ]);
    setDay(food);
    setProducts(mine.items);
    setDrafts(open.items);
  }

  useEffect(() => {
    void reload().catch((err: unknown) => setError(err instanceof Error ? err.message : "error"));
  }, []);

  useEffect(() => {
    const text = query.trim();
    if (text.length < 2) {
      setHits([]);
      return;
    }
    const timer = window.setTimeout(() => {
      void api<{ items: SearchItem[] }>(`/api/v1/webapp/products/search?q=${encodeURIComponent(text)}`)
        .then((found) => setHits(found.items))
        .catch(() => setHits([]));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    } finally {
      setBusy(false);
    }
  }

  async function logProduct(productId: number, portion: string) {
    await api("/api/v1/webapp/food-entries", {
      method: "POST",
      body: JSON.stringify({
        product_id: productId,
        grams: portion,
        meal_type: meal,
        idempotency_key: idempotencyKey(),
      }),
    });
    setNotice(t(lang, "saved"));
  }

  async function pick(item: SearchItem) {
    const portion = item.suggested_portion_g || grams || "100";
    await run(async () => {
      let productId = item.product_id;
      if (!productId) {
        if (item.provider !== "fatsecret" || !item.external_id) throw new Error("not_importable");
        const imported = await api<{ product_id: number }>("/api/v1/webapp/products/import", {
          method: "POST",
          body: JSON.stringify({ provider: "fatsecret", external_id: item.external_id, display_name: item.label }),
        });
        productId = imported.product_id;
      }
      await logProduct(productId, String(portion));
      setQuery("");
    });
  }

  async function remove(entry: FoodEntry) {
    if (entry.id == null || entry.version == null) return;
    await run(async () => {
      await api(`/api/v1/webapp/food-entries/${entry.id}?version=${entry.version}`, { method: "DELETE" });
    });
  }

  async function upload(file: File) {
    await run(async () => {
      const result = await api<{ draft: Draft | null; message: string | null }>(
        `/api/v1/webapp/uploads?idempotency_key=${idempotencyKey()}&caption=${encodeURIComponent(caption)}`,
        { method: "POST", body: await file.arrayBuffer(), headers: { "Content-Type": file.type || "image/jpeg" } },
      );
      if (result.message) setNotice(result.message);
    });
  }

  async function saveDraft(draft: Draft, nextGrams: Record<number, string>) {
    await run(async () => {
      const items = Object.entries(nextGrams)
        .filter(([, value]) => value.trim() !== "")
        .map(([index, value]) => ({ index: Number(index), grams: value }));
      const saved = await api<Draft>(`/api/v1/webapp/food-drafts/${draft.id}`, {
        method: "PATCH",
        body: JSON.stringify({ version: draft.version, items, meal_type: meal }),
      });
      await api(`/api/v1/webapp/food-drafts/${draft.id}/commit`, {
        method: "POST",
        body: JSON.stringify({ version: saved.version }),
      });
    });
  }

  return (
    <>
      <header className="top"><h1>{t(lang, "food")}</h1></header>
      {error && <p className="banner">{error}</p>}
      {notice && <p className="banner ok">{notice}</p>}
      <section className="card">
        <h2>{t(lang, "meal")}</h2>
        <div className="pills">
          {meals.map((item) => (
            <button key={item} type="button" className={meal === item ? "on" : ""} onClick={() => setMeal(item)}>
              {t(lang, item)}
            </button>
          ))}
        </div>
        <label>{t(lang, "grams")}</label>
        <input inputMode="decimal" value={grams} onChange={(event) => setGrams(event.target.value)} />
        <label>{t(lang, "search")}</label>
        <input value={query} onChange={(event) => setQuery(event.target.value)} />
        {hits.length > 0 && (
          <ul className="list">
            {hits.map((item) => (
              <li key={`${item.provider}-${item.external_id ?? item.product_id}`}>
                <button className="item" type="button" disabled={busy} onClick={() => void pick(item)}>
                  <span>{item.label}</span>
                  <span className="caption">{item.kcal_per_100g ? `${item.kcal_per_100g}/100g` : ""}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="card">
        <h2>{t(lang, "myProducts")}</h2>
        {products.length === 0 ? <p className="note">{t(lang, "noProducts")}</p> : (
          <ul className="list">
            {products.map((product) => (
              <li key={product.product_id}>
                <button className="item" type="button" disabled={busy} onClick={() => void run(() => logProduct(product.product_id, String(product.usual_portion_g || grams || "100")))}>
                  <span>{product.label}{product.incomplete ? " · …" : ""}</span>
                  <span>{product.kcal_per_100g ? `${product.kcal_per_100g}` : ""}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="card">
        <h2>{t(lang, "recent")}</h2>
        {!day || day.entries.length === 0 ? <p className="note">{t(lang, "emptyDay")}</p> : (
          <ul className="list">
            {day.entries.map((entry, index) => (
              <li key={`${entry.id ?? "r"}-${index}`}>
                <span>{entry.name}<br /><span className="caption">{entry.grams ? `${entry.grams} g` : t(lang, "remoteOnly")}</span></span>
                <span>
                  <strong>{formatNumber(lang, entry.energy_kcal)}</strong>
                  {entry.id != null && entry.version != null && (
                    <button className="danger" type="button" onClick={() => void remove(entry)}>{t(lang, "delete")}</button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="card">
        <h2>{t(lang, "photo")}</h2>
        <label>{t(lang, "photoCaption")}</label>
        <input value={caption} onChange={(event) => setCaption(event.target.value)} />
        <label>{t(lang, "sendPhoto")}</label>
        <input
          type="file"
          accept="image/jpeg,image/png,image/webp"
          capture="environment"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
            event.target.value = "";
          }}
        />
        <p className="note">{t(lang, "voiceHint")}</p>
      </section>
      {drafts.length > 0 && (
        <section className="card">
          <h2>{t(lang, "drafts")}</h2>
          {drafts.map((draft) => <DraftCard key={draft.id} draft={draft} lang={lang} busy={busy} onSave={saveDraft} onCancel={(id) => void run(async () => { await api(`/api/v1/webapp/food-drafts/${id}/cancel`, { method: "POST" }); })} />)}
        </section>
      )}
      <section className="card">
        <h2>{t(lang, "newProduct")}</h2>
        <label>{t(lang, "name")}</label>
        <input value={name} onChange={(event) => setName(event.target.value)} />
        <label>{t(lang, "kcal100")}</label>
        <input inputMode="decimal" value={kcal} onChange={(event) => setKcal(event.target.value)} />
        <label>{t(lang, "alias")}</label>
        <input value={alias} onChange={(event) => setAlias(event.target.value)} />
        <button
          className="primary"
          type="button"
          disabled={busy || name.trim().length === 0}
          onClick={() => void run(async () => {
            await api("/api/v1/webapp/products", {
              method: "POST",
              body: JSON.stringify({
                name: name.trim(),
                default_alias: alias.trim() || null,
                nutrition: kcal.trim() ? { basis_quantity: 100, basis_unit: "g", grams_per_basis: 100, energy_kcal: kcal } : null,
              }),
            });
            setName("");
            setKcal("");
            setAlias("");
          })}
        >{t(lang, "create")}</button>
      </section>
      <section className="card">
        <h2>{t(lang, "tryPhrase")}</h2>
        <input value={phrase} onChange={(event) => setPhrase(event.target.value)} />
        <button className="secondary" type="button" disabled={busy || phrase.trim().length === 0} onClick={() => void run(async () => {
          const result = await api<unknown>("/api/v1/webapp/default-rules/preview", {
            method: "POST",
            body: JSON.stringify({ text: phrase.trim() }),
          });
          setPreview(JSON.stringify(result));
        })}>{t(lang, "tryPhrase")}</button>
        {preview && <p className="note">{preview}</p>}
      </section>
    </>
  );
}

function DraftCard({
  draft, lang, busy, onSave, onCancel,
}: {
  draft: Draft;
  lang: Lang;
  busy: boolean;
  onSave: (draft: Draft, grams: Record<number, string>) => Promise<void>;
  onCancel: (id: number) => void;
}) {
  const [values, setValues] = useState<Record<number, string>>({});
  return (
    <div>
      <p className="caption">{draft.state}</p>
      {(draft.items || []).map((item, index) => (
        <div key={index}>
          <label>{item.selected?.label || item.text || `#${index + 1}`}</label>
          <input
            inputMode="decimal"
            placeholder={t(lang, "grams")}
            value={values[index] ?? (item.grams == null ? "" : String(item.grams))}
            onChange={(event) => setValues({ ...values, [index]: event.target.value })}
          />
        </div>
      ))}
      <div className="actions">
        <button className="primary" type="button" disabled={busy} onClick={() => void onSave(draft, values)}>{t(lang, "commit")}</button>
        <button className="danger" type="button" disabled={busy} onClick={() => onCancel(draft.id)}>{t(lang, "cancel")}</button>
      </div>
    </div>
  );
}
