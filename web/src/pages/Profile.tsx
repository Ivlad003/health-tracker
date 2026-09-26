import { PageHeader, Section } from "../components/ui";
import { useT } from "../LangContext";
import { telegramApp } from "../telegram";
import type { Me } from "../types";
import { DefaultRulesCard } from "./profile/DefaultRulesCard";
import { GoalCard } from "./profile/GoalCard";
import { IntegrationsCard } from "./profile/IntegrationsCard";
import { PersonalCard } from "./profile/PersonalCard";
import { PreferencesCard } from "./profile/PreferencesCard";

/** Each card loads and saves its own resource, so one failure never leaves
 * the others half-saved. */
export function ProfilePage({ me, onMe }: { me: Me; onMe: (me: Me) => void }) {
  const { t } = useT();
  // Display only: identity for the API always comes from the server session.
  const user = telegramApp()?.initDataUnsafe.user;
  const title = user?.username ? `@${user.username}` : user?.first_name ?? `#${me.telegram_user_id}`;

  return (
    <>
      <PageHeader title={t("profile")} />
      <Section title={title}>
        <p className="caption">Telegram ID {me.telegram_user_id}</p>
      </Section>
      <GoalCard />
      <PersonalCard me={me} onMe={onMe} />
      <PreferencesCard />
      <IntegrationsCard />
      <DefaultRulesCard />
    </>
  );
}
