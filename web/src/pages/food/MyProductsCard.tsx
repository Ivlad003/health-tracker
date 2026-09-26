import { ActionFeedback, Section } from "../../components/ui";
import { useAction } from "../../hooks/useAction";
import { formatNumber, type Meal } from "../../i18n";
import { useT } from "../../LangContext";
import { portionFor } from "../../lib/meal";
import type { Product } from "../../types";
import { logProduct } from "./actions";

export function MyProductsCard({ products, meal, grams, onLogged }: {
  products: Product[]; meal: Meal; grams: string; onLogged: () => void;
}) {
  const { t, lang } = useT();
  const action = useAction();
  const log = (product: Product) => action.run(async () => {
    await logProduct(product.product_id, portionFor(grams, product.usual_portion_g), meal);
    onLogged();
    return t("saved");
  });

  return (
    <Section title={t("myProducts")}>
      <ActionFeedback error={action.error} notice={action.notice} />
      {products.length === 0 ? <p className="note">{t("noProducts")}</p> : (
        <ul className="list">
          {products.map((product) => (
            <li key={product.product_id}>
              <button className="item" type="button" disabled={action.busy} onClick={() => void log(product)}>
                <span>{product.label}{product.incomplete ? " · …" : ""}</span>
                <span className="caption">
                  {product.kcal_per_100g ? `${formatNumber(lang, product.kcal_per_100g)} ${t("kcal")}${t("per100g")}` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
