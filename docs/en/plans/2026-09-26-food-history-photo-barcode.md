# Food logging: history, photos, barcodes, and Telegram Web App management

[Українська версія](../../uk/plans/2026-09-26-food-history-photo-barcode.md)

**Date:** 2026-09-26 · **Status:** P1 backend implemented (phases 1–4, Web App + admin API, migrations 016–017) — see [food-logging.md](../food-logging.md). Not done: phase 0 live checks, the React frontend (W1 UI/W4), the evaluation dataset; plate recognition ships disabled.

## 1. Goal and scope

Reuse the user's previously selected food and serving, calculate nutrition from an explicit weight, and support Telegram photos of food, packaging/nutrition labels, and barcodes. The expanded scope includes a Telegram Web App (Mini App) with a personal management area and an owner/admin area, manual entry, an automatically populated and manually editable product catalog, and explicit default-product rules. Bot and Web App share one backend and diary.

**Scope update:** Web App requirements, default-product semantics, configurable settings and delivery phases are detailed in sections 13–16. P1 is the core release; P2 is a subsequent extension.

**Catalog source clarification:** FatSecret diary history is the primary source of My Products, including foods recorded before this bot was connected. History-based catalog population and selection/removal/manual completion are required P1 functionality, not an optional import. Bot entries and manually created products supplement that catalog.

Research consisted of repository inspection and the official documentation linked in section 12. Account entitlements, Ukrainian product coverage, recognition quality, and live write/read behavior have not been measured. Existing uncommitted application changes were present during research; file references describe the inspected working tree and must be rechecked before implementation.

### User stories

- **US-1 / P1:** As a returning user, I enter “cooked buckwheat 180 g” and get my previously selected cooked product, not a new generic search result.
- **US-2 / P1:** I send a barcode photo with “135 g”; the bot resolves the exact product and calculates my portion.
- **US-3 / P1:** I send a packaging/nutrition-label photo when a barcode is missing from the database, correct the extracted fields if necessary, and reuse this product later.
- **US-4 / P2:** I send a plate photo and grams; the bot suggests recognizable foods, prioritizes compatible history, and asks only for missing distinctions or component weights.
- **US-5 / P1:** I correct a product or weight, and the bot remembers the corrected choice without duplicating the diary entry.
- **US-6 / P1:** I open the Web App from Telegram, enter or correct a meal for a chosen date, and see the same result in the bot.
- **US-7 / P1:** I create a product without logging a meal, assign “my yogurt” to it as a default, and the next matching bot message uses that product.
- **US-8 / P1:** My Products is populated from my FatSecret history even when I have never logged food in this bot. I can select which products to keep, remove unwanted ones, fill missing fields, create products manually and pin defaults; new confirmed bot entries also extend the catalog.
- **US-9 / P1:** As owner/admin, I maintain the shared starter catalog, see integration/job failures and control supported features without exposing service credentials to the browser.
- **US-10 / P2:** I maintain recipes, meal templates and manual health measurements, while keeping imported source data distinguishable.

## 2. Current implementation and prerequisites

| Observation | Evidence | Consequence / required change |
|---|---|---|
| Every entry searches FatSecret with `max_results=1` | `app/services/telegram_bot.py`, `_handle_log_food`, around lines 147–175 | Add a shared history-first resolver; use several candidates only when history does not resolve the item. |
| The prompt removes preparation details and estimates unspecified grams | `app/services/ai_assistant.py`, `SYSTEM_PROMPT` | Preserve brand, fat percentage, raw/cooked state, preparation, and explicit vs inferred quantity. Missing grams must be nullable. |
| Successful FatSecret writes are not stored locally; fallback inserts omit `fatsecret_food_id` | `telegram_bot.py`, around lines 249–279; migration `002`, lines 53–69 | Persist a local logging event and provider identity for every confirmed entry. Existing local history is incomplete. |
| The initially observed ignored `False` result is fixed in the current working tree; writes still return a boolean | `fatsecret_api.py`, `create_food_diary_entry`; `telegram_bot.py`, `_handle_log_food` | Preserve this fix; add a typed result with the remote entry ID and reject malformed success responses. |
| Diary reads discard `food_entry_id`, `food_id`, `serving_id` and structured quantities | `fatsecret_api.py:279–290` | Preserve identifiers for history lookup and reconciliation. |
| Nutrition is parsed from a search description, separately from the serving used for diary writes | `telegram_bot.py:68–105, 169–175, 183–255` | Calculate from the chosen structured serving. Do not equate ml or oz with grams. |
| The message handler adds just-logged calories to a newly fetched daily total unconditionally | `telegram_bot.py:490–495` | Remove this heuristic: an already-visible remote entry is counted twice. |
| Daily intake uses FatSecret only, otherwise zero | `ai_assistant.py`, `get_today_stats`, `total_in` assignment | OFF/label/local entries must be included without double counting linked remote entries. |
| Only text and voice handlers exist; deletion is local-only | `telegram_bot.py:290–306, 972–977` | Add photo/document/callback handlers and linked edit/delete semantics. |
| No scheduled history import exists | `app/scheduler.py` | Add bounded history refresh explicitly; do not rely on older session notes about hourly diary sync. |

These are implementation prerequisites: history can improve consistency, but reusing an old incorrect product without retaining its identity or preparation state perpetuates the error.

**Web App inspection update:** `docs/design/` contains bilingual Dashboard, Food Log, Activity, History and Profile designs, including manual food entry and a serving editor. There is no frontend package in the inspected tree. Language, timezone, calorie goal, profile fields for BMR and journal reminder settings now exist in backend code; reuse them. `app/security.py:require_admin` protects operator endpoints with a service token, but does not implement Mini App user sessions or roles. Some old profile mockups list Google Fit; that integration is not part of this release.

## 3. Provider research and recommended choices

| Capability | Verified documentation | Recommendation |
|---|---|---|
| FatSecret personal history | `foods.get_recently_eaten.v2`, `foods.get_most_eaten.v2`, and favorites use user OAuth 1.0; recent/most return food and serving IDs | Bootstrap candidates from these methods. Verify actual ordering and availability: the recent/most pages contain a generic “favorite foods” description. |
| FatSecret historical diary | `food_entries.get.v2` returns entry, food and serving IDs plus nutrition for a requested day | Required resumable catalog population, initially 30 days with a selectable range. Deduplicate catalog products by provider food ID and any imported diary links by remote entry ID; never perform a history scan on every message. |
| FatSecret barcode v2 | Premier Exclusive, OAuth2 scope `barcode`; returns full food data, unlike v1 which returns an ID | Optional adapter when enabled for the account. Current token requests only `basic`; adding the scope does not grant entitlement. |
| FatSecret image recognition v2 | Optional add-on, scope `image-recognition`, accepts `eaten_foods` to improve history-aware matching | Evaluate against OpenAI if available. It rejects plain nutrition-label images by design, so it cannot replace label OCR. Respect its Base64/body limits. |
| Open Food Facts (OFF) | Public barcode product reads; current docs recommend API v3, latest documented sub-version 3.6 | Default external packaged-food lookup without requiring a FatSecret barcode add-on. Pin an explicit version and normalize through an adapter. |
| Barcode decoding | `zxing-cpp` Python bindings expose `read_barcodes` | Decode locally using `zxing-cpp` and Pillow. Validate wheels on production Python 3.12/Linux; do not add OpenCV unless the spike proves necessary. |
| Food and label images | OpenAI vision accepts images with text through Chat Completions or Responses | Use a separately configurable vision model and validated structured extraction. Existing SDK `openai==1.61.0` must be checked before using newer API methods; Chat Completions is already used. |
| Telegram media | PTB 21.10 supports file download to bytes; hosted Bot API download limit documented as 20 MB | Accept photos and JPEG/PNG/WebP image documents, read captions, use an application limit of 10 MB plus decoded-pixel bounds. |

OFF docs currently state 15 product reads/minute/IP and 10 searches/minute/IP. Configure a shared limiter, cache, short negative-cache TTL, backoff, and custom `User-Agent`; recheck published limits at rollout. API v3.5 changed nutrition structure: do not assume legacy `nutriments.*_100g` fields exist in a v3.6 response. Bind normalization to documented fixtures for the selected version.

### Storage implications that affect the design

FatSecret's published storable-data rules allow indefinite storage of identifiers such as `food_id`, `serving_id`, `food_entry_id`, but restrict caching of other returned user data to at most 24 hours and require other information to be requested again. Therefore history-first matching must work with permanent IDs and the user's own input/preferences, plus refreshable provider details. Do not build an indefinitely retained FatSecret nutrition catalog or assume a user clicking Confirm changes the origin of provider data. Check the account's agreement for any broader snapshot rights before enabling them.

Keep OFF provenance and attribution (ODbL/database contents terms; separate image terms). Provider records must remain distinguishable from user-entered label data and from FatSecret records. Cross-provider matching creates links, not an indistinguishable merged catalog.

## 4. Functional requirements and matching policy

- **FR-01:** One resolver serves text, voice, barcode and photo inputs. AI identifies candidates; backend code calculates quantities and calories.
- **FR-02:** Restrict personal-history retrieval to the authenticated user. Store product identity separately from a consumption event.
- **FR-03:** Exact barcode/product identity and explicit current attributes override historical frequency. A historical favorite cannot override a different scanned barcode, brand, fat percentage, or raw/cooked state.
- **FR-04:** Within compatible candidates, rank: explicit selection or pinned user alias → previously confirmed exact match → user's recent/frequent/favorite products → external text search. Use recency/frequency only as tie-breakers after identity compatibility.
- **FR-05:** Auto-select a single unambiguous previously confirmed match with explicit grams unless the user enabled review of every entry; reply with the selected product and an Undo/Change action. For ambiguous/new photographic matches show up to three candidates with brand, preparation, kcal basis and source. Model-reported confidence alone is not an auto-commit criterion.
- **FR-06:** Save corrections as user-specific aliases/preferences after a real selection. Do not promote imported legacy rows or AI-only matches to “confirmed” automatically.
- **FR-07:** If grams are missing, ask for them. “Same as last time” may reuse a prior amount only when explicitly requested. The printed package net weight is not the amount consumed.
- **FR-08:** Unknown nutrients are `null`, not zero. Missing energy or a usable mass conversion keeps the entry in a draft rather than logging 0 kcal.
- **FR-09:** Use `Decimal`: `portion_kcal = kcal_per_100g × grams / 100`, likewise for macros. Derive per-100g values from a structured serving with a known gram mass. Convert kJ to kcal using 4.184; convert mass ounces explicitly; do not convert volume to mass without product-specific density.
- **FR-10:** Preserve “as sold/as prepared”, edible/drained portion, and recipe identity. A whole-plate weight does not determine component proportions. Saved recipes may calculate per-gram nutrition from ingredients and measured final yield.
- **FR-11:** Replaying an update or clicking Confirm twice creates one local consumption event. Re-sending the same photo in a new intentional meal is allowed; image hash is not a permanent meal deduplication key.
- **FR-12:** Report source and freshness and distinguish measured grams, label/catalog nutrition, and approximate dish identification. Retain the nutrition revision for durable sources so future catalog changes do not rewrite past meals.
- **FR-13:** Web App manual food logging uses the same validation, calculation, draft commit, daily totals and remote sync services as bot logging, with `origin=web_manual`.
- **FR-14:** Populate personal catalog membership from FatSecret diary history on connection and refresh it incrementally. Support selection of all or individual discovered products, removal, restoration and manual completion/creation. Creating/importing a catalog product or selecting a default never creates a consumption event. Confirmed bot entries also upsert membership without duplicates and respect explicit exclusions.
- **FR-15:** Defaults are user-scoped matching rules that can be set before first consumption. Explicit selection, barcode and incompatible current attributes override a default; automatically learned candidates never overwrite a pinned rule.
- **FR-16:** Personal catalog cards support manual creation, edits via nutrition revisions, aliases, barcode, preparation, portion presets, pin/unpin and archive. Changes affect future meals; past entries change only through explicit entry edits.
- **FR-17:** Web App requests use a backend-validated Telegram identity and ownership checks. Admin actions require a separate server-side role; hiding an admin tab is not authorization.
- **FR-18:** User preferences and admin settings are typed, versioned and shared between bot and Web App. Service secrets remain deployment settings; unsupported provider capabilities cannot be enabled merely by a UI toggle.
- **FR-19:** Bot and Web App editing the same entry/draft/preferences use optimistic version checks and stable commit keys. A conflicting edit returns a conflict with reload/resolve behavior instead of silently overwriting changes.
- **FR-20:** Manual health measurements in P2 are separate `manual` observations or explicit display overrides, not fabricated Apple Health/WHOOP imports; calorie sources with different meanings must not be summed indiscriminately.

Initial matching can use normalized aliases and PostgreSQL queries; optional `pg_trgm` for typo-tolerant candidate retrieval. Embeddings/vector storage are unnecessary for the first release. Retrieve a bounded shortlist rather than sending the full food diary to a model.

## 5. Telegram flows

### A. Text or voice → history

`Cooked buckwheat 180 g` → exact compatible saved choice → backend calculation → `Added: your previously selected cooked buckwheat, 180 g, … kcal` + Change/Undo. If both raw and cooked candidates remain possible, ask which one before recording.

### B. Barcode photo + grams

`Photo + caption “135 g”` → image normalization → barcode decoder → checksum/symbology validation → exact personal product link → OFF → optional FatSecret barcode adapter → product card → record after selection if needed. A known confirmed exact barcode with explicit grams can follow the fast path.

Keep barcodes as strings, preserving leading zeros and original symbology. Support EAN-13, EAN-8, UPC-A and correctly expanded UPC-E. FatSecret requires its own GTIN-13 conversion; OFF normalization is provider-specific. Reject unsupported variable-weight/store codes as unresolved instead of guessing a product or weight. Multiple barcodes require selection. For unreadable images request a closer photo or typed digits; OCR digits remain tentative until validated.

### C. Unknown product → nutrition-label photo

Request the front/name and nutrition panel (a follow-up photo can join the same draft). Extract visible brand/name, energy, macros, units and basis: per 100 g, per 100 ml, or per serving. Preserve uncertain/missing fields. Show editable extracted values; a confirmed mass-based label becomes a reusable personal product, optionally linked to the scanned barcode. Example with fictional label: 246 kcal/100 g × 135 g = **332.1 kcal**.

### D. Plate photo + grams

Recognize candidate foods → constrain by visible/user-specified preparation → resolve against personal history → ask for missing product identity or component grams → calculate from selected foods. For a photo containing rice and chicken with only “350 g total”, ask for the split or a saved recipe; do not assign 350 g to each component or silently invent a precise recipe. Oils, sauces and hidden ingredients remain explicit unknowns. An approximate mode can be a later feature, visibly distinct from confirmed values.

### Draft lifecycle

`received → recognizing → needs_product / needs_weight / needs_label → ready → committed`, plus `cancelled`, `expired`, `failed`. Store drafts in PostgreSQL with user/chat, originating message, reply-to linkage, version and expiry (proposed 24 h). A reply “135 g” updates the referenced draft, not a new food message. When several drafts exist, require reply/selection to disambiguate. Group album messages by `media_group_id` before processing so packaging and barcode images form one draft. Restart and duplicate callbacks must preserve state; callback data contains short IDs, with ownership/version checked server-side. Editing committed messages opens an explicit revision, not another meal.

## 6. Proposed data model

Use additive migrations with INTEGER/SERIAL keys; choose the next migration number at implementation time.

| Entity | Main fields / invariants |
|---|---|
| `food_products` | Stable identity, owner for personal products, provider + external ID, barcode/symbology where available, provenance. User-entered names/preparation remain separate from expiring provider metadata. |
| `food_nutrition_versions` | Product, basis quantity/unit, energy/macros, source/revision, retrieved/expiry times, storage policy. Durable revisions for permitted OFF/label/recipe data; FatSecret details follow its refresh/cache policy. |
| `user_food_preferences` | User + product, selected serving ID, user-authored aliases, explicit preparation constraints, pinned/confirmed flags, bot-observed last use/count. No global “rice” → one product mapping. |
| `food_log_drafts` | User/chat/message/media-group linkage, parsed input, selected candidates, quantity, state/version/expiry, temporary media references. |
| `food_entries` (extend) | Product/version reference, user-provided grams, local date/timezone, input origin, entry status, idempotency key, remote food/serving/entry IDs. New unresolved/expired nutrition is nullable or kept in a separate cache; legacy zero values do not establish validity. |
| `food_sync_outbox` | Entry + revision + operation unique key, pending/sending/succeeded/failed/unknown/not_supported status, attempts, lease, remote ID, next retry. |

Unique constraints: provider identity with correct personal ownership scope; `(user_id, provider, remote_entry_id)` when present; committed draft/item or `(bot_id, chat_id, message_id, item_index)` for ingestion. Do not identify products solely by name. Provider metadata must expire wherever cached, including drafts/log payloads, not just in one table.

Bootstrap existing local rows as **unverified legacy** candidates. Only derive per-100g nutrition when historical mass and provenance are reliable; do not convert failed zero-calorie lookups into learned products. Store imported FatSecret IDs indefinitely; other imported fields follow the provider policy. Make bootstrap resumable and non-destructive.

## 7. Reliable diary writes and daily totals

1. Commit the local logging event and outbox operation in one DB transaction; show local success independently from remote sync status. Persist only the nutrition data allowed by the selected provider policy.
2. Worker uses selected `food_id` + real `serving_id`; validate current serving-unit semantics using create/read round trips for 1 g, 100 g, and branded servings. Derived `serving_id=0` must not silently become a writable serving.
3. Require an acknowledged remote entry ID. On timeout after dispatch, mark `unknown` and reconcile; an idempotent local outbox does not make a remote create API exactly-once. Never blindly retry an ambiguous write and never merge two legitimate equal meals merely by name/calories.
4. Custom label/OFF products without a verified FatSecret mapping remain local with `not_supported` remote status. Evaluate `food.create.v2` only if entitled; do not substitute an unrelated FatSecret product to force synchronization.
5. Daily view represents the union of local events and remote diary entries linked by remote ID: a mirrored entry counts once; remote-only entries and local-only entries also count. Pending local events are included only when definitely not represented remotely. Ambiguous sync or unavailable/expired nutrition produces an explicitly partial total, not a fabricated zero or an unqualified complete total.
6. Use the user's local calendar date for both providers and the ledger; derive FatSecret's date integer from that local date, not `floor(time.time()/86400)`.
7. Weight corrections and Undo target one entry/revision; linked remote edit/delete gets an outbox operation and visible status. A failed remote delete must not make the entry silently reappear as new local intake.

## 8. Suggested code boundaries

- `app/services/food_resolver.py`: compatible history retrieval and candidate ranking.
- `app/services/food_catalog.py`: provider identities, preferences and allowed caches.
- `app/services/food_nutrition.py`: validated quantities, units and deterministic calculations.
- `app/services/food_vision.py`: dish/packaging/label extraction with typed results.
- `app/services/barcode_reader.py`: local decoding and provider-specific normalization.
- `app/services/open_food_facts.py`: versioned product adapter, cache and shared limiter.
- `app/services/food_logging.py`: draft transitions, commit, correction and daily view.
- `app/services/food_sync.py`: FatSecret outbox worker and reconciliation.
- Extend `fatsecret_api.py` for history, detailed servings and typed write results; extend `telegram_bot.py` for photo/document/callback flows; update `ai_assistant.py`, `briefings.py`, `scheduler.py`, config, migrations and tests.
- Add `web/` (proposed React + TypeScript + Vite), `app/routers/webapp.py`, `app/routers/admin.py`, `app/services/webapp_auth.py`, and a typed preferences service. Authenticated web routes call the shared services above, not the operator-only `/food/search` endpoint. See section 16 for API and deployment details.

Media processing is bounded and offloaded from the async event loop; AI calls have concurrency/time limits. Download image bytes on the backend and send a data URL, never a Telegram download URL containing the bot token. Normalize orientation and strip metadata on the processing copy; retain barcode resolution before making a smaller vision copy. Start with transient image processing and expiring file references, not permanent photo storage.

## 9. Implementation sequence

| Phase | Work and exit criterion | Indicative effort |
|---|---|---|
| **0. Integration spike** | Verify FatSecret history/scopes, storage policy and write response; establish OFF versioned fixtures; measure representative Ukrainian barcode coverage; check decoder Linux wheels and vision model/SDK. Record results rather than assuming access. | 1–2 days |
| **1. Accurate logging foundation** | Preserve corrected sync-result handling, fix serving-based calculations and duplicate totals; add event/ID storage, outbox, partial-state semantics and linked Undo. No lost entry on provider failure. | 3–4 days |
| **2. History-first text/voice** | Required resumable FatSecret diary-to-catalog population plus recent/frequent/favorite enrichment, persistent exclusions, compatible ranking, aliases, correction learning and grams drafts. Pre-bot history populates My Products; repeated foods reuse their identity. | 2–3 days |
| **3. Barcode photo + grams** | Add media handling, decoder, GTIN validation, OFF adapter, optional FatSecret barcode lookup and draft linking. Exact product lookup before generic search. | 2–3 days |
| **4. Label/packaging photos** | Add OCR/vision extraction, unit/basis validation, editable product card and reusable personal products. Unknown barcode can be completed from its label. | 2–3 days |
| **5. Plate photos + rollout** | Add history-aware dish candidates, multi-item clarification, metrics and end-to-end regressions. Deploy behind separate history/barcode/vision flags. | 2–4 days |
| **W1. Web App foundation** | HTTPS `/app/`, Telegram launch button, verified initData/session, profile bootstrap, uk/en UI and Telegram theme/safe areas. Start after phase 1. | 2–3 days |
| **W2. Personal food management** | Catalog, manual product/meal forms, diary edits, defaults, shared drafts and bot/Web App consistency. Requires phase 2 + W1; deliver before plate recognition. | 3–5 days |
| **W3. Core settings and owner admin** | P1 settings in section 15, integration status, shared starter catalog, role-protected operations, change history and feature flags. Requires W2. | 2–3 days |
| **W4. Cross-client verification** | Telegram iOS/Android/Desktop, expired sessions, version conflicts, retry/double-click behavior, image upload fallback, production frontend build. | 2–3 days |

Original bot scope: **12–19 engineering days**. Core Web App/admin adds **9–14 days**, for an expanded planning estimate of **21–33 engineering days** for one developer, excluding provider access delays, sample collection and P2 features. Suggested order: 0 → 1 → 2 → W1/W2 → 3 → 4 → W3 → 5 → W4. First usable Web App milestone is a manual diary + catalog + defaults; image recognition is not a prerequisite for it. No new Redis/vector service is required: PostgreSQL and the existing scheduler suffice initially.

## 10. Acceptance criteria and evaluation

- **AC-01:** Repeated “cooked buckwheat 180 g” selects the confirmed food/serving without a new global text search; raw buckwheat and another user's preference cannot override it.
- **AC-02:** An explicit different brand/fat percentage/barcode defeats the historical favorite; ambiguous “cheese” shows candidates.
- **AC-03:** 246 kcal/100 g and 135 g calculate to 332.1 kcal; per-serving labels, kJ, decimal commas, missing fields, invalid/negative/NaN weights and ml-without-density are handled explicitly.
- **AC-04:** Caption grams and follow-up reply grams give the same result. A bare “135” cannot accidentally create a food entry or attach to the wrong draft.
- **AC-05:** Barcode checks cover leading zeros, EAN-8/13, UPC-A/E, multiple codes, checksum failures, blurred images, unknown products and unsupported store codes; no fabricated digits/IDs.
- **AC-06:** A label with no energy stays unresolved; absent macros are not zeros; package weight is not consumed weight.
- **AC-07:** A multi-item photo with only total grams requires clarification or an identified saved recipe; no exact-calorie claim from appearance alone.
- **AC-08:** Two Confirm callbacks, retried Telegram delivery, an album, or restart during processing produce one intended event; a deliberate later repeat meal remains possible.
- **AC-09:** FatSecret `False`, HTTP-200 error bodies, malformed success bodies and uncertain timeouts do not become successful sync; no blind retry after uncertain remote commit.
- **AC-10:** Remote entry already visible / delayed / remote-only / local-only / provider unavailable / local-day boundary / edit / delete cases never count a known linked event twice.
- **AC-11:** Expired provider cache is refreshed or shown as unavailable; expired snapshots are not resurrected from drafts, logs, or imported history.
- **AC-12:** PostgreSQL tests verify user isolation, uniqueness, draft version races and transaction rollback. Container checks verify image dependencies on the deployment architecture.
- **AC-13:** Create a product manually with zero consumption history, set a default, then log it through the bot: the product is reused, and catalog creation itself adds no calories.
- **AC-14:** Connect a user with FatSecret history and no bot entries: eligible historical products populate My Products. Repeated meals or different portions of the same provider product create one card, retaining serving choices. New confirmed bot entries reuse the card; an unconfirmed photo draft does not become a trusted default.
- **AC-15:** A pinned “yogurt” rule is respected for a compatible generic input, but cannot override a different exact barcode or explicit brand/fat percentage; unpin/archive takes effect in both clients.
- **AC-16:** Add/edit/delete an entry in the Web App and read it through the bot: same date, grams, nutrition, daily total and sync status. Concurrent edits return a version conflict; two commits of the same draft count once.
- **AC-17:** Forged/expired/future initData, client-supplied identity, another user's IDs and non-admin calls to admin APIs are rejected. No bot/admin/provider token is present in frontend assets or responses.
- **AC-18:** Changing an existing product/default/goal never silently recalculates old meals or changes another user's defaults. Disabling auto-add-to-catalog prevents new membership without losing food entries.
- **AC-19:** P1 settings persist across restart and are applied by bot and Web App. Notification times use the user's timezone and suppress duplicate sends; an unavailable provider feature is shown as unavailable.
- **AC-20:** Mini App works on Telegram iOS/Android/Desktop with theme/safe-area changes and expired-session recovery. EAN scanning has photo/manual-digit fallback and does not depend on Telegram's QR-only scanner.
- **AC-21:** Select only some discovered FatSecret products, remove another, then refresh/restart/reconnect: excluded products remain excluded until explicitly restored. Pinned defaults and personal field overrides survive refresh.
- **AC-22:** Removing a catalog item disables its default rules without deleting FatSecret diary entries or altering historical totals. Completing its missing nutrition manually records provenance; catalog import itself adds no meals or calories.
- **AC-23:** A failed/rate-limited history scan resumes from its checkpoint; an empty or partial response does not delete existing catalog membership. The UI reports the covered date range, progress and partial/error status accurately.

Before rollout assemble 30–50 real Ukrainian packaged products (barcode + readable label), 20 repeated-food phrases and 20 plate examples with known components/weights. Keep a holdout set. Measure exact barcode decoding, product coverage with usable nutrition (separately from decoding), top-1/top-3 identity accuracy, user correction rate, portion calculation error against the label, clarification rate, p50/p95 latency and actual provider cost per completed entry. A self-reported AI confidence or a database hit is not a measured accuracy rate. Required deterministic acceptance tests must all pass; set recognition rollout targets after the baseline spike.

## 11. Decisions still requiring live evidence

1. Which FatSecret history, barcode, image, custom-food capabilities and regions are enabled for this account? What storage rights does its agreement grant?
2. What history range and quotas are available for this FatSecret account? Diary-based catalog population is mandatory; start with 30 days, allow a selected date range and resume larger scans within provider limits. Recent/frequent/favorite lists enrich the catalog but cannot replace a complete scan of the selected period.
3. What is actual Ukrainian product coverage and how frequently do labels disagree with provider data? Explicit label corrections override older values for future entries via a new revision.
4. Which available vision model has the best measured recognition/OCR quality, latency and cost on these samples? FatSecret image add-on is a comparison candidate, not a prerequisite.
5. Does the exact write endpoint in use return a reliable entry ID, and can ambiguous timeouts be reconciled? Verify against a test account before enabling automatic remote retries.

Proposed defaults: grams entered by the user; fast path for exact previously confirmed matches; selection for ambiguity/new photo interpretations; OFF for new barcodes; existing FatSecret for known identities; OpenAI for dish/label extraction; local-only entries when remote mapping is unavailable.

## 12. Sources consulted

All retrieved on 2026-09-26; documented availability is not confirmation of this account's entitlement.

1. [FatSecret recently eaten](https://platform.fatsecret.com/docs/v2/foods.get_recently_eaten)
2. [FatSecret most eaten](https://platform.fatsecret.com/docs/v2/foods.get_most_eaten)
3. [FatSecret diary entries](https://platform.fatsecret.com/docs/v2/food_entries.get)
4. [FatSecret barcode v2](https://platform.fatsecret.com/docs/v2/food.find_id_for_barcode)
5. [FatSecret image recognition v2 and eaten_foods](https://platform.fatsecret.com/docs/v2/image.recognition)
6. [FatSecret storable data](https://platform.fatsecret.com/docs/guides/storable-data)
7. [OFF API, limits, attribution and versioning](https://openfoodfacts.github.io/openfoodfacts-server/api/)
8. [OFF schema change log](https://openfoodfacts.github.io/openfoodfacts-server/api/ref-api-and-product-schema-change-log/)
9. [ZXing-C++ Python bindings](https://github.com/zxing-cpp/zxing-cpp/tree/master/wrappers/python)
10. [OpenAI image inputs and limitations](https://platform.openai.com/docs/guides/images-vision)
11. [PTB 21.10 file downloads](https://docs.python-telegram-bot.org/en/v21.10/telegram.file.html)
12. [Telegram Mini Apps: launches, initData validation, themes, safe areas and QR scanner](https://core.telegram.org/bots/webapps)
13. [Existing bilingual Web App design system](../../design/README.md), [food log](../../design/pages/02-food-log.md), [profile/settings](../../design/pages/05-profile.md)

## 13. Web App: personal management and owner/admin area

### Screens and primary actions

| Screen | Core scope (P1) | Follow-up (P2) |
|---|---|---|
| Today / Dashboard | Intake, remaining goal, source/freshness, Add food, recent entries and incomplete drafts | Broader health trends and configurable dashboard widgets |
| Diary | Calendar, meal/source filters, manual entry, grams/date/meal editing, Undo, copy an entry to another day | Multi-day meal planning and bulk operations |
| My Products | Populate from FatSecret history; select all/individual products; search/filter, fill missing data, create manually, set default, remove/restore singly or in bulk; show import range/status | Validated CSV import/export, duplicate review |
| Default products | Alias/category rules, preparation constraints, product/serving choice, explicit priority and “test phrase” preview | More contextual rules by meal type |
| Add / Edit product | Name/brand, raw/cooked state, calorie/macronutrient basis, optional barcode, known gram portions; Save product and separate Log portion action | Ingredient-based recipes with final cooked yield and reusable meal templates |
| Settings | Food matching/recording policy, goals, profile, language/timezone, reminders, integration state | More display/unit options and advanced source preferences |
| Owner/Admin | Shared starter products, feature availability, anonymized operational counts, failed jobs and known-safe retry/reconcile actions | Catalog import tooling and additional administrative workflows |

Use existing Dashboard/Food Log/History/Profile designs as the visual basis. Food opens tabs for Diary, Products and Defaults; settings live under Profile. Admin appears only for an authorized operator. Keep Activity available as source-aware read-only summaries initially. Add responsive desktop tables for administration instead of forcing all admin screens into the old 428 px mobile width.

**Personal manager (`user`):** owns products/preferences/entries and can manage them without an admin role. **Owner/Admin (`admin`):** additionally manages published starter products and operational configuration. Bootstrap the owner from configured Telegram IDs and store roles server-side. Being an admin does not automatically grant edit access to other users' private health diaries; cross-user support access is a separate future capability. A starter catalog provides suggestions, not enforced replacements of personal defaults.

### Manual workflows

1. **Pre-create a default:** Products → New → enter a product or select a provider result → Save → “Use for: my yogurt” → optionally suggest a usual portion. No meal is created. The bot can use it immediately.
2. **Log food without AI:** Add food → select catalog item → grams → local date/time + meal → save. A personal product can be created inline. Frontend previews are advisory; backend recalculates authoritative values.
3. **Correct a bot entry:** Open its Web App deep link → review the shared draft/entry → choose product/weight → save revision. Deep links contain identifiers only; backend verifies ownership.
4. **Fill other data (P2):** manual weight with measurement time, sleep interval, workout and journal mood/energy. Reuse journal/gym services where appropriate and add explicit manual health storage. Show manual vs imported source and allow a deliberate display override without rewriting provider records. Do not send manual entries to Apple Health automatically: the current webhook receives data only.

## 14. Automatically populated catalog and default-product rules

Three different concepts are visible in the UI:

- **My Products membership:** primarily populated from FatSecret diary history, with additional membership from confirmed bot consumption or manual creation before any consumption. These are user-owned links with distinct origins. Imported history does not automatically pin a default. Merely opening a search result or receiving an AI proposal does not add a trusted card.
- **Default product:** an explicit selection rule such as `yogurt → Brand A, natural, 2%` or `buckwheat → cooked`. Store normalized alias, product ID, preparation constraints, selected serving, enabled state, rule origin (`manual` or `learned`) and version. A unique active manual rule per user/alias/context prevents competing defaults. The system may suggest a learned default; only explicit user action pins/replaces one.
- **Diary event:** actual consumed product and grams at a time. Neither catalog creation, pinning nor reminders imply consumption. A usual portion prefills an editor; it is not used as unspoken evidence of eaten grams. Scheduled automatic meal creation is outside the core release; templates are explicitly applied.

Resolver order: explicit product/barcode → compatible manual default → confirmed compatible history → learned/recent candidates → external search. Check explicit attributes first. “Why selected?” shows the rule/source; “Try phrase” runs resolution without recording a meal. Rules should be visible and editable, not hidden inside a system prompt.

Auto-ingestion upserts membership by canonical product identity, not display name. Creating a personal product that looks like an existing one offers reuse or a distinct variant; different brands/preparations remain separate. User edits of shared/provider data create a personal override/new revision and retain provenance. Archiving hides an item from future matching and disables its default rules while preserving historical references; the next query falls back or asks for a replacement. Automatic history refresh must not resurrect an archived card or overwrite a pinned choice.

### FatSecret history → catalog: required P1 flow

1. After connecting FatSecret, automatically start a background scan of the last 30 local calendar days. Offer another date range and a selective mode; default mode adds all eligible discovered products except explicitly excluded ones. The Web App shows progress, covered dates and partial/error state.
2. Read diary entries with `food_entries.get.v2`, retain stable food/serving references and load current structured product details. Recent/most-eaten/favorites enrich ordering and discovery but are not the complete diary. One `(provider, food_id)` produces one product card, with multiple known servings when applicable. Do not create consumed-food events as a side effect of catalog population.
3. In selective mode, show checkbox candidates with Select all / Add selected. Applying a selection records explicitly skipped candidates as excluded; unreviewed pages remain pending rather than excluded. Catalog actions include Select for a meal, Set default, Edit/fill fields, Add manually, Remove selected and Restore. Missing gram conversion or nutrition is marked incomplete and can be filled from a label or explicit user input; never default missing values to zero.
4. Refresh on demand and with a bounded daily background job; reread a recent overlapping window for late changes, and allow rescanning an older range. Persist a scan checkpoint and retry within provider quotas. Missing/partial history results do not remove catalog products automatically.
5. “Remove from My Products” records a user-owned exclusion keyed by provider food identity, disables associated default rules and hides the card. Automatic history imports and bot auto-add respect that exclusion; the user can explicitly restore it. Removal does not delete a FatSecret diary entry, shared provider product, or past local meal. Personal edits are field-level overrides with provenance, preserved across refresh.
6. Keep permanent IDs and user-authored preferences; expire provider metadata according to section 3. Imported usage dates/counts and nutrition do not become indefinitely cached simply because they appear in a catalog or import preview.

## 15. Settings worth exposing

**Existing** means backend fields/behavior already exist, not that a Web App control is implemented. **New** requires schema/service work. Validate ranges and explain the effective value and whether a setting is personal or system-wide.

| Group | What can be configured | Existing foundation / new work | Priority / owner |
|---|---|---|---|
| Products | Defaults, aliases, preparation, preferred serving, favorites/archive | New preferences and resolver | P1 user |
| FatSecret catalog population | History date range, automatic/selective mode, refresh now, daily refresh toggle, import progress and excluded-product restoration | New resumable history scan; independent from adding confirmed bot products | P1 user |
| Recording policy | Auto-record only exact confirmed matches, or review every entry; automatically add used products to My Products | New; default is exact-confirmed auto-record and catalog auto-add on | P1 user |
| Quantity | Gram presets, usual portion per product, ask for missing grams | New presets; explicit grams remain authoritative | P1 user |
| Nutrition goals | Daily kcal and protein/fat/carbohydrate grams | `users.daily_calorie_goal` exists; macro targets and date-effective goal history are new | P1 user |
| Profile / BMR | Birth year, sex used by the formula, height; show weight source and estimated BMR | Migration 012 + `bmr.py` exist; manual weight history is new | P1 profile; P2 manual weight |
| Language / timezone | uk/en and IANA timezone | Existing fields, i18n and timezone helpers; invalidate bot language caches after web edits | P1 user |
| Meal schedule | Breakfast/lunch/dinner boundaries and default meal suggestion | Currently inferred in the prompt; move to typed shared rules | P2 user |
| Notifications | Morning/evening briefing opt-in and local times; journal enabled and two times; sync-error notices | Journal fields exist; configurable briefing times, notification preferences and quiet hours are new | P1 existing journal + briefing controls; P2 quiet hours |
| Integrations | Connection status, reconnect/disconnect, last successful sync, last error; FatSecret export toggle | OAuth and Apple Health onboarding exist; self-service disconnect/status UI and export policy are new | P1 user |
| Health sources | Preferred weight/sleep/activity source; manual override; distinguish active energy, total burn and BMR estimate | Current WHOOP/Apple Health/BMR priority exists; configurable per-metric policy is new | P2 user |
| Product search | Preferred market, excluded candidates and provider fallback order when supported | New; backend still enforces account scopes and quotas | P2 user; P1 admin provider availability |
| Recipes / templates | Ingredients, cooked yield, named portions, saved breakfast combinations | New; changes produce a recipe revision | P2 user |
| Appearance | Follow Telegram light/dark theme, text sizing; later dashboard widgets and display units | Existing design only; store grams/kg canonically despite display units | P1 theme; P2 customization |
| Data portability | Export own entries/products/defaults; import personal products with preview and deduplication | New; omit secrets and follow provider-specific export/storage rules | P2 user |
| Operations | Available providers/models, feature flags, bounded AI usage budgets, job status, safe retries and audit of config changes | Environment settings/scheduler exist; role-protected UI and typed runtime allowlist are new | P1 admin core; P2 detailed budgets |

Settings are not arbitrary JSON edited in a textbox. Keep existing canonical user columns authoritative; add validated namespaced preferences for new values. Return `version` and effective defaults in the API, with field-level errors. Record goal changes by effective date and use local-day snapshots for historical goal comparisons. Share notification scheduling with bot commands and deduplicate by user/notification/local date/time, including DST and restart behavior. Integration controls cannot change iPhone permissions or create iOS automations; Web App must hand off to the existing device setup instructions.

## 16. Web App API, authorization, persistence and verification

### Architecture and endpoints

Proposed frontend: React + TypeScript + Vite in `web/`, built in a Docker build stage and served under HTTPS `/app/` on the FastAPI origin; `/api/v1/webapp/*` and `/api/v1/admin/*` serve JSON. Use Telegram's SDK for launch/theme/navigation; browser file upload plus backend barcode decoding is the MVP scan path. Telegram `showScanQrPopup` is documented for QR, not a general EAN/UPC scanner. Live camera scanning can follow after device capability testing.

| API group | Intended operations |
|---|---|
| `POST /api/v1/webapp/auth/telegram` | Validate raw initData and establish a short-lived user session |
| `/api/v1/webapp/me`, `/preferences`, `/goals` | Read/update own profile, supported preferences and date-effective goals |
| `/api/v1/webapp/products`, `/products/{id}/membership`, `/default-rules`, `/default-rules/preview` | Search/create/edit personal products, manage membership/archive, rules and read-only phrase preview |
| `/api/v1/webapp/catalog-imports`, `/catalog-imports/{id}`, `/catalog-imports/{id}/selection` | Start/resume FatSecret history scan, view status/candidates and add selected products; bulk membership removal/restoration uses the same ownership checks |
| `/api/v1/webapp/food-drafts`, `/food-drafts/{id}/commit`, `/food-entries` | Shared drafts, idempotent commits, manual entries and revision-based editing/voiding |
| `/api/v1/webapp/uploads`, `/integrations` | Bounded image intake, status and supported connect/disconnect/reconcile actions |
| `/api/v1/admin/catalog`, `/features`, `/jobs`, `/audit` | Role-protected shared catalog and operations; paginated/filtered results |

All resource IDs are resolved with ownership/visibility checks. Dates, weights, product versions and rule constraints are backend-validated. Use explicit idempotency keys for web-created requests (which have no Telegram message ID), and reuse the shared draft commit key for bot-to-Web App handoff. Write requests carry a version; stale versions return HTTP 409 (HTTP 412 if implementing the equivalent `If-Match` precondition). Invalidate/refetch affected queries on successful mutation and app reactivation; no full realtime infrastructure is needed initially.

### Authentication and admin access

Validate `Telegram.WebApp.initData` using Telegram's documented HMAC verification and backend bot token; reject invalid signatures, stale/future `auth_date` and missing user identity. Proposed login freshness window: five minutes with bounded clock skew, followed by a revocable one-hour server session. `initDataUnsafe`, URL IDs, usernames and client-submitted roles are not authorization. Use a same-origin Secure/HttpOnly cookie, SameSite policy plus CSRF token/Origin checks for mutations; test cookie behavior in Telegram clients and document a session transport alternative if needed. A normal browser without valid Telegram login gets an “Open in Telegram” entry page, not a production auth bypass.

Admin role is checked on each admin request and can be revoked server-side. Existing `ADMIN_API_TOKEN` remains an operator/server credential and is never embedded into a Mini App. Sessions, OAuth tokens and bot tokens never appear in links or frontend logs. Audit admin catalog/config actions by actor, target, revision and result; do not copy provider response bodies or private diary content into operational logs.

### Persistence and rollout additions

- Extend `user_food_preferences` with explicit default-rule scope, rule origin, pinned state, optional suggested portion and version; use a separate rules table if multiple aliases/contexts require it.
- Add `user_product_memberships` so one shared product can be in many personal catalogs without sharing preferences; fields include origin (`fatsecret_history`, `bot`, `manual`), active/excluded state, exclusion timestamp and stable provider identity. Retain exclusions across reconnects; archiving is not a deletion of the exclusion marker. Add resumable `catalog_import_jobs` with owner, provider, date range, mode, checkpoint, counts and status. Expiring candidate details follow the provider cache policy. `food_products` carries personal/shared visibility and revision references.
- Add typed new preferences, date-effective goal history, expiring Web App sessions, role assignments and compact admin change audit. Reuse existing profile/language/timezone/journal columns instead of duplicating them.
- All required migrations must be idempotent and registered in the current preflight migration path (`APPLE_HEALTH_MIGRATIONS` despite its historical name), with verification for added tables/constraints. Allocate numbers after the current migration set.
- Add frontend tests for product/default/gram editing and auth-expiry handling, backend ownership/role/version tests, PostgreSQL cross-client commit tests and end-to-end manual-default → bot-use → web-correction coverage.
- Update both documentation languages and bilingual page designs during implementation. Launch personal management first, then owner operations, behind separately controlled flags. P2 health forms, recipes, CSV imports and advanced settings receive separate estimates after the core release; FatSecret history population is P1.
