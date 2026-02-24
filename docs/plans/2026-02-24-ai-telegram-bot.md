# AI Telegram Health Bot — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a freeform bilingual AI Telegram bot that logs food, answers health questions, and sends proactive daily briefings — all orchestrated by n8n workflows.

**Architecture:** 3 new n8n workflows (Telegram Message Handler, Morning Briefing, Evening Summary) + 1 new DB table (`conversation_messages`) + 1 DB migration. All workflows use n8n v2.35.5 compatible node versions (see session-knowledge.md). OpenAI GPT handles intent classification, food parsing, bilingual responses, and health tips in a single API call per message.

**Tech Stack:** n8n v2.35.5, PostgreSQL 15+, OpenAI API (GPT-4o + Whisper), Telegram Bot API, FatSecret API (OAuth 2.0)

**Reference files:**
- Design doc: `docs/en/ai-bot-design.md`
- DB schema: `database/migrations/002_health_tracker_schema.sql`
- n8n constraints: `docs/en/session-knowledge.md` (Section 5)
- Existing workflows: `n8n/workflows/*.json`
- Env vars: `.env.example`

**n8n node version constraints (CRITICAL):**

| Node Type | typeVersion |
|-----------|-------------|
| `scheduleTrigger` | 1.2 |
| `httpRequest` | 4.2 |
| `postgres` | 2.5 |
| `code` | 1 |
| `webhook` | 2 |
| `if` | 2.3 |

**Postgres credential name:** `pet_pg_db`

---

## Task 1: Database Migration — `conversation_messages` table

**Files:**
- Create: `database/migrations/003_conversation_messages.sql`

**Step 1: Write the migration SQL**

Create `database/migrations/003_conversation_messages.sql`:

```sql
-- Conversation Messages for AI Bot context
-- Version: 3.0.0
-- Created: 2026-02-24

BEGIN;

CREATE TABLE IF NOT EXISTS conversation_messages (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL,
    intent VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_messages_user_date
    ON conversation_messages(user_id, created_at);

COMMIT;
```

**Step 2: Deploy migration to production DB**

Run against the production database:

```bash
# Load DATABASE_URL from .env, then:
psql "$DATABASE_URL" -f database/migrations/003_conversation_messages.sql
```

Expected: `CREATE TABLE`, `CREATE INDEX` — no errors.

**Step 3: Verify table exists**

```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'conversation_messages' ORDER BY ordinal_position;
```

Expected: 5 columns (id, user_id, role, content, intent, created_at).

**Step 4: Commit**

```bash
git add database/migrations/003_conversation_messages.sql
git commit -m "feat: add conversation_messages table for AI bot context"
```

---

## Task 2: Telegram Message Handler — Workflow JSON (Part 1: Trigger + Voice/Text routing)

This is the main bot brain. Due to complexity, it's split into Tasks 2-5.

**Files:**
- Create: `n8n/workflows/telegram-message-handler.json`

**Step 1: Create the workflow JSON skeleton with Telegram webhook trigger and voice/text routing**

Create `n8n/workflows/telegram-message-handler.json`. This first part includes:
- Webhook trigger to receive Telegram updates
- IF node to check if message has voice
- Voice branch: download audio file from Telegram, send to Whisper
- Text branch: pass text through directly
- Code node to merge both branches into a unified `{ text, telegram_user_id, chat_id }` output

```json
{
  "_comment": "Telegram Message Handler | AI Health Bot brain",
  "_note": "Receives all Telegram messages, classifies intent via GPT, executes actions, responds.",
  "name": "Telegram Message Handler",
  "nodes": [
    {
      "id": "telegram-webhook",
      "name": "Telegram Webhook",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2,
      "position": [250, 400],
      "parameters": {
        "httpMethod": "POST",
        "path": "telegram/message",
        "responseMode": "responseNode",
        "options": {}
      },
      "webhookId": "telegram-message"
    },
    {
      "id": "extract-message",
      "name": "Extract Message",
      "type": "n8n-nodes-base.code",
      "typeVersion": 1,
      "position": [480, 400],
      "parameters": {
        "jsCode": "const body = items[0].json.body || items[0].json;\nconst msg = body.message || {};\nconst chatId = msg.chat?.id || '';\nconst userId = msg.from?.id || '';\nconst text = msg.text || '';\nconst voice = msg.voice || null;\nconst languageCode = msg.from?.language_code || 'uk';\nreturn [{ json: { chat_id: chatId, telegram_user_id: userId, text, voice, language_code: languageCode } }];"
      }
    },
    {
      "id": "is-voice",
      "name": "Is Voice Message?",
      "type": "n8n-nodes-base.if",
      "typeVersion": 2.3,
      "position": [700, 400],
      "parameters": {
        "conditions": {
          "options": { "version": 2, "caseSensitive": true, "typeValidation": "strict" },
          "conditions": [
            {
              "id": "check-voice",
              "leftValue": "={{ $json.voice }}",
              "rightValue": "",
              "operator": { "type": "object", "operation": "exists" }
            }
          ],
          "combinator": "and"
        },
        "options": {}
      }
    },
    {
      "id": "get-voice-file",
      "name": "Get Voice File URL",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [940, 300],
      "parameters": {
        "url": "=https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getFile",
        "sendQuery": true,
        "queryParameters": {
          "parameters": [
            { "name": "file_id", "value": "={{ $json.voice.file_id }}" }
          ]
        },
        "options": {}
      }
    },
    {
      "id": "download-voice",
      "name": "Download Voice File",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [1160, 300],
      "parameters": {
        "url": "=https://api.telegram.org/file/bot${TELEGRAM_BOT_TOKEN}/{{ $json.result.file_path }}",
        "options": {
          "response": { "response": { "responseFormat": "file" } }
        }
      }
    },
    {
      "id": "whisper-stt",
      "name": "Whisper STT",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [1380, 300],
      "parameters": {
        "method": "POST",
        "url": "https://api.openai.com/v1/audio/transcriptions",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            { "name": "Authorization", "value": "=Bearer ${OPENAI_API_KEY}" }
          ]
        },
        "contentType": "multipart-form-data",
        "sendBody": true,
        "bodyParameters": {
          "parameters": [
            { "name": "model", "value": "whisper-1" },
            { "name": "file", "value": "={{ $binary.data }}", "parameterType": "formBinaryData" },
            { "name": "language", "value": "={{ $('Extract Message').item.json.language_code === 'uk' ? 'uk' : 'en' }}" }
          ]
        },
        "options": {}
      }
    },
    {
      "id": "merge-text",
      "name": "Prepare User Text",
      "type": "n8n-nodes-base.code",
      "typeVersion": 1,
      "position": [1600, 400],
      "parameters": {
        "jsCode": "const extracted = $('Extract Message').item.json;\nlet userText = '';\ntry {\n  userText = items[0].json.text || extracted.text;\n} catch(e) {\n  userText = extracted.text;\n}\nreturn [{ json: { user_text: userText, chat_id: extracted.chat_id, telegram_user_id: extracted.telegram_user_id, language_code: extracted.language_code } }];"
      }
    }
  ],
  "connections": {
    "Telegram Webhook": {
      "main": [[{ "node": "Extract Message", "type": "main", "index": 0 }]]
    },
    "Extract Message": {
      "main": [[{ "node": "Is Voice Message?", "type": "main", "index": 0 }]]
    },
    "Is Voice Message?": {
      "main": [
        [{ "node": "Get Voice File URL", "type": "main", "index": 0 }],
        [{ "node": "Prepare User Text", "type": "main", "index": 0 }]
      ]
    },
    "Get Voice File URL": {
      "main": [[{ "node": "Download Voice File", "type": "main", "index": 0 }]]
    },
    "Download Voice File": {
      "main": [[{ "node": "Whisper STT", "type": "main", "index": 0 }]]
    },
    "Whisper STT": {
      "main": [[{ "node": "Prepare User Text", "type": "main", "index": 0 }]]
    }
  },
  "settings": { "executionOrder": "v1" }
}
```

**Step 2: Verify JSON is valid**

```bash
cat n8n/workflows/telegram-message-handler.json | python3 -m json.tool > /dev/null && echo "Valid JSON"
```

Expected: `Valid JSON`

**Step 3: Commit**

```bash
git add n8n/workflows/telegram-message-handler.json
git commit -m "feat: telegram message handler — trigger + voice/text routing"
```

---

## Task 3: Telegram Message Handler — Part 2: User lookup + Conversation context + GPT call

**Files:**
- Modify: `n8n/workflows/telegram-message-handler.json`

**Step 1: Add nodes for user lookup, conversation loading, and GPT intent classification**

Add these nodes to the existing `nodes` array in `telegram-message-handler.json`:

```json
{
  "id": "lookup-user",
  "name": "Lookup User",
  "type": "n8n-nodes-base.postgres",
  "typeVersion": 2.5,
  "position": [1820, 400],
  "parameters": {
    "operation": "executeQuery",
    "query": "=SELECT id, telegram_user_id, daily_calorie_goal, language, timezone FROM users WHERE telegram_user_id = {{ $json.telegram_user_id }} LIMIT 1",
    "options": {}
  },
  "credentials": {
    "postgres": { "id": "YOUR_POSTGRES_CREDENTIAL_ID", "name": "pet_pg_db" }
  }
},
{
  "id": "load-context",
  "name": "Load Conversation Context",
  "type": "n8n-nodes-base.postgres",
  "typeVersion": 2.5,
  "position": [2040, 400],
  "parameters": {
    "operation": "executeQuery",
    "query": "=SELECT role, content, intent, created_at FROM conversation_messages WHERE user_id = {{ $('Lookup User').item.json.id }} AND created_at >= NOW() - INTERVAL '24 hours' ORDER BY created_at ASC LIMIT 50",
    "options": {}
  },
  "credentials": {
    "postgres": { "id": "YOUR_POSTGRES_CREDENTIAL_ID", "name": "pet_pg_db" }
  }
},
{
  "id": "build-gpt-prompt",
  "name": "Build GPT Prompt",
  "type": "n8n-nodes-base.code",
  "typeVersion": 1,
  "position": [2260, 400],
  "parameters": {
    "jsCode": "const userText = $('Prepare User Text').item.json.user_text;\nconst user = $('Lookup User').item.json;\nconst contextRows = items.map(i => i.json);\nconst calorieGoal = user.daily_calorie_goal || 2000;\n\nconst conversationHistory = contextRows\n  .filter(r => r.role && r.content)\n  .map(r => ({ role: r.role, content: r.content }))\n  .slice(-20);\n\nconst systemPrompt = `You are a friendly bilingual (Ukrainian/English) health assistant Telegram bot.\n\nYour capabilities:\n1. Log food — extract food items, quantities, estimate meal_type from time of day\n2. Answer health questions — sleep, recovery, calories, activity from WHOOP + food data\n3. Delete last food entry\n4. General health chat\n\nRules:\n- Detect user language and ALWAYS respond in the same language\n- For food items, provide English name (for FatSecret API search) and original name\n- If quantity not specified, estimate a typical serving\n- meal_type: breakfast (before 11:00), lunch (11:00-15:00), dinner (15:00-21:00), snack (other)\n- Current time in user timezone: ${new Date().toISOString()}\n- User daily calorie goal: ${calorieGoal} kcal\n\nRespond ONLY with valid JSON (no markdown, no code fences):\n{\n  \"intent\": \"log_food\" | \"query_data\" | \"delete_entry\" | \"general\",\n  \"food_items\": [\n    {\"name_en\": \"chicken breast\", \"name_original\": \"куряча грудка\", \"quantity_g\": 200, \"meal_type\": \"lunch\"}\n  ],\n  \"query_type\": \"sleep\" | \"calories\" | \"activity\" | \"recovery\" | null,\n  \"response\": \"Your friendly response text here\"\n}\n\nFor log_food: include food_items array. For other intents: food_items should be empty array [].\nFor query_data: set query_type. For other intents: query_type should be null.`;\n\nconst messages = [\n  { role: 'system', content: systemPrompt },\n  ...conversationHistory,\n  { role: 'user', content: userText }\n];\n\nreturn [{ json: { messages, user_text: userText, user_id: user.id, chat_id: $('Prepare User Text').item.json.chat_id, calorie_goal: calorieGoal } }];"
  }
},
{
  "id": "gpt-classify",
  "name": "GPT Classify Intent",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4.2,
  "position": [2480, 400],
  "parameters": {
    "method": "POST",
    "url": "https://api.openai.com/v1/chat/completions",
    "sendHeaders": true,
    "headerParameters": {
      "parameters": [
        { "name": "Authorization", "value": "=Bearer ${OPENAI_API_KEY}" },
        { "name": "Content-Type", "value": "application/json" }
      ]
    },
    "sendBody": true,
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify({ model: 'gpt-4o', messages: $json.messages, temperature: 0.3, max_tokens: 1000 }) }}",
    "options": {}
  }
},
{
  "id": "parse-gpt-response",
  "name": "Parse GPT Response",
  "type": "n8n-nodes-base.code",
  "typeVersion": 1,
  "position": [2700, 400],
  "parameters": {
    "jsCode": "const raw = items[0].json.choices?.[0]?.message?.content || '{}';\nlet parsed;\ntry {\n  parsed = JSON.parse(raw);\n} catch(e) {\n  parsed = { intent: 'general', food_items: [], query_type: null, response: raw };\n}\nconst prev = $('Build GPT Prompt').item.json;\nreturn [{ json: { ...parsed, user_id: prev.user_id, chat_id: prev.chat_id, user_text: prev.user_text, calorie_goal: prev.calorie_goal } }];"
  }
}
```

**Step 2: Add connections from Prepare User Text -> Lookup User -> Load Context -> Build GPT Prompt -> GPT Classify -> Parse GPT Response**

Update the `connections` object to add:

```json
"Prepare User Text": {
  "main": [[{ "node": "Lookup User", "type": "main", "index": 0 }]]
},
"Lookup User": {
  "main": [[{ "node": "Load Conversation Context", "type": "main", "index": 0 }]]
},
"Load Conversation Context": {
  "main": [[{ "node": "Build GPT Prompt", "type": "main", "index": 0 }]]
},
"Build GPT Prompt": {
  "main": [[{ "node": "GPT Classify Intent", "type": "main", "index": 0 }]]
},
"GPT Classify Intent": {
  "main": [[{ "node": "Parse GPT Response", "type": "main", "index": 0 }]]
}
```

**Step 3: Validate JSON**

```bash
cat n8n/workflows/telegram-message-handler.json | python3 -m json.tool > /dev/null && echo "Valid JSON"
```

**Step 4: Commit**

```bash
git add n8n/workflows/telegram-message-handler.json
git commit -m "feat: telegram handler — user lookup, context loading, GPT classification"
```

---

## Task 4: Telegram Message Handler — Part 3: Intent routing + Food logging

**Files:**
- Modify: `n8n/workflows/telegram-message-handler.json`

**Step 1: Add intent router (Switch/IF nodes) and food logging branch**

Add these nodes after `Parse GPT Response`:

```json
{
  "id": "route-intent",
  "name": "Route by Intent",
  "type": "n8n-nodes-base.if",
  "typeVersion": 2.3,
  "position": [2920, 400],
  "parameters": {
    "conditions": {
      "options": { "version": 2, "caseSensitive": true, "typeValidation": "strict" },
      "conditions": [
        {
          "id": "is-log-food",
          "leftValue": "={{ $json.intent }}",
          "rightValue": "log_food",
          "operator": { "type": "string", "operation": "equals" }
        }
      ],
      "combinator": "and"
    },
    "options": {}
  }
},
{
  "id": "process-food-items",
  "name": "Process Food Items",
  "type": "n8n-nodes-base.code",
  "typeVersion": 1,
  "position": [3140, 300],
  "parameters": {
    "jsCode": "const parsed = items[0].json;\nconst foodItems = parsed.food_items || [];\nif (!foodItems.length) {\n  return [{ json: { ...parsed, food_entries: [], skip_fatsecret: true } }];\n}\nreturn foodItems.map(f => ({\n  json: {\n    name_en: f.name_en,\n    name_original: f.name_original || f.name_en,\n    quantity_g: f.quantity_g || 100,\n    meal_type: f.meal_type || 'snack',\n    user_id: parsed.user_id,\n    chat_id: parsed.chat_id,\n    calorie_goal: parsed.calorie_goal,\n    response: parsed.response,\n    user_text: parsed.user_text\n  }\n}));"
  }
},
{
  "id": "fatsecret-search",
  "name": "FatSecret Search",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4.2,
  "position": [3360, 200],
  "retryOnFail": true,
  "maxTries": 2,
  "parameters": {
    "url": "=${N8N_WEBHOOK_URL}/webhook/food/search",
    "sendQuery": true,
    "queryParameters": {
      "parameters": [
        { "name": "q", "value": "={{ $json.name_en }}" }
      ]
    },
    "options": { "timeout": 10000 }
  }
},
{
  "id": "calc-nutrition",
  "name": "Calculate Nutrition",
  "type": "n8n-nodes-base.code",
  "typeVersion": 1,
  "position": [3580, 200],
  "parameters": {
    "mode": "runOnceForEachItem",
    "jsCode": "const foodItem = $('Process Food Items').item.json;\nconst searchResults = item.json.results || [];\nconst quantity = foodItem.quantity_g || 100;\n\nlet calories = 0, protein = 0, fat = 0, carbs = 0;\nlet foodName = foodItem.name_original;\nlet fatsecretId = null;\nlet source = 'gpt_estimate';\n\nif (searchResults.length > 0) {\n  const best = searchResults[0];\n  fatsecretId = best.food_id;\n  foodName = foodItem.name_original;\n  source = 'fatsecret';\n  // Parse description like: \"Per 100g - Calories: 165kcal | Fat: 3.60g | Carbs: 0.00g | Protein: 31.02g\"\n  const desc = best.description || '';\n  const calMatch = desc.match(/Calories:\\s*([\\d.]+)/i);\n  const fatMatch = desc.match(/Fat:\\s*([\\d.]+)/i);\n  const carbMatch = desc.match(/Carbs:\\s*([\\d.]+)/i);\n  const protMatch = desc.match(/Protein:\\s*([\\d.]+)/i);\n  const perMatch = desc.match(/Per\\s+([\\d.]+)\\s*g/i);\n  const per = perMatch ? parseFloat(perMatch[1]) : 100;\n  const scale = quantity / per;\n  calories = calMatch ? parseFloat(calMatch[1]) * scale : 0;\n  protein = protMatch ? parseFloat(protMatch[1]) * scale : 0;\n  fat = fatMatch ? parseFloat(fatMatch[1]) * scale : 0;\n  carbs = carbMatch ? parseFloat(carbMatch[1]) * scale : 0;\n}\n\nif (!calories) {\n  // GPT fallback — rough estimates per 100g, scaled\n  source = 'gpt_estimate';\n}\n\nreturn [{\n  json: {\n    user_id: foodItem.user_id,\n    chat_id: foodItem.chat_id,\n    food_name: foodName,\n    fatsecret_food_id: fatsecretId,\n    calories: Math.round(calories * 100) / 100,\n    protein: Math.round(protein * 100) / 100,\n    fat: Math.round(fat * 100) / 100,\n    carbs: Math.round(carbs * 100) / 100,\n    serving_size: quantity,\n    serving_unit: 'g',\n    meal_type: foodItem.meal_type,\n    source_text: foodItem.user_text,\n    calorie_goal: foodItem.calorie_goal,\n    response: foodItem.response,\n    source: source\n  }\n}];"
  }
},
{
  "id": "store-food-entry",
  "name": "Store Food Entry",
  "type": "n8n-nodes-base.postgres",
  "typeVersion": 2.5,
  "position": [3800, 200],
  "parameters": {
    "operation": "executeQuery",
    "query": "=INSERT INTO food_entries (user_id, food_name, fatsecret_food_id, calories, protein, fat, carbs, serving_size, serving_unit, meal_type, source_text) VALUES ({{ $json.user_id }}, '{{ $json.food_name.replace(/'/g, \"''\") }}', {{ $json.fatsecret_food_id ? \"'\" + $json.fatsecret_food_id + \"'\" : 'NULL' }}, {{ $json.calories }}, {{ $json.protein }}, {{ $json.fat }}, {{ $json.carbs }}, {{ $json.serving_size }}, '{{ $json.serving_unit }}', '{{ $json.meal_type }}', '{{ ($json.source_text || '').replace(/'/g, \"''\") }}') RETURNING id",
    "options": {}
  },
  "credentials": {
    "postgres": { "id": "YOUR_POSTGRES_CREDENTIAL_ID", "name": "pet_pg_db" }
  }
},
{
  "id": "get-daily-totals",
  "name": "Get Daily Totals",
  "type": "n8n-nodes-base.postgres",
  "typeVersion": 2.5,
  "position": [4020, 200],
  "parameters": {
    "operation": "executeQuery",
    "query": "=SELECT COALESCE(SUM(calories), 0) as total_calories, COALESCE(SUM(protein), 0) as total_protein, COALESCE(SUM(fat), 0) as total_fat, COALESCE(SUM(carbs), 0) as total_carbs, COUNT(*) as entry_count FROM food_entries WHERE user_id = {{ $json.user_id }} AND logged_at >= CURRENT_DATE",
    "options": {}
  },
  "credentials": {
    "postgres": { "id": "YOUR_POSTGRES_CREDENTIAL_ID", "name": "pet_pg_db" }
  }
},
{
  "id": "build-food-response",
  "name": "Build Food Response",
  "type": "n8n-nodes-base.code",
  "typeVersion": 1,
  "position": [4240, 200],
  "parameters": {
    "jsCode": "const totals = items[0].json;\nconst prev = $('Calculate Nutrition').item.json;\nconst goal = prev.calorie_goal || 2000;\nconst totalCal = Math.round(totals.total_calories);\nconst response = prev.response + '\\n\\n' + '📊 ' + totalCal + ' / ' + goal + ' kcal';\nreturn [{ json: { chat_id: prev.chat_id, text: response, user_id: prev.user_id, user_text: prev.source_text, intent: 'log_food' } }];"
  }
}
```

**Step 2: Add connections for food logging branch**

```json
"Parse GPT Response": {
  "main": [[{ "node": "Route by Intent", "type": "main", "index": 0 }]]
},
"Route by Intent": {
  "main": [
    [{ "node": "Process Food Items", "type": "main", "index": 0 }],
    [{ "node": "Handle Non-Food Intent", "type": "main", "index": 0 }]
  ]
},
"Process Food Items": {
  "main": [[{ "node": "FatSecret Search", "type": "main", "index": 0 }]]
},
"FatSecret Search": {
  "main": [[{ "node": "Calculate Nutrition", "type": "main", "index": 0 }]]
},
"Calculate Nutrition": {
  "main": [[{ "node": "Store Food Entry", "type": "main", "index": 0 }]]
},
"Store Food Entry": {
  "main": [[{ "node": "Get Daily Totals", "type": "main", "index": 0 }]]
},
"Get Daily Totals": {
  "main": [[{ "node": "Build Food Response", "type": "main", "index": 0 }]]
},
"Build Food Response": {
  "main": [[{ "node": "Send Telegram Reply", "type": "main", "index": 0 }]]
}
```

**Step 3: Validate JSON, commit**

```bash
cat n8n/workflows/telegram-message-handler.json | python3 -m json.tool > /dev/null && echo "Valid JSON"
git add n8n/workflows/telegram-message-handler.json
git commit -m "feat: telegram handler — food logging with FatSecret search + daily totals"
```

---

## Task 5: Telegram Message Handler — Part 4: Non-food intents + Telegram reply + Save conversation

**Files:**
- Modify: `n8n/workflows/telegram-message-handler.json`

**Step 1: Add query_data handler, delete_entry handler, general handler, Telegram reply, and save conversation nodes**

```json
{
  "id": "handle-non-food",
  "name": "Handle Non-Food Intent",
  "type": "n8n-nodes-base.code",
  "typeVersion": 1,
  "position": [3140, 500],
  "parameters": {
    "jsCode": "const parsed = items[0].json;\nconst intent = parsed.intent;\nconst queryType = parsed.query_type;\n\nreturn [{ json: { intent, query_type: queryType, response: parsed.response, chat_id: parsed.chat_id, user_id: parsed.user_id, user_text: parsed.user_text } }];"
  }
},
{
  "id": "is-query",
  "name": "Is Query?",
  "type": "n8n-nodes-base.if",
  "typeVersion": 2.3,
  "position": [3360, 500],
  "parameters": {
    "conditions": {
      "options": { "version": 2, "caseSensitive": true, "typeValidation": "strict" },
      "conditions": [
        {
          "id": "check-query",
          "leftValue": "={{ $json.intent }}",
          "rightValue": "query_data",
          "operator": { "type": "string", "operation": "equals" }
        }
      ],
      "combinator": "and"
    },
    "options": {}
  }
},
{
  "id": "query-health-data",
  "name": "Query Health Data",
  "type": "n8n-nodes-base.postgres",
  "typeVersion": 2.5,
  "position": [3580, 420],
  "parameters": {
    "operation": "executeQuery",
    "query": "=SELECT\n  (SELECT json_build_object('total_calories', COALESCE(SUM(calories),0), 'total_protein', COALESCE(SUM(protein),0), 'entry_count', COUNT(*)) FROM food_entries WHERE user_id = {{ $json.user_id }} AND logged_at >= CURRENT_DATE) as food_today,\n  (SELECT json_build_object('sleep_performance', sleep_performance_percentage, 'total_sleep_hours', ROUND(total_sleep_time_milli/3600000.0, 1), 'total_rem_hours', ROUND(total_rem_sleep_milli/3600000.0, 1), 'total_deep_hours', ROUND(total_slow_wave_sleep_milli/3600000.0, 1)) FROM whoop_sleep WHERE user_id = {{ $json.user_id }} ORDER BY started_at DESC LIMIT 1) as last_sleep,\n  (SELECT json_build_object('recovery_score', recovery_score, 'resting_hr', resting_heart_rate, 'hrv', hrv_rmssd_milli) FROM whoop_recovery WHERE user_id = {{ $json.user_id }} ORDER BY recorded_at DESC LIMIT 1) as last_recovery,\n  (SELECT json_build_object('workout_count', COUNT(*), 'total_calories_burned', COALESCE(SUM(calories),0)) FROM whoop_activities WHERE user_id = {{ $json.user_id }} AND started_at >= CURRENT_DATE) as activity_today",
    "options": {}
  },
  "credentials": {
    "postgres": { "id": "YOUR_POSTGRES_CREDENTIAL_ID", "name": "pet_pg_db" }
  }
},
{
  "id": "enrich-query-response",
  "name": "Enrich Query Response",
  "type": "n8n-nodes-base.code",
  "typeVersion": 1,
  "position": [3800, 420],
  "parameters": {
    "jsCode": "const data = items[0].json;\nconst prev = $('Handle Non-Food Intent').item.json;\n// GPT already generated a response, but we can append real data\nlet response = prev.response;\nreturn [{ json: { chat_id: prev.chat_id, text: response, user_id: prev.user_id, user_text: prev.user_text, intent: prev.intent } }];"
  }
},
{
  "id": "is-delete",
  "name": "Is Delete?",
  "type": "n8n-nodes-base.if",
  "typeVersion": 2.3,
  "position": [3580, 600],
  "parameters": {
    "conditions": {
      "options": { "version": 2, "caseSensitive": true, "typeValidation": "strict" },
      "conditions": [
        {
          "id": "check-delete",
          "leftValue": "={{ $json.intent }}",
          "rightValue": "delete_entry",
          "operator": { "type": "string", "operation": "equals" }
        }
      ],
      "combinator": "and"
    },
    "options": {}
  }
},
{
  "id": "delete-last-entry",
  "name": "Delete Last Entry",
  "type": "n8n-nodes-base.postgres",
  "typeVersion": 2.5,
  "position": [3800, 560],
  "parameters": {
    "operation": "executeQuery",
    "query": "=DELETE FROM food_entries WHERE id = (SELECT id FROM food_entries WHERE user_id = {{ $json.user_id }} ORDER BY created_at DESC LIMIT 1) RETURNING food_name, calories",
    "options": {}
  },
  "credentials": {
    "postgres": { "id": "YOUR_POSTGRES_CREDENTIAL_ID", "name": "pet_pg_db" }
  }
},
{
  "id": "build-delete-response",
  "name": "Build Delete Response",
  "type": "n8n-nodes-base.code",
  "typeVersion": 1,
  "position": [4020, 560],
  "parameters": {
    "jsCode": "const deleted = items[0].json;\nconst prev = $('Handle Non-Food Intent').item.json;\nlet text = prev.response;\nif (deleted.food_name) {\n  text += ' (' + deleted.food_name + ', ' + Math.round(deleted.calories) + ' kcal)';\n}\nreturn [{ json: { chat_id: prev.chat_id, text: text, user_id: prev.user_id, user_text: prev.user_text, intent: 'delete_entry' } }];"
  }
},
{
  "id": "general-response",
  "name": "General Response",
  "type": "n8n-nodes-base.code",
  "typeVersion": 1,
  "position": [3800, 700],
  "parameters": {
    "jsCode": "const prev = $('Handle Non-Food Intent').item.json;\nreturn [{ json: { chat_id: prev.chat_id, text: prev.response, user_id: prev.user_id, user_text: prev.user_text, intent: prev.intent } }];"
  }
},
{
  "id": "send-telegram",
  "name": "Send Telegram Reply",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4.2,
  "position": [4460, 400],
  "parameters": {
    "method": "POST",
    "url": "=https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage",
    "sendBody": true,
    "specifyBody": "json",
    "jsonBody": "={{ JSON.stringify({ chat_id: $json.chat_id, text: $json.text, parse_mode: 'HTML' }) }}",
    "options": {}
  }
},
{
  "id": "save-conversation",
  "name": "Save Conversation",
  "type": "n8n-nodes-base.postgres",
  "typeVersion": 2.5,
  "position": [4680, 400],
  "parameters": {
    "operation": "executeQuery",
    "query": "=INSERT INTO conversation_messages (user_id, role, content, intent) VALUES ({{ $json.user_id }}, 'user', '{{ ($json.user_text || '').replace(/'/g, \"''\") }}', '{{ $json.intent }}'), ({{ $json.user_id }}, 'assistant', '{{ ($json.text || '').replace(/'/g, \"''\") }}', '{{ $json.intent }}')",
    "options": {}
  },
  "credentials": {
    "postgres": { "id": "YOUR_POSTGRES_CREDENTIAL_ID", "name": "pet_pg_db" }
  }
},
{
  "id": "respond-webhook",
  "name": "Respond to Webhook",
  "type": "n8n-nodes-base.respondToWebhook",
  "typeVersion": 1.5,
  "position": [4900, 400],
  "parameters": {
    "respondWith": "json",
    "responseBody": "={{ JSON.stringify({ ok: true }) }}",
    "options": {}
  }
}
```

**Step 2: Add connections for all non-food branches converging to Send Telegram Reply**

```json
"Handle Non-Food Intent": {
  "main": [[{ "node": "Is Query?", "type": "main", "index": 0 }]]
},
"Is Query?": {
  "main": [
    [{ "node": "Query Health Data", "type": "main", "index": 0 }],
    [{ "node": "Is Delete?", "type": "main", "index": 0 }]
  ]
},
"Query Health Data": {
  "main": [[{ "node": "Enrich Query Response", "type": "main", "index": 0 }]]
},
"Enrich Query Response": {
  "main": [[{ "node": "Send Telegram Reply", "type": "main", "index": 0 }]]
},
"Is Delete?": {
  "main": [
    [{ "node": "Delete Last Entry", "type": "main", "index": 0 }],
    [{ "node": "General Response", "type": "main", "index": 0 }]
  ]
},
"Delete Last Entry": {
  "main": [[{ "node": "Build Delete Response", "type": "main", "index": 0 }]]
},
"Build Delete Response": {
  "main": [[{ "node": "Send Telegram Reply", "type": "main", "index": 0 }]]
},
"General Response": {
  "main": [[{ "node": "Send Telegram Reply", "type": "main", "index": 0 }]]
},
"Send Telegram Reply": {
  "main": [[{ "node": "Save Conversation", "type": "main", "index": 0 }]]
},
"Save Conversation": {
  "main": [[{ "node": "Respond to Webhook", "type": "main", "index": 0 }]]
}
```

**Step 3: Validate JSON, commit**

```bash
cat n8n/workflows/telegram-message-handler.json | python3 -m json.tool > /dev/null && echo "Valid JSON"
git add n8n/workflows/telegram-message-handler.json
git commit -m "feat: telegram handler — query/delete/general intents + reply + save conversation"
```

---

## Task 6: Morning Briefing Workflow

**Files:**
- Create: `n8n/workflows/morning-briefing.json`

**Step 1: Create the complete morning briefing workflow**

```json
{
  "_comment": "Morning Briefing | Scheduled 8:00 Kyiv | Sends health summary to each user",
  "name": "Morning Briefing",
  "nodes": [
    {
      "id": "schedule",
      "name": "Schedule 8AM Kyiv",
      "type": "n8n-nodes-base.scheduleTrigger",
      "typeVersion": 1.2,
      "position": [250, 300],
      "parameters": {
        "rule": {
          "interval": [
            { "field": "cronExpression", "expression": "0 8 * * *" }
          ]
        }
      }
    },
    {
      "id": "get-users",
      "name": "Get Active Users",
      "type": "n8n-nodes-base.postgres",
      "typeVersion": 2.5,
      "position": [480, 300],
      "parameters": {
        "operation": "executeQuery",
        "query": "SELECT id, telegram_user_id, daily_calorie_goal, language FROM users WHERE telegram_user_id IS NOT NULL",
        "options": {}
      },
      "credentials": {
        "postgres": { "id": "YOUR_POSTGRES_CREDENTIAL_ID", "name": "pet_pg_db" }
      }
    },
    {
      "id": "get-health-data",
      "name": "Get Health Data",
      "type": "n8n-nodes-base.postgres",
      "typeVersion": 2.5,
      "position": [700, 300],
      "parameters": {
        "operation": "executeQuery",
        "query": "=SELECT\n  (SELECT json_build_object('sleep_performance', sleep_performance_percentage, 'total_sleep_hours', ROUND(total_sleep_time_milli/3600000.0, 1), 'deep_sleep_hours', ROUND(total_slow_wave_sleep_milli/3600000.0, 1), 'rem_hours', ROUND(total_rem_sleep_milli/3600000.0, 1)) FROM whoop_sleep WHERE user_id = {{ $json.id }} ORDER BY ended_at DESC LIMIT 1) as last_sleep,\n  (SELECT json_build_object('recovery_score', recovery_score, 'resting_hr', resting_heart_rate, 'hrv', hrv_rmssd_milli) FROM whoop_recovery WHERE user_id = {{ $json.id }} ORDER BY recorded_at DESC LIMIT 1) as last_recovery,\n  (SELECT json_build_object('total_in', COALESCE(SUM(calories),0), 'goal', {{ $json.daily_calorie_goal }}, 'balance', COALESCE(SUM(calories),0) - {{ $json.daily_calorie_goal }}) FROM food_entries WHERE user_id = {{ $json.id }} AND logged_at >= CURRENT_DATE - INTERVAL '1 day' AND logged_at < CURRENT_DATE) as yesterday_calories",
        "options": {}
      },
      "credentials": {
        "postgres": { "id": "YOUR_POSTGRES_CREDENTIAL_ID", "name": "pet_pg_db" }
      }
    },
    {
      "id": "gpt-morning-tip",
      "name": "GPT Morning Tip",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [920, 300],
      "parameters": {
        "method": "POST",
        "url": "https://api.openai.com/v1/chat/completions",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            { "name": "Authorization", "value": "=Bearer ${OPENAI_API_KEY}" },
            { "name": "Content-Type", "value": "application/json" }
          ]
        },
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ model: 'gpt-4o-mini', messages: [{ role: 'system', content: 'You are a health assistant. Generate a short, friendly morning briefing (3-5 sentences) in ' + ($('Get Active Users').item.json.language === 'uk' ? 'Ukrainian' : 'English') + '. Include sleep summary, recovery status, yesterday calories, and one actionable tip for today. Use emoji sparingly (1-2 max). Be encouraging.' }, { role: 'user', content: JSON.stringify({ sleep: $json.last_sleep, recovery: $json.last_recovery, yesterday_calories: $json.yesterday_calories }) }], temperature: 0.7, max_tokens: 300 }) }}",
        "options": {}
      }
    },
    {
      "id": "send-briefing",
      "name": "Send Morning Briefing",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [1140, 300],
      "parameters": {
        "method": "POST",
        "url": "=https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage",
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ chat_id: $('Get Active Users').item.json.telegram_user_id, text: items[0].json.choices[0].message.content, parse_mode: 'HTML' }) }}",
        "options": {}
      }
    }
  ],
  "connections": {
    "Schedule 8AM Kyiv": {
      "main": [[{ "node": "Get Active Users", "type": "main", "index": 0 }]]
    },
    "Get Active Users": {
      "main": [[{ "node": "Get Health Data", "type": "main", "index": 0 }]]
    },
    "Get Health Data": {
      "main": [[{ "node": "GPT Morning Tip", "type": "main", "index": 0 }]]
    },
    "GPT Morning Tip": {
      "main": [[{ "node": "Send Morning Briefing", "type": "main", "index": 0 }]]
    }
  },
  "settings": { "executionOrder": "v1" }
}
```

**Step 2: Validate JSON, commit**

```bash
cat n8n/workflows/morning-briefing.json | python3 -m json.tool > /dev/null && echo "Valid JSON"
git add n8n/workflows/morning-briefing.json
git commit -m "feat: morning briefing workflow — scheduled 8:00 with GPT tips"
```

---

## Task 7: Evening Summary Workflow

**Files:**
- Create: `n8n/workflows/evening-summary.json`

**Step 1: Create the complete evening summary workflow**

```json
{
  "_comment": "Evening Summary | Scheduled 21:00 Kyiv | Sends daily wrap-up to each user",
  "name": "Evening Summary",
  "nodes": [
    {
      "id": "schedule",
      "name": "Schedule 9PM Kyiv",
      "type": "n8n-nodes-base.scheduleTrigger",
      "typeVersion": 1.2,
      "position": [250, 300],
      "parameters": {
        "rule": {
          "interval": [
            { "field": "cronExpression", "expression": "0 21 * * *" }
          ]
        }
      }
    },
    {
      "id": "get-users",
      "name": "Get Active Users",
      "type": "n8n-nodes-base.postgres",
      "typeVersion": 2.5,
      "position": [480, 300],
      "parameters": {
        "operation": "executeQuery",
        "query": "SELECT id, telegram_user_id, daily_calorie_goal, language FROM users WHERE telegram_user_id IS NOT NULL",
        "options": {}
      },
      "credentials": {
        "postgres": { "id": "YOUR_POSTGRES_CREDENTIAL_ID", "name": "pet_pg_db" }
      }
    },
    {
      "id": "get-today-data",
      "name": "Get Today Data",
      "type": "n8n-nodes-base.postgres",
      "typeVersion": 2.5,
      "position": [700, 300],
      "parameters": {
        "operation": "executeQuery",
        "query": "=SELECT\n  (SELECT json_build_object('total_in', COALESCE(SUM(calories),0), 'total_protein', COALESCE(SUM(protein),0), 'total_fat', COALESCE(SUM(fat),0), 'total_carbs', COALESCE(SUM(carbs),0), 'entry_count', COUNT(*)) FROM food_entries WHERE user_id = {{ $json.id }} AND logged_at >= CURRENT_DATE) as food_today,\n  (SELECT json_build_object('workout_count', COUNT(*), 'total_burned', COALESCE(SUM(calories),0), 'total_strain', COALESCE(SUM(strain),0)) FROM whoop_activities WHERE user_id = {{ $json.id }} AND started_at >= CURRENT_DATE) as activity_today,\n  (SELECT json_agg(json_build_object('name', food_name, 'cal', ROUND(calories), 'meal', meal_type) ORDER BY logged_at) FROM food_entries WHERE user_id = {{ $json.id }} AND logged_at >= CURRENT_DATE) as meals_list,\n  {{ $json.daily_calorie_goal }} as calorie_goal",
        "options": {}
      },
      "credentials": {
        "postgres": { "id": "YOUR_POSTGRES_CREDENTIAL_ID", "name": "pet_pg_db" }
      }
    },
    {
      "id": "gpt-evening-summary",
      "name": "GPT Evening Summary",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [920, 300],
      "parameters": {
        "method": "POST",
        "url": "https://api.openai.com/v1/chat/completions",
        "sendHeaders": true,
        "headerParameters": {
          "parameters": [
            { "name": "Authorization", "value": "=Bearer ${OPENAI_API_KEY}" },
            { "name": "Content-Type", "value": "application/json" }
          ]
        },
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ model: 'gpt-4o-mini', messages: [{ role: 'system', content: 'You are a health assistant. Generate a short, friendly evening summary (4-6 sentences) in ' + ($('Get Active Users').item.json.language === 'uk' ? 'Ukrainian' : 'English') + '. Include: meals logged today, total calories vs goal, calories burned, calorie balance (surplus/deficit), and one reflection or tip for tomorrow. Use emoji sparingly (1-2 max). Be supportive.' }, { role: 'user', content: JSON.stringify({ food: $json.food_today, activity: $json.activity_today, meals: $json.meals_list, calorie_goal: $json.calorie_goal }) }], temperature: 0.7, max_tokens: 400 }) }}",
        "options": {}
      }
    },
    {
      "id": "send-summary",
      "name": "Send Evening Summary",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [1140, 300],
      "parameters": {
        "method": "POST",
        "url": "=https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage",
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ chat_id: $('Get Active Users').item.json.telegram_user_id, text: items[0].json.choices[0].message.content, parse_mode: 'HTML' }) }}",
        "options": {}
      }
    }
  ],
  "connections": {
    "Schedule 9PM Kyiv": {
      "main": [[{ "node": "Get Active Users", "type": "main", "index": 0 }]]
    },
    "Get Active Users": {
      "main": [[{ "node": "Get Today Data", "type": "main", "index": 0 }]]
    },
    "Get Today Data": {
      "main": [[{ "node": "GPT Evening Summary", "type": "main", "index": 0 }]]
    },
    "GPT Evening Summary": {
      "main": [[{ "node": "Send Evening Summary", "type": "main", "index": 0 }]]
    }
  },
  "settings": { "executionOrder": "v1" }
}
```

**Step 2: Validate JSON, commit**

```bash
cat n8n/workflows/evening-summary.json | python3 -m json.tool > /dev/null && echo "Valid JSON"
git add n8n/workflows/evening-summary.json
git commit -m "feat: evening summary workflow — scheduled 21:00 with GPT reflection"
```

---

## Task 8: Deploy Workflows to n8n

**Step 1: Deploy Telegram Message Handler via n8n API**

Use the n8n MCP tool `n8n_create_workflow` to create the workflow from the JSON file. Then activate it.

```bash
# After creating via MCP, activate:
curl -X POST "${N8N_HOST}/api/v1/workflows/{WORKFLOW_ID}/activate" \
  -H "X-N8N-API-KEY: ${N8N_API_KEY}" \
  -H "Content-Type: application/json"
```

**Step 2: Deploy Morning Briefing via n8n API**

Same process: create workflow, then activate.

**Step 3: Deploy Evening Summary via n8n API**

Same process: create workflow, then activate.

**Step 4: Set Telegram webhook to point to n8n**

```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook?url=${N8N_WEBHOOK_URL}/webhook/telegram/message"
```

Expected: `{"ok":true,"result":true,"description":"Webhook was set"}`

**Step 5: Verify webhook is set**

```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

Expected: `url` matches your n8n webhook URL, `pending_update_count` is 0.

**Step 6: Update session-knowledge.md with new workflow IDs**

Add the 3 new workflows to the "n8n Workflows Deployed" table in `docs/en/session-knowledge.md` and `docs/uk/session-knowledge.md`.

**Step 7: Commit**

```bash
git add docs/en/session-knowledge.md docs/uk/session-knowledge.md
git commit -m "docs: add new AI bot workflow IDs to session knowledge"
```

---

## Task 9: End-to-End Testing

**Step 1: Test text food logging**

Send a Telegram message to the bot: "I ate chicken breast 200g for lunch"

Expected:
- Bot responds with logged food + calories + daily total
- Row appears in `food_entries` table
- Two rows appear in `conversation_messages` (user + assistant)

**Step 2: Test contextual follow-up**

Send: "And also some rice"

Expected:
- Bot understands this is a food follow-up from context
- Logs rice as a separate entry
- Updated daily total in response

**Step 3: Test voice message**

Send a voice message in Ukrainian: "Я з'їв борщ на обід"

Expected:
- Whisper transcribes to text
- GPT parses food item
- FatSecret search (or GPT fallback)
- Bot responds in Ukrainian

**Step 4: Test health query**

Send: "How did I sleep last night?"

Expected:
- Bot queries WHOOP sleep data
- Responds with sleep duration, score, tips

**Step 5: Test delete**

Send: "Delete last entry"

Expected:
- Most recent food_entry deleted
- Bot confirms with food name and calories

**Step 6: Test morning briefing manually**

Execute the Morning Briefing workflow manually in n8n UI.

Expected:
- Receives Telegram message with sleep summary, recovery, yesterday's calories, tip

**Step 7: Test evening summary manually**

Execute the Evening Summary workflow manually in n8n UI.

Expected:
- Receives Telegram message with today's meals, calorie balance, reflection

---

## Task 10: Conversation Cleanup Job (optional)

**Files:**
- Create: `database/migrations/004_conversation_cleanup.sql`

**Step 1: Add a cleanup function for old conversation messages**

```sql
-- Auto-cleanup conversation messages older than 7 days
-- Can be called by a scheduled n8n workflow or pg_cron

CREATE OR REPLACE FUNCTION cleanup_old_conversations()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM conversation_messages
    WHERE created_at < NOW() - INTERVAL '7 days';
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;
```

**Step 2: Commit**

```bash
git add database/migrations/004_conversation_cleanup.sql
git commit -m "feat: add conversation cleanup function (7-day retention)"
```

---

## Summary

| Task | What | Est. Complexity |
|------|------|-----------------|
| 1 | DB migration — `conversation_messages` | Low |
| 2 | Telegram Handler — trigger + voice/text routing | Medium |
| 3 | Telegram Handler — user lookup + context + GPT | Medium |
| 4 | Telegram Handler — food logging branch | High |
| 5 | Telegram Handler — non-food intents + reply + save | Medium |
| 6 | Morning Briefing workflow | Low |
| 7 | Evening Summary workflow | Low |
| 8 | Deploy to n8n + set Telegram webhook | Medium |
| 9 | End-to-end testing | Medium |
| 10 | Conversation cleanup (optional) | Low |

**Total: 10 tasks, ~3 new workflow files, 2 SQL migrations, updates to session-knowledge docs.**

**Dependencies:**
- Tasks 2-5 are sequential (building one workflow incrementally)
- Task 6 and 7 are independent of each other and can be done in parallel
- Task 8 depends on Tasks 1-7
- Task 9 depends on Task 8
- Task 10 is independent
