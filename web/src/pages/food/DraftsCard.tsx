import { useState } from "react";
import { ActionFeedback, Field, Section } from "../../components/ui";
import { useAction } from "../../hooks/useAction";
import type { Meal } from "../../i18n";
import { useT } from "../../LangContext";
import type { Draft } from "../../types";
import { cancelDraft, commitDraft } from "./actions";

export function DraftsCard({ drafts, meal, onChanged }: { drafts: Draft[]; meal: Meal; onChanged: () => void }) {
  const { t } = useT();
  if (drafts.length === 0) return null;
  return (
    <Section title={t("drafts")}>
      {drafts.map((draft) => <DraftItem key={draft.id} draft={draft} meal={meal} onChanged={onChanged} />)}
    </Section>
  );
}

function DraftItem({ draft, meal, onChanged }: { draft: Draft; meal: Meal; onChanged: () => void }) {
  const { t } = useT();
  const [values, setValues] = useState<Record<number, string>>({});
  const action = useAction();

  return (
    <div className="draft">
      {(draft.items ?? []).map((item, index) => (
        <Field
          // Draft items have no id; their position is their identity on the server.
          key={`${draft.id}-${index}`}
          label={item.selected?.label || item.text || `#${index + 1}`}
          inputMode="decimal"
          placeholder={t("grams")}
          value={values[index] ?? (item.grams == null ? "" : String(item.grams))}
          onChange={(event) => setValues((current) => ({ ...current, [index]: event.target.value }))}
        />
      ))}
      <ActionFeedback error={action.error} />
      <div className="actions">
        <button className="primary" type="button" disabled={action.busy}
          onClick={() => void action.run(async () => { await commitDraft(draft, values, meal); onChanged(); })}>
          {t("commit")}
        </button>
        <button className="danger" type="button" disabled={action.busy}
          onClick={() => void action.run(async () => { await cancelDraft(draft.id); onChanged(); })}>
          {t("cancel")}
        </button>
      </div>
    </div>
  );
}
