import { ActionFeedback, Section } from "../../components/ui";
import { useAction } from "../../hooks/useAction";
import { formatNumber, type Meal } from "../../i18n";
import { useT } from "../../LangContext";
import { portionFor } from "../../lib/meal";
import type { Product } from "../../types";
import { ApiError, api } from "../../api";
import { confirmAction } from "../../telegram";
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
              <button className="secondary" type="button" disabled={action.busy}
                onClick={() => void action.run(async () => {
                  const body = {
                    alias: product.label,
                    suggested_portion_g: product.usual_portion_g,
                    replace: false,
                  };
                  try {
                    await api(`/api/v1/webapp/products/${product.product_id}/pin`, {
                      method: "POST", body: JSON.stringify(body),
                    });
                  } catch (err) {
                    if (!(err instanceof ApiError) || err.status !== 409) throw err;
                    if (!(await confirmAction(t("pinReplace")))) return;
                    await api(`/api/v1/webapp/products/${product.product_id}/pin`, {
                      method: "POST", body: JSON.stringify({ ...body, replace: true }),
                    });
                  }
                  return t("saved");
                })}>
                {t("pin")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}
