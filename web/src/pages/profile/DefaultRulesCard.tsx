import { useCallback, useState } from "react";
import { api } from "../../api";
import { ActionFeedback, ErrorState, Field, Loading, Section, SelectField } from "../../components/ui";
import { useAction } from "../../hooks/useAction";
import { useApi } from "../../hooks/useApi";
import { useT } from "../../LangContext";
import type { DefaultRule, Product } from "../../types";

export const RULE_PRODUCTS_LIMIT = 200;

export function DefaultRulesCard() {
  const { t } = useT();
  const load = useCallback((signal: AbortSignal) => Promise.all([
    api<{ items: DefaultRule[] }>("/api/v1/webapp/default-rules", { signal }),
    api<{ items: Product[] }>(`/api/v1/webapp/products?limit=${RULE_PRODUCTS_LIMIT}`, { signal }),
  ]), []);
  const { data, error, reload } = useApi(load);
  const [alias, setAlias] = useState("");
  const [productId, setProductId] = useState("");
  const action = useAction();

  if (error != null && !data) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <Loading cards={1} />;
  const [{ items: rules }, { items: products }] = data;

  const remove = (rule: DefaultRule) => action.run(async () => {
    await api(`/api/v1/webapp/default-rules/${rule.id}?version=${rule.version}`, { method: "DELETE" });
    reload();
  });
  const pin = () => action.run(async () => {
    await api("/api/v1/webapp/default-rules", {
      method: "POST",
      body: JSON.stringify({ alias: alias.trim(), product_id: Number(productId) }),
    });
    setAlias("");
    setProductId("");
    reload();
    return t("saved");
  });

  return (
    <Section title={t("defaults")}>
      <ul className="list">
        {rules.map((rule) => (
          <li key={rule.id}>
            <span>{rule.alias_display}<br /><span className="caption">{rule.product_label}</span></span>
            <button className="danger" type="button" disabled={action.busy}
              aria-label={`${t("delete")}: ${rule.alias_display}`} onClick={() => void remove(rule)}>
              {t("delete")}
            </button>
          </li>
        ))}
      </ul>
      <Field label={t("alias")} value={alias} maxLength={255} onChange={(event) => setAlias(event.target.value)} />
      <SelectField label={t("product")} value={productId} onChange={(event) => setProductId(event.target.value)}>
        <option value="">{t("chooseProduct")}</option>
        {products.map((product) => <option key={product.product_id} value={product.product_id}>{product.label}</option>)}
      </SelectField>
      <ActionFeedback error={action.error} notice={action.notice} />
      <button className="secondary" type="button" disabled={action.busy || !alias.trim() || !productId}
        onClick={() => void pin()}>
        {t("pin")}
      </button>
    </Section>
  );
}
