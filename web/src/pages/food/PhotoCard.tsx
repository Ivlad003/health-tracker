import { useId, useState } from "react";
import { ActionFeedback, Field, Section } from "../../components/ui";
import { useAction } from "../../hooks/useAction";
import { useT } from "../../LangContext";
import { uploadPhoto } from "./actions";

export function PhotoCard({ onUploaded }: { onUploaded: () => void }) {
  const { t } = useT();
  const [caption, setCaption] = useState("");
  const action = useAction();
  const fileId = useId();

  const upload = (file: File) => action.run(async () => {
    const result = await uploadPhoto(file, caption);
    setCaption("");
    onUploaded();
    return result.message ?? t("saved");
  });

  return (
    <Section title={t("photo")}>
      <Field label={t("photoCaption")} value={caption} maxLength={300} onChange={(event) => setCaption(event.target.value)} />
      <label htmlFor={fileId}>{t("sendPhoto")}</label>
      <input
        id={fileId}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        capture="environment"
        disabled={action.busy}
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void upload(file);
        }}
      />
      {action.busy && <p className="note" role="status">{t("uploading")}</p>}
      <ActionFeedback error={action.error} notice={action.notice} />
      <p className="note">{t("voiceHint")}</p>
    </Section>
  );
}
