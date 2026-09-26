import { useCallback } from "react";
import { api } from "../api";
import { ActionFeedback, ErrorState, Loading, PageHeader, Section } from "../components/ui";
import { useAction } from "../hooks/useAction";
import { useApi } from "../hooks/useApi";
import { useT } from "../LangContext";

interface Flag {
  key: string;
  description: string;
  enabled: boolean;
  effective: boolean;
  available: boolean;
  unavailable_reason: string | null;
  version: number;
}

interface Jobs {
  outbox: { operation: string; status: string; n: number }[];
  outbox_failures: { id: number; operation: string; status: string; attempts: number; last_error: string | null }[];
  imports: { status: string; n: number }[];
  import_failures: { id: number; status: string; kind: string; last_error: string | null }[];
}

export default function AdminPage() {
  const { t } = useT();
  const load = useCallback((signal: AbortSignal) => Promise.all([
    api<{ items: Flag[] }>("/api/v1/admin/features", { signal }),
    api<Jobs>("/api/v1/admin/jobs", { signal }),
  ]), []);
  const { data, error, reload } = useApi(load);
  const action = useAction();

  const header = <PageHeader title={t("admin")} />;
  if (error != null && !data) return <>{header}<ErrorState error={error} onRetry={reload} /></>;
  if (!data) return <>{header}<Loading /></>;
  const [{ items: flags }, jobs] = data;

  const mutate = (path: string, init: RequestInit) => action.run(async () => {
    await api(path, init);
    reload();
  });

  return (
    <>
      {header}
      <ActionFeedback error={action.error} />
      <Section title={t("features")}>
        <ul className="list">
          {flags.map((flag) => (
            <li key={flag.key}>
              <span>
                {flag.key}<br />
                <span className="caption">{flag.description}{flag.available ? "" : ` · ${flag.unavailable_reason ?? ""}`}</span>
              </span>
              <button
                className={flag.enabled ? "primary compact" : "secondary compact"}
                type="button"
                role="switch"
                aria-checked={flag.effective}
                aria-label={flag.key}
                disabled={action.busy}
                onClick={() => void mutate(`/api/v1/admin/features/${flag.key}`, {
                  method: "PUT",
                  body: JSON.stringify({ enabled: !flag.enabled, version: flag.version }),
                })}
              >
                {flag.effective ? t("on") : t("off")}
              </button>
            </li>
          ))}
        </ul>
      </Section>
      <Section title={t("jobs")}>
        <p className="caption">{jobs.outbox.map((row) => `${row.operation}/${row.status}: ${row.n}`).join(" · ") || "—"}</p>
        <ul className="list">
          {jobs.outbox_failures.map((row) => (
            <li key={row.id}>
              <span>#{row.id} {row.operation}<br /><span className="caption">{row.status} · {row.last_error ?? "—"}</span></span>
              <button className="secondary compact" type="button" disabled={action.busy}
                onClick={() => void mutate(`/api/v1/admin/jobs/outbox/${row.id}/retry`, { method: "POST" })}>
                {t("retryJob")}
              </button>
            </li>
          ))}
        </ul>
      </Section>
      <Section title={t("importJobs")}>
        <p className="caption">{jobs.imports.map((row) => `${row.status}: ${row.n}`).join(" · ") || "—"}</p>
        <ul className="list">
          {jobs.import_failures.map((row) => (
            <li key={row.id}>
              <span>#{row.id} {row.kind}<br /><span className="caption">{row.status} · {row.last_error ?? "—"}</span></span>
              <button className="secondary compact" type="button" disabled={action.busy}
                onClick={() => void mutate(`/api/v1/admin/jobs/imports/${row.id}/retry`, { method: "POST" })}>
                {t("retryJob")}
              </button>
            </li>
          ))}
        </ul>
      </Section>
    </>
  );
}
