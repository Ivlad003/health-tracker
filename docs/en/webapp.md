# 📱 Telegram Web App (Mini App)

[🇺🇦 Українська версія](../uk/webapp.md)

The Mini App is a React 19 + TypeScript + Vite app in `web/`, built into `web/dist` by the Docker image and served by FastAPI at `/app/`. Users open it with the bot's `/app` command. Its JSON API is described in [food-logging.md §6](food-logging.md#6-web-app-api).

## 1. Layout

```
web/
├── index.html              # loads telegram-web-app.js from telegram.org
├── eslint.config.js        # typescript-eslint (type-checked) + react-hooks
├── vite.config.ts          # base /app/, dev proxy /api → :8000, vitest config
└── src/
    ├── App.tsx             # boot/auth phases, hash router, bottom nav, BackButton
    ├── api.ts              # fetch wrapper: bearer session, 401 → re-login once, ApiError
    ├── errors.ts           # error code → translated message
    ├── i18n.ts             # uk/en dictionaries (key parity enforced by the type), formatters
    ├── LangContext.tsx     # useT(): { lang, t, err }
    ├── telegram.ts         # theme, haptics, native confirm, openLink wrappers
    ├── hooks/              # useApi (AbortController), useAction (busy/error/notice), useBackButton
    ├── components/         # Section, Field (label ↔ input), Switch, Banner, Loading (skeleton), EntryList, NavIcon
    ├── lib/                # pure logic: balance tone, meal/portion, image downscale
    └── pages/              # Dashboard, Food (+ food/*), Activity, History, Profile (+ profile/*), Admin (lazy)
```

## 2. Authentication and sessions

1. On start the app calls `Telegram.WebApp.ready()/expand()` and, without a stored session, posts `initData` to `POST /api/v1/webapp/auth/telegram`.
2. The session token is kept in `sessionStorage` (cleared when the WebView closes) and sent as `Authorization: Bearer`.
3. Any activity slides the server-side expiry (`WEBAPP_SESSION_TTL_SECONDS`, default 1 h idle) up to an absolute lifetime of 12 h (`SESSION_MAX_LIFETIME` in `app/services/webapp_auth.py`).
4. On a 401 `api()` logs in again with the same `initData` (single flight for concurrent requests) and replays the request. That works while `initData` is fresh (`WEBAPP_AUTH_MAX_AGE_SECONDS`, 5 min); after that the app shows "Session ended — reopen from the bot". Each re-login revokes the session the same `initData` created before, and the number of exchanges is capped at 5 (`INIT_DATA_MAX_USES`, same file).
5. Identity always comes from the validated server session. `initDataUnsafe.user` is used for display only; `ADMIN_API_TOKEN` never reaches the browser; the admin tab is shown from `/me.is_admin`, and the server re-checks the role on every admin request.

## 3. Data loading and errors

- `useApi(loader)` cancels the previous request on re-run/unmount (no state updates after unmount, no stale overwrite). Loaders are `useCallback`s.
- `useAction()` wraps mutations: one at a time, buttons are disabled while busy, errors never become unhandled rejections, success/error haptics.
- `api()` turns HTML error pages (proxy 502) and network failures into `ApiError` (`server_error`, `network_error`, `bad_response`); `errorMessage()` maps codes to translated text — raw backend codes are never shown.
- Every screen has a skeleton while loading and an error state with **Retry**.

## 4. Screens

| Screen | What it does |
|---|---|
| Dashboard | Balance (deficit green / on target blue / surplus orange / large surplus red), goal progress bar, food & activity tiles (tap → History / Activity), quick log, today's entries |
| Food | Meal pills (default from local time, same boundaries as the bot), grams (empty = the product's usual portion; a typed value always wins), debounced search with FatSecret import, My Products one-tap log, today's entries with delete, photo upload (downscaled to ≤ 1600 px JPEG before upload), drafts, new product, "Try phrase" |
| Activity | WHOOP connect/metrics, Apple Health metrics with localized units and last sync time |
| History | Last 7 days from one `GET /food-entries/range` call, localized day names, expandable days |
| Profile | Separate cards with their own Save: calorie goal (validated 500–10 000), personal data (timezone picker, `type="time"` reminders), recording preferences, integrations (disconnect asks for confirmation), default phrases (product chosen from My Products) |
| Admin | Feature flags, outbox failures and history-import failures with retry (lazy-loaded chunk) |

## 5. Telegram integration

- Theme: CSS uses the `--tg-theme-*` variables the SDK sets; `<html data-theme>` follows `colorScheme` and `themeChanged`; outside Telegram `prefers-color-scheme` is used. Header/background colours follow the theme.
- Native **BackButton** on every screen except Dashboard; **HapticFeedback** on mutations; **showConfirm** for destructive actions; `openLink` for OAuth.
- Safe areas: `env(safe-area-inset-bottom)`, `--tg-viewport-stable-height`, `--tg-content-safe-area-inset-top`.
- Accessibility: every input has a `<label for>`, switches use `role="switch"`, banners `role="alert"/"status"`, the progress bar `role="progressbar"`, nav buttons `aria-current`; touch targets ≥ 44 px.

## 6. Serving and caching

`app/main.py` adds for `/app/*`: `Content-Security-Policy` (scripts only from self and `https://telegram.org`, `frame-ancestors` self + `*.telegram.org` for Telegram Web), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-cache` for `index.html`, and `public, max-age=31536000, immutable` for the content-hashed `/app/assets/*`. The API gets `Cache-Control: no-store`. Routing is hash-based (`/app/#food`), so no server-side SPA fallback is needed.

## 7. Development

```bash
cd web
npm ci
npm run dev        # http://localhost:5173/app/, /api proxied to uvicorn on :8000
npm run lint       # eslint (type-aware, react-hooks, no floating promises)
npm test           # vitest: api client, i18n parity, pure logic
npm run build      # tsc (app + vite config) and vite build → web/dist
```

Outside Telegram there is no `initData`, so the app shows the "open from the bot" screen. To work in a desktop browser, create a session with a signed initData (e.g. `app.services.webapp_auth.sign_init_data` against a local bot token), then run `sessionStorage.setItem("ht_session", "<token>")` in the dev console and reload.

CI (`.github/workflows/apple-health-postgres.yml`, job `webapp`) runs `npm ci`, `lint`, `test` and `build` on every push/PR.

## 8. Not implemented yet (design roadmap)

`docs/design/` describes the target UI. These parts are **not** built yet and are tracked as roadmap items, not bugs:

- Dashboard: voice button (voice goes to the bot), pull-to-refresh, activity entries mixed into "Recent".
- Food Log: in-app voice recording, free-text "Analyze" with parsed-item checkboxes, live barcode camera (photo upload is the scan path; Telegram's scanner is QR-only), serving editor modal.
- Activity: WHOOP sync button, HR-zone chart, workout list/detail modal.
- History: Day/Week/Month/Custom periods, summary card, charts, filters, search, export.
- Profile: avatar/"member since", protein/activity goals, units, account export/delete, About/Help.
