import { useEffect, useState } from "react";
import { api, telegramApp } from "../api";
import { t, type Lang } from "../i18n";
import type { DefaultRule, Integrations, Me, Preferences } from "../types";

export function ProfilePage({ me, onMe }: { me: Me; onMe: (me: Me) => void }) {
  const lang: Lang = me.language === "en" ? "en" : "uk";
  const [form, setForm] = useState(me);
  const [prefs, setPrefs] = useState<Preferences | null>(null);
  const [prefVersion, setPrefVersion] = useState(0);
  const [goal, setGoal] = useState(String(me.daily_calorie_goal ?? ""));
  const [links, setLinks] = useState<Integrations | null>(null);
  const [rules, setRules] = useState<DefaultRule[]>([]);
  const [alias, setAlias] = useState("");
  const [productId, setProductId] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api<{ preferences: Preferences; version: number }>("/api/v1/webapp/preferences"),
      api<Integrations>("/api/v1/webapp/integrations"),
      api<{ items: DefaultRule[] }>("/api/v1/webapp/default-rules"),
    ]).then(([preferences, integrations, defaults]) => {
      setPrefs(preferences.preferences);
      setPrefVersion(preferences.version);
      setLinks(integrations);
      setRules(defaults.items);
    }).catch((err: unknown) => setError(err instanceof Error ? err.message : "error"));
  }, []);

  async function saveProfile() {
    setError("");
    setNotice("");
    try {
      const updated = await api<Me>("/api/v1/webapp/me", {
        method: "PATCH",
        body: JSON.stringify({
          profile_version: form.profile_version,
          language: form.language,
          timezone: form.timezone,
          birth_year: form.birth_year,
          sex: form.sex,
          height_cm: form.height_cm,
          journal_enabled: form.journal_enabled,
          journal_time_1: form.journal_time_1,
          journal_time_2: form.journal_time_2,
        }),
      });
      setForm(updated);
      onMe(updated);
      if (goal.trim()) {
        await api("/api/v1/webapp/goals", {
          method: "PUT",
          body: JSON.stringify({ calories: Number(goal) }),
        });
      }
      if (prefs) {
        const saved = await api<{ preferences: Preferences; version: number }>("/api/v1/webapp/preferences", {
          method: "PUT",
          body: JSON.stringify({ version: prefVersion, changes: prefs }),
        });
        setPrefs(saved.preferences);
        setPrefVersion(saved.version);
      }
      setNotice(t(lang, "saved"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "error");
    }
  }

  async function connect(provider: "whoop" | "fatsecret") {
    const result = await api<{ url: string }>(`/api/v1/webapp/integrations/${provider}/connect-link`, { method: "POST" });
    const tg = telegramApp();
    if (tg) tg.openLink(result.url);
    else window.location.href = result.url;
  }

  const user = telegramApp()?.initDataUnsafe.user;

  return (
    <>
      <header className="top"><h1>{t(lang, "profile")}</h1></header>
      {error && <p className="banner">{error}</p>}
      {notice && <p className="banner ok">{notice}</p>}
      <section className="card">
        <h2>{user?.username ? `@${user.username}` : user?.first_name || "Telegram"}</h2>
        <p className="caption">id {me.telegram_user_id}</p>
      </section>
      <section className="card">
        <h2>{t(lang, "goal")}</h2>
        <label>{t(lang, "calories")}</label>
        <input inputMode="numeric" value={goal} onChange={(event) => setGoal(event.target.value)} />
        <label>{t(lang, "language")}</label>
        <select value={form.language} onChange={(event) => setForm({ ...form, language: event.target.value as Lang })}>
          <option value="uk">Українська</option>
          <option value="en">English</option>
        </select>
        <label>{t(lang, "timezone")}</label>
        <input value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })} />
        <label>{t(lang, "height")}</label>
        <input inputMode="decimal" value={form.height_cm ?? ""} onChange={(event) => setForm({ ...form, height_cm: event.target.value ? Number(event.target.value) : null })} />
        <label>{t(lang, "birthYear")}</label>
        <input inputMode="numeric" value={form.birth_year ?? ""} onChange={(event) => setForm({ ...form, birth_year: event.target.value ? Number(event.target.value) : null })} />
        <label>{t(lang, "sex")}</label>
        <select value={form.sex ?? ""} onChange={(event) => setForm({ ...form, sex: (event.target.value || null) as Me["sex"] })}>
          <option value="">{t(lang, "unspecified")}</option>
          <option value="female">{t(lang, "female")}</option>
          <option value="male">{t(lang, "male")}</option>
        </select>
        <div className="switch">
          <span>{t(lang, "journal")}</span>
          <input type="checkbox" checked={form.journal_enabled} onChange={(event) => setForm({ ...form, journal_enabled: event.target.checked })} />
        </div>
        <label>1</label>
        <input value={form.journal_time_1 ?? ""} placeholder="21:00" onChange={(event) => setForm({ ...form, journal_time_1: event.target.value || null })} />
        <label>2</label>
        <input value={form.journal_time_2 ?? ""} placeholder="08:00" onChange={(event) => setForm({ ...form, journal_time_2: event.target.value || null })} />
      </section>
      {prefs && (
        <section className="card">
          <h2>{t(lang, "recording")}</h2>
          <select value={prefs.recording_policy} onChange={(event) => setPrefs({ ...prefs, recording_policy: event.target.value as Preferences["recording_policy"] })}>
            <option value="auto_confirmed">{t(lang, "auto")}</option>
            <option value="review_all">{t(lang, "review")}</option>
          </select>
          <div className="switch"><span>{t(lang, "catalogAdd")}</span><input type="checkbox" checked={prefs.catalog_auto_add} onChange={(event) => setPrefs({ ...prefs, catalog_auto_add: event.target.checked })} /></div>
          <div className="switch"><span>{t(lang, "exportFs")}</span><input type="checkbox" checked={prefs.fatsecret_export} onChange={(event) => setPrefs({ ...prefs, fatsecret_export: event.target.checked })} /></div>
          <div className="switch"><span>{t(lang, "morning")}</span><input type="checkbox" checked={prefs.briefing_morning_enabled} onChange={(event) => setPrefs({ ...prefs, briefing_morning_enabled: event.target.checked })} /></div>
          <input value={prefs.briefing_morning_time} onChange={(event) => setPrefs({ ...prefs, briefing_morning_time: event.target.value })} />
          <div className="switch"><span>{t(lang, "evening")}</span><input type="checkbox" checked={prefs.briefing_evening_enabled} onChange={(event) => setPrefs({ ...prefs, briefing_evening_enabled: event.target.checked })} /></div>
          <input value={prefs.briefing_evening_time} onChange={(event) => setPrefs({ ...prefs, briefing_evening_time: event.target.value })} />
        </section>
      )}
      <button className="primary" type="button" onClick={() => void saveProfile()}>{t(lang, "save")}</button>
      <section className="card">
        <h2>{t(lang, "fatsecret")}</h2>
        <p>{links?.fatsecret.connected ? t(lang, "connected") : t(lang, "notConnected")}</p>
        {links && Object.keys(links.fatsecret.outbox).length > 0 && (
          <p className="caption">{t(lang, "outbox")}: {Object.entries(links.fatsecret.outbox).map(([key, count]) => `${key} ${count}`).join(", ")}</p>
        )}
        <div className="actions">
          {!links?.fatsecret.connected && <button className="secondary" type="button" onClick={() => void connect("fatsecret")}>{t(lang, "connect")}</button>}
          {links?.fatsecret.connected && (
            <>
              <button className="secondary" type="button" onClick={() => void api("/api/v1/webapp/catalog-imports", { method: "POST", body: "{}" }).then(() => setNotice(t(lang, "saved"))).catch((err: unknown) => setError(err instanceof Error ? err.message : "error"))}>{t(lang, "importHistory")}</button>
              <button className="danger" type="button" onClick={() => void api("/api/v1/webapp/integrations/fatsecret/disconnect", { method: "POST" }).then(() => setNotice(t(lang, "saved")))}>{t(lang, "disconnect")}</button>
            </>
          )}
        </div>
      </section>
      <section className="card">
        <h2>{t(lang, "whoop")}</h2>
        <p>{links?.whoop.connected ? t(lang, "connected") : t(lang, "notConnected")}</p>
        {!links?.whoop.connected && <button className="secondary" type="button" onClick={() => void connect("whoop")}>{t(lang, "connect")}</button>}
        <p className="note">{t(lang, "appleHint")}</p>
      </section>
      <section className="card">
        <h2>{t(lang, "defaults")}</h2>
        <ul className="list">
          {rules.map((rule) => (
            <li key={rule.id}>
              <span>{rule.alias_display}<br /><span className="caption">{rule.product_label}</span></span>
              <button className="danger" type="button" onClick={() => void api(`/api/v1/webapp/default-rules/${rule.id}?version=${rule.version}`, { method: "DELETE" }).then(() => setRules(rules.filter((item) => item.id !== rule.id)))}>{t(lang, "delete")}</button>
            </li>
          ))}
        </ul>
        <label>{t(lang, "alias")}</label>
        <input value={alias} onChange={(event) => setAlias(event.target.value)} />
        <label>product id</label>
        <input inputMode="numeric" value={productId} onChange={(event) => setProductId(event.target.value)} />
        <button className="secondary" type="button" disabled={!alias.trim() || !productId.trim()} onClick={() => void api("/api/v1/webapp/default-rules", {
          method: "POST",
          body: JSON.stringify({ alias: alias.trim(), product_id: Number(productId) }),
        }).then(() => api<{ items: DefaultRule[] }>("/api/v1/webapp/default-rules")).then((data) => { setRules(data.items); setAlias(""); setProductId(""); })}>{t(lang, "pin")}</button>
      </section>
    </>
  );
}
