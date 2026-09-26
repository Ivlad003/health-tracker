import { useEffect, useState } from "react";
import { api } from "../api";
import { t, type Lang } from "../i18n";

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

export function AdminPage({ lang }: { lang: Lang }) {
  const [flags, setFlags] = useState<Flag[]>([]);
  const [jobs, setJobs] = useState<Jobs | null>(null);
  const [error, setError] = useState("");

  function load() {
    return Promise.all([
      api<{ items: Flag[] }>("/api/v1/admin/features"),
      api<Jobs>("/api/v1/admin/jobs"),
    ]).then(([featureList, jobList]) => {
      setFlags(featureList.items);
      setJobs(jobList);
    });
  }

  useEffect(() => {
    void load().catch((err: unknown) => setError(err instanceof Error ? err.message : "error"));
  }, []);

  async function toggle(flag: Flag) {
    setError("");
    try {
      await api(`/api/v1/admin/features/${flag.key}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: !flag.enabled, version: flag.version }),
      });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    }
  }

  async function retry(id: number) {
    await api(`/api/v1/admin/jobs/outbox/${id}/retry`, { method: "POST" });
    await load();
  }

  return (
    <>
      <header className="top"><h1>{t(lang, "admin")}</h1></header>
      {error && <p className="banner">{error}</p>}
      <section className="card">
        <h2>{t(lang, "features")}</h2>
        <ul className="list">
          {flags.map((flag) => (
            <li key={flag.key}>
              <span>{flag.key}<br /><span className="caption">{flag.description}{flag.available ? "" : ` · ${flag.unavailable_reason}`}</span></span>
              <button className={flag.enabled ? "primary" : "secondary"} type="button" onClick={() => void toggle(flag)}>
                {flag.effective ? "on" : "off"}
              </button>
            </li>
          ))}
        </ul>
      </section>
      <section className="card">
        <h2>{t(lang, "jobs")}</h2>
        {jobs && (
          <>
            <p className="caption">{jobs.outbox.map((row) => `${row.operation}/${row.status}: ${row.n}`).join(" · ") || "—"}</p>
            <ul className="list">
              {jobs.outbox_failures.map((row) => (
                <li key={row.id}>
                  <span>#{row.id} {row.operation}<br /><span className="caption">{row.status} · {row.last_error}</span></span>
                  <button className="secondary" type="button" onClick={() => void retry(row.id)}>{t(lang, "retryJob")}</button>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
    </>
  );
}
