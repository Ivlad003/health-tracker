# Spec: Critical Issues & Risk Mitigation

## Status: Active
## Version: 1.0.0
## Last Updated: 2026-01-28

---

## 🔴 КРИТИЧНІ ПРОБЛЕМИ / CRITICAL ISSUES

### 1. Безпека OAuth токенів / OAuth Token Security

**Проблема / Problem:**
```sql
-- Токени зберігаються як plain text!
whoop_access_token TEXT,
whoop_refresh_token TEXT,
```
Якщо БД скомпрометована — всі WHOOP акаунти користувачів під загрозою.

**Рішення / Solution:**
- Шифрування at-rest використовуючи `pgcrypto` або application-level AES-256-GCM
- Окрема таблиця `user_credentials` з обмеженим доступом
- Використовувати HashiCorp Vault або AWS Secrets Manager для production

```sql
-- Приклад з pgcrypto
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE user_credentials (
    user_id UUID PRIMARY KEY REFERENCES users(id),
    whoop_access_token_encrypted BYTEA,
    whoop_refresh_token_encrypted BYTEA,
    encryption_key_id VARCHAR(50) -- для key rotation
);

-- Шифрування
INSERT INTO user_credentials (user_id, whoop_access_token_encrypted)
VALUES (
    'user-uuid',
    pgp_sym_encrypt('token-value', 'encryption-key')
);
```

**Пріоритет:** 🔴 P0 — Обов'язково до production

---

### 2. WHOOP API — Вимоги та обмеження / Requirements & Limitations

**Проблема / Problem:**
- Потрібен WHOOP пристрій та активна підписка для Developer Program
- Немає realtime heart rate через API (тільки aggregated data)
- Rate limits: 100 requests/minute
- Concurrent token refresh може зламати сесію

**Офіційні обмеження:**
> "Continuous heart rate data is not available via the WHOOP API"
> "We require all developers on the Developer Platform to have a WHOOP device"

**Рішення / Solution:**
```typescript
// Token refresh з mutex для уникнення race conditions
class WhoopTokenManager {
  private refreshMutex = new Mutex();
  
  async getValidToken(userId: string): Promise<string> {
    const release = await this.refreshMutex.acquire();
    try {
      const user = await db.users.findById(userId);
      if (this.isTokenExpired(user.whoop_token_expires_at)) {
        return await this.refreshToken(user);
      }
      return user.whoop_access_token;
    } finally {
      release();
    }
  }
}
```

**План Б (якщо немає WHOOP):**
- Ручне введення тренувань
- Інтеграція з Apple Health / Google Fit (простіший доступ)
- CSV імпорт з WHOOP exports

**Пріоритет:** 🔴 P0 — Перевірити доступ до API перед розробкою

---

### 3. FatSecret API — Локалізація українських продуктів / Ukrainian Food Localization

**Проблема / Problem:**
- FatSecret має погану базу українських продуктів
- Немає офіційної підтримки `region=UA`
- Традиційні страви (борщ, вареники, сирники) можуть бути відсутні або неточні

**Офіційна інформація:**
> "56 countries supported" — але Україна не в списку Premier регіонів
> "Free tier is limited to US dataset"

**Тестові запити які потрібно виконати:**
```javascript
// Перевірити до початку розробки
const testQueries = [
  'борщ',           // borscht
  'вареники',       // varenyky
  'сирники',        // syrnyky
  'голубці',        // holubtsi
  'сало',           // salo
  'гречка',         // buckwheat
  'каша вівсяна',   // oatmeal
];
```

**Рішення / Solution:**
1. Локальна база українських продуктів (fallback)
2. Можливість користувачу додавати власні продукти
3. Використовувати USDA FoodData Central як альтернативу (безкоштовно)

```sql
-- Таблиця для кастомних продуктів
CREATE TABLE custom_foods (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id),
    name_uk VARCHAR(255) NOT NULL,
    name_en VARCHAR(255),
    calories_per_100g DECIMAL(10, 2) NOT NULL,
    protein_per_100g DECIMAL(10, 2),
    fat_per_100g DECIMAL(10, 2),
    carbs_per_100g DECIMAL(10, 2),
    is_public BOOLEAN DEFAULT false,
    verified BOOLEAN DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

**Пріоритет:** 🔴 P0 — Тестувати API до вибору рішення

---

### 4. n8n не підходить для Production API / n8n Not Suitable for Production API

**Проблема / Problem:**
```
Telegram Web App → n8n → Database
```
- n8n не призначений для high-load API
- Немає вбудованого rate limiting, authentication middleware
- Складно масштабувати горизонтально
- Cold start delays

**Рішення / Solution:**
Додати легкий API layer між Web App і n8n:

```
Telegram Web App → Fastify/Hono API → n8n (background jobs) → Database
                         ↓
                    Direct DB access (for reads)
```

**Рекомендований стек:**
- **Fastify** або **Hono** — lightweight, fast API framework
- **n8n** — тільки для background jobs (sync, notifications)
- **BullMQ** — queue для async tasks

**Пріоритет:** 🟠 P1 — До запуску MVP

---

### 5. Calorie Balance — Неповний розрахунок / Incomplete Calculation

**Проблема / Problem:**
```sql
-- View враховує тільки WHOOP calories
total_calories_out = whoop_activities.calories
```

**Не враховано:**
- **BMR (Basal Metabolic Rate)** — калорії спалені в спокої (~1500-2000/день)
- **NEAT** — Non-Exercise Activity Thermogenesis
- **TEF** — Термічний ефект їжі (~10% від з'їденого)

**Результат:** Користувач бачить `IN: 2000, OUT: 300` і думає що переїдає.

**Рішення / Solution:**
```typescript
interface DailyCalorieBalance {
  caloriesIn: number;
  caloriesOut: {
    bmr: number;           // Mifflin-St Jeor formula
    neat: number;          // estimated from steps/activity
    tef: number;           // 10% of caloriesIn
    exercise: number;      // from WHOOP
    total: number;
  };
  netBalance: number;
}

function calculateBMR(user: User): number {
  // Mifflin-St Jeor Equation
  if (user.sex === 'male') {
    return 10 * user.weight + 6.25 * user.height - 5 * user.age + 5;
  } else {
    return 10 * user.weight + 6.25 * user.height - 5 * user.age - 161;
  }
}
```

**Пріоритет:** 🟠 P1 — Критично для UX

---

### 6. Voice Flow — Відсутня обробка помилок / Missing Error Handling

**Проблема / Problem:**
```
Voice → Whisper → GPT → FatSecret → DB
```

| Крок | Можлива помилка |
|------|-----------------|
| Whisper | Не розпізнав мову, шум, акцент |
| GPT | Неправильно витягнув продукти, галюцинації |
| FatSecret | Не знайшов продукт, rate limit |
| DB | Дублікати, constraint violations |

**Рішення / Solution:**
```typescript
interface VoiceFoodLogResult {
  status: 'success' | 'partial' | 'failed';
  transcription?: string;
  parsedItems: ParsedFoodItem[];
  savedItems: FoodEntry[];
  errors: VoiceFlowError[];
  suggestions?: string[]; // "Ви мали на увазі...?"
}

async function processVoiceFoodLog(
  audioFile: Buffer,
  userId: string
): Promise<VoiceFoodLogResult> {
  const result: VoiceFoodLogResult = {
    status: 'success',
    parsedItems: [],
    savedItems: [],
    errors: [],
  };

  // Step 1: Transcription with retry
  try {
    result.transcription = await withRetry(
      () => whisperTranscribe(audioFile),
      { maxAttempts: 3, backoff: 'exponential' }
    );
  } catch (e) {
    result.errors.push({ step: 'transcription', error: e.message });
    result.status = 'failed';
    return result;
  }

  // Step 2: Parse with GPT (allow partial results)
  try {
    result.parsedItems = await gptParseFoodItems(result.transcription);
  } catch (e) {
    result.errors.push({ step: 'parsing', error: e.message });
    result.status = 'partial';
  }

  // Step 3: Lookup each item (continue on individual failures)
  for (const item of result.parsedItems) {
    try {
      const food = await fatSecretLookup(item);
      if (food) {
        const saved = await saveFoodEntry(userId, food, item);
        result.savedItems.push(saved);
      } else {
        result.errors.push({ 
          step: 'lookup', 
          item: item.name,
          error: 'Not found',
          suggestions: await getSimilarFoods(item.name)
        });
        result.status = 'partial';
      }
    } catch (e) {
      result.errors.push({ step: 'save', item: item.name, error: e.message });
      result.status = 'partial';
    }
  }

  return result;
}
```

**Пріоритет:** 🟠 P1 — До запуску MVP

---

### 7. Telegram Mini App — WebView обмеження / WebView Limitations

**Проблема / Problem:**
- WebView має обмежену пам'ять та API support
- Telegram контролює lifecycle (може закрити app без попередження)
- Local storage не надійний
- iOS keyboard handling баги
- `requestFullScreen` не працює
- Query parameters обрізаються в links

**Офіційна документація:**
> "Browser assumptions common in web development—persistent cookies, stable refresh behavior, predictable storage—do not hold reliably"

**Рішення / Solution:**
```typescript
// 1. Не покладатися на localStorage для критичних даних
// Використовувати Telegram CloudStorage API
const saveData = async (key: string, value: string) => {
  if (window.Telegram?.WebApp?.CloudStorage) {
    await window.Telegram.WebApp.CloudStorage.setItem(key, value);
  }
  // Fallback to server
  await api.saveUserData(key, value);
};

// 2. Обробка viewport змін
window.Telegram?.WebApp?.onEvent('viewportChanged', (event) => {
  if (event.isStateStable) {
    // Тільки тут оновлювати UI
    updateLayout();
  }
});

// 3. Оптимізація для low-end devices
const shouldReduceAnimations = () => {
  const memory = (navigator as any).deviceMemory;
  return memory && memory < 4;
};

// 4. Graceful degradation для keyboard issues на iOS
const handleInputFocus = (e: FocusEvent) => {
  if (isIOS()) {
    setTimeout(() => {
      (e.target as HTMLElement).scrollIntoView({ 
        behavior: 'smooth', 
        block: 'center' 
      });
    }, 300);
  }
};
```

**Пріоритет:** 🟠 P1 — Тестувати на реальних пристроях

---

## 🟡 АРХІТЕКТУРНІ РЕКОМЕНДАЦІЇ / ARCHITECTURAL RECOMMENDATIONS

### Timezone Handling

```typescript
// Завжди зберігати в UTC, конвертувати на клієнті
const logFood = async (entry: FoodEntry) => {
  entry.logged_at = new Date().toISOString(); // UTC
  entry.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  await api.saveFoodEntry(entry);
};

// Daily summaries базувати на user timezone
const getDailySummary = async (userId: string, date: string) => {
  const user = await getUser(userId);
  const startOfDay = zonedTimeToUtc(
    startOfDay(parseISO(date)), 
    user.timezone
  );
  const endOfDay = zonedTimeToUtc(
    endOfDay(parseISO(date)), 
    user.timezone
  );
  return db.query(`
    SELECT * FROM food_entries 
    WHERE user_id = $1 
    AND logged_at >= $2 
    AND logged_at < $3
  `, [userId, startOfDay, endOfDay]);
};
```

### Database Partitioning

```sql
-- Партиціонування для food_entries (буде рости швидко)
CREATE TABLE food_entries (
    -- ... columns
) PARTITION BY RANGE (logged_at);

CREATE TABLE food_entries_2026_q1 
    PARTITION OF food_entries 
    FOR VALUES FROM ('2026-01-01') TO ('2026-04-01');
```

### Soft Delete для GDPR

```sql
ALTER TABLE users ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE food_entries ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE;

-- Index для швидкого фільтрування
CREATE INDEX idx_users_not_deleted ON users(id) WHERE deleted_at IS NULL;
```

---

## 📦 ГОТОВІ РІШЕННЯ / EXISTING SOLUTIONS

### WHOOP API Libraries

| Library | Language | Stars | Status | Notes |
|---------|----------|-------|--------|-------|
| [whoopy](https://pypi.org/project/whoopy/) | Python | - | Active | Official OAuth 2.0, async support, Pandas integration |
| [hedgertronic/whoop](https://github.com/hedgertronic/whoop) | Python | 50+ | Active | Simple client, good for scripts |
| [kryoseu/whoops](https://github.com/kryoseu/whoops) | Python/Flask | 10+ | Active | Export to PostgreSQL/MySQL, Docker ready |
| [whoop-mcp](https://github.com/topics/whoop) | TypeScript | - | New | MCP server for Claude integration |

**Рекомендація:** Використовувати `whoopy` для Python або написати власний клієнт для n8n/Node.js.

### FatSecret API Libraries

| Library | Language | Stars | Status | Notes |
|---------|----------|-------|--------|-------|
| [pyfatsecret](https://pypi.org/project/fatsecret/) | Python | 50+ | Maintained | OAuth 1.0, all endpoints |
| [fatsecret (npm)](https://github.com/OverFlow636/fatsecret) | Node.js | 20+ | Maintained | Promise-based |
| [fatsecret4j](https://github.com/fatsecret/fatsecret4j) | Java | 30+ | Official | Android support |

**Рекомендація:** Для n8n використовувати HTTP Request node з OAuth 2.0.

### Telegram Mini App Templates

| Template | Stack | Notes |
|----------|-------|-------|
| [reactjs-template](https://github.com/Telegram-Mini-Apps/reactjs-template) | React + Vite | Official, recommended |
| [nextjs-template](https://github.com/Telegram-Mini-Apps/nextjs-template) | Next.js | SSR support |
| [@telegram-apps/sdk-react](https://www.npmjs.com/package/@telegram-apps/sdk-react) | React | Pre-built hooks |

---

## 📋 ACTION PLAN

| Пріоритет | Задача | Зусилля | Блокує |
|-----------|--------|---------|--------|
| 🔴 P0 | Перевірити WHOOP API доступ (потрібен пристрій) | 1 день | Все |
| 🔴 P0 | Тест FatSecret з українськими продуктами | 2 години | Food logging |
| 🔴 P0 | Імплементувати шифрування токенів | 1 день | Production |
| 🟠 P1 | Додати BMR до calorie balance | 4 години | UX |
| 🟠 P1 | Error handling у voice flow | 1 день | Voice feature |
| 🟠 P1 | API layer (Fastify) замість прямого n8n | 2-3 дні | Scale |
| 🟠 P1 | Telegram Mini App тестування на devices | 2 дні | Launch |
| 🟡 P2 | Локальна база українських продуктів | 3-5 днів | UA users |
| 🟡 P2 | Fallback для non-WHOOP users | 2 дні | User acquisition |
| 🟡 P2 | Database partitioning | 1 день | Long-term |

---

## 🔗 References

### WHOOP
- [WHOOP Developer Platform](https://developer.whoop.com/)
- [WHOOP OAuth 2.0 Guide](https://developer.whoop.com/docs/developing/oauth/)
- [WHOOP API Changelog](https://developer.whoop.com/docs/api-changelog/)

### FatSecret
- [FatSecret Platform API](https://platform.fatsecret.com/)
- [FatSecret API Editions & Pricing](https://platform.fatsecret.com/api-editions)
- [FatSecret Localization](https://platform.fatsecret.com/docs/guides/localization)

### Telegram Mini Apps
- [Telegram Mini Apps Docs](https://core.telegram.org/bots/webapps)
- [Community Documentation](https://docs.telegram-mini-apps.com/)
- [Known Issues](https://github.com/Telegram-Mini-Apps/issues)

### Alternative APIs
- [USDA FoodData Central](https://fdc.nal.usda.gov/api-guide.html) — Free, public domain
- [Open Food Facts](https://world.openfoodfacts.org/data) — Open source food database
