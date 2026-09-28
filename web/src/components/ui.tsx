import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import { formatNumber, mealLabel } from "../i18n";
import { useT } from "../LangContext";
import type { FoodEntry } from "../types";

export function Section({ title, children, className = "" }: { title?: ReactNode; children: ReactNode; className?: string }) {
  const id = useId();
  return (
    <section className={`card ${className}`.trim()} aria-labelledby={title ? id : undefined}>
      {title && <h2 id={id}>{title}</h2>}
      {children}
    </section>
  );
}

export function PageHeader({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <header className="top">
      <h1>{title}</h1>
      {action}
    </header>
  );
}

export function Banner({ tone = "warn", children }: { tone?: "warn" | "ok" | "error"; children: ReactNode }) {
  return (
    <p className={`banner ${tone}`} role={tone === "ok" ? "status" : "alert"}>
      {children}
    </p>
  );
}

/** Error + success notice of a mutation. */
export function ActionFeedback({ error, notice }: { error: unknown; notice?: string }) {
  const { err } = useT();
  return (
    <>
      {error != null && <Banner tone="error">{err(error)}</Banner>}
      {notice && <Banner tone="ok">{notice}</Banner>}
    </>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { t, err } = useT();
  return (
    <div className="card error-state">
      <Banner tone="error">{err(error)}</Banner>
      <button className="secondary" type="button" onClick={onRetry}>{t("retry")}</button>
    </div>
  );
}

export function Loading({ cards = 2 }: { cards?: number }) {
  const { t } = useT();
  return (
    <div aria-busy="true" aria-live="polite">
      <span className="sr-only">{t("loading")}</span>
      {Array.from({ length: cards }, (_, index) => (
        <div className="card skeleton" key={index} aria-hidden="true">
          <span className="sk-line short" />
          <span className="sk-line" />
          <span className="sk-line" />
        </div>
      ))}
    </div>
  );
}

type FieldProps = InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string };

export function Field({ label, hint, ...input }: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <>
      <label htmlFor={id}>{label}</label>
      <input id={id} aria-describedby={hint ? hintId : undefined} {...input} />
      {hint && <p className="caption" id={hintId}>{hint}</p>}
    </>
  );
}

type SelectFieldProps = SelectHTMLAttributes<HTMLSelectElement> & { label: string; children: ReactNode };

export function SelectField({ label, children, ...select }: SelectFieldProps) {
  const id = useId();
  return (
    <>
      <label htmlFor={id}>{label}</label>
      <select id={id} {...select}>{children}</select>
    </>
  );
}

export function Switch({ label, checked, onChange, disabled }: {
  label: string; checked: boolean; onChange: (value: boolean) => void; disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="switch">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="checkbox"
        role="switch"
        aria-checked={checked}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
    </div>
  );
}

export function EntryList({ entries, onDelete, busy, extra }: {
  entries: FoodEntry[];
  onDelete?: (entry: FoodEntry) => void;
  busy?: boolean;
  extra?: (entry: FoodEntry) => ReactNode;
}) {
  const { t, lang } = useT();
  if (entries.length === 0) return <p className="note">{t("emptyDay")}</p>;
  return (
    <ul className="list">
      {entries.map((entry, index) => {
        const details = [
          mealLabel(lang, entry.meal_type),
          entry.grams ? `${formatNumber(lang, entry.grams)} ${t("unitG")}` : t("remoteOnly"),
        ].filter(Boolean).join(" · ");
        return (
          <li key={entry.id ?? `remote-${index}`}>
            <span>{entry.name || "—"}<br /><span className="caption">{details}</span></span>
            <span className="entry-end">
              <strong>{formatNumber(lang, entry.energy_kcal)} {t("kcal")}</strong>
              {extra?.(entry)}
              {onDelete && entry.id != null && entry.version != null && (
                <button className="danger" type="button" disabled={busy} onClick={() => onDelete(entry)}
                  aria-label={`${t("delete")}: ${entry.name ?? ""}`}>
                  {t("delete")}
                </button>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
