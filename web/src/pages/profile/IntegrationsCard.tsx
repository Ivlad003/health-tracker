import { useCallback } from "react";
import { api } from "../../api";
import { ActionFeedback, ErrorState, Loading, Section } from "../../components/ui";
import { useAction } from "../../hooks/useAction";
import { useApi } from "../../hooks/useApi";
import { formatDateTime } from "../../i18n";
import { useT } from "../../LangContext";
import { confirmAction } from "../../telegram";
import type { Integrations } from "../../types";
import { openConnectLink } from "../Activity";

export function IntegrationsCard() {
  const { t, lang } = useT();
  const load = useCallback((signal: AbortSignal) => api<Integrations>("/api/v1/webapp/integrations", { signal }), []);
  const { data: links, error, reload } = useApi(load);
  const action = useAction();

  if (error != null && !links) return <ErrorState error={error} onRetry={reload} />;
  if (!links) return <Loading cards={1} />;
  const outbox = Object.entries(links.fatsecret.outbox);

  const disconnect = async () => {
    if (!(await confirmAction(t("disconnectConfirm")))) return;
    await action.run(async () => {
      await api("/api/v1/webapp/integrations/fatsecret/disconnect", { method: "POST" });
      reload();
      return t("saved");
    });
  };

  return (
    <>
      <Section title={t("fatsecret")}>
        <p>{links.fatsecret.connected ? t("connected") : t("notConnected")}</p>
        {links.fatsecret.last_successful_sync && (
          <p className="caption">{t("lastSync")}: {formatDateTime(lang, links.fatsecret.last_successful_sync)}</p>
        )}
        {outbox.length > 0 && (
          <p className="caption">{t("outbox")}: {outbox.map(([status, count]) => `${status} ${count}`).join(", ")}</p>
        )}
        <ActionFeedback error={action.error} notice={action.notice} />
        <div className="actions">
          {!links.fatsecret.connected && (
            <button className="secondary" type="button" disabled={action.busy}
              onClick={() => void action.run(() => openConnectLink("fatsecret"))}>
              {t("connect")}
            </button>
          )}
          {links.fatsecret.connected && (
            <>
              <button className="secondary" type="button" disabled={action.busy}
                onClick={() => void action.run(async () => {
                  await api("/api/v1/webapp/catalog-imports", {
                    method: "POST", body: JSON.stringify({ days: 180 }),
                  });
                  return t("importStarted");
                })}>
                {t("importHistory")}
              </button>
              <button className="danger" type="button" disabled={action.busy} onClick={() => void disconnect()}>
                {t("disconnect")}
              </button>
            </>
          )}
        </div>
      </Section>
      <Section title={t("whoop")}>
        <p>{links.whoop.connected ? t("connected") : t("notConnected")}</p>
        {!links.whoop.connected && (
          <button className="secondary" type="button" disabled={action.busy}
            onClick={() => void action.run(() => openConnectLink("whoop"))}>
            {t("connect")}
          </button>
        )}
        <p className="note">{t("appleHint")}</p>
      </Section>
    </>
  );
}
