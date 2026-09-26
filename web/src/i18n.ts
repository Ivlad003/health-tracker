export type Lang = "uk" | "en";

const UK = {
  dashboard: "Дашборд",
  food: "Їжа",
  activity: "Активність",
  history: "Історія",
  profile: "Профіль",
  admin: "Адмін",
  navigation: "Навігація",
  loading: "Завантаження…",
  retry: "Ще раз",
  save: "Зберегти",
  cancel: "Скасувати",
  delete: "Видалити",
  saved: "Збережено",
  open: "Відкрити",
  gateTitle: "Health Tracker",
  gateBody: "Відкрийте застосунок з Telegram-бота командою /app. Звичайний браузер не передає дані входу Telegram.",
  expiredTitle: "Сесія завершилась",
  expiredBody: "Закрийте застосунок і відкрийте його з бота ще раз (/app).",
  close: "Закрити",
  balance: "Баланс за сьогодні",
  deficit: "Дефіцит",
  onTarget: "У межах цілі",
  surplus: "Профіцит",
  eaten: "З'їдено",
  burned: "Витрачено",
  goal: "Ціль",
  partial: "Сума неповна: частина записів ще звіряється.",
  quick: "Швидкий запис",
  manual: "Вручну",
  photo: "Фото",
  voiceHint: "Голос надсилайте повідомленням боту.",
  recent: "Сьогодні",
  emptyDay: "За цей день ще нічого немає.",
  logFood: "Записати їжу",
  protein: "Б",
  fat: "Ж",
  carbs: "В",
  kcal: "ккал",
  unitG: "г",
  unitH: "год",
  unitKm: "км",
  unitMin: "хв",
  unitKg: "кг",
  per100g: "/100 г",
  strain: "Навантаження",
  recovery: "Відновлення",
  sleep: "Сон",
  workouts: "Тренування",
  steps: "Кроки",
  heart: "Пульс",
  distance: "Дистанція",
  exercise: "Вправи",
  weight: "Вага",
  bmr: "Базовий обмін",
  source: "Джерело витрати",
  whoop: "WHOOP",
  fatsecret: "FatSecret",
  apple: "Apple Health",
  connected: "Підключено",
  notConnected: "Не підключено",
  connect: "Підключити",
  disconnect: "Відключити",
  disconnectConfirm: "Відключити FatSecret? Записи, що ще не надіслані, залишаться лише тут.",
  lastSync: "Остання синхронізація",
  appleHint: "Налаштування Apple Health лишається на iPhone: /apple_health_help",
  search: "Пошук продукту",
  searchHint: "Введіть щонайменше 2 символи.",
  myProducts: "Мої продукти",
  grams: "Грами",
  gramsAuto: "Порожньо — звична порція продукту",
  meal: "Прийом їжі",
  breakfast: "Сніданок",
  lunch: "Обід",
  dinner: "Вечеря",
  snack: "Перекус",
  drafts: "Чернетки",
  commit: "Підтвердити",
  newProduct: "Новий продукт",
  name: "Назва",
  kcal100: "ккал на 100 г",
  create: "Створити",
  importHistory: "Імпортувати історію FatSecret",
  importStarted: "Імпорт запущено",
  language: "Мова",
  timezone: "Часовий пояс",
  height: "Зріст, см",
  birthYear: "Рік народження",
  sex: "Стать",
  male: "Чоловіча",
  female: "Жіноча",
  unspecified: "Не вказано",
  personal: "Особисті дані",
  journal: "Нагадування журналу",
  journalTime1: "Перше нагадування",
  journalTime2: "Друге нагадування",
  calories: "Калорії на день",
  goalRange: "Від 500 до 10 000 ккал.",
  recording: "Запис їжі",
  auto: "Автоматично, якщо збіг точний",
  review: "Завжди питати",
  catalogAdd: "Додавати підтверджене в «Мої продукти»",
  exportFs: "Надсилати в FatSecret",
  morning: "Ранкове зведення",
  evening: "Вечірнє зведення",
  features: "Можливості",
  jobs: "Завдання",
  importJobs: "Імпорт історії",
  retryJob: "Повторити",
  on: "увімк.",
  off: "вимк.",
  noProducts: "Продуктів ще немає. Знайдіть або створіть перший.",
  week: "Останні 7 днів",
  today: "Сьогодні",
  yesterday: "Вчора",
  remoteOnly: "Лише у FatSecret",
  photoCaption: "Підпис до фото",
  sendPhoto: "Надіслати фото",
  uploading: "Надсилаю фото…",
  defaults: "Фрази за замовчуванням",
  alias: "Фраза",
  product: "Продукт",
  chooseProduct: "Оберіть продукт",
  pin: "Закріпити",
  tryPhrase: "Перевірити фразу",
  decisionAuto: "Буде записано автоматично",
  decisionAsk: "Бот запропонує варіанти",
  decisionNone: "Збігів не знайдено",
  outbox: "Черга FatSecret",
  errGeneric: "Щось пішло не так.",
  errNetwork: "Немає з'єднання з сервером.",
  errServer: "Сервер тимчасово недоступний. Спробуйте пізніше.",
  errSession: "Сесія завершилась. Відкрийте застосунок з бота ще раз.",
  errConflict: "Дані змінилися в іншому місці. Оновіть і спробуйте ще раз.",
  errNotFound: "Не знайдено.",
  errValidation: "Перевірте введені значення.",
  errImageTooLarge: "Фото завелике.",
  errImageType: "Непідтримуваний формат фото (JPEG, PNG, WebP).",
  errFatsecretNotConnected: "Спершу підключіть FatSecret.",
  errProvider: "FatSecret зараз недоступний.",
  errForbidden: "Недостатньо прав.",
  errNotImportable: "Цей продукт не можна додати.",
  errRange: "Задовгий період.",
  errFeatureUnavailable: "Ця можливість недоступна на сервері.",
} as const;

export type Key = keyof typeof UK;

const EN: Record<Key, string> = {
  dashboard: "Dashboard",
  food: "Food",
  activity: "Activity",
  history: "History",
  profile: "Profile",
  admin: "Admin",
  navigation: "Navigation",
  loading: "Loading…",
  retry: "Retry",
  save: "Save",
  cancel: "Cancel",
  delete: "Delete",
  saved: "Saved",
  open: "Open",
  gateTitle: "Health Tracker",
  gateBody: "Open this app from the Telegram bot with /app. A normal browser does not send a Telegram login.",
  expiredTitle: "Session ended",
  expiredBody: "Close the app and open it again from the bot (/app).",
  close: "Close",
  balance: "Today's balance",
  deficit: "Deficit",
  onTarget: "On target",
  surplus: "Surplus",
  eaten: "Eaten",
  burned: "Burned",
  goal: "Goal",
  partial: "The total is partial: some entries are still being reconciled.",
  quick: "Quick log",
  manual: "Manual",
  photo: "Photo",
  voiceHint: "Send voice as a message to the bot.",
  recent: "Today",
  emptyDay: "Nothing logged for this day yet.",
  logFood: "Log food",
  protein: "P",
  fat: "F",
  carbs: "C",
  kcal: "kcal",
  unitG: "g",
  unitH: "h",
  unitKm: "km",
  unitMin: "min",
  unitKg: "kg",
  per100g: "/100 g",
  strain: "Strain",
  recovery: "Recovery",
  sleep: "Sleep",
  workouts: "Workouts",
  steps: "Steps",
  heart: "Heart rate",
  distance: "Distance",
  exercise: "Exercise",
  weight: "Weight",
  bmr: "Basal burn",
  source: "Burn source",
  whoop: "WHOOP",
  fatsecret: "FatSecret",
  apple: "Apple Health",
  connected: "Connected",
  notConnected: "Not connected",
  connect: "Connect",
  disconnect: "Disconnect",
  disconnectConfirm: "Disconnect FatSecret? Entries not sent yet will stay here only.",
  lastSync: "Last sync",
  appleHint: "Apple Health setup stays on the iPhone: /apple_health_help",
  search: "Search a product",
  searchHint: "Type at least 2 characters.",
  myProducts: "My products",
  grams: "Grams",
  gramsAuto: "Empty — the product's usual portion",
  meal: "Meal",
  breakfast: "Breakfast",
  lunch: "Lunch",
  dinner: "Dinner",
  snack: "Snack",
  drafts: "Drafts",
  commit: "Confirm",
  newProduct: "New product",
  name: "Name",
  kcal100: "kcal per 100 g",
  create: "Create",
  importHistory: "Import FatSecret history",
  importStarted: "Import started",
  language: "Language",
  timezone: "Timezone",
  height: "Height, cm",
  birthYear: "Birth year",
  sex: "Sex",
  male: "Male",
  female: "Female",
  unspecified: "Not set",
  personal: "Personal details",
  journal: "Journal reminders",
  journalTime1: "First reminder",
  journalTime2: "Second reminder",
  calories: "Daily calories",
  goalRange: "From 500 to 10,000 kcal.",
  recording: "Food recording",
  auto: "Auto-save an exact match",
  review: "Always ask",
  catalogAdd: "Add confirmed foods to My Products",
  exportFs: "Send entries to FatSecret",
  morning: "Morning briefing",
  evening: "Evening summary",
  features: "Features",
  jobs: "Jobs",
  importJobs: "History import",
  retryJob: "Retry",
  on: "on",
  off: "off",
  noProducts: "No products yet. Search or create the first one.",
  week: "Last 7 days",
  today: "Today",
  yesterday: "Yesterday",
  remoteOnly: "FatSecret only",
  photoCaption: "Photo caption",
  sendPhoto: "Send photo",
  uploading: "Uploading photo…",
  defaults: "Default phrases",
  alias: "Phrase",
  product: "Product",
  chooseProduct: "Choose a product",
  pin: "Pin",
  tryPhrase: "Try phrase",
  decisionAuto: "Will be logged automatically",
  decisionAsk: "The bot will offer choices",
  decisionNone: "No match found",
  outbox: "FatSecret queue",
  errGeneric: "Something went wrong.",
  errNetwork: "Cannot reach the server.",
  errServer: "The server is temporarily unavailable. Try again later.",
  errSession: "Your session ended. Open the app from the bot again.",
  errConflict: "This was changed elsewhere. Refresh and try again.",
  errNotFound: "Not found.",
  errValidation: "Check the values you entered.",
  errImageTooLarge: "The photo is too large.",
  errImageType: "Unsupported photo format (JPEG, PNG, WebP).",
  errFatsecretNotConnected: "Connect FatSecret first.",
  errProvider: "FatSecret is unavailable right now.",
  errForbidden: "You do not have access.",
  errNotImportable: "This product cannot be added.",
  errRange: "The period is too long.",
  errFeatureUnavailable: "This feature is not available on the server.",
};

export const DICTIONARIES: Record<Lang, Record<Key, string>> = { uk: UK, en: EN };

export function t(lang: Lang, key: Key): string {
  return DICTIONARIES[lang][key];
}

export function asLang(value: string | null | undefined): Lang {
  return value === "en" ? "en" : "uk";
}

export function locale(lang: Lang): string {
  return lang === "uk" ? "uk-UA" : "en-US";
}

export function formatNumber(lang: Lang, value: number | string | null | undefined, digits = 0): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "—";
  return n.toLocaleString(locale(lang), { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

const MEALS = ["breakfast", "lunch", "dinner", "snack"] as const;
export type Meal = (typeof MEALS)[number];
export const MEAL_TYPES: readonly Meal[] = MEALS;

export function mealLabel(lang: Lang, meal: string | null | undefined): string {
  return (MEALS as readonly string[]).includes(meal ?? "") ? t(lang, meal as Meal) : "";
}

/** "Today" / "Yesterday" / "Mon, 28 Jan" for an ISO local date. */
export function formatDay(lang: Lang, iso: string, todayIso: string): string {
  if (iso === todayIso) return t(lang, "today");
  if (iso === shiftDay(todayIso, -1)) return t(lang, "yesterday");
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year ?? 1970, (month ?? 1) - 1, day ?? 1));
  return new Intl.DateTimeFormat(locale(lang), {
    weekday: "short", day: "numeric", month: "short", timeZone: "UTC",
  }).format(date);
}

export function formatDateTime(lang: Lang, iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(locale(lang), { dateStyle: "medium", timeStyle: "short" });
}

export function shiftDay(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(year ?? 1970, (month ?? 1) - 1, day ?? 1));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function todayIn(timezone: string, now: Date = new Date()): string {
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
    }).format(now);
  } catch {
    return now.toISOString().slice(0, 10);
  }
}
