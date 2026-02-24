# 💪 Page: Activity
# Сторінка: Активність

## Overview | Огляд

**EN:** Page displaying WHOOP activity data including workouts, strain, and recovery.

**UA:** Сторінка з даними активності WHOOP: тренування, навантаження, відновлення.

---

## Layout | Макет

```
┌─────────────────────────────────────┐
│           HEADER                     │
│  [←]     "Activity"        [Sync]    │
├─────────────────────────────────────┤
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     WHOOP CONNECTION            │ │
│  │  ✓ Connected as @username       │ │
│  │  Last sync: 5 min ago           │ │
│  └─────────────────────────────────┘ │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     TODAY'S METRICS             │ │
│  │                                 │ │
│  │  ┌─────┐ ┌─────┐ ┌─────┐       │ │
│  │  │STRAIN│ │RECOV│ │SLEEP│       │ │
│  │  │ 12.5 │ │ 78% │ │7h32m│       │ │
│  │  │  🔥  │ │  💚  │ │  😴  │       │ │
│  │  └─────┘ └─────┘ └─────┘       │ │
│  │                                 │ │
│  └─────────────────────────────────┘ │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     CALORIES BURNED             │ │
│  │                                 │ │
│  │         450 kcal                │ │
│  │     from 1 workout              │ │
│  │                                 │ │
│  │  ═══════════════════════        │ │
│  │  Active: 320  |  Rest: 130      │ │
│  └─────────────────────────────────┘ │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     TODAY'S WORKOUTS            │ │
│  │  ─────────────────────────────  │ │
│  │  🏃 Running           10:00 AM  │ │
│  │     45 min | 320 kcal | 8.5     │ │
│  │     ├ Zone 3: 20 min            │ │
│  │     └ Zone 4: 15 min            │ │
│  │  [View Details →]               │ │
│  └─────────────────────────────────┘ │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     HEART RATE ZONES            │ │
│  │                                 │ │
│  │  Zone 5 ██░░░░░░░░ 5min         │ │
│  │  Zone 4 ████░░░░░░ 15min        │ │
│  │  Zone 3 ██████░░░░ 20min        │ │
│  │  Zone 2 ███░░░░░░░ 10min        │ │
│  │  Zone 1 █░░░░░░░░░ 5min         │ │
│  │                                 │ │
│  └─────────────────────────────────┘ │
│                                      │
├─────────────────────────────────────┤
│  [🏠]  [🍎]  [💪]  [📅]  [👤]      │
│               ●                      │
└─────────────────────────────────────┘
```

---

## Components | Компоненти

### 1. WHOOP Connection Status
**EN:** Shows connection status and last sync time.
**UA:** Показує статус підключення та час останньої синхронізації.

**Connected State:**
| Element | Specification |
|---------|---------------|
| Icon | ✓ checkmark, green |
| Status | "Connected as @username" |
| Last Sync | "Last sync: X min ago" |
| Sync Button | In header, triggers manual sync |

**Disconnected State:**
| Element | Specification |
|---------|---------------|
| Icon | ⚠️ warning, orange |
| Status | "Not connected" |
| Button | "Connect WHOOP" → OAuth flow |

### 2. Today's Metrics Row
**EN:** Three key WHOOP metrics.
**UA:** Три ключові показники WHOOP.

| Metric | Icon | Value | Color Logic |
|--------|------|-------|-------------|
| Strain | 🔥 | 0-21 scale | <10 blue, 10-15 yellow, >15 red |
| Recovery | 💚/💛/❤️ | 0-100% | >66% green, 33-66% yellow, <33% red |
| Sleep | 😴 | hours:min | Based on sleep performance |

**Card Style:**
- 3 equal columns
- Each card: centered content
- Icon below value
- Light background tint matching status color

### 3. Calories Burned Card
**EN:** Total calories burned today with breakdown.
**UA:** Загальна кількість спалених калорій з розбивкою.

| Element | Specification |
|---------|---------------|
| Total | 32px, Bold, Accent color |
| Subtitle | "from X workouts" |
| Progress Bar | Segmented: Active vs Rest |
| Legend | Active: [color] | Rest: [color] |

**Calculation:**
- Active calories = Sum of workout kilojoules / 4.184
- Rest calories = Estimated BMR portion for day

### 4. Today's Workouts List
**EN:** List of workouts performed today.
**UA:** Список тренувань за сьогодні.

| Element | Specification |
|---------|---------------|
| Sport Icon | Emoji based on sport_name |
| Sport Name | 16px, Bold |
| Time | 14px, Secondary, right |
| Stats Row | Duration | Calories | Strain |
| Zone Preview | Top 2 zones with time |
| Action | "View Details →" link |

**Sport Icons Mapping:**
| Sport | Icon |
|-------|------|
| Running | 🏃 |
| Cycling | 🚴 |
| Swimming | 🏊 |
| Strength | 🏋️ |
| HIIT | 💪 |
| Yoga | 🧘 |
| Other | 🏃 |

### 5. Heart Rate Zones Chart
**EN:** Horizontal bar chart showing time in each zone.
**UA:** Горизонтальна діаграма часу в кожній зоні.

| Zone | Color | HR Range |
|------|-------|----------|
| Zone 5 | Red | Max effort |
| Zone 4 | Orange | Hard |
| Zone 3 | Yellow | Moderate |
| Zone 2 | Light Green | Light |
| Zone 1 | Green | Very light |
| Zone 0 | Gray | Rest |

**Chart Style:**
- Horizontal bars
- Width proportional to time
- Time label at end of bar
- Zone label on left

---

## Workout Detail Modal | Модалка деталей тренування

```
┌─────────────────────────────────────┐
│  [×]        Running        Share    │
├─────────────────────────────────────┤
│                                      │
│  📅 January 28, 2026                │
│  🕐 10:00 AM - 10:45 AM (45 min)    │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │        METRICS                  │ │
│  │                                 │ │
│  │  Calories    Strain    Distance │ │
│  │    320        8.5       5.2km   │ │
│  │                                 │ │
│  │  Avg HR      Max HR    Recorded │ │
│  │   145         172        98%    │ │
│  └─────────────────────────────────┘ │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     HEART RATE CHART            │ │
│  │  180─┐        ╭─╮               │ │
│  │      │     ╭──╯ ╰──╮            │ │
│  │  140─┤  ╭──╯       ╰──╮         │ │
│  │      │╭─╯             ╰─╮       │ │
│  │  100─┴──────────────────┴──     │ │
│  │      0    15    30    45 min    │ │
│  └─────────────────────────────────┘ │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │     ZONE BREAKDOWN              │ │
│  │  Zone 5: 3 min (7%)             │ │
│  │  Zone 4: 12 min (27%)           │ │
│  │  Zone 3: 18 min (40%)           │ │
│  │  Zone 2: 8 min (18%)            │ │
│  │  Zone 1: 4 min (9%)             │ │
│  └─────────────────────────────────┘ │
│                                      │
└─────────────────────────────────────┘
```

---

## WHOOP Connection Flow | Потік підключення WHOOP

### Step 1: Connect Button
```
┌─────────────────────────────────────┐
│                                      │
│     [WHOOP Logo]                     │
│                                      │
│  Connect your WHOOP to sync          │
│  workouts and recovery data          │
│  automatically.                      │
│                                      │
│  Підключіть WHOOP для автоматичної   │
│  синхронізації тренувань.            │
│                                      │
│  [ Connect WHOOP ]                   │
│                                      │
│  We'll request access to:            │
│  • Workouts & strain                 │
│  • Recovery scores                   │
│  • Sleep data                        │
│                                      │
└─────────────────────────────────────┘
```

### Step 2: OAuth Redirect
- Opens WHOOP authorization page
- User logs in & approves
- Redirect back to app

### Step 3: Success
```
┌─────────────────────────────────────┐
│                                      │
│           ✓                          │
│                                      │
│  WHOOP Connected!                    │
│  WHOOP підключено!                   │
│                                      │
│  Syncing your data...                │
│                                      │
└─────────────────────────────────────┘
```

---

## States | Стани

### No WHOOP Connected
- Show connection CTA
- Explain benefits
- "Connect WHOOP" button

### No Workouts Today
```
No workouts yet today.
Ще немає тренувань сьогодні.

Your WHOOP will automatically
detect and sync activities.

[View past workouts]
```

### Syncing State
- Spinner on sync button
- "Syncing..." text
- Disable sync button

### Error State
```
Couldn't sync WHOOP data.
Не вдалося синхронізувати.

[Try Again]  [Reconnect]
```

---

## Interactions | Взаємодії

| Element | Action |
|---------|--------|
| Sync Button | Manual sync trigger |
| Metric Card | Tap → show explanation modal |
| Workout Item | Tap → Workout detail modal |
| Zone Bar | Tap → highlight zone info |
| "View Details" | → Workout detail modal |

---

## Data Refresh | Оновлення даних

- Auto-refresh on page load
- Pull-to-refresh support
- Background sync every 15 minutes
- Manual sync button in header

---

## Accessibility | Доступність

- Metric colors have text equivalents
- Chart has alternative text representation
- All values announced by screen readers
- Sync status clearly announced
