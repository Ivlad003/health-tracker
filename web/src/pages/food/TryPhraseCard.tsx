import { useState } from "react";
import { api } from "../../api";
import { ActionFeedback, Field, Section } from "../../components/ui";
import { useAction } from "../../hooks/useAction";
import type { Key } from "../../i18n";
import { useT } from "../../LangContext";
import type { PreviewResult } from "../../types";

function decisionKey(result: PreviewResult): Key {
  if (result.decision === "auto") return "decisionAuto";
  return result.candidates.length > 0 ? "decisionAsk" : "decisionNone";
}

export function TryPhraseCard() {
  const { t } = useT();
  const [phrase, setPhrase] = useState("");
  const [result, setResult] = useState<PreviewResult | null>(null);
  const action = useAction();

  const check = () => action.run(async () => {
    setResult(await api<PreviewResult>("/api/v1/webapp/default-rules/preview", {
      method: "POST",
      body: JSON.stringify({ text: phrase.trim() }),
    }));
  });

  return (
    <Section title={t("tryPhrase")}>
      <Field label={t("alias")} value={phrase} maxLength={255} onChange={(event) => setPhrase(event.target.value)} />
      <button className="secondary" type="button" disabled={action.busy || phrase.trim().length === 0} onClick={() => void check()}>
        {t("tryPhrase")}
      </button>
      <ActionFeedback error={action.error} />
      {result && (
        <div role="status">
          <p><strong>{t(decisionKey(result))}</strong></p>
          {result.candidates.length > 0 && (
            <ul className="list">
              {result.candidates.slice(0, 5).map((candidate, index) => (
                <li key={`${candidate.product_id ?? "c"}-${index}`}>
                  <span>{candidate.label ?? "—"}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Section>
  );
}
