# AI Health Bot — Design Document

[Ukrainian version](../uk/ai-bot-design.md)

> Brainstorming result (2026-02-24). Defines the conversational AI Telegram bot built on top of the existing Health Tracker infrastructure.

---

## 1. Overview

A freeform, bilingual (UK/EN) Telegram bot powered by OpenAI GPT that acts as a personal health assistant. Users talk naturally, GPT understands intent, and n8n orchestrates actions behind the scenes.

**This is a pure Telegram chat bot** — no Web App frontend. All interaction happens via text and voice messages.

---

## 2. Design Decisions

| # | Aspect | Decision |
|---|--------|----------|
| 1 | Interface | Pure Telegram bot, freeform natural language |
| 2 | AI | OpenAI — Whisper (STT) + GPT (single-call intent + response) |
| 3 | Language | Bilingual UK/EN, GPT auto-detects and translates for FatSecret |
| 4 | Food logging | Fully automated, FatSecret first, GPT fallback, echo with totals |
| 5 | Conversation | Full day context stored in DB |
| 6 | Proactive | Morning briefing (8:00) + Evening summary (21:00) |
| 7 | Calorie math | Simple intake - expenditure |
| 8 | Architecture | 5 separate n8n workflows (3 new + 2 existing) |

---

## 3. Bot Personality & Interaction Examples

The bot responds conversationally, echoing actions it takes:

> **User:** "I ate chicken breast with rice for lunch, about 300 grams total"
> **Bot:** "Logged: grilled chicken 200g (330 kcal, 62g protein) + white rice 100g (130 kcal, 27g carbs). Today: 1,240 / 2,200 kcal"

> **User:** "And also a coffee with milk"
> **Bot:** "Logged: coffee with milk (50 kcal). Today: 1,290 / 2,200 kcal"

> **User:** "How did I sleep?"
> **Bot:** "Last night: 7h 12m, sleep score 82. Deep sleep 1h 45m (good). Your recovery is 71% — moderate day, don't push too hard."

Ukrainian example:

> **User:** "Я з'їв курячу грудку з рисом на обід, грам 300 загалом"
> **Bot:** "Записав: курка гриль 200г (330 kcal, 62g білка) + рис білий 100г (130 kcal, 27g вуглеводів). Сьогодні: 1,240 / 2,200 kcal"

---

## 4. n8n Workflow Architecture (5 workflows)

### 4.1 Telegram Message Handler (NEW)

The main brain of the bot. Handles all incoming messages.

```
Telegram Trigger (webhook)
  ├── Voice? -> Whisper STT -> text
  └── Text? -> use directly
         │
         v
Load conversation context (last day from DB)
         │
         v
GPT Single Call (system prompt + context + message)
  -> Returns JSON: { intent, food_items[], response }
         │
         ├── intent: log_food
         │     -> FatSecret search per item
         │     -> GPT fallback if not found
         │     -> INSERT into food_entries
         │     -> Reply with totals
         │
         ├── intent: query_data
         │     -> SELECT from DB (sleep/food/whoop)
         │     -> Reply with analysis
         │
         ├── intent: delete_entry
         │     -> DELETE last food_entry
         │     -> Reply confirmation
         │
         └── intent: general
               -> Reply with GPT response

Save message + response to conversation_messages
```

### 4.2 Morning Briefing (NEW — scheduled 8:00 Kyiv)

```
Schedule Trigger -> For each user:
  -> Query last night's WHOOP sleep + recovery
  -> Query yesterday's calorie balance
  -> GPT: generate morning tip from data
  -> Send Telegram message
```

**Content:**
- Last night's sleep score + duration (WHOOP)
- Recovery score (WHOOP)
- Yesterday's calorie balance
- One tip for the day

### 4.3 Evening Summary (NEW — scheduled 21:00 Kyiv)

```
Schedule Trigger -> For each user:
  -> Query today's food_entries (meals + totals)
  -> Query today's WHOOP workouts + calories burned
  -> Calculate surplus/deficit
  -> GPT: generate evening reflection + tip
  -> Send Telegram message
```

**Content:**
- Today's total calories in
- Today's total calories burned (WHOOP)
- Calorie surplus/deficit
- Meals logged today
- One reflection/tip

### 4.4 WHOOP Data Sync (EXISTING — hourly)

Workflow ID: `nAjGDfKdddSDH2MD` — already active and working.

### 4.5 FatSecret Food Search (EXISTING — webhook)

Workflow ID: `qTHRcgiqFx9SqTNm` — already active at `/webhook/food/search?q=`.

---

## 5. New DB Table: `conversation_messages`

```sql
CREATE TABLE conversation_messages (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    role VARCHAR(20) NOT NULL,        -- 'user' or 'assistant'
    content TEXT NOT NULL,
    intent VARCHAR(50),               -- classified intent
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_conv_user_date
    ON conversation_messages(user_id, created_at);
```

- Full day's context loaded per GPT call
- Older messages auto-cleaned (keep 7 days)

---

## 6. GPT System Prompt (single-call design)

```
You are a health assistant bot. Analyze the user's message and:

1. Classify intent: log_food | query_data | delete_entry | general
2. If log_food: extract food items with quantities
3. Generate a friendly response in the user's language

Respond in JSON:
{
  "intent": "log_food",
  "food_items": [
    {"name_en": "chicken breast", "name_original": "куряча грудка", "quantity_g": 200},
    {"name_en": "white rice", "name_original": "рис", "quantity_g": 100}
  ],
  "response": "Записав: курка 200г + рис 100г. Шукаю калорії..."
}

Context: user's conversation history and health data are provided.
```

---

## 7. Key Technical Notes

### Bilingual handling
- GPT auto-detects user language from message
- Food items translated to English for FatSecret API search
- Response sent in user's language
- Whisper auto-detects language (can hint `uk` or `en` from Telegram settings)

### Food lookup pipeline
1. GPT extracts food items with English names
2. FatSecret search for each item
3. If FatSecret returns no results -> GPT estimates calories from own knowledge
4. All entries logged to `food_entries` table

### Conversation context
- All messages stored in `conversation_messages` table
- Each GPT call loads the full day's conversation
- Enables multi-message context: "I ate chicken" -> "And rice with it" -> bot understands both

### n8n constraints (v2.35.5)
- Use exact `typeVersion` values from `session-knowledge.md`
- Code node v1 syntax only
- Cannot `require('crypto')` in Code nodes

---

## 8. Open Items / Risks

1. **FatSecret OAuth1 still blocked** — food diary sync won't work until IP whitelist propagates. Food search (OAuth2) works fine for now.
2. **OpenAI costs** — full day context per message could be ~$1-3/day for a personal bot.
3. **n8n conversation state** — n8n isn't designed for stateful conversations. DB-backed approach works but adds latency per message.
4. **Whisper language detection** — auto-detect works but hinting `uk` or `en` improves accuracy.
5. **SQL injection risk** — ensure parameterized queries in n8n Store nodes (no string interpolation for user content).
